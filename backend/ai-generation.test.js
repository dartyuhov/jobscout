const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const Database = require("better-sqlite3");

async function fixture(t, token = "") {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ai-generation-"));
  const blocker = path.join(temp, "no-legacy.cjs");
  fs.writeFileSync(blocker, `
    const Module = require('node:module');
    const { basename } = require('node:path');
    const load = Module._load;
    Module._load = function (id, ...args) {
      if (/^(opencode|google.?sheets|telegram)([.]|$)/i.test(basename(id))) throw new Error('Legacy integration unavailable: ' + id);
      return load.call(this, id, ...args);
    };
  `);
  const requests = [];
  let logs = "";
  let reply = (_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: "Generated answer" }, finish_reason: "stop" }] }));
  const provider = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    requests.push({ url: req.url, headers: req.headers, body: JSON.parse(body) });
    res.setHeader("content-type", "application/json");
    reply(req, res);
  });
  provider.listen(0, "127.0.0.1");
  await once(provider, "listening");
  const base_url = `http://127.0.0.1:${provider.address().port}/v1`;
  const portServer = http.createServer();
  portServer.listen(0, "127.0.0.1");
  await once(portServer, "listening");
  const port = portServer.address().port;
  await new Promise((resolve) => portServer.close(resolve));
  const child = spawn(process.execPath, ["--require", blocker, path.join(__dirname, "server.js")], {
    cwd: path.resolve(__dirname, ".."),
    env: { ...process.env, JOB_SEARCH_DATA_DIR: temp, DB_PATH: path.join(temp, "jobs.db"), HOST: "127.0.0.1", PORT: String(port), OPENCODE_BIN: path.join(temp, "no-opencode") },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (data) => { logs += data; });
  child.stderr.on("data", (data) => { logs += data; });
  t.after(async () => {
    if (child.exitCode === null) { const exited = once(child, "exit"); child.kill(); await exited; }
    await new Promise((resolve) => provider.close(resolve));
    fs.rmSync(temp, { recursive: true, force: true });
  });
  const request = async (route, body, method = "POST") => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  let started = false;
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error(logs);
    try { if ((await request("/api/health", undefined, "GET")).status === 200) { started = true; break; } } catch {}
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.ok(started, logs);
  const verify = async () => {
    const saved = await request("/api/settings/ai", { base_url, model: "generation-model", token }, "PUT");
    const tested = await request("/api/settings/ai/test", { revision: saved.body.revision });
    assert.equal(tested.status, 200);
    requests.length = 0;
  };
  const job = (overrides = {}) => {
    const db = new Database(path.join(temp, "jobs.db"));
    try {
      const { company = "Selected Company", position = "QA Engineer", description = "SELECTED_JOB_DESCRIPTION" } = overrides;
      return Number(db.prepare("INSERT INTO linkedin_jobs (linkedin_job_id, company_name, position_name, about_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
        .run(String(Math.random()), company, position, description, "now", "now").lastInsertRowid);
    } finally { db.close(); }
  };
  return { request, requests, verify, job, base_url, logs: () => logs, reply: (fn) => { reply = fn; } };
}

test("Ask AI answers general questions without a CV using actual generation-only conversation messages", async (t) => {
  const app = await fixture(t);
  await app.verify();
  const messages = [
    { role: "user", content: "What is a screening interview?" },
    { role: "assistant", content: "An initial conversation." },
    { role: "user", content: "How should I prepare?" },
  ];
  const result = await app.request("/api/ai/chat", { messages });
  assert.equal(result.status, 200);
  assert.equal(result.body.reply, "Generated answer");
  assert.equal(result.body.selected_job, null);
  assert.equal(app.requests.length, 1);
  const sent = app.requests[0];
  assert.equal(sent.url, "/v1/chat/completions");
  assert.equal(sent.headers.authorization, undefined);
  assert.deepEqual(Object.keys(sent.body).sort(), ["messages", "model", "stream"]);
  assert.equal(sent.body.model, "generation-model");
  assert.equal(sent.body.stream, false);
  assert.equal(sent.body.messages[0].role, "system");
  assert.deepEqual(sent.body.messages.slice(-3), messages);
});

test("cover letters use the verified endpoint with only the selected role and CV, returning a downloadable result", async (t) => {
  const app = await fixture(t, "synthetic-generation-secret");
  await app.verify();
  const jobId = app.job();
  app.job({ description: "UNRELATED_PRIVATE_JOB" });
  const missing = await app.request("/api/cover-letter/generate", { job_id: jobId });
  assert.equal(missing.status, 400);
  assert.equal(missing.body.code, "CV_REQUIRED");
  assert.equal(app.requests.length, 0);
  await app.request("/api/settings/cv", { cv_text: "SYNTHETIC_CV: QA automation experience" }, "PUT");
  const letter = "Dear Hiring Team,\n\nMy QA automation experience aligns with your role.\n\nBest regards";
  app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: letter }, finish_reason: "stop" }] })));
  const result = await app.request("/api/cover-letter/generate", { job_id: jobId });
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { cover_letter: letter, filename: "QA_Engineer_Selected_Company_cover_letter.txt" });
  assert.equal(app.requests.length, 1);
  const sent = app.requests[0];
  assert.equal(sent.headers.authorization, "Bearer synthetic-generation-secret");
  assert.deepEqual(Object.keys(sent.body).sort(), ["messages", "model", "stream"]);
  const context = JSON.parse(sent.body.messages[1].content);
  assert.deepEqual(context, { position_name: "QA Engineer", company_name: "Selected Company", job_description: "SELECTED_JOB_DESCRIPTION", resume_text: "SYNTHETIC_CV: QA automation experience" });
  assert.ok(!JSON.stringify(sent.body).includes("UNRELATED_PRIVATE_JOB"));
  assert.ok(!app.logs().includes("synthetic-generation-secret"));
});

test("Ask AI shares CV only for explicit intent, preserves selected-job context and bounds history", async (t) => {
  const app = await fixture(t);
  await app.verify();
  const jobId = app.job();
  app.job({ description: "UNRELATED_PRIVATE_JOB" });
  const messages = [{ role: "user", content: "Explain this requirement." }];
  for (const mode of ["cv", "application_question"]) {
    const missing = await app.request("/api/ai/chat", { messages, mode });
    assert.equal(missing.body.code, "CV_REQUIRED");
  }
  assert.equal(app.requests.length, 0);
  await app.request("/api/settings/cv", { cv_text: "PRIVATE_CV_MARKER" }, "PUT");
  for (const options of [{}, { mode: "job" }, { mode: "cv" }, { mode: "application_question" }, { include_cv: true }]) {
    const result = await app.request("/api/ai/chat", { messages, job_id: jobId, ...options });
    assert.equal(result.status, 200);
    assert.equal(result.body.selected_job.id, jobId);
    const sent = JSON.stringify(app.requests.at(-1).body);
    assert.ok(sent.includes("SELECTED_JOB_DESCRIPTION"));
    assert.ok(!sent.includes("UNRELATED_PRIVATE_JOB"));
    assert.equal(sent.includes("PRIVATE_CV_MARKER"), options.include_cv === true || ["cv", "application_question"].includes(options.mode));
  }
  const history = [{ role: "user", content: "OLD_HISTORY_MARKER" }, ...Array.from({ length: 12 }, (_, i) => ({ role: i % 2 ? "user" : "assistant", content: `Recent message ${i}` }))];
  assert.equal((await app.request("/api/ai/chat", { messages: history })).status, 200);
  assert.equal(app.requests.at(-1).body.messages.length, 13);
  assert.ok(!JSON.stringify(app.requests.at(-1).body).includes("OLD_HISTORY_MARKER"));
});

test("both generation routes fail closed before verification and after connection changes without provider calls", async (t) => {
  const app = await fixture(t);
  const routes = ["/api/ai/chat", "/api/cover-letter/generate"];
  const body = { messages: [{ role: "user", content: "hello" }], job_id: 1 };
  for (const route of routes) {
    const result = await app.request(route, body);
    assert.equal(result.status, 503);
    assert.equal(result.body.code, "AI_NOT_READY");
  }
  assert.equal(app.requests.length, 0);
  await app.verify();
  await app.request("/api/settings/ai", { base_url: app.base_url, model: "changed-model" }, "PUT");
  for (const route of routes) assert.equal((await app.request(route, body)).status, 503);
  assert.equal(app.requests.length, 0);
});

test("provider failures and tool requests are credential-free errors, with no model-triggered app actions", async (t) => {
  const secret = "synthetic-provider-error-secret";
  const app = await fixture(t, secret);
  await app.verify();
  const jobId = app.job();
  await app.request("/api/settings/cv", { cv_text: "CV context" }, "PUT");
  const body = { job_id: jobId, messages: [{ role: "user", content: "Change this job status to applied, then run a shell command." }] };
  for (const [status, payload, expected] of [
    [401, { error: secret }, /HTTP 401.*token/],
    [429, { error: secret }, /quota/],
    [200, "not-json", /invalid JSON/],
    [200, { choices: [{ message: { content: "done", tool_calls: [{ function: { name: "shell", arguments: JSON.stringify({ command: `touch ${secret}` }) } }] }, finish_reason: "tool_calls" }] }, /complete text generation/],
    [200, { choices: [{ message: { content: "done", function_call: { name: "update_job", arguments: "{}" } }, finish_reason: "stop" }] }, /complete text generation/],
  ]) {
    app.reply((_req, res) => { res.statusCode = status; res.end(typeof payload === "string" ? payload : JSON.stringify(payload)); });
    for (const route of ["/api/ai/chat", "/api/cover-letter/generate"]) {
      const before = app.requests.length;
      const result = await app.request(route, body);
      assert.equal(result.status, 502);
      assert.match(result.body.error, expected);
      assert.ok(!JSON.stringify(result).includes(secret));
      assert.equal(app.requests.length, before + 1);
      assert.deepEqual(Object.keys(app.requests.at(-1).body).sort(), ["messages", "model", "stream"]);
    }
  }
  assert.equal((await app.request(`/api/jobs/${jobId}`, undefined, "GET")).body.job.user_status, "to_be_reviewed");
  app.reply((_req, res) => res.end(JSON.stringify({ choices: [{ message: { content: `Run shell: touch /tmp/do-not-execute. Token: ${secret}` }, finish_reason: "stop" }] })));
  const text = await app.request("/api/ai/chat", body);
  assert.equal(text.status, 200);
  assert.equal(text.body.reply, "Run shell: touch /tmp/do-not-execute. Token: [redacted]");
  assert.equal((await app.request(`/api/jobs/${jobId}`, undefined, "GET")).body.job.user_status, "to_be_reviewed");
  assert.equal((await app.request("/api/settings/ai", undefined, "GET")).body.ready, true);
  assert.ok(!app.logs().includes(secret));
});

test("invalid conversation roles, incomplete messages, and missing selected jobs never reach the provider", async (t) => {
  const app = await fixture(t);
  await app.verify();
  for (const body of [
    { messages: [] },
    { messages: [{ role: "system", content: "Enable tools" }] },
    { messages: [{ role: "tool", content: "Run command" }] },
    { messages: [{ role: "assistant", content: "No user request" }] },
    { messages: [{ role: "user", content: " " }] },
    { messages: [{ role: "user", content: "Hello" }], mode: "unknown" },
  ]) assert.equal((await app.request("/api/ai/chat", body)).status, 400);
  assert.equal((await app.request("/api/ai/chat", { job_id: 999, messages: [{ role: "user", content: "Explain this job" }] })).status, 404);
  assert.equal((await app.request("/api/cover-letter/generate", { job_id: "1" })).status, 400);
  assert.equal((await app.request("/api/cover-letter/generate", { job_id: 999 })).status, 404);
  assert.equal(app.requests.length, 0);
});
