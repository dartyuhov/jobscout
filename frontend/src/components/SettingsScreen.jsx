import { useEffect, useState } from "react";

import {
  fetchCvText,
  fetchChromeProfileSettings,
  fetchIgnoredCompaniesSettings,
  fetchNotificationSettings,
  killAllActiveChromeProfiles,
  killChromeProfile,
  saveThemeSettings,
  saveCvText,
  saveIgnoredCompaniesSettings,
  saveNotificationSettings,
} from "../api/jobs";
import "./SettingsScreen.css";
import AiConnectionSettings from "./AiConnectionSettings";

const THEME_OPTIONS = [
  {
    value: "system",
    label: "System",
    description: "Match your operating system preference automatically.",
  },
  {
    value: "light",
    label: "Light",
    description: "Always use the light portal theme.",
  },
  {
    value: "dark",
    label: "Dark",
    description: "Always use the dark portal theme.",
  },
];

export default function SettingsScreen({
  themePreference,
  effectiveTheme,
  onThemePreferenceChange,
  onAiReadinessChange,
}) {
  const [cvText, setCvText] = useState("");
  const [ignoredCompaniesText, setIgnoredCompaniesText] = useState("");
  const [browserNotificationsEnabled, setBrowserNotificationsEnabled] = useState(true);
  const [chromeProfiles, setChromeProfiles] = useState([]);
  const [chromeProfilesChecked, setChromeProfilesChecked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadingChromeProfiles, setLoadingChromeProfiles] = useState(false);
  const [cleaningProfilePath, setCleaningProfilePath] = useState("");
  const [cleaningAllProfiles, setCleaningAllProfiles] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function loadSettings() {
    return Promise.all([
      fetchCvText(),
      fetchNotificationSettings(),
      fetchIgnoredCompaniesSettings(),
    ]);
  }

  useEffect(() => {
    let canceled = false;

    setLoading(true);
    setError("");

    loadSettings()
      .then(([text, notificationSettings, ignoredCompaniesSettings]) => {
        if (!canceled) {
          setCvText(text || "");
          setBrowserNotificationsEnabled(
            notificationSettings.browser_notifications_enabled !== false
          );
          setIgnoredCompaniesText((ignoredCompaniesSettings.ignored_companies || []).join("\n"));
        }
      })
      .catch((err) => {
        if (!canceled) {
          setError(err.message || "Could not load settings.");
        }
      })
      .finally(() => {
        if (!canceled) {
          setLoading(false);
        }
      });

    return () => {
      canceled = true;
    };
  }, []);

  function formatCleanupSuccess(cleanedProfiles) {
    if (!cleanedProfiles || cleanedProfiles.length === 0) {
      return "No active configured Chrome profiles were found.";
    }

    const closedSessions = cleanedProfiles.reduce(
      (total, item) => total + (item.closed_session_count || 0),
      0
    );
    const cancelledRuns = cleanedProfiles.reduce(
      (total, item) => total + (item.cancelled_run_count || 0),
      0
    );

    return `Cleaned ${cleanedProfiles.length} Chrome profile(s), closed ${closedSessions} tracked session(s), and cancelled ${cancelledRuns} run(s).`;
  }

  function parseIgnoredCompaniesInput(value) {
    const seen = new Set();
    const companies = [];

    for (const line of String(value || "").split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) {
        continue;
      }

      const normalizedKey = trimmed.toLowerCase();
      if (seen.has(normalizedKey)) {
        continue;
      }

      seen.add(normalizedKey);
      companies.push(trimmed);
    }

    return companies;
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess("");

    try {
      const ignoredCompanies = parseIgnoredCompaniesInput(ignoredCompaniesText);
      const [, , , ignoredCompaniesResult] = await Promise.all([
        saveCvText(cvText),
        saveNotificationSettings(browserNotificationsEnabled),
        saveThemeSettings(themePreference),
        saveIgnoredCompaniesSettings(ignoredCompanies),
      ]);
      setIgnoredCompaniesText((ignoredCompaniesResult.ignored_companies || []).join("\n"));
      const updatedJobCount = ignoredCompaniesResult.updated_job_count || 0;
      setSuccess(
        updatedJobCount > 0
          ? `Settings saved successfully. ${updatedJobCount} matching job(s) were marked as not suitable.`
          : "Settings saved successfully!"
      );
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message || "Could not save settings.");
    } finally {
      setSaving(false);
    }
  }

  async function refreshChromeProfiles() {
    setLoadingChromeProfiles(true);

    try {
      const profiles = await fetchChromeProfileSettings();
      setChromeProfiles(profiles.filter((profile) => profile.is_in_use));
      setChromeProfilesChecked(true);
    } finally {
      setLoadingChromeProfiles(false);
    }
  }

  async function handleKillProfile(chromeProfilePath) {
    setCleaningProfilePath(chromeProfilePath);
    setError("");
    setSuccess("");

    try {
      const payload = await killChromeProfile(chromeProfilePath);
      await refreshChromeProfiles();
      setSuccess(formatCleanupSuccess([payload.cleaned]));
    } catch (err) {
      setError(err.message || "Could not clean Chrome profile.");
    } finally {
      setCleaningProfilePath("");
    }
  }

  async function handleKillAllProfiles() {
    const activeProfileCount = chromeProfiles.filter((profile) => profile.is_in_use).length;
    if (activeProfileCount === 0) {
      setSuccess("No active configured Chrome profiles were found.");
      return;
    }

    const confirmed = window.confirm(
      `Kill ${activeProfileCount} active configured Chrome profile(s)? This will also cancel any active runs using them.`
    );
    if (!confirmed) {
      return;
    }

    setCleaningAllProfiles(true);
    setError("");
    setSuccess("");

    try {
      const payload = await killAllActiveChromeProfiles();
      await refreshChromeProfiles();
      setSuccess(formatCleanupSuccess(payload.cleaned));
    } catch (err) {
      setError(err.message || "Could not clean active Chrome profiles.");
    } finally {
      setCleaningAllProfiles(false);
    }
  }

  const activeChromeProfileCount = chromeProfiles.filter((profile) => profile.is_in_use).length;

  return (
    <div className="settings-screen">
      <h2>Settings</h2>
      <AiConnectionSettings onReadinessChange={onAiReadinessChange} />
      <p className="settings-description">
        Configure global settings for JobScout. Your CV/resume text is used to generate
        personalized cover letters for job applications.
      </p>

      {error ? <p className="error-banner">{error}</p> : null}
      {success ? <p className="success-banner">{success}</p> : null}

      <div className="settings-section">
        <h3>Appearance</h3>
        <p className="section-description">
          Use the system theme by default, or force the portal to stay in light or dark mode.
        </p>

        <div className="theme-option-grid" role="radiogroup" aria-label="Theme preference">
          {THEME_OPTIONS.map((option) => {
            const selected = themePreference === option.value;
            return (
              <label
                key={option.value}
                className={`theme-option-card${selected ? " selected" : ""}`}
              >
                <input
                  type="radio"
                  name="theme-preference"
                  value={option.value}
                  checked={selected}
                  onChange={(event) => onThemePreferenceChange(event.target.value)}
                  disabled={saving}
                />
                <span className="theme-option-copy">
                  <strong>{option.label}</strong>
                  <span>{option.description}</span>
                </span>
              </label>
            );
          })}
        </div>

        <p className="settings-helper-text">
          Active theme right now: <strong>{effectiveTheme}</strong>
          {themePreference === "system" ? " (following your system setting)." : "."}
        </p>
      </div>

      <div className="settings-section">
        <h3>Notifications</h3>
        <p className="section-description">
          Control whether completed workflow batches can trigger native system
          notifications from the portal while it is open.
        </p>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <label className="settings-toggle-row">
            <span>
              <strong>Browser system notifications</strong>
              <span className="settings-toggle-note">
                Shows an OS notification after a whole batch finishes.
              </span>
            </span>
            <input
              type="checkbox"
              checked={browserNotificationsEnabled}
              onChange={(e) => setBrowserNotificationsEnabled(e.target.checked)}
              disabled={saving}
            />
          </label>
        )}
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>Chrome Profiles</h3>
            <p className="section-description settings-section-description-compact">
              Manually check which configured Chrome profiles are active, then clean them up if needed.
            </p>
          </div>
          <div className="settings-section-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={refreshChromeProfiles}
              disabled={loading || loadingChromeProfiles || cleaningAllProfiles || saving}
            >
              {loadingChromeProfiles ? "Checking..." : "Check Active Status"}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={handleKillAllProfiles}
              disabled={
                loading ||
                loadingChromeProfiles ||
                cleaningAllProfiles ||
                saving ||
                activeChromeProfileCount === 0
              }
            >
              {cleaningAllProfiles ? "Killing Active Profiles..." : "Kill All Active"}
            </button>
          </div>
        </div>

        {loading || loadingChromeProfiles ? (
          <p>Loading...</p>
        ) : !chromeProfilesChecked ? (
          <p className="settings-helper-text">
            Click <strong>Check Active Status</strong> to load only profiles that are currently in use.
          </p>
        ) : chromeProfiles.length === 0 ? (
          <p className="settings-helper-text">No active configured Chrome profiles found.</p>
        ) : (
          <div className="chrome-profile-list" role="list">
            {chromeProfiles.map((profile) => {
              const isCleaning = cleaningProfilePath === profile.chrome_profile_path;
              return (
                <div
                  key={profile.chrome_profile_path}
                  className="chrome-profile-card"
                  role="listitem"
                >
                  <div className="chrome-profile-copy">
                    <div className="chrome-profile-heading-row">
                      <strong>{profile.chrome_profile_path}</strong>
                      <span
                        className={`chrome-profile-status${profile.is_in_use ? " is-active" : ""}`}
                      >
                        {profile.is_in_use ? "In use" : "Idle"}
                      </span>
                    </div>
                    <p className="chrome-profile-meta">
                      Profiles: {profile.session_names.join(", ") || "-"}
                    </p>
                    <p className="chrome-profile-meta">
                      Tracked sessions: {profile.tracked_session_names.join(", ") || "None"}
                    </p>
                    <p className="chrome-profile-meta">
                      Active runs: {profile.active_run_ids.length > 0 ? profile.active_run_ids.join(", ") : "None"}
                    </p>
                  </div>

                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => handleKillProfile(profile.chrome_profile_path)}
                    disabled={saving || cleaningAllProfiles || Boolean(cleaningProfilePath)}
                  >
                    {isCleaning ? "Killing..." : "Kill"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="settings-section">
        <h3>Ignored Companies</h3>
        <p className="section-description">
          Add one company name per line to automatically mark matching review jobs as not suitable.
          Matching is case-insensitive and uses the exact company name after trimming spaces.
        </p>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <textarea
            className="settings-textarea settings-textarea-compact"
            value={ignoredCompaniesText}
            onChange={(e) => setIgnoredCompaniesText(e.target.value)}
            placeholder={["Company A", "Company B", "Company C"].join("\n")}
            rows={8}
            disabled={saving}
          />
        )}
      </div>

      <div className="settings-section">
        <h3>CV / Resume Text</h3>
        <p className="section-description">
          Paste your CV or resume text below. This will be used when generating cover letters for
          job applications. Plain text format works best.
        </p>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <>
            <textarea
              className="settings-textarea cv-textarea"
              value={cvText}
              onChange={(e) => setCvText(e.target.value)}
              placeholder="Paste your CV/resume text here..."
              rows={20}
              disabled={saving}
            />

            <div className="settings-actions">
              <button
                type="button"
                className="save-button"
                onClick={handleSave}
                disabled={saving || loading}
              >
                {saving ? "Saving..." : "Save Settings"}
              </button>
              <span className="char-count">
                {cvText.length.toLocaleString()} characters
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
