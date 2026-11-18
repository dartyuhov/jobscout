const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const Database = require("better-sqlite3");

const repo = path.resolve(__dirname, "..");
const python = path.join(repo, ".venv", "bin", "python");

test("direct backend refuses every checkout storage override before writing", () => {
  const forbidden = path.join(repo, "forbidden-review-runtime");
  for (const key of ["JOB_SEARCH_DATA_DIR", "DB_PATH", "SESSIONS_PATH"]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, "server.js")], {
      cwd: os.tmpdir(), env: { ...process.env, [key]: forbidden }, encoding: "utf8", timeout: 5000,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /outside the checkout/);
    assert.equal(fs.existsSync(forbidden), false);
  }
});

async function startPortal(env, cwd) {
  const listener = net.createServer();
  await new Promise((resolve) => listener.listen(0, "127.0.0.1", resolve));
  const port = listener.address().port;
  await new Promise((resolve) => listener.close(resolve));
  const child = spawn(process.execPath, [path.join(__dirname, "server.js")], {
    cwd, env: { ...env, PORT: String(port), HOST: "127.0.0.1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (data) => { output += data; });
  child.stderr.on("data", (data) => { output += data; });
  const url = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error(output);
    try { if ((await fetch(`${url}/api/health`)).ok) return { child, url }; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  child.kill();
  throw new Error(`Portal did not start: ${output}`);
}

async function stopPortal(child) {
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  await exited;
}

function cleanEnv(home) {
  const env = { ...process.env, HOME: home, XDG_DATA_HOME: path.join(home, "xdg") };
  for (const key of ["DB_PATH", "SESSIONS_PATH", "JOB_SEARCH_DATA_DIR"]) delete env[key];
  return env;
}

test("portal and CLI exchange only allowlisted search settings with fresh local identities", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "profile-exchange-"));
  const data = path.join(temp, "private");
  const database = path.join(temp, "selected.sqlite3");
  const env = { ...cleanEnv(path.join(temp, "home")), JOB_SEARCH_DATA_DIR: data, DB_PATH: database };
  const cli = (...args) => spawnSync(python, ["-m", "job_search_cli.cli", ...args], {
    cwd: temp, env: { ...env, PYTHONPATH: repo }, encoding: "utf8",
  });
  let portal;
  try {
    portal = await startPortal(env, temp);
    const request = (route, body) => fetch(`${portal.url}${route}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const created = await request("/api/profiles", {
      sessionName: "PRIVATE-SESSION", chromeProfilePath: "/PRIVATE-MACHINE/chrome",
      locationGeoId: "123", f_TPR: "r86400", f_WT: "2", headless: true,
      jobTitle: "Python engineer", want: "Remote", not_want: "On-call", maxPage: 3,
      statePath: "/PRIVATE-STATE", reporting: { token: "PRIVATE-TOKEN", chat: "PRIVATE-DESTINATION" },
    });
    assert.equal(created.status, 201);
    const { profile } = await created.json();
    assert.equal(profile.chromeProfilePath, path.join(data, "chrome", "Default"));
    assert.equal(profile.headless, false);
    const updated = await fetch(`${portal.url}/api/profiles/${profile.id}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...profile, chromeProfilePath: "/PRIVATE-OTHER", headless: true }),
    });
    assert.equal(updated.status, 200);
    const edited = (await updated.json()).profile;
    assert.equal(edited.chromeProfilePath, profile.chromeProfilePath);
    assert.equal(edited.headless, false);
    const db = new Database(database);
    db.prepare("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)").run("cv_text", "PRIVATE-CV", "now");
    db.prepare("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)").run("ai_connection", "PRIVATE-PROVIDER-SECRET", "now");
    db.prepare("INSERT INTO linkedin_jobs (linkedin_job_id, profile_id, about_text, ai_summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
      .run("98765", profile.id, "PRIVATE-JOB", "PRIVATE-GENERATED", "now", "now");
    db.close();
    const exported = await fetch(`${portal.url}/api/profiles/${profile.id}/export`);
    assert.equal(exported.status, 200);
    assert.match(exported.headers.get("content-disposition"), /attachment;.*search-settings\.json/);
    const text = await exported.text();
    assert.doesNotMatch(text, /PRIVATE-/);
    const document = JSON.parse(text);
    assert.match(document.notice, /personal details/);
    assert.deepEqual(document.settings, {
      jobTitle: "Python engineer", locationGeoId: "123", f_TPR: "r86400", f_WT: "2",
      want: "Remote", not_want: "On-call", maxPage: 3,
    });
    const file = path.join(temp, "settings.json");
    fs.writeFileSync(file, text);
    const importedCli = cli("profiles", "import", file);
    assert.equal(importedCli.status, 0, importedCli.stderr);
    const cliProfile = JSON.parse(importedCli.stdout);
    assert.notEqual(cliProfile.id, profile.id);
    const cliExport = cli("profiles", "export", String(cliProfile.id));
    assert.equal(cliExport.status, 0, cliExport.stderr);
    assert.deepEqual(JSON.parse(cliExport.stdout), document);
    const hostile = {
      ...document, id: profile.id, cv_text: "PRIVATE-CV", token: "PRIVATE-TOKEN",
      settings: { ...document.settings, ...profile, cron: "* * * * *", conversations: ["PRIVATE-CHAT"] },
    };
    const imported = await request("/api/profiles/import", hostile);
    assert.equal(imported.status, 201, await imported.clone().text());
    const local = (await imported.json()).profile;
    assert.notEqual(local.id, profile.id);
    assert.notEqual(local.id, cliProfile.id);
    assert.notEqual(local.sessionName, profile.sessionName);
    assert.equal(local.chromeProfilePath, path.join(data, "chrome", "Default"));
    assert.equal(local.headless, false);
    assert.equal(local.statePath, "");
    assert.equal(Object.hasOwn(local, "reporting"), false);
    assert.equal(local.cron, null);
    assert.equal(local.archived_at, null);
    assert.deepEqual(await (await fetch(`${portal.url}/api/profiles/${local.id}/export`)).json(), document);
    const form = new FormData();
    form.append("file", new Blob([JSON.stringify(hostile)], { type: "application/json" }), "settings.json");
    const uploaded = await fetch(`${portal.url}/api/profiles/upload`, { method: "POST", body: form });
    assert.equal(uploaded.status, 201);
    assert.equal((await uploaded.json()).profile.chromeProfilePath, local.chromeProfilePath);
    assert.equal((await fetch(`${portal.url}/api/profiles/999/export`)).status, 404);
  } finally {
    if (portal) await stopPortal(portal.child);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("portal rejects malformed exchange without restoring data or echoing private input", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "profile-exchange-invalid-"));
  let portal;
  try {
    portal = await startPortal({ ...cleanEnv(temp), JOB_SEARCH_DATA_DIR: path.join(temp, "private") }, temp);
    const valid = { format: "job-search-settings", version: 1, settings: { locationGeoId: "123", f_TPR: "r86400" } };
    const documents = [
      ["{PRIVATE-SECRET", "JSON"], ["[]", "JSON object"],
      [JSON.stringify({ ...valid, version: true }), "version"],
      [JSON.stringify({ ...valid, version: 2 }), "version"],
      [JSON.stringify({ ...valid, settings: [] }), "settings"],
      ...[["locationGeoId", {}], ["locationGeoId", " "], ["f_TPR", null],
        ["want", ["PRIVATE-TEXT"]], ["not_want", false], ["jobTitle", 12], ["f_WT", {}],
        ["maxPage", true], ["maxPage", 1.5], ["maxPage", "3"], ["maxPage", 0], ["maxPage", 2147483648],
      ].map(([key, value]) => [JSON.stringify({ ...valid, settings: { ...valid.settings, [key]: value } }), key]),
    ];
    for (const [body, field] of documents) {
      const response = await fetch(`${portal.url}/api/profiles/import`, {
        method: "POST", headers: { "content-type": "application/json" }, body,
      });
      assert.equal(response.status, 400);
      const text = await response.text();
      assert.doesNotMatch(text, /PRIVATE-/);
      assert.match(JSON.parse(text).error, new RegExp(field));
    }
    const form = new FormData();
    form.append("file", new Blob(["{PRIVATE-SECRET"]), "invalid.json");
    const uploaded = await fetch(`${portal.url}/api/profiles/upload`, { method: "POST", body: form });
    assert.equal(uploaded.status, 400);
    assert.deepEqual(await uploaded.json(), { error: "Invalid JSON file." });
    for (const [filename, body] of [["too-large.json", "x".repeat(1024 * 1024 + 1)], ["PRIVATE-NAME.json", "{}"]]) {
      const form = new FormData();
      form.append(filename === "PRIVATE-NAME.json" ? "unexpected" : "file", new Blob([body]), filename);
      const response = await fetch(`${portal.url}/api/profiles/upload`, { method: "POST", body: form });
      assert.equal(response.status, filename === "too-large.json" ? 413 : 400);
      assert.match(response.headers.get("content-type"), /application\/json/);
      assert.doesNotMatch(await response.text(), /PRIVATE-NAME|stack|MulterError/);
    }
    assert.deepEqual(await (await fetch(`${portal.url}/api/profiles`)).json(), { profiles: [] });
  } finally {
    if (portal) await stopPortal(portal.child);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("portal-created and edited profiles are selected directly by CLI without JSON sync", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "database-profiles-"));
  const data = path.join(temp, "private");
  const env = { ...cleanEnv(path.join(temp, "home")), JOB_SEARCH_DATA_DIR: data };
  let portal;
  const cli = (...args) => spawnSync(python, ["-m", "job_search_cli.cli", ...args], {
    cwd: temp, env: { ...env, PYTHONPATH: repo }, encoding: "utf8",
  });
  try {
    portal = await startPortal(env, temp);
    const request = async (route, method, body) => fetch(`${portal.url}${route}`, {
      method, headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const created = await request("/api/profiles", "POST", {
      sessionName: "database-search", locationGeoId: "123", f_TPR: "r86400",
      headless: false, jobTitle: "Original title", want: "Remote", chromeProfilePath: "",
    });
    assert.equal(created.status, 201, await created.clone().text());
    const { profile } = await created.json();
    assert.equal(profile.chromeProfilePath, path.join(data, "chrome", "Default"));
    const shown = cli("profiles", "show", String(profile.id));
    assert.equal(shown.status, 0, shown.stderr);
    assert.equal(JSON.parse(shown.stdout).jobTitle, "Original title");
    const edited = await request(`/api/profiles/${profile.id}`, "PUT", {
      ...profile, jobTitle: "Edited title", not_want: "On-call",
    });
    assert.equal(edited.status, 200);
    const current = cli("profiles", "show", String(profile.id));
    assert.equal(current.status, 0, current.stderr);
    assert.equal(JSON.parse(current.stdout).jobTitle, "Edited title");
    assert.equal(JSON.parse(current.stdout).not_want, "On-call");
    assert.equal((await request(`/api/profiles/${profile.id}/archive`, "POST", {})).status, 200);
    const archived = cli("linkedin", "launch", "--profile-id", String(profile.id));
    assert.equal(archived.status, 1);
    assert.match(archived.stderr, /archived.*Restore it in the portal/);
    assert.equal((await request(`/api/profiles/${profile.id}/restore`, "POST", {})).status, 200);
    assert.equal(JSON.parse(cli("profiles", "show", String(profile.id)).stdout).want, "Remote");
    const missing = cli("linkedin", "launch", "--profile-id", "999");
    assert.equal(missing.status, 1);
    assert.match(missing.stderr, /profile id '999' was not found/);
    assert.equal((await fetch(`${portal.url}/api/profiles/999`)).status, 404);
    assert.equal(JSON.parse(cli("profiles", "list").stdout).length, 1);
    assert.deepEqual(fs.readdirSync(path.join(data, "runtime")), ["browser.lock"]);
  } finally {
    if (portal) await stopPortal(portal.child);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("fresh portal uses private platform storage and shares it with CLI across cwd changes", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-storage-"));
  const env = cleanEnv(path.join(temp, "home"));
  const root = process.platform === "darwin"
    ? path.join(env.HOME, "Library", "Application Support", "job-search")
    : path.join(env.XDG_DATA_HOME, "job-search");
  // Recognizable old data must neither be imported nor overwritten.
  fs.writeFileSync(path.join(temp, "sessions.json"), "private legacy sessions");
  fs.writeFileSync(path.join(temp, "linkedin_jobs.sqlite3"), "private legacy database");
  let portal;
  try {
    portal = await startPortal(env, temp);
    assert.deepEqual(await (await fetch(`${portal.url}/api/profiles`)).json(), { profiles: [] });
    const jobs = await (await fetch(`${portal.url}/api/jobs`)).json();
    assert.equal(jobs.total, 0);
    assert.deepEqual(await (await fetch(`${portal.url}/api/settings/cv`)).json(), { cv_text: null });
    assert.deepEqual(await (await fetch(`${portal.url}/api/settings/chrome-profiles`)).json(), { chrome_profiles: [] });
    await fetch(`${portal.url}/api/settings/theme`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ theme_preference: "dark" }),
    });
    assert.equal(fs.statSync(root).mode & 0o777, 0o700);
    assert.equal(fs.statSync(path.join(root, "linkedin_jobs.sqlite3")).mode & 0o777, 0o600);
    for (const directory of ["runtime", "logs", "chrome", "chrome/Default"]) {
      assert.equal(fs.statSync(path.join(root, directory)).mode & 0o777, 0o700);
    }
    const cli = spawnSync(python, ["-m", "job_search_cli.cli", "linkedin", "close", "--all"], {
      cwd: repo, env: { ...env, PYTHONPATH: repo }, encoding: "utf8",
    });
    assert.equal(cli.status, 0, cli.stderr);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, "sessions.json"))), []);
    assert.equal(fs.readFileSync(path.join(temp, "sessions.json"), "utf8"), "private legacy sessions");
    assert.equal(fs.readFileSync(path.join(temp, "linkedin_jobs.sqlite3"), "utf8"), "private legacy database");
    await stopPortal(portal.child);
    portal = null;
    portal = await startPortal(env, repo);
    assert.deepEqual(await (await fetch(`${portal.url}/api/settings/theme`)).json(), { theme_preference: "dark" });
  } finally {
    if (portal) await stopPortal(portal.child);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("relative database and session overrides reach interactive and scheduled workflow children", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-workflow-"));
  const data = path.join(temp, "private");
  const env = {
    ...cleanEnv(path.join(temp, "home")), JOB_SEARCH_DATA_DIR: data,
    DB_PATH: "selected/jobs.sqlite3", SESSIONS_PATH: "selected/sessions.json",
    PYTHONPATH: `${temp}${path.delimiter}${repo}`,
  };
  // Substitute the external browser boundary only. The real orchestrator, CLI,
  // session store, SQLite, and scheduler run unchanged in child processes.
  fs.writeFileSync(path.join(temp, "sitecustomize.py"), `
import json, os, sys
from pathlib import Path
from job_search_cli import chrome
def launch(profile):
    payload_path = Path(sys.argv[sys.argv.index("--profile") + 1])
    Path(os.environ["JOB_SEARCH_DATA_DIR"], "capture.json").write_text(json.dumps({
        "db": os.environ["DB_PATH"], "sessions": os.environ["SESSIONS_PATH"],
        "payload_path": str(payload_path), "payload_mode": payload_path.stat().st_mode & 0o777,
        "jobTitle": profile.get("jobTitle"), "headless": profile["headless"],
    }))
    return chrome.ChromeLaunchResult(pid=999999, cdp_http_endpoint="http://localhost:9333", ws_endpoint="ws://localhost/test", launch_args=())
chrome.launch_google_chrome = launch
chrome.cleanup_chrome_processes_for_profile = lambda *a: None
chrome.open_url_in_chrome_session = lambda *a: None
def collect(*args, **kwargs):
    Path(os.environ["JOB_SEARCH_DATA_DIR"], "collection.json").write_text(json.dumps(kwargs["page_urls"]))
    return json.loads(os.environ.get("SYNTHETIC_JOB_IDS", "[]"))
chrome.get_job_ids_from_chrome_session = collect
chrome.get_session_ws_endpoint = lambda *a: "ws://localhost/test"
chrome.close_tracked_chrome_session = lambda *a: None
`);
  let portal;
  try {
    portal = await startPortal(env, temp);
    const response = await fetch(`${portal.url}/api/profiles`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        sessionName: "synthetic", chromeProfilePath: temp, locationGeoId: "123",
        f_TPR: "r86400", headless: false, maxPage: 1,
      }),
    });
    assert.equal(response.status, 201);
    const { profile } = await response.json();
    const updated = await fetch(`${portal.url}/api/profiles/${profile.id}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...profile, jobTitle: "Current database title", headless: true }),
    });
    assert.equal(updated.status, 200);
    const run = await fetch(`${portal.url}/api/runs`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile_id: profile.id }),
    });
    assert.equal(run.status, 201);
    async function waitForCompletedRun() {
      for (let attempt = 0; attempt < 150; attempt++) {
        const db = new Database(path.join(temp, "selected/jobs.sqlite3"));
        const row = db.prepare("SELECT status, stderr_log FROM workflow_runs ORDER BY id DESC LIMIT 1").get();
        db.close();
        if (row && ["completed", "failed"].includes(row.status)) {
          assert.equal(row.status, "completed", row.stderr_log);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.fail("workflow did not finish");
    }
    await waitForCompletedRun();
    const capture = JSON.parse(fs.readFileSync(path.join(data, "capture.json")));
    assert.equal(fs.realpathSync(capture.db), fs.realpathSync(path.join(temp, "selected/jobs.sqlite3")));
    assert.equal(fs.realpathSync(capture.sessions), fs.realpathSync(path.join(temp, "selected/sessions.json")));
    assert.equal(fs.realpathSync(path.dirname(capture.payload_path)), fs.realpathSync(path.join(data, "runtime")));
    assert.equal(capture.payload_mode, 0o600);
    assert.equal(capture.jobTitle, "Current database title");
    assert.equal(capture.headless, false);
    assert.deepEqual(JSON.parse(fs.readFileSync(capture.sessions)), []);
    assert.equal((await (await fetch(`${portal.url}/api/profiles`)).json()).profiles.length, 1);
    await stopPortal(portal.child);
    portal = null;
    const db = new Database(capture.db);
    db.prepare("UPDATE profile SET cron = '* * * * *'").run();
    db.close();
    fs.unlinkSync(path.join(data, "capture.json"));
    portal = await startPortal(env, temp);
    await waitForCompletedRun();
    const scheduledCapture = JSON.parse(fs.readFileSync(path.join(data, "capture.json")));
    assert.equal(scheduledCapture.jobTitle, "Current database title");
    const scheduled = new Database(capture.db);
    assert.equal(scheduled.prepare("SELECT COUNT(*) AS n FROM scheduled_runs WHERE trigger_source = 'catch_up'").get().n, 1);
    scheduled.close();
    assert.deepEqual(fs.readdirSync(path.join(data, "runtime")), ["browser.lock"]);
    const reports = fs.readdirSync(path.join(data, "logs"));
    assert.equal(reports.length, 2);
    for (const report of reports) assert.equal(fs.statSync(path.join(data, "logs", report)).mode & 0o777, 0o600);
    const opened = await fetch(`${portal.url}/api/profiles/${profile.id}/open-browser`, { method: "POST" });
    assert.equal(opened.status, 200);
    assert.equal(JSON.parse(fs.readFileSync(path.join(data, "capture.json"))).headless, false);
    const afterOpen = await (await fetch(`${portal.url}/api/profiles/${profile.id}`)).json();
    assert.equal(afterOpen.profile.headless, false);
    const collected = spawnSync(python, ["-m", "job_search_cli.cli", "linkedin", "get-job-ids", "--profile-id", String(profile.id)], {
      cwd: repo,
      env: { ...env, DB_PATH: capture.db, SESSIONS_PATH: capture.sessions, SYNTHETIC_JOB_IDS: '["12345"]' },
      encoding: "utf8",
    });
    assert.equal(collected.status, 0, collected.stderr);
    const pageUrls = JSON.parse(fs.readFileSync(path.join(data, "collection.json")));
    assert.equal(pageUrls.length, 1);
    assert.match(pageUrls[0], /keywords=Current\+database\+title/);
    const visibleJobs = await (await fetch(`${portal.url}/api/jobs?match_only=false`)).json();
    assert.equal(visibleJobs.total, 1);
    assert.equal(visibleJobs.jobs[0].linkedin_job_id, "12345");
  } finally {
    if (portal) await stopPortal(portal.child);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
