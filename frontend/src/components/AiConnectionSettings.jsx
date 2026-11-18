import { useEffect, useState } from "react";
import { fetchAiSettings, saveAiSettings, testAiSettings, saveAiAutomation } from "../api/jobs";
import { DEFAULT_AI_READINESS } from "../aiReadiness";

export default function AiConnectionSettings({ onReadinessChange }) {
  const [saved, setSaved] = useState(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty = !saved || baseUrl !== saved.base_url || model !== saved.model || token !== "";

  function apply(settings) {
    setSaved(settings);
    setBaseUrl(settings.base_url);
    setModel(settings.model);
    setToken("");
    onReadinessChange?.({ ...DEFAULT_AI_READINESS, ...settings });
  }

  useEffect(() => {
    let canceled = false;
    fetchAiSettings().then((settings) => { if (!canceled) apply(settings); })
      .catch(() => { if (!canceled) setError("Could not load AI settings. Reload to retry."); });
    return () => { canceled = true; };
  }, []);

  async function save(removeToken = false) {
    setBusy(true);
    setError("");
    const body = { base_url: baseUrl, model, ...(removeToken ? { token: "" } : token ? { token } : {}) };
    // The draft token is transient component state, never browser storage.
    setToken("");
    try { apply(await saveAiSettings(body)); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function test() {
    setBusy(true);
    setError("");
    onReadinessChange?.(DEFAULT_AI_READINESS);
    setSaved((current) => ({ ...current, ready: false }));
    try { apply(await testAiSettings(saved.revision)); }
    catch (err) {
      setError(err.message);
      try { apply(await fetchAiSettings()); } catch {}
    } finally { setBusy(false); }
  }

  async function setAutomation(key, enabled) {
    const previous = saved;
    setSaved((current) => ({ ...current, [key]: enabled }));
    setBusy(true);
    setError("");
    try {
      apply(await saveAiAutomation({ auto_analyze: saved.auto_analyze, auto_summarize: saved.auto_summarize, [key]: enabled }));
    } catch (err) { setSaved(previous); setError(err.message); }
    finally { setBusy(false); }
  }

  return (
    <section className="settings-section ai-connection-settings" aria-label="AI connection">
      <h3>AI connection</h3>
      <p className="section-description">Configure an OpenAI-compatible Chat Completions endpoint. Collection, parsing, and review work without AI.</p>
      <p className="section-description">Enter the full API base URL, including any path such as /v1. We append /chat/completions. No credentials or query parameters in the URL.</p>
      <fieldset disabled={busy || !saved}>
        <label htmlFor="ai-base-url">AI base URL</label>
        <input id="ai-base-url" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" autoComplete="off" />
        <label htmlFor="ai-model">AI model</label>
        <input id="ai-model" value={model} onChange={(event) => setModel(event.target.value)} autoComplete="off" />
        <label htmlFor="ai-token">AI token (optional)</label>
        <input id="ai-token" type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="new-password" aria-describedby="ai-token-help" />
        <p id="ai-token-help" className="settings-helper-text">Leave blank to keep the saved token. Token-free servers are supported.</p>
        <p>{saved?.token_present ? "Token saved" : "No token saved"}</p>
        <div className="settings-section-actions">
          <button type="button" disabled={!baseUrl.trim() || !model.trim() || !dirty} onClick={() => save()}>Save AI connection</button>
          <button type="button" disabled={dirty || !saved?.revision} onClick={test}>Test saved connection</button>
          {saved?.token_present && <button type="button" disabled={dirty} onClick={() => save(true)}>Remove saved token</button>}
        </div>
      </fieldset>
      <p role="status">{busy ? "Working…" : dirty ? "Save changes before testing" : saved?.ready ? "AI connection verified" : "Saved connection needs verification"}</p>
      {error && <p role="alert" className="error-banner">{error}</p>}
      <p className="section-description">Testing sends a short generation request to this provider and may incur costs. AI features send their required context to your configured provider.</p>
      <p className="section-description">The token is stored unencrypted in your private backend database. Database backups contain the token. Saved tokens are never returned to the browser.</p>
      <p className="section-description">Automatic processing is off initially. Saving or testing a connection does not enable it. Enabling a feature allows recurring provider requests and possible costs. Analysis sends job details and exclusions; summaries send job descriptions. Neither includes your CV.</p>
      <fieldset disabled={busy || dirty || !saved}>
        <legend>Automatic AI processing</legend>
        <label><input type="checkbox" disabled={!saved?.ready && !saved?.auto_analyze} checked={saved?.auto_analyze || false} onChange={(event) => setAutomation("auto_analyze", event.target.checked)} /> Automatically analyze collected jobs</label>
        <label><input type="checkbox" disabled={!saved?.ready && !saved?.auto_summarize} checked={saved?.auto_summarize || false} onChange={(event) => setAutomation("auto_summarize", event.target.checked)} /> Automatically summarize matched jobs (including existing matches)</label>
      </fieldset>
    </section>
  );
}
