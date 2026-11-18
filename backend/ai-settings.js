const { randomUUID } = require("node:crypto");
const { getAiReadiness, readConnection } = require("./ai-readiness");
const { generateText, AiConnectionError } = require("./ai-client");

function publicSettings(db) {
  const connection = readConnection(db);
  return {
    base_url: connection?.base_url || "", model: connection?.model || "",
    token_present: Boolean(connection?.token), revision: connection?.revision || null,
    ...getAiReadiness(db),
  };
}

function persist(db, connection) {
  db.prepare(`INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`)
    .run("ai_connection", JSON.stringify(connection), new Date().toISOString());
}

function registerAiSettings(app, db) {
  app.put("/api/settings/ai/automation", (req, res) => {
    const { auto_analyze, auto_summarize } = req.body || {};
    if (typeof auto_analyze !== "boolean" || typeof auto_summarize !== "boolean") {
      return res.status(400).json({ error: "Automatic analysis and summaries must each be explicit booleans." });
    }
    const current = getAiReadiness(db);
    if (((auto_analyze && !current.auto_analyze) || (auto_summarize && !current.auto_summarize)) && !current.ready) {
      return res.status(503).json({ error: getAiReadiness(db).guidance });
    }
    db.prepare(`INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`)
      .run("ai_automation", JSON.stringify({ auto_analyze, auto_summarize }), new Date().toISOString());
    res.json(publicSettings(db));
  });
  app.get("/api/settings/ai", (_req, res) => res.json(publicSettings(db)));
  app.put("/api/settings/ai", (req, res) => {
    const { base_url, model, token } = req.body || {};
    let url;
    try { url = new URL(base_url); } catch {}
    if (typeof base_url !== "string" || !url || !["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      return res.status(400).json({ error: "Base URL must be a full HTTP(S) URL without credentials, query, or fragment." });
    }
    if (typeof model !== "string" || !model.trim() || /[\r\n]/.test(model)) {
      return res.status(400).json({ error: "A model name is required." });
    }
    if (token !== undefined && (typeof token !== "string" || /[\r\n]/.test(token))) {
      return res.status(400).json({ error: "Token must be a single-line string; use an empty string to remove it." });
    }
    const previous = readConnection(db);
    const next = { base_url: base_url.trim().replace(/\/+$/, ""), model: model.trim(), token: token === undefined ? previous?.token || "" : token.trim() };
    const unchanged = previous && ["base_url", "model", "token"].every((key) => previous[key] === next[key]);
    persist(db, unchanged ? previous : { ...next, revision: randomUUID(), verified_revision: null });
    res.json(publicSettings(db));
  });
  app.post("/api/settings/ai/test", async (req, res) => {
    const connection = readConnection(db);
    if (!connection || req.body?.revision !== connection.revision) {
      return res.status(409).json({ error: "AI settings changed. Reload Settings, save, and test the current connection." });
    }
    // A re-test is an explicit verification attempt, not an ordinary runtime failure.
    const testAttempt = randomUUID();
    persist(db, { ...connection, verified_revision: null, test_attempt: testAttempt });
    try {
      await generateText(connection, [{ role: "user", content: "Reply with OK to test text generation." }]);
      const current = readConnection(db);
      if (current?.revision !== connection.revision || current.test_attempt !== testAttempt) {
        return res.status(409).json({ error: "AI settings changed during the test. Test the current saved connection." });
      }
      persist(db, { ...current, verified_revision: current.revision });
      res.json(publicSettings(db));
    } catch (error) {
      res.status(502).json({ error: error instanceof AiConnectionError ? error.message : "AI connection test failed. Check Settings and retry." });
    }
  });
}

module.exports = { registerAiSettings };
