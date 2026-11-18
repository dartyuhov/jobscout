export const THEME_STORAGE_KEY = "jobsPortal_themePreference";
export const THEME_PREFERENCES = ["system", "light", "dark"];

function canUseBrowserApi() {
  return typeof window !== "undefined" && typeof document !== "undefined";
}

export function isThemePreference(value) {
  return THEME_PREFERENCES.includes(value);
}

export function getStoredThemePreference() {
  if (!canUseBrowserApi()) {
    return "system";
  }

  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(value) ? value : "system";
  } catch {
    return "system";
  }
}

export function getSystemTheme() {
  if (!canUseBrowserApi() || typeof window.matchMedia !== "function") {
    return "light";
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveTheme(preference) {
  return preference === "system" ? getSystemTheme() : preference;
}

export function applyThemePreference(preference) {
  const nextPreference = isThemePreference(preference) ? preference : "system";
  const resolvedTheme = resolveTheme(nextPreference);

  if (canUseBrowserApi()) {
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.dataset.themePreference = nextPreference;
    document.documentElement.style.colorScheme = resolvedTheme;

    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, nextPreference);
    } catch {
      // Ignore storage failures.
    }
  }

  return resolvedTheme;
}
