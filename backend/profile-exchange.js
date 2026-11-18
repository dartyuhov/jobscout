// Explicit portable contract. Never spread a runtime profile into an export.
const FORMAT = "job-search-settings";
const NOTICE = "Preference text can contain personal details. Review it before sharing.";
const DEFAULTS = {
  jobTitle: "", locationGeoId: "", f_TPR: "", f_WT: "", want: "", not_want: "", maxPage: 5,
};

function exportSettings(profile) {
  return {
    format: FORMAT, version: 1, notice: NOTICE,
    settings: Object.fromEntries(Object.entries(DEFAULTS).map(([key, fallback]) => [key, profile[key] ?? fallback])),
  };
}

function validateSettings(document) {
  if (!document || Array.isArray(document) || document.format !== FORMAT) {
    throw new Error("Expected a job-search-settings JSON object.");
  }
  if (document.version !== 1) throw new Error("Unsupported search settings version; expected 1.");
  const source = document.settings;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw new Error("settings must be a JSON object.");
  }
  const settings = {};
  for (const [key, fallback] of Object.entries(DEFAULTS)) {
    const value = Object.hasOwn(source, key) ? source[key] : fallback;
    if (key === "maxPage") {
      if (!Number.isInteger(value) || value < 1 || value > 2147483647) {
        throw new Error("maxPage must be a positive 32-bit integer.");
      }
    } else if (typeof value !== "string") {
      throw new Error(`${key} must be a string.`);
    }
    settings[key] = value;
  }
  for (const key of ["locationGeoId", "f_TPR"]) {
    if (!settings[key].trim()) throw new Error(`${key} must be a non-empty string.`);
  }
  return settings;
}

module.exports = { exportSettings, validateSettings };
