import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchJobs,
  fetchAiReadiness,
  fetchJobStats,
  fetchProfiles,
  fetchThemeSettings,
  updateJobStatus,
} from "./api/jobs";
import AskAiScreen from "./components/AskAiScreen";
import { DEFAULT_AI_READINESS } from "./aiReadiness";
import CvRequiredModal from "./components/CvRequiredModal";
import FilterBar from "./components/FilterBar";
import JobModal from "./components/JobModal";
import JobsTable from "./components/JobsTable";
import ProfilesScreen from "./components/ProfilesScreen";
import RunWorkflowScreen from "./components/RunWorkflowScreen";
import SettingsScreen from "./components/SettingsScreen";
import TabNav from "./components/TabNav";
import WorkflowBatchNotifier from "./components/WorkflowBatchNotifier";
import { applyThemePreference, getStoredThemePreference, resolveTheme } from "./theme";
import "./App.css";

const FILTERS_STORAGE_KEY = "jobsPortal_filters";
const TAB_PATHS = {
  jobs: "/jobs",
  ai: "/ask-ai",
  profiles: "/profiles",
  runs: "/runs",
  settings: "/settings",
};

const DEFAULT_FILTERS = {
  profile_id: "",
  location: "",
  statuses: ["to_be_reviewed"],
  date_from: "",
  date_to: "",
  search: "",
  match_only: false,
  sort_by: "posted_date",
  sort_order: "desc",
  page: 1,
  per_page: 25,
};

function loadFiltersFromStorage() {
  try {
    const stored = localStorage.getItem(FILTERS_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      // Merge with defaults to handle any new filter fields
      return { ...DEFAULT_FILTERS, ...parsed, page: 1 };
    }
  } catch {
    // Ignore parse errors, use defaults
  }
  return DEFAULT_FILTERS;
}

function saveFiltersToStorage(filters) {
  try {
    // Don't persist page number - always start at page 1
    const toSave = { ...filters, page: 1 };
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(toSave));
  } catch {
    // Ignore storage errors
  }
}

function normalizePathname(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }
  return pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

function getTabFromPathname(pathname) {
  const normalizedPathname = normalizePathname(pathname);
  const matchingEntry = Object.entries(TAB_PATHS).find(([, path]) => path === normalizedPathname);
  return matchingEntry ? matchingEntry[0] : "jobs";
}

function getPathForTab(tabId) {
  return TAB_PATHS[tabId] || TAB_PATHS.jobs;
}

export default function App() {
  const [aiReadiness, setAiReadiness] = useState(DEFAULT_AI_READINESS);
  useEffect(() => {
    fetchAiReadiness()
      .then((ai) => setAiReadiness({ ...DEFAULT_AI_READINESS, ...ai }))
      .catch(() => setAiReadiness(DEFAULT_AI_READINESS));
  }, []);
  const [activeTab, setActiveTab] = useState(() => getTabFromPathname(window.location.pathname));
  const [themePreference, setThemePreference] = useState(getStoredThemePreference);
  const [effectiveTheme, setEffectiveTheme] = useState(() => resolveTheme(getStoredThemePreference()));
  const [profiles, setProfiles] = useState([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [filters, setFilters] = useState(loadFiltersFromStorage);
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [requestedAskAiJobId, setRequestedAskAiJobId] = useState(null);
  const [updatingJobIds, setUpdatingJobIds] = useState(() => new Set());
  const [showCvRequiredModal, setShowCvRequiredModal] = useState(false);
  const [jobStats, setJobStats] = useState({
    total_jobs: 0,
    matched_jobs: 0,
    applied_jobs: 0,
  });

  const loadProfiles = useCallback(async () => {
    setProfilesLoading(true);
    try {
      const items = await fetchProfiles();
      setProfiles(items);
      return items;
    } catch (err) {
      setError(err.message || "Could not load profiles.");
      return [];
    } finally {
      setProfilesLoading(false);
    }
  }, []);

  const loadJobStats = useCallback(() => {
    fetchJobStats()
      .then((stats) => {
        setJobStats({
          total_jobs: stats?.total_jobs || 0,
          matched_jobs: stats?.matched_jobs || 0,
          applied_jobs: stats?.applied_jobs || 0,
        });
      })
      .catch((err) => {
        setError(err.message || "Could not load job statistics.");
      });
  }, []);

  useEffect(() => {
    loadProfiles();
    loadJobStats();
  }, [loadProfiles, loadJobStats]);

  useEffect(() => {
    const resolvedTheme = applyThemePreference(themePreference);
    setEffectiveTheme(resolvedTheme);

    if (themePreference !== "system" || typeof window.matchMedia !== "function") {
      return undefined;
    }

    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleThemeChange = () => {
      setEffectiveTheme(applyThemePreference("system"));
    };

    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", handleThemeChange);
    } else {
      mediaQuery.addListener(handleThemeChange);
    }

    return () => {
      if (typeof mediaQuery.removeEventListener === "function") {
        mediaQuery.removeEventListener("change", handleThemeChange);
      } else {
        mediaQuery.removeListener(handleThemeChange);
      }
    };
  }, [themePreference]);

  useEffect(() => {
    let canceled = false;

    fetchThemeSettings()
      .then((settings) => {
        if (!canceled && settings?.theme_preference) {
          setThemePreference(settings.theme_preference);
        }
      })
      .catch((err) => {
        console.error("Failed to load theme settings:", err);
      });

    return () => {
      canceled = true;
    };
  }, []);

  useEffect(() => {
    const currentPath = normalizePathname(window.location.pathname);
    const expectedPath = getPathForTab(activeTab);

    if (currentPath !== expectedPath) {
      window.history.replaceState({}, "", expectedPath);
    }

    function handlePopState() {
      setActiveTab(getTabFromPathname(window.location.pathname));
      setError("");
    }

    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, [activeTab]);

  useEffect(() => {
    loadJobStats();
  }, [activeTab, loadJobStats]);

  // Persist filters to localStorage whenever they change
  useEffect(() => {
    saveFiltersToStorage(filters);
  }, [filters]);

  useEffect(() => {
    // Only fetch jobs when on jobs tab
    if (activeTab !== "jobs") {
      return undefined;
    }

    let canceled = false;
    setLoading(true);
    setError("");

    fetchJobs(filters)
      .then((payload) => {
        if (canceled) {
          return;
        }

        setJobs(payload.jobs || []);
        setTotal(payload.total || 0);
        setTotalPages(payload.total_pages || 1);
      })
      .catch((err) => {
        if (canceled) {
          return;
        }
        setError(err.message || "Could not load jobs.");
      })
      .finally(() => {
        if (!canceled) {
          setLoading(false);
        }
      });

    return () => {
      canceled = true;
    };
  }, [filters, activeTab]);

  const showingText = useMemo(() => {
    if (loading) {
      return "Loading jobs...";
    }
    return `Showing ${jobs.length} of ${total} job opportunities`;
  }, [jobs.length, loading, total]);

  // Extract job IDs for modal navigation
  const jobIds = useMemo(() => jobs.map((job) => job.id), [jobs]);

  // Navigation handlers for modal
  const selectedJobIndex = selectedJobId !== null ? jobIds.indexOf(selectedJobId) : -1;

  function handlePreviousJob() {
    if (selectedJobIndex > 0) {
      setSelectedJobId(jobIds[selectedJobIndex - 1]);
    }
  }

  function handleNextJob() {
    if (selectedJobIndex < jobIds.length - 1) {
      setSelectedJobId(jobIds[selectedJobIndex + 1]);
    }
  }

  function updateFilters(changes, { resetPage = true } = {}) {
    setFilters((previous) => ({
      ...previous,
      ...changes,
      page: resetPage ? 1 : (changes.page ?? previous.page),
    }));
  }

  function handleSort(column) {
    updateFilters({
      sort_by: column,
      sort_order:
        filters.sort_by === column && filters.sort_order === "asc" ? "desc" : "asc",
    });
  }

  function handlePageChange(page) {
    updateFilters({ page }, { resetPage: false });
  }

  async function handleStatusChange(jobId, status) {
    setUpdatingJobIds((previous) => {
      const next = new Set(previous);
      next.add(jobId);
      return next;
    });

    try {
      const updatedJob = await updateJobStatus(jobId, status);
      setJobs((previous) =>
        previous.map((job) =>
          job.id === jobId
            ? {
                ...job,
                user_status: updatedJob.user_status,
                updated_at: updatedJob.updated_at,
              }
            : job
        )
      );
      loadJobStats();
    } catch (err) {
      setError(err.message || "Could not update job status.");
    } finally {
      setUpdatingJobIds((previous) => {
        const next = new Set(previous);
        next.delete(jobId);
        return next;
      });
    }
  }

  function clearFilters() {
    setFilters({ ...DEFAULT_FILTERS });
  }

  function handleTabChange(tabId) {
    const nextPath = getPathForTab(tabId);
    if (normalizePathname(window.location.pathname) !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
    setActiveTab(tabId);
    setError("");
  }

  function handleCvRequired() {
    setShowCvRequiredModal(true);
  }

  function handleGoToSettings() {
    setShowCvRequiredModal(false);
    setSelectedJobId(null);
    handleTabChange("settings");
  }

  function handleOpenAskAiForJob(jobId) {
    setSelectedJobId(null);
    setRequestedAskAiJobId(jobId);
    handleTabChange("ai");
  }

  return (
    <div className="portal-shell">
      <WorkflowBatchNotifier />
      <header className="portal-header">
        <div className="portal-header-main">
          <div>
            <h1>JobScout</h1>
          </div>

          <div className="portal-stats" aria-label="Portal statistics">
            <article className="portal-stat-card">
              <span className="portal-stat-label">Total jobs found</span>
              <strong className="portal-stat-value">{jobStats.total_jobs}</strong>
            </article>
            <article className="portal-stat-card">
              <span className="portal-stat-label">Jobs matched found</span>
              <strong className="portal-stat-value">{jobStats.matched_jobs}</strong>
              <span className="portal-stat-note">AI matched jobs</span>
            </article>
            <article className="portal-stat-card">
              <span className="portal-stat-label">Total jobs applied</span>
              <strong className="portal-stat-value">{jobStats.applied_jobs}</strong>
              <span className="portal-stat-note">Status set to applied</span>
            </article>
          </div>
        </div>
      </header>

      <TabNav activeTab={activeTab} onTabChange={handleTabChange} />

      {activeTab === "jobs" && (
        <>
          <section className="panel">
            <FilterBar
              filters={filters}
              profiles={profiles}
              onChange={(changes) => updateFilters(changes)}
              onClear={clearFilters}
            />
          </section>

          <section className="panel table-panel">
            <div className="table-meta">
              <strong>{showingText}</strong>
            </div>

            {error ? <p className="error-banner">{error}</p> : null}

            <JobsTable
              jobs={jobs}
              loading={loading}
              filters={filters}
              updatingJobIds={updatingJobIds}
              onSort={handleSort}
              onStatusChange={handleStatusChange}
              onOpenDescription={(jobId) => setSelectedJobId(jobId)}
            />

            <div className="pagination">
              <button
                type="button"
                onClick={() => handlePageChange(Math.max(1, filters.page - 1))}
                disabled={filters.page <= 1}
              >
                Previous
              </button>
              <span>
                Page {filters.page} / {totalPages}
              </span>
              <button
                type="button"
                onClick={() => handlePageChange(Math.min(totalPages, filters.page + 1))}
                disabled={filters.page >= totalPages}
              >
                Next
              </button>
            </div>
          </section>

          <JobModal
            aiReadiness={aiReadiness}
            jobId={selectedJobId}
            jobIds={jobIds}
            onClose={() => setSelectedJobId(null)}
            onPrevious={handlePreviousJob}
            onNext={handleNextJob}
            onStatusChange={handleStatusChange}
            onAskAi={handleOpenAskAiForJob}
            onCvRequired={handleCvRequired}
          />
        </>
      )}

      {activeTab === "ai" && (
        <section className="panel">
          <AskAiScreen
            aiReadiness={aiReadiness}
            onGoToSettings={handleGoToSettings}
            requestedJobId={requestedAskAiJobId}
            onRequestedJobHandled={() => setRequestedAskAiJobId(null)}
          />
        </section>
      )}

      {activeTab === "profiles" && (
        <section className="panel">
          <ProfilesScreen
            profiles={profiles}
            loading={profilesLoading}
            onRefresh={loadProfiles}
          />
        </section>
      )}

      {activeTab === "runs" && (
        <section className="panel">
          <RunWorkflowScreen />
        </section>
      )}

      {activeTab === "settings" && (
        <section className="panel">
          <SettingsScreen
            themePreference={themePreference}
            effectiveTheme={effectiveTheme}
            onThemePreferenceChange={setThemePreference}
            onAiReadinessChange={setAiReadiness}
          />
        </section>
      )}

      {showCvRequiredModal && (
        <CvRequiredModal
          onClose={() => setShowCvRequiredModal(false)}
          onGoToSettings={handleGoToSettings}
        />
      )}
    </div>
  );
}
