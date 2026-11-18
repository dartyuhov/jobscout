const AI_SETUP_GUIDANCE =
  "AI is not configured. Configure and verify an AI connection in Settings before using AI features. " +
  "Collection, parsing, and review work without AI.";

function validConnection(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.keys(value).some((key) => !["base_url", "model", "token", "revision", "verified_revision", "test_attempt"].includes(key))) return false;
  const text = (v) => typeof v === "string" && Boolean(v.trim()) && !/[\r\n]/.test(v);
  if (!text(value.base_url) || !text(value.model) || !text(value.revision) ||
      typeof value.token !== "string" || /[\r\n]/.test(value.token) ||
      !(value.verified_revision === null || text(value.verified_revision)) ||
      !(value.test_attempt === undefined || text(value.test_attempt))) return false;
  if (!/^https?:\/\//i.test(value.base_url) || /\s/.test(value.base_url)) return false;
  try {
    const url = new URL(value.base_url);
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname) &&
      !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

function readConnection(database) {
  const row = database.prepare("SELECT value FROM settings WHERE key = ?").get("ai_connection");
  try {
    const connection = row ? JSON.parse(row.value) : null;
    if (!validConnection(connection)) return null;
    return connection;
  } catch {
    // JSON parser diagnostics can contain excerpts of the saved private token.
    // Invalid settings must disable AI without breaking review or Settings recovery.
    return null;
  }
}

function getAiReadiness(database) {
  const connection = readConnection(database);
  const ready = Boolean(connection &&
    connection.verified_revision === connection.revision);
  let automation = {};
  try { automation = JSON.parse(database.prepare("SELECT value FROM settings WHERE key = ?").get("ai_automation")?.value || "{}"); } catch {}
  return { ready, auto_analyze: automation?.auto_analyze === true, auto_summarize: automation?.auto_summarize === true, guidance: ready ? "" : AI_SETUP_GUIDANCE };
}

module.exports = { getAiReadiness, readConnection };
