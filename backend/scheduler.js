/**
 * Scheduler module for automated workflow runs based on cron expressions.
 *
 * This module manages scheduling profiles that have cron expressions set,
 * handles catch-up for missed schedules on startup, and tracks scheduled
 * run history in the database.
 */

const cron = require("node-cron");
const cronParser = require("cron-parser");

// Map of profileId -> cron job instance
const scheduledJobs = new Map();

let missedScheduleInterval = null;

// Reference to database (set during init)
let db = null;

// Reference to startWorkflow function (injected from server.js)
let startWorkflowFn = null;

// Reference to utcNow function (injected from server.js)
let utcNowFn = null;

/**
 * Validate a cron expression.
 * @param {string} expression - The cron expression to validate
 * @returns {{ valid: boolean, error?: string, nextRuns?: string[] }}
 */
function validateCronExpression(expression) {
  if (!expression || typeof expression !== "string") {
    return { valid: false, error: "Cron expression is required" };
  }

  const trimmed = expression.trim();
  if (!trimmed) {
    return { valid: false, error: "Cron expression cannot be empty" };
  }

  // node-cron uses 5-field format (minute, hour, day of month, month, day of week)
  // Optionally with seconds (6 fields)
  if (!cron.validate(trimmed)) {
    return {
      valid: false,
      error: `Invalid cron expression: "${trimmed}". Use format: minute hour day-of-month month day-of-week (e.g., "0 2 * * *" for daily at 2am)`,
    };
  }

  // Calculate next run times using cron-parser
  try {
    const interval = cronParser.parseExpression(trimmed);
    const nextRuns = [];
    for (let i = 0; i < 3; i++) {
      nextRuns.push(interval.next().toISOString());
    }
    return { valid: true, nextRuns };
  } catch (err) {
    return { valid: false, error: `Failed to parse cron expression: ${err.message}` };
  }
}

/**
 * Get the next run time for a cron expression.
 * @param {string} expression - The cron expression
 * @returns {string|null} - ISO timestamp of next run, or null if invalid
 */
function getNextRunTime(expression, currentDate = undefined) {
  if (!expression) return null;
  try {
    const interval = cronParser.parseExpression(
      expression.trim(),
      currentDate ? { currentDate } : undefined
    );
    return interval.next().toISOString();
  } catch {
    return null;
  }
}

/**
 * Get the previous run time for a cron expression (most recent past occurrence).
 * @param {string} expression - The cron expression
 * @returns {string|null} - ISO timestamp of previous run, or null if invalid
 */
function getPreviousRunTime(expression, currentDate = undefined) {
  if (!expression) return null;
  try {
    const interval = cronParser.parseExpression(
      expression.trim(),
      currentDate ? { currentDate } : undefined
    );
    return interval.prev().toISOString();
  } catch {
    return null;
  }
}

/**
 * Create a scheduled_runs record.
 * @param {number} profileId
 * @param {string} scheduledFor - ISO timestamp
 * @param {string} triggerSource - 'schedule' | 'catch_up' | 'manual'
 * @returns {number} - The created record ID
 */
function createScheduledRunRecord(profileId, scheduledFor, triggerSource) {
  const timestamp = utcNowFn();
  const result = db
    .prepare(
      `
      INSERT INTO scheduled_runs (
        profile_id,
        scheduled_for,
        trigger_source,
        status,
        created_at
      )
      VALUES (?, ?, ?, 'pending', ?)
    `
    )
    .run(profileId, scheduledFor, triggerSource, timestamp);
  return result.lastInsertRowid;
}

/**
 * Update a scheduled_runs record when triggered.
 * @param {number} scheduledRunId
 * @param {number} runId - workflow_runs.id
 */
function markScheduledRunTriggered(scheduledRunId, runId) {
  const timestamp = utcNowFn();
  db.prepare(
    `
    UPDATE scheduled_runs
    SET triggered_at = ?,
        run_id = ?,
        status = 'triggered'
    WHERE id = ?
  `
  ).run(timestamp, runId, scheduledRunId);
}

/**
 * Mark a scheduled_runs record as skipped.
 * @param {number} scheduledRunId
 * @param {string} reason
 */
function markScheduledRunSkipped(scheduledRunId, reason) {
  db.prepare(
    `
    UPDATE scheduled_runs
    SET status = 'skipped',
        skip_reason = ?
    WHERE id = ?
  `
  ).run(reason, scheduledRunId);
}

/**
 * Check if a profile has an active workflow.
 * @param {number} profileId
 * @returns {number|null} - Run ID if active, null otherwise
 */
function getActiveWorkflowForProfile(profileId) {
  const row = db
    .prepare(
      "SELECT id FROM workflow_runs WHERE profile_id = ? AND status IN ('pending', 'running', 'queued')"
    )
    .get(profileId);
  return row ? row.id : null;
}

function hasActiveWorkflow() {
  const row = db
    .prepare("SELECT id FROM workflow_runs WHERE status IN ('pending', 'running') LIMIT 1")
    .get();
  return Boolean(row);
}

/**
 * Get a profile by ID with all fields needed for workflow execution.
 * @param {number} profileId
 * @returns {object|null}
 */
function getProfileById(profileId) {
  return db
    .prepare(
      `
      SELECT
        id,
        "sessionName",
        "chromeProfilePath",
        "jobTitle",
        "locationGeoId",
        "f_TPR",
        "f_WT",
        "want",
        "not_want",
        "headless",
        "maxPage",
        "statePath",
        "cron",
        archived_at
      FROM profile
      WHERE id = ?
        AND archived_at IS NULL
    `
    )
    .get(profileId);
}

function createWorkflowRunRecord(profileId, triggerSource, batchId, status = "pending") {
  const timestamp = utcNowFn();
  const result = db
    .prepare(
      `
      INSERT INTO workflow_runs (
        profile_id,
        batch_id,
        status,
        trigger_source,
        created_at,
        updated_at
      )
      VALUES (?, ?, ?, ?, ?, ?)
    `
    )
    .run(profileId, batchId, status, triggerSource, timestamp, timestamp);
  return result.lastInsertRowid;
}

/**
 * Trigger a scheduled workflow run for a profile.
 * @param {number} profileId
 * @param {string} triggerSource - 'schedule' | 'catch_up'
 * @param {string} [scheduledFor] - ISO timestamp of when this was scheduled for
 */
function triggerScheduledRun(profileId, triggerSource = "schedule", scheduledFor = null) {
  const effectiveScheduledFor = normalizeScheduledFor(scheduledFor || utcNowFn());
  const batchId = createScheduledBatchId(triggerSource, effectiveScheduledFor);

  // Create scheduled_runs record first
  const scheduledRunId = createScheduledRunRecord(profileId, effectiveScheduledFor, triggerSource);

  // Get fresh profile from DB
  const profile = getProfileById(profileId);

  if (!profile) {
    console.log(`[Scheduler] Profile ${profileId} not found, skipping scheduled run`);
    markScheduledRunSkipped(scheduledRunId, "profile_deleted");
    return;
  }

  // Check if cron is still set (profile may have been updated)
  if (!profile.cron || !profile.cron.trim()) {
    console.log(`[Scheduler] Profile ${profileId} no longer has cron set, skipping`);
    markScheduledRunSkipped(scheduledRunId, "cron_disabled");
    return;
  }

  // Avoid stacking multiple active runs for the same profile.
  const activeProfileRunId = getActiveWorkflowForProfile(profileId);
  if (activeProfileRunId) {
    console.log(
      `[Scheduler] Profile ${profileId} already has active workflow (run ${activeProfileRunId}), skipping`
    );
    markScheduledRunSkipped(scheduledRunId, `already_active:${activeProfileRunId}`);
    return;
  }

  const initialStatus = hasActiveWorkflow() ? "queued" : "pending";

  // Create workflow_runs record
  const runId = createWorkflowRunRecord(profileId, triggerSource, batchId, initialStatus);

  // Update scheduled_runs with the run ID
  markScheduledRunTriggered(scheduledRunId, runId);

  if (initialStatus === "queued") {
    console.log(
      `[Scheduler] Queued ${triggerSource} run for profile ${profileId} (${profile.sessionName}), run ID: ${runId}`
    );
    return;
  }

  console.log(
    `[Scheduler] Triggering ${triggerSource} run for profile ${profileId} (${profile.sessionName}), run ID: ${runId}`
  );

  // Start the workflow
  startWorkflowFn(runId, profile);
}

/**
 * Schedule a profile's cron job.
 * @param {object} profile - Profile object with id, sessionName, cron
 */
function scheduleProfile(profile) {
  const profileId = profile.id;

  // Remove existing job if any
  if (scheduledJobs.has(profileId)) {
    scheduledJobs.get(profileId).destroy();
    scheduledJobs.delete(profileId);
  }

  // If no cron expression, nothing to schedule
  if (!profile.cron || !profile.cron.trim()) {
    console.log(`[Scheduler] Profile ${profileId} (${profile.sessionName}) has no cron, not scheduling`);
    return;
  }

  const cronExpr = profile.cron.trim();

  // Validate before scheduling
  if (!cron.validate(cronExpr)) {
    console.error(
      `[Scheduler] Invalid cron expression for profile ${profileId} (${profile.sessionName}): "${cronExpr}"`
    );
    return;
  }

  // Schedule the job
  const job = cron.schedule(cronExpr, () => {
    triggerScheduledRun(profileId, "schedule", utcNowFn());
  });

  scheduledJobs.set(profileId, job);

  const nextRun = getNextRunTime(cronExpr);
  console.log(
    `[Scheduler] Scheduled profile ${profileId} (${profile.sessionName}) with cron "${cronExpr}", next run: ${nextRun}`
  );
}

/**
 * Remove a profile's scheduled job.
 * @param {number} profileId
 */
function unscheduleProfile(profileId) {
  if (scheduledJobs.has(profileId)) {
    scheduledJobs.get(profileId).destroy();
    scheduledJobs.delete(profileId);
    console.log(`[Scheduler] Unscheduled profile ${profileId}`);
  }
}

/**
 * Check for missed schedules and trigger catch-up runs.
 * Called on server startup.
 */
function checkMissedSchedules() {
  console.log("[Scheduler] Checking for missed schedules...");

  const profiles = db
    .prepare("SELECT * FROM profile WHERE cron IS NOT NULL AND cron != '' AND archived_at IS NULL")
    .all();

  for (const profile of profiles) {
    // Get the previous scheduled time (most recent past occurrence)
    const expectedLastRun = getPreviousRunTime(profile.cron, utcNowFn());
    if (!expectedLastRun) continue;

    // Find the last recorded scheduled slot for this profile.
    // Count both triggered and skipped rows so we do not re-create a slot
    // that was already accounted for.
    const lastRecordedRun = db
      .prepare(
        `
        SELECT scheduled_for
        FROM scheduled_runs
        WHERE profile_id = ?
          AND trigger_source IN ('schedule', 'catch_up')
        ORDER BY scheduled_for DESC
        LIMIT 1
      `
      )
      .get(profile.id);

    const lastRecordedTime = lastRecordedRun?.scheduled_for;

    // If we've never recorded this slot, queue a catch-up run.
    if (!lastRecordedTime || new Date(expectedLastRun) > new Date(lastRecordedTime)) {
      console.log(
        `[Scheduler] Missed schedule detected for profile ${profile.id} (${profile.sessionName}). ` +
          `Expected: ${expectedLastRun}, Last recorded: ${lastRecordedTime || "never"}`
      );

      // Trigger catch-up run
      triggerScheduledRun(profile.id, "catch_up", expectedLastRun);
    }
  }

  console.log("[Scheduler] Missed schedule check complete");
}

/**
 * Mark orphaned workflow runs as failed.
 * Called on server startup to clean up runs that were interrupted.
 */
function cleanupOrphanedRuns() {
  console.log("[Scheduler] Cleaning up orphaned workflow runs...");

  const timestamp = utcNowFn();
  const result = db
    .prepare(
      `
      UPDATE workflow_runs
      SET status = 'failed',
          finished_at = ?,
          error_message = 'Server restarted while workflow was running',
          updated_at = ?
      WHERE status IN ('pending', 'running')
    `
    )
    .run(timestamp, timestamp);

  if (result.changes > 0) {
    console.log(`[Scheduler] Marked ${result.changes} orphaned run(s) as failed`);
  }
}

/**
 * Initialize the scheduler.
 * @param {object} database - better-sqlite3 database instance
 * @param {function} startWorkflow - Function to start a workflow
 * @param {function} utcNow - Function to get current UTC timestamp
 */
function initScheduler(database, startWorkflow, utcNow) {
  db = database;
  startWorkflowFn = startWorkflow;
  utcNowFn = utcNow;

  console.log("[Scheduler] Initializing scheduler...");

  // Clean up orphaned runs from previous server instance
  cleanupOrphanedRuns();

  // Check for missed schedules and trigger catch-up runs
  checkMissedSchedules();

  if (missedScheduleInterval) {
    clearInterval(missedScheduleInterval);
  }
  missedScheduleInterval = setInterval(checkMissedSchedules, 60 * 1000);
  if (typeof missedScheduleInterval.unref === "function") {
    missedScheduleInterval.unref();
  }

  // Load all profiles with cron expressions and schedule them
  const profiles = db
    .prepare(
      "SELECT id, \"sessionName\", cron FROM profile WHERE cron IS NOT NULL AND cron != '' AND archived_at IS NULL"
    )
    .all();

  console.log(`[Scheduler] Found ${profiles.length} profile(s) with cron schedules`);

  for (const profile of profiles) {
    scheduleProfile(profile);
  }

  console.log("[Scheduler] Initialization complete");
}

/**
 * Stop all scheduled jobs.
 * Called on graceful shutdown.
 */
function stopAll() {
  console.log("[Scheduler] Stopping all scheduled jobs...");
  for (const [profileId, job] of scheduledJobs) {
    job.destroy();
    console.log(`[Scheduler] Stopped job for profile ${profileId}`);
  }
  scheduledJobs.clear();

  if (missedScheduleInterval) {
    clearInterval(missedScheduleInterval);
    missedScheduleInterval = null;
  }
}

/**
 * Get schedule info for all profiles.
 * @returns {Array<{profileId: number, sessionName: string, cron: string, nextRun: string|null}>}
 */
function getScheduleInfo() {
  const profiles = db
    .prepare(
      `
      SELECT id, "sessionName", cron
      FROM profile
      WHERE cron IS NOT NULL AND cron != ''
        AND archived_at IS NULL
    `
    )
    .all();

  return profiles.map((p) => ({
    profileId: p.id,
    sessionName: p.sessionName,
    cron: p.cron,
    nextRun: getNextRunTime(p.cron),
    isActive: scheduledJobs.has(p.id),
  }));
}

/**
 * Get scheduled runs history for a profile.
 * @param {number} profileId
 * @param {number} [limit=50]
 * @returns {Array}
 */
function getScheduledRunsHistory(profileId, limit = 50) {
  return db
    .prepare(
      `
      SELECT
        sr.id,
        sr.profile_id,
        sr.scheduled_for,
        sr.trigger_source,
        sr.triggered_at,
        sr.run_id,
        sr.status,
        sr.skip_reason,
        sr.created_at,
        wr.status as run_status,
        wr.finished_at as run_finished_at
      FROM scheduled_runs sr
      LEFT JOIN workflow_runs wr ON wr.id = sr.run_id
      WHERE sr.profile_id = ?
      ORDER BY sr.scheduled_for DESC
      LIMIT ?
    `
    )
    .all(profileId, limit);
}

function normalizeScheduledFor(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  parsed.setUTCSeconds(0, 0);
  return parsed.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function createScheduledBatchId(triggerSource, scheduledFor) {
  return `scheduled:${triggerSource}:${scheduledFor}`;
}

module.exports = {
  initScheduler,
  stopAll,
  scheduleProfile,
  unscheduleProfile,
  validateCronExpression,
  getNextRunTime,
  getScheduleInfo,
  getScheduledRunsHistory,
  __testOnly: {
    checkMissedSchedules,
    triggerScheduledRun,
  },
};
