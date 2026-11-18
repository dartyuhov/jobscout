const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const Database = require("better-sqlite3");

const repo = path.resolve(__dirname, "..");
test("malformed saved schema disables portal and CLI and prevents credential reads", async (t) => {
  const app = await fixture(t);
  const db = new Database(app.env.DB_PATH);
  t.after(() => db.close());
  const valid = { base_url: app.base_url, model: "local", token: "secret", revision: "1", verified_revision: "1" };
  for (const change of [{ base_url: [] }, { base_url: "file:///private" }, { model: {} }, { model: " " }, { token: {} }, { revision: 1, verified_revision: 1 }, { revision: true, verified_revision: true }, { verified_revision: {} }]) {
    db.prepare("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('ai_connection', ?, 'now')").run(JSON.stringify({ ...valid, ...change }));
    assert.equal((await app.request()).body.ready, false, JSON.stringify(change));
    assert.equal((await app.request("/api/ai/chat", "POST", { messages: [{ role: "user", content: "hello" }] })).status, 503);
    const cli = spawnSync(path.join(repo, ".venv/bin/python"), ["-c", "from job_search_cli.ai_client import read_connection; read_connection()"], { cwd: repo, env: app.env, encoding: "utf8" });
    assert.notEqual(cli.status, 0, JSON.stringify(change));
    assert.match(cli.stderr, /AI is not configured/);
  }
  assert.equal(app.requests.length, 0);
});

test("startup backfill sends nothing after verification until summaries alone are opted in", async (t) => {
  const app = await fixture(t);
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local" });
  await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  const db = new Database(app.env.DB_PATH);
  t.after(() => db.close());
  const id = Number(db.prepare("INSERT INTO linkedin_jobs (linkedin_job_id, about_text, created_at, updated_at) VALUES ('456', 'Python job', 'now', 'now')").run().lastInsertRowid);
  db.prepare("INSERT INTO ai_analysis (job_id, analyzed, desicion) VALUES (?, 1, 1)").run(id);
  const summary = { must_haves: ["Python"], nice_to_haves: [], company: { name: "Unknown", industry: "Unknown" }, application_type: "Unknown" };
  app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(summary) }, finish_reason: "stop" }] })));
  await app.restart();
  assert.equal(app.requests.length, 1);
  await app.request("/api/settings/ai/automation", "PUT", { auto_analyze: true, auto_summarize: false });
  await app.restart();
  assert.equal(app.requests.length, 1);
  await app.request("/api/settings/ai/automation", "PUT", { auto_analyze: false, auto_summarize: true });
  await app.restart();
  for (let i = 0; i < 100 && !db.prepare("SELECT ai_summary FROM linkedin_jobs WHERE id=?").get(id).ai_summary; i++) await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(app.requests.length, 2);
  assert.deepEqual(JSON.parse(db.prepare("SELECT ai_summary FROM linkedin_jobs WHERE id=?").get(id).ai_summary), summary);
});

test("automatic processing requires independent explicit boolean opt-ins shared with CLI", async (t) => {
  const app = await fixture(t);
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local" });
  await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  assert.equal(app.requests.length, 1);
  let settings = await app.request("/api/settings/ai/automation", "PUT", { auto_analyze: true, auto_summarize: false });
  assert.equal(settings.status, 200);
  assert.equal(settings.body.auto_analyze, true);
  assert.equal(settings.body.auto_summarize, false);
  const cli = spawnSync(path.join(repo, ".venv/bin/python"), ["-c", "from job_search_cli.ai_readiness import get_ai_readiness; print(get_ai_readiness())"], { cwd: repo, env: app.env, encoding: "utf8" });
  assert.equal(cli.status, 0, cli.stderr);
  assert.match(cli.stdout, /auto_analyze=True, auto_summarize=False/);
  settings = await app.request("/api/settings/ai/automation", "PUT", { auto_analyze: false, auto_summarize: true });
  assert.equal(settings.body.auto_analyze, false);
  assert.equal(settings.body.auto_summarize, true);
  assert.equal((await app.request("/api/settings/ai/automation", "PUT", { auto_analyze: "true", auto_summarize: true })).status, 400);
  await app.request(undefined, "PUT", { base_url: app.base_url, model: "changed" });
  assert.equal((await app.request()).body.ready, false);
  // Connection writes never enqueue processing, even after an earlier opt-in.
  assert.equal(app.requests.length, 1);
});

test("portal analysis and summaries persist validated compatible generations with minimal context", async (t) => {
  const app = await fixture(t);
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local", token: "private-token" });
  await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  const db = new Database(app.env.DB_PATH);
  t.after(() => db.close());
  db.prepare("INSERT INTO settings (key, value, updated_at) VALUES ('cv_text', 'PRIVATE-CV', 'now')").run();
  const id = Number(db.prepare("INSERT INTO linkedin_jobs (linkedin_job_id, company_name, position_name, about_text, created_at, updated_at) VALUES ('123', 'Acme', 'QA', 'Remote testing role', 'now', 'now')").run().lastInsertRowid);
  const decision = { analyzed: 1, desicion: 1, reasoning: "No exclusions apply" };
  const summary = { must_haves: ["Testing"], nice_to_haves: [], company: { name: "Acme", industry: "Unknown" }, application_type: "Unknown" };
  let output = decision;
  app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(output) }, finish_reason: "stop" }] })));
  assert.equal((await app.request(`/api/jobs/${id}/analyze`, "POST")).status, 200);
  assert.equal(db.prepare("SELECT desicion FROM ai_analysis WHERE job_id = ?").get(id).desicion, 1);
  output = summary;
  assert.equal((await app.request(`/api/jobs/${id}/summarize`, "POST")).status, 200);
  assert.deepEqual(JSON.parse(db.prepare("SELECT ai_summary FROM linkedin_jobs WHERE id = ?").get(id).ai_summary), summary);
  for (const request of app.requests.slice(1)) {
    assert.equal(request.body.model, "local");
    assert.equal(request.body.tools, undefined);
    assert.ok(!JSON.stringify(request.body).includes("PRIVATE-CV"));
    assert.ok(!JSON.stringify(request.body).includes("private-token"));
  }
  output = { ...decision, desicion: true, reasoning: "private-token" };
  const malformed = await app.request(`/api/jobs/${id}/analyze`, "POST");
  assert.equal(malformed.status, 502);
  assert.ok(!JSON.stringify(malformed).includes("private-token"));
  assert.equal(db.prepare("SELECT reasoning FROM ai_analysis WHERE job_id = ?").get(id).reasoning, decision.reasoning);
  db.prepare("UPDATE linkedin_jobs SET ai_summary=NULL WHERE id=?").run(id);
  for (const invalid of ["not JSON", { ...summary, must_haves: "Python" }, { ...summary, company: { name: "Acme" } }]) {
    output = invalid;
    assert.equal((await app.request(`/api/jobs/${id}/summarize`, "POST")).status, 502);
    assert.equal(db.prepare("SELECT ai_summary FROM linkedin_jobs WHERE id=?").get(id).ai_summary, null);
  }
  app.reply((_req, res) => { res.statusCode = 401; res.end("private-token"); });
  for (const action of ["analyze", "summarize"]) {
    const failure = await app.request(`/api/jobs/${id}/${action}`, "POST");
    assert.equal(failure.status, 502);
    assert.match(failure.body.error, /HTTP 401/);
    assert.ok(!JSON.stringify(failure).includes("private-token"));
  }
  assert.ok(!app.logs().includes("private-token"));
});

test("escaped credentials are redacted after structured output decoding before persistence", async (t) => {
  const app = await fixture(t);
  const db = new Database(app.env.DB_PATH);
  t.after(() => db.close());
  for (const token of ['quote"secret', "unicode-é-secret"]) {
    const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local", token });
    await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
    const id = Number(db.prepare("INSERT INTO linkedin_jobs (linkedin_job_id, about_text, created_at, updated_at) VALUES (?, 'job', 'now', 'now')").run(token).lastInsertRowid);
    for (const [action, output] of [["analyze", { analyzed: 1, desicion: 1, reasoning: token }], ["summarize", { must_haves: [token], nice_to_haves: [], company: { name: token, industry: "Unknown" }, application_type: token }]]) {
      const content = JSON.stringify(output).replace(/é/g, "\\u00e9");
      app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content }, finish_reason: "stop" }] })));
      assert.equal((await app.request(`/api/jobs/${id}/${action}`, "POST")).status, 200);
    }
    assert.equal(db.prepare("SELECT reasoning FROM ai_analysis WHERE job_id=?").get(id).reasoning, "[redacted]");
    assert.deepEqual(JSON.parse(db.prepare("SELECT ai_summary FROM linkedin_jobs WHERE id=?").get(id).ai_summary).must_haves, ["[redacted]"]);
    app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: "OK" }, finish_reason: "stop" }] })));
  }
});

async function fixture(t) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ai-settings-"));
  const env = { ...process.env, JOB_SEARCH_DATA_DIR: temp, DB_PATH: path.join(temp, "jobs.db"), HOST: "127.0.0.1" };
  let child;
  let logs = "";
  const requests = [];
  let reply = (_req, res) => res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "OK" }, finish_reason: "stop" }] }));
  const provider = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    requests.push({ url: req.url, headers: req.headers, body: JSON.parse(body) });
    res.setHeader("content-type", "application/json");
    reply(req, res);
  });
  provider.listen(0, "127.0.0.1");
  await once(provider, "listening");
  const base_url = `http://127.0.0.1:${provider.address().port}/compatible/v1`;
  let url;
  async function stop() {
    if (!child) return;
    const exited = once(child, "exit");
    child.kill();
    await exited;
    child = null;
  }
  async function start() {
    const portServer = http.createServer();
    portServer.listen(0, "127.0.0.1");
    await once(portServer, "listening");
    const port = portServer.address().port;
    await new Promise((resolve) => portServer.close(resolve));
    url = `http://127.0.0.1:${port}`;
    child = spawn(process.execPath, [path.join(__dirname, "server.js")], { cwd: repo, env: { ...env, PORT: String(port) }, stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", (data) => { logs += data; });
    child.stderr.on("data", (data) => { logs += data; });
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(logs);
      try { if ((await fetch(`${url}/api/health`)).ok) return; } catch {}
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
    throw new Error(logs);
  }
  t.after(async () => { await stop(); await new Promise((resolve) => provider.close(resolve)); fs.rmSync(temp, { recursive: true, force: true }); });
  await start();
  return {
    base_url, requests, env,
    logs: () => logs,
    reply: (fn) => { reply = fn; },
    restart: async () => { await stop(); await start(); },
    request: async (route = "/api/settings/ai", method = "GET", body) => {
      const response = await fetch(`${url}${route}`, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
      return { status: response.status, body: await response.json() };
    },
    raw: (body) => fetch(`${url}/api/settings/ai`, { method: "PUT", headers: { "content-type": "application/json" }, body }),
  };
}

test("Settings saves a private connection and explicitly verifies real authenticated generation across restarts", async (t) => {
  const app = await fixture(t);
  const secret = "synthetic-private-token";
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "test-model", token: secret });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.token_present, true);
  assert.equal(saved.body.ready, false);
  assert.equal(app.requests.length, 0);
  await app.restart();
  const read = await app.request();
  assert.equal(read.body.base_url, app.base_url);
  assert.equal(read.body.model, "test-model");
  assert.equal(read.body.token_present, true);
  const tested = await app.request("/api/settings/ai/test", "POST", { revision: read.body.revision });
  assert.equal(tested.status, 200);
  assert.equal(tested.body.ready, true);
  assert.equal(tested.body.auto_analyze, false);
  assert.equal(tested.body.auto_summarize, false);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].url, "/compatible/v1/chat/completions");
  assert.equal(app.requests[0].headers.authorization, `Bearer ${secret}`);
  assert.deepEqual(app.requests[0].body, { model: "test-model", messages: [{ role: "user", content: "Reply with OK to test text generation." }], stream: false });
  await app.restart();
  assert.equal((await app.request()).body.ready, true);
  assert.equal((await app.request("/api/health")).body.ai.ready, true);
  for (const result of [saved, read, tested]) assert.ok(!JSON.stringify(result).includes(secret));
  assert.ok(!app.logs().includes(secret));
});

test("malformed JSON never echoes a submitted token in responses or logs", async (t) => {
  const app = await fixture(t);
  const secret = "SECRET";
  const response = await app.raw(`${secret} {"token":"${secret}", invalid}`);
  assert.equal(response.status, 400);
  assert.ok(!(await response.text()).includes(secret));
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.ok(!app.logs().includes(secret));
});

test("malformed stored connection fails closed without exposing private data", async (t) => {
  const app = await fixture(t);
  const secret = "synthetic-corrupted-secret";
  const db = new Database(app.env.DB_PATH);
  try {
    db.prepare("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)")
      .run("ai_connection", `{"token":"${secret}", invalid}`, "now");
  } finally { db.close(); }
  const health = await app.request("/api/health");
  assert.equal(health.status, 200);
  assert.equal(health.body.ai.ready, false);
  const settings = await app.request();
  assert.equal(settings.body.ready, false);
  assert.equal(settings.body.token_present, false);
  assert.equal((await app.request("/api/ai/chat", "POST", { message: "hello" })).status, 503);
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local" });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.ready, false);
  assert.equal(app.requests.length, 0);
  assert.ok(!app.logs().includes(secret));
});

test("token removal, replacement, model and endpoint changes invalidate verification; CLI reads the same readiness", async (t) => {
  const app = await fixture(t);
  const save = (body) => app.request(undefined, "PUT", body);
  const verify = async () => app.request("/api/settings/ai/test", "POST", { revision: (await app.request()).body.revision });
  await save({ base_url: app.base_url, model: "local", token: "first-token" });
  await verify();
  const cliReadiness = () => {
    const result = spawnSync(path.join(repo, ".venv/bin/python"), ["-c", "import json; from dataclasses import asdict; from job_search_cli.ai_readiness import get_ai_readiness; print(json.dumps(asdict(get_ai_readiness())))"], { cwd: repo, env: app.env, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  assert.equal(cliReadiness().ready, true);
  for (const change of [{ token: "second-token" }, { token: "" }, { model: "another-model" }, { base_url: `${app.base_url}/other` }]) {
    const current = (await app.request()).body;
    const saved = await save({ base_url: current.base_url, model: current.model, ...change });
    assert.equal(saved.body.ready, false);
    assert.equal(cliReadiness().ready, false);
    assert.equal((await app.request("/api/ai/chat", "POST", { message: "hello" })).status, 503);
    assert.equal((await verify()).body.ready, true);
  }
  assert.equal(app.requests[1].headers.authorization, "Bearer second-token");
  assert.equal(app.requests[2].headers.authorization, undefined);
  assert.equal((await app.request()).body.token_present, false);
  assert.equal(app.requests.at(-1).body.model, "another-model");
  assert.equal(cliReadiness().auto_analyze, false);
  assert.equal(cliReadiness().auto_summarize, false);
  // Real CLI commands get past the shared gate when verified (then reject a
  // missing profile), and stop at that gate again after a settings change.
  for (const command of ["analyze-all", "summarize-all"]) {
    const run = () => spawnSync(path.join(repo, ".venv/bin/python"), ["-m", "job_search_cli.cli", command, "--profile-id", "999"], { cwd: repo, env: app.env, encoding: "utf8" });
    assert.match(run().stderr, /was not found/);
  }
  const unchanged = await save({ base_url: `${app.base_url}/other/`, model: "another-model" });
  assert.equal(unchanged.body.ready, true);
  await save({ base_url: app.base_url, model: "unverified" });
  for (const command of ["analyze-all", "summarize-all"]) {
    const result = spawnSync(path.join(repo, ".venv/bin/python"), ["-m", "job_search_cli.cli", command, "--profile-id", "999"], { cwd: repo, env: app.env, encoding: "utf8" });
    assert.match(result.stderr, /Configure and verify an AI connection in Settings/);
  }
});

test("failed generation and malformed replies stay unready and never echo provider secrets", async (t) => {
  const app = await fixture(t);
  const secret = "synthetic-error-token";
  await app.request(undefined, "PUT", { base_url: app.base_url, model: "local", token: secret });
  const revision = (await app.request()).body.revision;
  for (const [status, payload, expected] of [
    [401, { error: { message: `Bearer ${secret}` } }, /HTTP 401.*token/],
    [404, { error: secret }, /base URL and model/],
    [429, { error: secret }, /quota/],
    [200, { models: ["local"], secret }, /complete text generation/],
    [200, { choices: [{ message: { content: "", tool_calls: [{ function: { name: secret } }] }, finish_reason: "tool_calls" }] }, /complete text generation/],
    [200, "not-json", /invalid JSON/],
  ]) {
    app.reply((_req, res) => { res.statusCode = status; res.end(typeof payload === "string" ? payload : JSON.stringify(payload)); });
    const response = await app.request("/api/settings/ai/test", "POST", { revision });
    assert.equal(response.status, 502);
    assert.match(response.body.error, expected);
    assert.ok(!JSON.stringify(response).includes(secret));
    assert.equal((await app.request()).body.ready, false);
  }
  assert.ok(!app.logs().includes(secret));
});

test("a test cannot verify changed settings, including an A-B-A change", async (t) => {
  const app = await fixture(t);
  const original = { base_url: app.base_url, model: "model-A" };
  let release;
  let arrived;
  const waiting = new Promise((resolve) => { arrived = resolve; });
  app.reply((_req, res) => { release = () => res.end(JSON.stringify({ choices: [{ message: { content: "OK" }, finish_reason: "stop" }] })); arrived(); });
  const saved = await app.request(undefined, "PUT", original);
  const pending = app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  await waiting;
  await app.request(undefined, "PUT", { ...original, model: "model-B" });
  await app.request(undefined, "PUT", original);
  release();
  assert.equal((await pending).status, 409);
  assert.equal((await app.request()).body.ready, false);
  const stale = await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  assert.equal(stale.status, 409);
  assert.equal(app.requests.length, 1);
});

test("the newest explicit test wins when tests overlap", async (t) => {
  const app = await fixture(t);
  const saved = await app.request(undefined, "PUT", { base_url: app.base_url, model: "local" });
  let release;
  let arrived;
  const waiting = new Promise((resolve) => { arrived = resolve; });
  app.reply((_req, res) => { release = () => res.end(JSON.stringify({ choices: [{ message: { content: "OK" }, finish_reason: "stop" }] })); arrived(); });
  const oldTest = app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision });
  await waiting;
  app.reply((_req, res) => { res.statusCode = 401; res.end("unauthorized"); });
  assert.equal((await app.request("/api/settings/ai/test", "POST", { revision: saved.body.revision })).status, 502);
  release();
  assert.equal((await oldTest).status, 409);
  assert.equal((await app.request()).body.ready, false);
});

test("invalid Settings input is rejected without echoing credentials", async (t) => {
  const app = await fixture(t);
  const secret = "synthetic-validation-secret";
  for (const body of [
    { base_url: `http://user:${secret}@localhost/v1`, model: "local" },
    { base_url: `https://localhost/v1?token=${secret}`, model: "local" },
    { base_url: `file:///${secret}`, model: "local" },
    { base_url: app.base_url, model: "", token: secret },
    { base_url: app.base_url, model: "local", token: `${secret}\r\nAuthorization: other` },
    { base_url: app.base_url, model: "local", token: { secret } },
  ]) {
    const response = await app.request(undefined, "PUT", body);
    assert.equal(response.status, 400);
    assert.ok(!JSON.stringify(response).includes(secret));
  }
  assert.equal(app.requests.length, 0);
  assert.equal((await app.request()).body.token_present, false);
  assert.ok(!app.logs().includes(secret));
});
