const test = require("node:test");
const assert = require("node:assert/strict");

const Database = require("better-sqlite3");

const scheduler = require("./scheduler");
const cron = require("node-cron");

test("schedule replacement, removal and shutdown destroy registry tasks without immediate execution", async () => {
  const db = createTestDb();
  const calls = [];
  scheduler.initScheduler(db, (...args) => calls.push(args), () => "2026-03-16T10:01:00Z");
  try {
    insertProfile(db, 1, "lifecycle", "0 0 1 1 *");
    const profile = { id: 1, sessionName: "lifecycle", cron: "0 0 1 1 *" };
    assert.equal(scheduler.validateCronExpression("invalid").valid, false);
    assert.equal(scheduler.validateCronExpression(profile.cron).valid, true);
    scheduler.scheduleProfile(profile);
    const first = [...cron.getTasks().values()][0];
    assert.equal(cron.getTasks().size, 1);
    scheduler.scheduleProfile(profile);
    assert.equal(first.getStatus(), "destroyed");
    assert.equal(cron.getTasks().size, 1);
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(calls.length, 0);
    assert.equal(scheduler.getScheduledRunsHistory(1).length, 0);
    scheduler.unscheduleProfile(1);
    assert.equal(cron.getTasks().size, 0);
    scheduler.scheduleProfile(profile);
    scheduler.stopAll();
    assert.equal(cron.getTasks().size, 0);
  } finally { scheduler.stopAll(); db.close(); }
});

function createTestDb() {
  const db = new Database(":memory:");

  db.exec(`
    CREATE TABLE profile (
      id INTEGER PRIMARY KEY,
      "sessionName" TEXT NOT NULL,
      "chromeProfilePath" TEXT,
      "jobTitle" TEXT,
      "locationGeoId" TEXT,
      "f_TPR" TEXT,
      "f_WT" TEXT,
      "want" TEXT,
      "not_want" TEXT,
      "headless" INTEGER DEFAULT 0,
      "maxPage" INTEGER,
      "statePath" TEXT,
      "reporting" TEXT,
      "cron" TEXT,
      archived_at TEXT
    );

    CREATE TABLE workflow_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_id INTEGER NOT NULL,
      batch_id TEXT,
      status TEXT NOT NULL,
      trigger_source TEXT NOT NULL DEFAULT 'manual',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT,
      error_message TEXT
    );

    CREATE TABLE scheduled_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_id INTEGER NOT NULL,
      scheduled_for TEXT NOT NULL,
      trigger_source TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      triggered_at TEXT,
      run_id INTEGER,
      skip_reason TEXT
    );
  `);

  return db;
}

function insertProfile(db, id, sessionName, cronExpression) {
  db.prepare(
    `
      INSERT INTO profile (
        id,
        "sessionName",
        "chromeProfilePath",
        "locationGeoId",
        "f_TPR",
        "headless",
        "reporting",
        "cron"
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `
  ).run(
    id,
    sessionName,
    `/tmp/${sessionName}`,
    "105088894",
    "r604800",
    0,
    "{}",
    cronExpression
  );
}

test("scheduled runs queue behind an active workflow instead of starting in parallel", () => {
  const db = createTestDb();
  const startWorkflowCalls = [];
  const timestamp = "2026-03-16T10:00:30Z";

  scheduler.initScheduler(db, (runId) => startWorkflowCalls.push(runId), () => timestamp);

  insertProfile(db, 1, "linkedin-qa-bcn", "0 8-18/3 * * 1-5");
  insertProfile(db, 2, "linkedin-qa-pl", "0 8-18/3 * * 1-5");
  insertProfile(db, 99, "linkedin-qa-blocker", null);

  db.prepare(
    `
      INSERT INTO workflow_runs (
        profile_id,
        batch_id,
        status,
        trigger_source,
        created_at,
        updated_at
      )
      VALUES (?, ?, 'running', 'manual', ?, ?)
    `
  ).run(99, "manual:blocker", timestamp, timestamp);

  try {
    scheduler.__testOnly.triggerScheduledRun(1, "schedule", "2026-03-16T10:00:00Z");
    scheduler.__testOnly.triggerScheduledRun(2, "schedule", "2026-03-16T10:00:00Z");

    const scheduledRuns = db
      .prepare(
        `
          SELECT profile_id, status, trigger_source, run_id
          FROM scheduled_runs
          ORDER BY profile_id ASC
        `
      )
      .all();
    const queuedRuns = db
      .prepare(
        `
          SELECT profile_id, status, trigger_source
          FROM workflow_runs
          WHERE trigger_source = 'schedule'
          ORDER BY profile_id ASC
        `
      )
      .all();

    assert.equal(startWorkflowCalls.length, 0);
    assert.deepEqual(
      scheduledRuns.map((run) => ({
        profile_id: run.profile_id,
        status: run.status,
        trigger_source: run.trigger_source,
        has_run_id: run.run_id !== null,
      })),
      [
        {
          profile_id: 1,
          status: "triggered",
          trigger_source: "schedule",
          has_run_id: true,
        },
        {
          profile_id: 2,
          status: "triggered",
          trigger_source: "schedule",
          has_run_id: true,
        },
      ]
    );
    assert.deepEqual(queuedRuns, [
      { profile_id: 1, status: "queued", trigger_source: "schedule" },
      { profile_id: 2, status: "queued", trigger_source: "schedule" },
    ]);
  } finally {
    scheduler.stopAll();
    db.close();
  }
});

test("missed schedule checks create catch-up runs for unrecorded slots", () => {
  const db = createTestDb();
  const startWorkflowCalls = [];

  scheduler.initScheduler(
    db,
    (runId, profile) => startWorkflowCalls.push({ runId, profileId: profile.id }),
    () => "2026-03-16T10:01:00Z"
  );

  try {
    insertProfile(db, 1, "linkedin-qa-bcn", "0 8-18/3 * * 1-5");
    scheduler.__testOnly.checkMissedSchedules();

    const scheduledRuns = db
      .prepare(
        `
          SELECT profile_id, scheduled_for, trigger_source, status
          FROM scheduled_runs
          ORDER BY id ASC
        `
      )
      .all();
    const workflowRuns = db
      .prepare(
        `
          SELECT profile_id, status, trigger_source
          FROM workflow_runs
          ORDER BY id ASC
        `
      )
      .all();

    assert.deepEqual(startWorkflowCalls, [{ runId: 1, profileId: 1 }]);
    assert.deepEqual(scheduledRuns, [
      {
        profile_id: 1,
        scheduled_for: "2026-03-16T10:00:00Z",
        trigger_source: "catch_up",
        status: "triggered",
      },
    ]);
    assert.deepEqual(workflowRuns, [
      { profile_id: 1, status: "pending", trigger_source: "catch_up" },
    ]);
  } finally {
    scheduler.stopAll();
    db.close();
  }
});
