const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const Database = require("better-sqlite3");

const runtimeTestDir = fs.mkdtempSync(path.join(os.tmpdir(), "portal-runtime-test-"));
process.env.JOB_SEARCH_DATA_DIR = runtimeTestDir;
test.after(() => fs.rmSync(runtimeTestDir, { recursive: true, force: true }));

function createTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "portal-server-test-"));
}

function pickPort() {
  return 32000 + Math.floor(Math.random() * 10000);
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Test server did not expose a TCP port."));
        return;
      }
      resolve(address.port);
    });
    server.once("error", reject);
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((err) => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
}

async function waitForServer(baseUrl) {
  const deadline = Date.now() + 10000;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // Keep polling until the server is ready.
    }

    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  throw new Error(`Timed out waiting for server at ${baseUrl}`);
}

async function stopServer(child) {
  if (child.exitCode !== null) {
    return;
  }

  child.kill("SIGTERM");
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("Timed out waiting for portal backend to stop"));
    }, 5000);

    child.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

function makeProfilePayload(overrides = {}) {
  return {
    sessionName: "linkedin-qa-bcn",
    chromeProfilePath: "/Users/test/Profile 10",
    jobTitle: "Software Engineer",
    locationGeoId: "105088894",
    f_TPR: "r604800",
    f_WT: "2",
    want: "remote",
    not_want: "onsite",
    headless: false,
    maxPage: 5,
    statePath: "",
    reporting: {},
    ...overrides,
  };
}

function makeSessionRecord(overrides = {}) {
  return {
    sessionName: "linkedin-qa-bcn",
    wsEndpoint: "ws://127.0.0.1:9222/devtools/browser/test",
    cdpHttpEndpoint: "http://127.0.0.1:9222",
    pid: 999999,
    chromeProfilePath: "/Users/test/Profile 10",
    headless: true,
    createdAt: "2026-03-18T09:30:47Z",
    lastUsedAt: "2026-03-18T09:30:47Z",
    ...overrides,
  };
}

test("profiles ignore retired reporting input and never expose stored destinations", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: { ...process.env, DB_PATH: dbPath, PORT: String(port), HOST: "127.0.0.1" },
    stdio: "ignore",
  });
  const url = `http://127.0.0.1:${port}`;
  try {
    await waitForServer(url);
    const response = await fetch(`${url}/api/profiles`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload({ reporting: "retired input is not validated" })),
    });
    assert.equal(response.status, 201);
    const { profile } = await response.json();
    assert.equal(Object.hasOwn(profile, "reporting"), false);
    const db = new Database(dbPath);
    try {
      db.prepare("UPDATE profile SET reporting = ? WHERE id = ?")
        .run('{"token":"PRIVATE-LEGACY-DESTINATION"}', profile.id);
      const read = await fetch(`${url}/api/profiles`);
      const text = await read.text();
      assert.doesNotMatch(text, /reporting|PRIVATE-LEGACY-DESTINATION/);
      const update = await fetch(`${url}/api/profiles/${profile.id}`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify(makeProfilePayload({ jobTitle: "Updated engineer" })),
      });
      assert.equal(update.status, 200);
      assert.equal(Object.hasOwn((await update.json()).profile, "reporting"), false);
      assert.equal(db.prepare("SELECT reporting FROM profile WHERE id = ?").get(profile.id).reporting,
        '{"token":"PRIVATE-LEGACY-DESTINATION"}');
    } finally { db.close(); }
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

function writeJobSearchCliStub(tempDir) {
  const stubPath = path.join(tempDir, "job_search_cli_stub.py");
  fs.writeFileSync(
    stubPath,
    [
      'from __future__ import annotations',
      '',
      'import json',
      'import os',
      'import sys',
      'from pathlib import Path',
      '',
      '',
      'def main() -> None:',
      '    argv = sys.argv[1:]',
      '    payload = {"argv": argv}',
      '',
      '    if "--profile" in argv:',
      '        profile_index = argv.index("--profile") + 1',
      '        profile_path = Path(argv[profile_index])',
      '        payload["profile_path"] = str(profile_path)',
      '        payload["profile"] = json.loads(profile_path.read_text(encoding="utf-8"))',
      '',
      '    if "--db-path" in argv:',
      '        db_path_index = argv.index("--db-path") + 1',
      '        payload["db_path"] = argv[db_path_index]',
      '',
      '    capture_path = os.environ.get("JOB_SEARCH_CLI_CAPTURE_PATH")',
      '    if capture_path:',
      '        Path(capture_path).write_text(json.dumps(payload, indent=2) + "\\n", encoding="utf-8")',
      '',
      '    fail_message = os.environ.get("JOB_SEARCH_CLI_FORCE_FAIL")',
      '    if fail_message:',
      '        print(fail_message, file=sys.stderr)',
      '        raise SystemExit(1)',
      '',
      '    session_name = payload.get("profile", {}).get("sessionName", "stub-session")',
      '    print(f"Created Chrome session \'{session_name}\'.")',
      '    print("PID: 12345")',
      '    print("CDP HTTP endpoint: http://127.0.0.1:9333")',
      '    print("CDP WebSocket endpoint: ws://127.0.0.1:9333/devtools/browser/test")',
      '    print("Opened URL: https://www.linkedin.com/jobs/search/?currentJobId=1")',
      '',
      '',
      'if __name__ == "__main__":',
      '    main()',
      '',
    ].join("\n"),
    "utf-8"
  );
  return stubPath;
}

test("queue-multiple allows duplicate Chrome profile paths for sequential runs", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const insertProfile = db.prepare(
      `
        INSERT INTO profile ("sessionName", "chromeProfilePath", created_at, updated_at)
        VALUES (?, ?, ?, ?)
      `
    );
    const firstProfileId = Number(
      insertProfile.run("linkedin-qa-bcn", "/Users/test/Profile 10", timestamp, timestamp)
        .lastInsertRowid
    );
    const secondProfileId = Number(
      insertProfile.run("linkedin-qa-pl", "/Users/test/Profile 10", timestamp, timestamp)
        .lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO workflow_runs (profile_id, status, created_at, updated_at)
        VALUES (?, 'running', ?, ?)
      `
    ).run(firstProfileId, timestamp, timestamp);
    db.close();

    const response = await fetch(`http://127.0.0.1:${port}/api/runs/queue-multiple`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile_ids: [firstProfileId, secondProfileId] }),
    });
    const payload = await response.json();

    assert.equal(response.status, 201);
    assert.equal(payload.runs.length, 2);
    assert.ok(payload.runs[0].batch_id);
    assert.equal(payload.runs[0].batch_id, payload.runs[1].batch_id);
    assert.deepEqual(
      payload.runs.map((run) => ({ profile_id: run.profile_id, status: run.status })),
      [
        { profile_id: firstProfileId, status: "queued" },
        { profile_id: secondProfileId, status: "queued" },
      ]
    );
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("workflow batch notification API returns ready-to-review count for completed batch", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const batchId = "manual:test-batch";
    const insertProfile = db.prepare(
      `
        INSERT INTO profile (
          "sessionName",
          "chromeProfilePath",
          "locationGeoId",
          "f_TPR",
          "headless",
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `
    );
    const firstProfileId = Number(
      insertProfile.run(
        "linkedin-qa-bcn",
        "/Users/test/Profile 10",
        "105088894",
        "r604800",
        0,
        "2026-03-14T10:00:00Z",
        "2026-03-14T10:00:00Z"
      ).lastInsertRowid
    );
    const secondProfileId = Number(
      insertProfile.run(
        "linkedin-qa-pl",
        "/Users/test/Profile 11",
        "105088894",
        "r604800",
        0,
        "2026-03-14T10:00:00Z",
        "2026-03-14T10:00:00Z"
      ).lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO workflow_runs (
          profile_id,
          batch_id,
          status,
          started_at,
          finished_at,
          created_at,
          updated_at
        )
        VALUES (?, ?, 'completed', ?, ?, ?, ?), (?, ?, 'completed', ?, ?, ?, ?)
      `
    ).run(
      firstProfileId,
      batchId,
      "2026-03-14T10:00:00Z",
      "2026-03-14T10:05:00Z",
      "2026-03-14T10:00:00Z",
      "2026-03-14T10:05:00Z",
      secondProfileId,
      batchId,
      "2026-03-14T10:06:00Z",
      "2026-03-14T10:10:00Z",
      "2026-03-14T10:06:00Z",
      "2026-03-14T10:10:00Z"
    );

    const insertJob = db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          position_name,
          job_location,
          posted_date,
          easy_apply,
          apply_link,
          about_text,
          user_status,
          applied,
          profile_id,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `
    );

    const wantedJobId = Number(
      insertJob.run(
        "123",
        "Acme",
        "QA Engineer",
        "Barcelona",
        "2026-03-14",
        0,
        "https://example.com/123",
        "Wanted job",
        "to_be_reviewed",
        0,
        firstProfileId,
        "2026-03-14T10:03:00Z",
        "2026-03-14T10:03:00Z"
      ).lastInsertRowid
    );
    const rejectedJobId = Number(
      insertJob.run(
        "456",
        "Acme",
        "QA Engineer",
        "Barcelona",
        "2026-03-14",
        0,
        "https://example.com/456",
        "Rejected job",
        "to_be_reviewed",
        0,
        secondProfileId,
        "2026-03-14T10:07:00Z",
        "2026-03-14T10:07:00Z"
      ).lastInsertRowid
    );
    const oldJobId = Number(
      insertJob.run(
        "789",
        "Acme",
        "QA Engineer",
        "Barcelona",
        "2026-03-13",
        0,
        "https://example.com/789",
        "Old job",
        "to_be_reviewed",
        0,
        firstProfileId,
        "2026-03-14T09:55:00Z",
        "2026-03-14T09:55:00Z"
      ).lastInsertRowid
    );
    const appliedJobId = Number(
      insertJob.run(
        "101",
        "Acme",
        "QA Engineer",
        "Barcelona",
        "2026-03-14",
        0,
        "https://example.com/101",
        "Applied job",
        "applied",
        1,
        secondProfileId,
        "2026-03-14T10:08:00Z",
        "2026-03-14T10:08:00Z"
      ).lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO ai_analysis (job_id, analyzed, desicion, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?), (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
      `
    ).run(
      wantedJobId,
      1,
      1,
      "2026-03-14T10:04:00Z",
      "2026-03-14T10:04:00Z",
      rejectedJobId,
      1,
      0,
      "2026-03-14T10:08:00Z",
      "2026-03-14T10:08:00Z",
      oldJobId,
      1,
      1,
      "2026-03-14T09:56:00Z",
      "2026-03-14T09:56:00Z",
      appliedJobId,
      1,
      1,
      "2026-03-14T10:09:00Z",
      "2026-03-14T10:09:00Z"
    );
    db.close();

    const response = await fetch(
      `http://127.0.0.1:${port}/api/run-batches/${encodeURIComponent(batchId)}/notification`
    );
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.summary.batch_id, batchId);
    assert.equal(payload.summary.is_complete, true);
    assert.equal(payload.summary.ready_to_review_count, 1);
    assert.deepEqual(payload.summary.profile_ids, [firstProfileId, secondProfileId]);
    assert.deepEqual(payload.summary.profile_session_names, ["linkedin-qa-bcn", "linkedin-qa-pl"]);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("runs API returns matched jobs count for completed runs", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const profileId = Number(
      db
        .prepare(
          `
            INSERT INTO profile (
              "sessionName",
              "chromeProfilePath",
              created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?)
          `
        )
        .run("linkedin-qa-bcn", "/Users/test/Profile 10", timestamp, timestamp).lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO workflow_runs (
          profile_id,
          status,
          started_at,
          finished_at,
          job_ids_found,
          matched_jobs_count,
          parsed_ok,
          parsed_failed,
          created_at,
          updated_at
        )
        VALUES (?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?)
      `
    ).run(profileId, timestamp, timestamp, 12, 4, 3, 1, timestamp, timestamp);
    db.close();

    const response = await fetch(`http://127.0.0.1:${port}/api/runs`);
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.runs.length, 1);
    assert.equal(payload.runs[0].job_ids_found, 12);
    assert.equal(payload.runs[0].matched_jobs_count, 4);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("profiles API allows duplicate Chrome profile paths with distinct session names", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const firstResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload()),
    });
    const firstPayload = await firstResponse.json();

    assert.equal(firstResponse.status, 201);
    assert.equal(firstPayload.profile.sessionName, "linkedin-qa-bcn");

    const secondResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(
        makeProfilePayload({
          sessionName: "linkedin-qa-pl",
          chromeProfilePath: "/Users/test/Profile 10",
        })
      ),
    });
    const secondPayload = await secondResponse.json();

    assert.equal(secondResponse.status, 201);
    assert.equal(secondPayload.profile.sessionName, "linkedin-qa-pl");
    assert.equal(secondPayload.profile.chromeProfilePath, path.join(runtimeTestDir, "chrome", "Default"));
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("profiles open-browser API launches the selected profile in headed mode", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const capturePath = path.join(tempDir, "launch-capture.json");
  const stubPath = writeJobSearchCliStub(tempDir);
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
      JOB_SEARCH_CLI_SCRIPT: stubPath,
      JOB_SEARCH_CLI_CAPTURE_PATH: capturePath,
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const createResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload({ headless: true })),
    });
    const createPayload = await createResponse.json();

    assert.equal(createResponse.status, 201);

    const response = await fetch(
      `http://127.0.0.1:${port}/api/profiles/${createPayload.profile.id}/open-browser`,
      { method: "POST" }
    );
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.launched, true);
    assert.equal(payload.profile_id, createPayload.profile.id);
    assert.match(payload.message, /^Created Chrome session/);
    assert.match(payload.output, /CDP HTTP endpoint/);

    const captured = JSON.parse(fs.readFileSync(capturePath, "utf-8"));
    assert.equal(captured.db_path, dbPath);
    assert.equal(captured.profile.sessionName, "linkedin-qa-bcn");
    assert.equal(captured.profile.id, createPayload.profile.id);
    assert.equal(captured.profile.headless, false);
    assert.equal(captured.profile.chromeProfilePath, path.join(runtimeTestDir, "chrome", "Default"));
    assert.equal(captured.profile.maxPage, 5);
    assert.equal(Object.hasOwn(captured.profile, "reporting"), false);
    assert.ok(Array.isArray(captured.argv));
    assert.equal(captured.argv[0], "linkedin");
    assert.equal(captured.argv[1], "launch");
    assert.ok(!fs.existsSync(captured.profile_path));
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("profiles open-browser API returns not found for missing profiles", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const response = await fetch(`http://127.0.0.1:${port}/api/profiles/999/open-browser`, {
      method: "POST",
    });
    const payload = await response.json();

    assert.equal(response.status, 404);
    assert.equal(payload.error, "Profile id '999' was not found.");
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("profiles open-browser API surfaces CLI launch failures", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const stubPath = writeJobSearchCliStub(tempDir);
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
      JOB_SEARCH_CLI_SCRIPT: stubPath,
      JOB_SEARCH_CLI_FORCE_FAIL: "Stub launch failure.",
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const createResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload()),
    });
    const createPayload = await createResponse.json();

    assert.equal(createResponse.status, 201);

    const response = await fetch(
      `http://127.0.0.1:${port}/api/profiles/${createPayload.profile.id}/open-browser`,
      { method: "POST" }
    );
    const payload = await response.json();

    assert.equal(response.status, 500);
    assert.equal(payload.error, "Stub launch failure.");
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("server startup migrates dont_want jobs to not_suitable and rejects legacy status updates", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const seedDb = new Database(dbPath);
  seedDb.exec(`
    CREATE TABLE linkedin_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      linkedin_job_id TEXT NOT NULL UNIQUE,
      user_status TEXT NOT NULL DEFAULT 'to_be_reviewed',
      applied INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT ''
    );
  `);
  seedDb
    .prepare(
      `
        INSERT INTO linkedin_jobs (linkedin_job_id, user_status, applied, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
      `
    )
    .run("legacy-123", "dont_want", 0, "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
  seedDb.close();

  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const migratedDb = new Database(dbPath, { readonly: true });
    const migratedRow = migratedDb
      .prepare("SELECT user_status FROM linkedin_jobs WHERE linkedin_job_id = ?")
      .get("legacy-123");
    migratedDb.close();

    assert.equal(migratedRow.user_status, "not_suitable");

    const response = await fetch(`http://127.0.0.1:${port}/api/jobs/1/status`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status: "dont_want" }),
    });
    const payload = await response.json();

    assert.equal(response.status, 400);
    assert.match(payload.error, /Unknown status 'dont_want'/);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("jobs API filters by location text", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const profileId = Number(
      db
        .prepare(
          `
            INSERT INTO profile (
              "sessionName",
              "chromeProfilePath",
              "locationGeoId",
              "f_TPR",
              "headless",
              created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
          `
        )
        .run(
          "linkedin-qa-bcn",
          "/Users/test/Profile 10",
          "105088894",
          "r604800",
          0,
          timestamp,
          timestamp
        ).lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          position_name,
          job_location,
          posted_date,
          easy_apply,
          apply_link,
          about_text,
          user_status,
          applied,
          profile_id,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `
    ).run(
      "123",
      "Acme",
      "Backend Engineer",
      "Barcelona, Catalonia, Spain",
      "2026-03-10",
      0,
      "https://example.com/barcelona",
      "Barcelona role",
      "to_be_reviewed",
      0,
      profileId,
      timestamp,
      timestamp
    );

    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          position_name,
          job_location,
          posted_date,
          easy_apply,
          apply_link,
          about_text,
          user_status,
          applied,
          profile_id,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `
    ).run(
      "456",
      "Acme",
      "Platform Engineer",
      "Warsaw, Mazowieckie, Poland",
      "2026-03-11",
      0,
      "https://example.com/warsaw",
      "Warsaw role",
      "to_be_reviewed",
      0,
      profileId,
      timestamp,
      timestamp
    );
    db.close();

    const response = await fetch(
      `http://127.0.0.1:${port}/api/jobs?match_only=false&location=barcelona`
    );
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.total, 1);
    assert.equal(payload.jobs.length, 1);
    assert.equal(payload.jobs[0].linkedin_job_id, "123");
    assert.equal(payload.jobs[0].job_location, "Barcelona, Catalonia, Spain");
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("jobs stats API returns total matched and applied counts", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const profileId = Number(
      db
        .prepare(
          `
            INSERT INTO profile (
              "sessionName",
              "chromeProfilePath",
              "locationGeoId",
              "f_TPR",
              "headless",
              created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
          `
        )
        .run(
          "linkedin-qa-bcn",
          "/Users/test/Profile 10",
          "105088894",
          "r604800",
          0,
          timestamp,
          timestamp
        ).lastInsertRowid
    );

    const insertJob = db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          position_name,
          job_location,
          posted_date,
          easy_apply,
          apply_link,
          about_text,
          user_status,
          applied,
          profile_id,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `
    );

    const firstJobId = Number(
      insertJob.run(
        "123",
        "Acme",
        "Backend Engineer",
        "Barcelona, Catalonia, Spain",
        "2026-03-10",
        0,
        "https://example.com/barcelona",
        "Barcelona role",
        "applied",
        1,
        profileId,
        timestamp,
        timestamp
      ).lastInsertRowid
    );

    const secondJobId = Number(
      insertJob.run(
        "456",
        "Acme",
        "Platform Engineer",
        "Warsaw, Mazowieckie, Poland",
        "2026-03-11",
        0,
        "https://example.com/warsaw",
        "Warsaw role",
        "to_be_reviewed",
        0,
        profileId,
        timestamp,
        timestamp
      ).lastInsertRowid
    );

    db.prepare(
      `
        INSERT INTO ai_analysis (job_id, analyzed, desicion, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
      `
    ).run(
      firstJobId,
      1,
      1,
      timestamp,
      timestamp,
      secondJobId,
      1,
      0,
      timestamp,
      timestamp
    );
    db.close();

    const response = await fetch(`http://127.0.0.1:${port}/api/jobs/stats`);
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.deepEqual(payload.stats, {
      total_jobs: 2,
      matched_jobs: 1,
      applied_jobs: 1,
    });
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("notification settings API defaults to enabled and can be updated", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const initialResponse = await fetch(`http://127.0.0.1:${port}/api/settings/notifications`);
    const initialPayload = await initialResponse.json();

    assert.equal(initialResponse.status, 200);
    assert.equal(initialPayload.browser_notifications_enabled, true);

    const updateResponse = await fetch(`http://127.0.0.1:${port}/api/settings/notifications`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ browser_notifications_enabled: false }),
    });
    const updatePayload = await updateResponse.json();

    assert.equal(updateResponse.status, 200);
    assert.equal(updatePayload.browser_notifications_enabled, false);

    const secondResponse = await fetch(`http://127.0.0.1:${port}/api/settings/notifications`);
    const secondPayload = await secondResponse.json();

    assert.equal(secondResponse.status, 200);
    assert.equal(secondPayload.browser_notifications_enabled, false);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("theme settings API defaults to system and can be updated", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const initialResponse = await fetch(`http://127.0.0.1:${port}/api/settings/theme`);
    const initialPayload = await initialResponse.json();

    assert.equal(initialResponse.status, 200);
    assert.equal(initialPayload.theme_preference, "system");

    const updateResponse = await fetch(`http://127.0.0.1:${port}/api/settings/theme`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ theme_preference: "dark" }),
    });
    const updatePayload = await updateResponse.json();

    assert.equal(updateResponse.status, 200);
    assert.equal(updatePayload.theme_preference, "dark");

    const secondResponse = await fetch(`http://127.0.0.1:${port}/api/settings/theme`);
    const secondPayload = await secondResponse.json();

    assert.equal(secondResponse.status, 200);
    assert.equal(secondPayload.theme_preference, "dark");
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("ignored companies settings API saves values and marks matching review jobs", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          user_status,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?)
      `
    ).run("job-1", "Acme", "to_be_reviewed", "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          user_status,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?)
      `
    ).run("job-2", "  aCmE  ", "to_be_reviewed", "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          user_status,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?)
      `
    ).run("job-3", "Globex", "applied", "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          user_status,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?)
      `
    ).run("job-4", "Umbrella", "to_be_reviewed", "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.close();

    const updateResponse = await fetch(`http://127.0.0.1:${port}/api/settings/ignored-companies`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ignored_companies: ["Acme", "acme", "Globex", "  "] }),
    });
    const updatePayload = await updateResponse.json();

    assert.equal(updateResponse.status, 200);
    assert.deepEqual(updatePayload.ignored_companies, ["Acme", "Globex"]);
    assert.equal(updatePayload.updated_job_count, 2);

    const readResponse = await fetch(`http://127.0.0.1:${port}/api/settings/ignored-companies`);
    const readPayload = await readResponse.json();

    assert.equal(readResponse.status, 200);
    assert.deepEqual(readPayload.ignored_companies, ["Acme", "Globex"]);

    const verifyDb = new Database(dbPath, { readonly: true });
    const jobs = verifyDb
      .prepare("SELECT linkedin_job_id, user_status FROM linkedin_jobs ORDER BY linkedin_job_id ASC")
      .all();
    verifyDb.close();

    assert.deepEqual(jobs, [
      { linkedin_job_id: "job-1", user_status: "not_suitable" },
      { linkedin_job_id: "job-2", user_status: "not_suitable" },
      { linkedin_job_id: "job-3", user_status: "applied" },
      { linkedin_job_id: "job-4", user_status: "to_be_reviewed" },
    ]);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("profiles can be archived and restored without deleting jobs or history", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const createResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(
        makeProfilePayload({
          sessionName: "archive-me",
          chromeProfilePath: "/Users/test/Profile Archive",
          cron: "0 2 * * *",
        })
      ),
    });
    const createPayload = await createResponse.json();

    assert.equal(createResponse.status, 201);
    const profileId = createPayload.profile.id;

    const initialSchedulesResponse = await fetch(`http://127.0.0.1:${port}/api/schedules`);
    const initialSchedulesPayload = await initialSchedulesResponse.json();
    assert.equal(initialSchedulesResponse.status, 200);
    assert.equal(initialSchedulesPayload.schedules.length, 1);

    const db = new Database(dbPath);
    db.prepare(
      `
        INSERT INTO linkedin_jobs (
          linkedin_job_id,
          company_name,
          user_status,
          profile_id,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
      `
    ).run("archive-job-1", "Acme", "to_be_reviewed", profileId, "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.prepare(
      `
        INSERT INTO workflow_runs (profile_id, status, created_at, updated_at)
        VALUES (?, 'queued', ?, ?)
      `
    ).run(profileId, "2026-03-14T10:00:00Z", "2026-03-14T10:00:00Z");
    db.close();

    const archiveResponse = await fetch(`http://127.0.0.1:${port}/api/profiles/${profileId}/archive`, {
      method: "POST",
    });
    const archivePayload = await archiveResponse.json();

    assert.equal(archiveResponse.status, 200);
    assert.equal(archivePayload.cancelled_run_count, 1);
    assert.equal(archivePayload.profile.is_archived, true);
    assert.ok(archivePayload.profile.archived_at);

    const activeProfilesResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`);
    const activeProfilesPayload = await activeProfilesResponse.json();
    assert.equal(activeProfilesResponse.status, 200);
    assert.equal(activeProfilesPayload.profiles.length, 0);

    const allProfilesResponse = await fetch(
      `http://127.0.0.1:${port}/api/profiles?include_archived=true`
    );
    const allProfilesPayload = await allProfilesResponse.json();
    assert.equal(allProfilesResponse.status, 200);
    assert.equal(allProfilesPayload.profiles.length, 1);
    assert.equal(allProfilesPayload.profiles[0].is_archived, true);

    const archivedScheduleResponse = await fetch(`http://127.0.0.1:${port}/api/schedules`);
    const archivedSchedulePayload = await archivedScheduleResponse.json();
    assert.equal(archivedScheduleResponse.status, 200);
    assert.equal(archivedSchedulePayload.schedules.length, 0);

    const openBrowserResponse = await fetch(
      `http://127.0.0.1:${port}/api/profiles/${profileId}/open-browser`,
      { method: "POST" }
    );
    const openBrowserPayload = await openBrowserResponse.json();
    assert.equal(openBrowserResponse.status, 409);
    assert.match(openBrowserPayload.error, /archived/i);

    const runResponse = await fetch(`http://127.0.0.1:${port}/api/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile_id: profileId }),
    });
    const runPayload = await runResponse.json();
    assert.equal(runResponse.status, 409);
    assert.match(runPayload.error, /archived/i);

    const verifyArchivedDb = new Database(dbPath, { readonly: true });
    const archivedProfileRow = verifyArchivedDb
      .prepare("SELECT archived_at FROM profile WHERE id = ?")
      .get(profileId);
    const archivedJobsCount = verifyArchivedDb
      .prepare("SELECT COUNT(*) AS total FROM linkedin_jobs WHERE profile_id = ?")
      .get(profileId);
    const archivedRunStatuses = verifyArchivedDb
      .prepare("SELECT status FROM workflow_runs WHERE profile_id = ? ORDER BY id ASC")
      .all(profileId)
      .map((row) => row.status);
    verifyArchivedDb.close();

    assert.ok(archivedProfileRow.archived_at);
    assert.equal(archivedJobsCount.total, 1);
    assert.deepEqual(archivedRunStatuses, ["cancelled"]);

    const restoreResponse = await fetch(`http://127.0.0.1:${port}/api/profiles/${profileId}/restore`, {
      method: "POST",
    });
    const restorePayload = await restoreResponse.json();

    assert.equal(restoreResponse.status, 200);
    assert.equal(restorePayload.profile.is_archived, false);
    assert.equal(restorePayload.profile.archived_at, null);

    const restoredProfilesResponse = await fetch(`http://127.0.0.1:${port}/api/profiles`);
    const restoredProfilesPayload = await restoredProfilesResponse.json();
    assert.equal(restoredProfilesResponse.status, 200);
    assert.equal(restoredProfilesPayload.profiles.length, 1);
    assert.equal(restoredProfilesPayload.profiles[0].id, profileId);

    const restoredScheduleResponse = await fetch(`http://127.0.0.1:${port}/api/schedules`);
    const restoredSchedulePayload = await restoredScheduleResponse.json();
    assert.equal(restoredScheduleResponse.status, 200);
    assert.equal(restoredSchedulePayload.schedules.length, 1);
    assert.equal(restoredSchedulePayload.schedules[0].profileId, profileId);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("chrome profile settings API groups configured paths and reports usage", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const sessionsPath = path.join(tempDir, "sessions.json");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      SESSIONS_PATH: sessionsPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const insertProfile = db.prepare(
      `
        INSERT INTO profile (
          "sessionName",
          "chromeProfilePath",
          "locationGeoId",
          "f_TPR",
          "headless",
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `
    );
    const firstProfileId = Number(
      insertProfile.run("linkedin-qa-bcn", "/Users/test/Profile 10", "105088894", "r604800", 1, timestamp, timestamp)
        .lastInsertRowid
    );
    const secondProfileId = Number(
      insertProfile.run("linkedin-qa-pl", "/Users/test/Profile 10", "105088894", "r604800", 1, timestamp, timestamp)
        .lastInsertRowid
    );
    insertProfile.run("linkedin-qa-mad", "/Users/test/Profile 11", "105088894", "r604800", 1, timestamp, timestamp);
    const runId = Number(
      db.prepare(
        `
          INSERT INTO workflow_runs (profile_id, status, created_at, updated_at)
          VALUES (?, 'running', ?, ?)
        `
      ).run(firstProfileId, timestamp, timestamp).lastInsertRowid
    );
    db.close();

    fs.writeFileSync(
      sessionsPath,
      `${JSON.stringify([
        makeSessionRecord({ sessionName: "linkedin-qa-pl", chromeProfilePath: "/Users/test/Profile 10" }),
        makeSessionRecord({ sessionName: "external", chromeProfilePath: "/Users/test/Profile 99" }),
      ], null, 2)}\n`
    );

    const response = await fetch(`http://127.0.0.1:${port}/api/settings/chrome-profiles`);
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.chrome_profiles.length, 2);

    const sharedProfile = payload.chrome_profiles.find(
      (profile) => profile.chrome_profile_path === "/Users/test/Profile 10"
    );
    assert.deepEqual(sharedProfile.profile_ids, [firstProfileId, secondProfileId]);
    assert.deepEqual(sharedProfile.session_names, ["linkedin-qa-bcn", "linkedin-qa-pl"]);
    assert.deepEqual(sharedProfile.tracked_session_names, ["linkedin-qa-pl"]);
    assert.deepEqual(sharedProfile.active_run_ids, [runId]);
    assert.equal(sharedProfile.is_in_use, true);

    const idleProfile = payload.chrome_profiles.find(
      (profile) => profile.chrome_profile_path === "/Users/test/Profile 11"
    );
    assert.deepEqual(idleProfile.tracked_session_names, []);
    assert.deepEqual(idleProfile.active_run_ids, []);
    assert.equal(idleProfile.is_in_use, false);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("chrome profile kill API cancels active runs and removes tracked sessions", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const sessionsPath = path.join(tempDir, "sessions.json");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      SESSIONS_PATH: sessionsPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const profileId = Number(
      db.prepare(
        `
          INSERT INTO profile (
            "sessionName",
            "chromeProfilePath",
            "locationGeoId",
            "f_TPR",
            "headless",
            created_at,
            updated_at
          )
          VALUES (?, ?, ?, ?, ?, ?, ?)
        `
      ).run("linkedin-qa-bcn", "/Users/test/Profile 10", "105088894", "r604800", 1, timestamp, timestamp)
        .lastInsertRowid
    );
    const runId = Number(
      db.prepare(
        `
          INSERT INTO workflow_runs (profile_id, status, created_at, updated_at)
          VALUES (?, 'running', ?, ?)
        `
      ).run(profileId, timestamp, timestamp).lastInsertRowid
    );
    db.close();

    fs.writeFileSync(
      sessionsPath,
      `${JSON.stringify([
        makeSessionRecord({ chromeProfilePath: "/Users/test/Profile 10" }),
      ], null, 2)}\n`
    );

    const response = await fetch(`http://127.0.0.1:${port}/api/settings/chrome-profiles/kill`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chrome_profile_path: "/Users/test/Profile 10" }),
    });
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.cleaned.chrome_profile_path, "/Users/test/Profile 10");
    assert.deepEqual(payload.cleaned.cancelled_run_ids, [runId]);
    assert.equal(payload.cleaned.cancelled_run_count, 1);
    assert.equal(payload.cleaned.session_records_removed, 1);

    const updatedDb = new Database(dbPath);
    const run = updatedDb.prepare("SELECT status FROM workflow_runs WHERE id = ?").get(runId);
    updatedDb.close();
    assert.equal(run.status, "cancelled");

    const sessionsPayload = JSON.parse(fs.readFileSync(sessionsPath, "utf-8"));
    assert.deepEqual(sessionsPayload, []);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("chrome profile kill-all API only cleans configured profiles currently in use", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const sessionsPath = path.join(tempDir, "sessions.json");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      SESSIONS_PATH: sessionsPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    const insertProfile = db.prepare(
      `
        INSERT INTO profile (
          "sessionName",
          "chromeProfilePath",
          "locationGeoId",
          "f_TPR",
          "headless",
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `
    );
    const activeProfileId = Number(
      insertProfile.run("linkedin-qa-bcn", "/Users/test/Profile 10", "105088894", "r604800", 1, timestamp, timestamp)
        .lastInsertRowid
    );
    insertProfile.run("linkedin-qa-mad", "/Users/test/Profile 11", "105088894", "r604800", 1, timestamp, timestamp);
    const activeRunId = Number(
      db.prepare(
        `
          INSERT INTO workflow_runs (profile_id, status, created_at, updated_at)
          VALUES (?, 'running', ?, ?)
        `
      ).run(activeProfileId, timestamp, timestamp).lastInsertRowid
    );
    db.close();

    fs.writeFileSync(
      sessionsPath,
      `${JSON.stringify([
        makeSessionRecord({ chromeProfilePath: "/Users/test/Profile 10" }),
      ], null, 2)}\n`
    );

    const response = await fetch(`http://127.0.0.1:${port}/api/settings/chrome-profiles/kill-all`, {
      method: "POST",
    });
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.cleaned_count, 1);
    assert.equal(payload.cleaned[0].chrome_profile_path, "/Users/test/Profile 10");
    assert.deepEqual(payload.cleaned[0].cancelled_run_ids, [activeRunId]);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("unconfigured job summaries backfill explains AI setup", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const response = await fetch(`http://127.0.0.1:${port}/api/jobs/summaries/backfill`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    const payload = await response.json();

    assert.equal(response.status, 503);
    assert.equal(payload.code, "AI_NOT_READY");
    assert.match(payload.error, /Configure and verify an AI connection in Settings/);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("schedules API updates cron for selected profiles", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const firstProfile = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload()),
    }).then((response) => response.json());
    const secondProfile = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(makeProfilePayload({ sessionName: "linkedin-qa-pl" })),
    }).then((response) => response.json());

    const response = await fetch(`http://127.0.0.1:${port}/api/schedules/profiles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        profile_ids: [firstProfile.profile.id, secondProfile.profile.id],
        cron: "0 6 * * *",
      }),
    });
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.profiles.length, 2);
    assert.equal(payload.cron, "0 6 * * *");
    assert.ok(payload.profiles.every((profile) => profile.cron === "0 6 * * *"));
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("unconfigured portal preserves review and makes no AI calls on startup or manual actions", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  let aiRequests = 0;
  const apiServer = http.createServer((_req, res) => {
    aiRequests += 1;
    res.writeHead(500);
    res.end("Unexpected AI request");
  });
  const opencodePort = await listen(apiServer);
  const serverOptions = {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
      OPENCODE_SERVER_URL: `http://127.0.0.1:${opencodePort}`,
    },
    stdio: ["ignore", "ignore", "ignore"],
  };
  let child = spawn("node", ["server.js"], serverOptions);

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const db = new Database(dbPath);
    const timestamp = new Date().toISOString();
    db.prepare(
      `
        INSERT INTO settings (key, value, updated_at)
        VALUES ('cv_text', ?, ?)
      `
    ).run("Built backend APIs and improved test automation.", timestamp);
    const jobId = Number(
      db.prepare(
        `
          INSERT INTO linkedin_jobs (
            linkedin_job_id,
            company_name,
            position_name,
            job_location,
            posted_date,
            about_text,
            ai_summary,
            user_status,
            created_at,
            updated_at
          )
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `
      ).run(
        "987654321",
        "Example Co",
        "Senior QA Engineer",
        "Remote",
        "2026-04-01",
        "Own quality strategy and improve automated coverage.",
        null,
        "to_be_reviewed",
        timestamp,
        timestamp
      ).lastInsertRowid
    );
    db.prepare(
      `
        INSERT INTO ai_analysis (job_id, analyzed, desicion, reasoning, created_at, updated_at)
        VALUES (?, 1, 1, ?, ?, ?)
      `
    ).run(jobId, "Strong quality automation fit.", timestamp, timestamp);
    db.close();

    // Restart with a matched, unsummarized job to exercise startup backfill.
    await stopServer(child);
    child = spawn("node", ["server.js"], serverOptions);
    await waitForServer(`http://127.0.0.1:${port}`);
    const health = await fetch(`http://127.0.0.1:${port}/api/health`).then((r) => r.json());
    assert.equal(health.ai.ready, false);
    assert.equal(health.ai.auto_analyze, false);
    assert.equal(health.ai.auto_summarize, false);

    const response = await fetch(`http://127.0.0.1:${port}/api/ai/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        mode: "application_question",
        messages: [{ role: "user", content: "Why are you a strong fit for this role?" }],
      }),
    });
    const payload = await response.json();

    assert.equal(response.status, 503);
    assert.equal(payload.code, "AI_NOT_READY");
    assert.match(payload.error, /Configure and verify an AI connection in Settings/);
    for (const route of ["/api/cover-letter/generate", `/api/jobs/${jobId}/summarize`, "/api/jobs/summaries/backfill"]) {
      const result = await fetch(`http://127.0.0.1:${port}${route}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ job_id: jobId }),
      });
      assert.equal(result.status, 503, route);
      assert.equal((await result.json()).code, "AI_NOT_READY");
    }
    const details = await fetch(`http://127.0.0.1:${port}/api/jobs/${jobId}`).then((r) => r.json());
    assert.equal(details.job.position_name, "Senior QA Engineer");
    const review = await fetch(`http://127.0.0.1:${port}/api/jobs/${jobId}/status`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status: "want" }),
    });
    assert.equal(review.status, 200);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(aiRequests, 0);
  } finally {
    await stopServer(child);
    await close(apiServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("Ask AI API reports connection setup before requiring CV", async () => {
  const tempDir = createTempDir();
  const dbPath = path.join(tempDir, "test.sqlite3");
  const port = pickPort();
  const child = spawn("node", ["server.js"], {
    cwd: __dirname,
    env: {
      ...process.env,
      DB_PATH: dbPath,
      HOST: "127.0.0.1",
      PORT: String(port),
    },
    stdio: ["ignore", "ignore", "ignore"],
  });

  try {
    await waitForServer(`http://127.0.0.1:${port}`);

    const response = await fetch(`http://127.0.0.1:${port}/api/ai/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: "Tell me about my CV." }],
      }),
    });
    const payload = await response.json();

    assert.equal(response.status, 503);
    assert.equal(payload.code, "AI_NOT_READY");
    assert.match(payload.error, /Configure and verify an AI connection in Settings/);
  } finally {
    await stopServer(child);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
