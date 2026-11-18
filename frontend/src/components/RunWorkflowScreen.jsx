import { useCallback, useEffect, useState, useRef } from "react";

import {
  cancelWorkflowRun,
  fetchSchedules,
  fetchProfiles,
  fetchWorkflowRunById,
  fetchWorkflowRuns,
  queueMultipleRuns,
  updateProfilesSchedule,
} from "../api/jobs";
import CronInput from "./CronInput";
import RunHistory from "./RunHistory";
import WorkflowProgress from "./WorkflowProgress";
import "./RunWorkflowScreen.css";

const POLL_INTERVAL_MS = 3000;

export default function RunWorkflowScreen() {
  const [profiles, setProfiles] = useState([]);
  const [runs, setRuns] = useState([]);
  const [currentRun, setCurrentRun] = useState(null);
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [loadingSchedules, setLoadingSchedules] = useState(true);
  const [queuingAll, setQueuingAll] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const [scheduleValue, setScheduleValue] = useState("");
  const [scheduledProfiles, setScheduledProfiles] = useState([]);
  const [showSchedulePanel, setShowSchedulePanel] = useState(false);
  const [browserNotificationPermission, setBrowserNotificationPermission] = useState(() => {
    if (typeof window === "undefined" || !("Notification" in window)) {
      return "unsupported";
    }
    return window.Notification.permission;
  });

  // Profile selection for batch run feature
  const [selectedProfileIds, setSelectedProfileIds] = useState(new Set());
  const [orderedProfileIds, setOrderedProfileIds] = useState([]);

  // Drag and drop state
  const [draggedId, setDraggedId] = useState(null);
  const dragOverId = useRef(null);

  const loadProfiles = useCallback(async () => {
    try {
      const data = await fetchProfiles();
      setProfiles(data);
      setOrderedProfileIds((previous) => {
        if (!previous.length) {
          return data.map((profile) => profile.id);
        }

        const availableIds = new Set(data.map((profile) => profile.id));
        const preserved = previous.filter((id) => availableIds.has(id));
        const missing = data.map((profile) => profile.id).filter((id) => !preserved.includes(id));
        return [...preserved, ...missing];
      });
      setSelectedProfileIds((previous) => {
        if (previous.size === 0) {
          return new Set(data.map((profile) => profile.id));
        }

        const next = new Set();
        for (const profile of data) {
          if (previous.has(profile.id)) {
            next.add(profile.id);
          }
        }
        return next;
      });
    } catch (err) {
      setError(err.message || "Failed to load profiles.");
    } finally {
      setLoadingProfiles(false);
    }
  }, []);

  const loadSchedules = useCallback(async () => {
    try {
      const data = await fetchSchedules();
      setScheduledProfiles(data);
    } catch (err) {
      setError(err.message || "Failed to load schedules.");
    } finally {
      setLoadingSchedules(false);
    }
  }, []);

  const requestBrowserNotificationPermission = useCallback(async () => {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setBrowserNotificationPermission("unsupported");
      return "unsupported";
    }

    if (window.Notification.permission !== "default") {
      setBrowserNotificationPermission(window.Notification.permission);
      return window.Notification.permission;
    }

    try {
      const permission = await window.Notification.requestPermission();
      setBrowserNotificationPermission(permission);
      return permission;
    } catch {
      setBrowserNotificationPermission(window.Notification.permission);
      return window.Notification.permission;
    }
  }, []);

  useEffect(() => {
    loadProfiles();
    loadSchedules();
  }, [loadProfiles, loadSchedules]);

  useEffect(() => {
    if (browserNotificationPermission === "default") {
      requestBrowserNotificationPermission();
    }
  }, [browserNotificationPermission, requestBrowserNotificationPermission]);

  // Load runs on mount and after actions
  const loadRuns = useCallback(async () => {
    try {
      const result = await fetchWorkflowRuns({ per_page: 50 });
      const nextRuns = result.runs || [];
      setRuns(nextRuns);

      // Find if there's an active run
      const activeRun = nextRuns.find(
        (r) => r.status === "pending" || r.status === "running"
      );
      if (activeRun) {
        setCurrentRun(activeRun);
      } else if (
        currentRun &&
        currentRun.status !== "completed" &&
        currentRun.status !== "failed" &&
        currentRun.status !== "cancelled" &&
        currentRun.status !== "interrupted"
      ) {
        // Current run may have finished, clear it
        setCurrentRun(null);
      }
    } catch (err) {
      console.error("Failed to load runs:", err);
    } finally {
      setLoadingRuns(false);
    }
  }, [currentRun]);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  // Poll for updates when there's an active run or queued runs
  useEffect(() => {
    const hasActiveRun =
      currentRun &&
      (currentRun.status === "pending" || currentRun.status === "running");
    const hasQueuedRuns = runs.some((r) => r.status === "queued");

    if (!hasActiveRun && !hasQueuedRuns) {
      return;
    }

    const interval = setInterval(async () => {
      try {
        if (currentRun) {
          const updatedRun = await fetchWorkflowRunById(currentRun.id);
          setCurrentRun(updatedRun);

          // Refresh runs list if status changed
          if (updatedRun.status !== currentRun.status) {
            loadRuns();
          }
        } else {
          // No current run but we have queued runs, refresh to pick up new active run
          loadRuns();
        }
      } catch (err) {
        console.error("Failed to poll run status:", err);
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [currentRun, runs, loadRuns]);

  async function handleRunSelected() {
    // Get selected profiles in the user-defined order
    const idsToRun = orderedProfileIds.filter((id) =>
      selectedProfileIds.has(id)
    );

    if (idsToRun.length === 0) {
      setError("Please select at least one profile.");
      return;
    }

    setQueuingAll(true);
    setError("");

    try {
      const createdRuns = await queueMultipleRuns(idsToRun);
      // Set the first run as current if it's pending/running
      if (createdRuns.length > 0) {
        const firstRun = createdRuns[0];
        if (firstRun.status === "pending" || firstRun.status === "running") {
          setCurrentRun(firstRun);
        }
      }
      loadRuns();
    } catch (err) {
      setError(err.message || "Failed to queue workflows.");
    } finally {
      setQueuingAll(false);
    }
  }

  async function handleSaveSchedule() {
    const idsToSchedule = orderedProfileIds.filter((id) => selectedProfileIds.has(id));

    if (idsToSchedule.length === 0) {
      setError("Please select at least one profile to schedule.");
      return;
    }

    if (!scheduleValue.trim()) {
      setError("Please choose a schedule before saving.");
      return;
    }

    setSavingSchedule(true);
    setError("");

    try {
      await updateProfilesSchedule(idsToSchedule, scheduleValue.trim());
      await Promise.all([loadProfiles(), loadSchedules()]);
    } catch (err) {
      setError(err.message || "Failed to save schedules.");
    } finally {
      setSavingSchedule(false);
    }
  }

  async function handleClearSchedule() {
    const idsToSchedule = orderedProfileIds.filter((id) => selectedProfileIds.has(id));

    if (idsToSchedule.length === 0) {
      setError("Please select at least one profile to unschedule.");
      return;
    }

    setSavingSchedule(true);
    setError("");

    try {
      await updateProfilesSchedule(idsToSchedule, "");
      await Promise.all([loadProfiles(), loadSchedules()]);
    } catch (err) {
      setError(err.message || "Failed to clear schedules.");
    } finally {
      setSavingSchedule(false);
    }
  }

  async function handleCancel() {
    if (!currentRun) {
      return;
    }

    setCancelling(true);
    setError("");

    try {
      const updatedRun = await cancelWorkflowRun(currentRun.id);
      setCurrentRun(updatedRun);
      loadRuns();
    } catch (err) {
      setError(err.message || "Failed to cancel workflow.");
    } finally {
      setCancelling(false);
    }
  }

  function handleViewRun(run) {
    setCurrentRun(run);
  }

  function handleSelectAll(checked) {
    if (checked) {
      setSelectedProfileIds(new Set(profiles.map((p) => p.id)));
    } else {
      setSelectedProfileIds(new Set());
    }
  }

  function handleToggleProfile(id) {
    const newSet = new Set(selectedProfileIds);
    if (newSet.has(id)) {
      newSet.delete(id);
    } else {
      newSet.add(id);
    }
    setSelectedProfileIds(newSet);
  }

  // Drag and drop handlers
  function handleDragStart(e, id) {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = "move";
  }

  function handleDragOver(e, id) {
    e.preventDefault();
    dragOverId.current = id;
  }

  function handleDragEnd() {
    if (draggedId !== null && dragOverId.current !== null && draggedId !== dragOverId.current) {
      const newOrder = [...orderedProfileIds];
      const draggedIndex = newOrder.indexOf(draggedId);
      const dropIndex = newOrder.indexOf(dragOverId.current);

      // Remove dragged item
      newOrder.splice(draggedIndex, 1);
      // Insert at new position
      newOrder.splice(dropIndex, 0, draggedId);

      setOrderedProfileIds(newOrder);
    }
    setDraggedId(null);
    dragOverId.current = null;
  }

  const isRunning =
    currentRun &&
    (currentRun.status === "pending" || currentRun.status === "running");
  const hasQueuedRuns = runs.some((r) => r.status === "queued");
  const queuedCount = runs.filter((r) => r.status === "queued").length;

  // Get profiles in user-defined order for display
  const orderedProfiles = orderedProfileIds
    .map((id) => profiles.find((p) => p.id === id))
    .filter(Boolean);

  const selectedScheduledCount = orderedProfiles.filter(
    (profile) => selectedProfileIds.has(profile.id) && profile.cron
  ).length;

  return (
    <div className="run-workflow-screen">
      <div className="run-workflow-header">
        <div>
          <h2>Run Workflow</h2>
          <p className="run-workflow-subtitle">
            Start a LinkedIn full-flow workflow to collect and analyze job
            listings.
          </p>
        </div>
      </div>

      {error && <p className="run-workflow-error">{error}</p>}

      <div className="run-workflow-controls panel">
        <h3>Start New Run</h3>

        {browserNotificationPermission !== "granted" && (
          <div className="control-hint">
            Browser notifications are {browserNotificationPermission === "unsupported" ? "not supported in this browser" : browserNotificationPermission === "denied" ? "blocked" : "not enabled yet"}.
            {" "}
            {browserNotificationPermission !== "unsupported" && (
              <button
                type="button"
                className="btn-toggle-schedule"
                onClick={requestBrowserNotificationPermission}
              >
                Enable notifications
              </button>
            )}
          </div>
        )}

        {/* Batch run section */}
        <div className="batch-run-section">
          <p className="batch-run-hint">
            Select profiles and drag to reorder. Workflows will run
            sequentially in the order shown.
          </p>

          {/* Profile selection list */}
          <div className="profile-selection">
            <div className="profile-selection-header">
              <label className="select-all-label">
                <input
                  type="checkbox"
                  checked={
                    selectedProfileIds.size === profiles.length &&
                    profiles.length > 0
                  }
                  onChange={(e) => handleSelectAll(e.target.checked)}
                  disabled={loadingProfiles || queuingAll}
                />
                Select All ({selectedProfileIds.size}/{profiles.length})
              </label>
            </div>
            <div className="profile-list">
              {orderedProfiles.map((p, index) => (
                <div
                  key={p.id}
                  className={`profile-item ${draggedId === p.id ? "dragging" : ""}`}
                  draggable
                  onDragStart={(e) => handleDragStart(e, p.id)}
                  onDragOver={(e) => handleDragOver(e, p.id)}
                  onDragEnd={handleDragEnd}
                >
                  <span className="drag-handle">⋮⋮</span>
                  <span className="profile-order">{index + 1}</span>
                  <label className="profile-label">
                    <input
                      type="checkbox"
                      checked={selectedProfileIds.has(p.id)}
                      onChange={() => handleToggleProfile(p.id)}
                      disabled={loadingProfiles || queuingAll}
                   />
                    <span>{p.sessionName}</span>
                    {p.cron ? (
                      <span className="profile-schedule-chip" title={p.cron}>
                        Scheduled
                      </span>
                    ) : null}
                  </label>
                  {p.nextRun ? (
                    <span className="profile-next-run">
                      Next: {new Date(p.nextRun).toLocaleString()}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          <div className="schedule-section">
            <div className="schedule-section-header">
              <div>
                <h4>Schedule Selected Profiles</h4>
                <p className="batch-run-hint">
                  Save one cron schedule for the currently selected profiles. This replaces
                  profile-level scheduling from the edit form.
                </p>
              </div>
              <div className="schedule-section-actions">
                <span className="schedule-summary-chip">
                  {selectedProfileIds.size} selected, {selectedScheduledCount} already scheduled
                </span>
                <button
                  type="button"
                  className="btn-toggle-schedule"
                  onClick={() => setShowSchedulePanel((previous) => !previous)}
                >
                  {showSchedulePanel ? "Hide scheduling" : "Show scheduling"}
                </button>
              </div>
            </div>

            {showSchedulePanel ? (
              <>
                <CronInput
                  value={scheduleValue}
                  onChange={setScheduleValue}
                  disabled={loadingProfiles || savingSchedule}
                />

                <div className="batch-run-buttons">
                  <button
                    type="button"
                    className="btn-run-all"
                    onClick={handleSaveSchedule}
                    disabled={loadingProfiles || savingSchedule || selectedProfileIds.size === 0}
                  >
                    {savingSchedule ? "Saving..." : `Save Schedule for Selected (${selectedProfileIds.size})`}
                  </button>
                  <button
                    type="button"
                    className="btn-clear-schedule"
                    onClick={handleClearSchedule}
                    disabled={loadingProfiles || savingSchedule || selectedProfileIds.size === 0}
                  >
                    Clear Schedule for Selected
                  </button>
                </div>

                <div className="scheduled-profiles-summary">
                  <strong>Active schedules</strong>
                  {loadingSchedules ? (
                    <p className="control-hint">Loading schedules...</p>
                  ) : scheduledProfiles.length > 0 ? (
                    <div className="scheduled-profiles-list">
                      {scheduledProfiles.map((schedule) => (
                        <div key={schedule.profileId} className="scheduled-profile-card">
                          <span className="scheduled-profile-name">{schedule.sessionName}</span>
                          <code>{schedule.cron}</code>
                          <span className="scheduled-profile-next-run">
                            Next: {schedule.nextRun ? new Date(schedule.nextRun).toLocaleString() : "-"}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="control-hint">No profiles are scheduled yet.</p>
                  )}
                </div>
              </>
            ) : null}
          </div>

          {/* Batch run buttons */}
          <div className="batch-run-buttons">
            <button
              type="button"
              className="btn-run-selected"
              onClick={handleRunSelected}
              disabled={
                selectedProfileIds.size === 0 ||
                loadingProfiles ||
                queuingAll
              }
            >
              {queuingAll
                ? "Queuing..."
                : `Run Selected (${selectedProfileIds.size})`}
            </button>
          </div>
        </div>

        {/* Status hints */}
        {isRunning && (
          <p className="control-hint">
            A workflow is currently running.
            {hasQueuedRuns && ` ${queuedCount} more queued.`}
          </p>
        )}
        {!isRunning && hasQueuedRuns && (
          <p className="control-hint">
            {queuedCount} workflow(s) queued and waiting to start.
          </p>
        )}
      </div>

      {currentRun && (
        <WorkflowProgress
          run={currentRun}
          onCancel={handleCancel}
          cancelling={cancelling}
        />
      )}

      <RunHistory
        runs={runs}
        loading={loadingRuns}
        currentRunId={currentRun?.id}
        onViewRun={handleViewRun}
      />
    </div>
  );
}
