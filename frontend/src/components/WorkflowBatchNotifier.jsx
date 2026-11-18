import { useCallback, useEffect, useRef } from "react";

import {
  fetchNotificationSettings,
  fetchWorkflowBatchNotification,
  fetchWorkflowRuns,
} from "../api/jobs";

const POLL_INTERVAL_MS = 3000;
const ACTIVE_RUN_STATUSES = new Set(["pending", "running", "queued"]);
const NOTIFIED_BATCHES_STORAGE_KEY = "jobsPortal_notifiedWorkflowBatches";

function loadNotifiedBatchIds() {
  try {
    const stored = window.localStorage.getItem(NOTIFIED_BATCHES_STORAGE_KEY);
    if (!stored) {
      return new Set();
    }
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(parsed.filter((value) => typeof value === "string" && value.trim()));
  } catch {
    return new Set();
  }
}

function saveNotifiedBatchIds(batchIds) {
  try {
    window.localStorage.setItem(
      NOTIFIED_BATCHES_STORAGE_KEY,
      JSON.stringify([...batchIds])
    );
  } catch {
    return;
  }
}

function buildBatchNotificationMessage(summary) {
  const count = summary.ready_to_review_count ?? 0;
  const profileCount = summary.profile_session_names?.length ?? 0;
  const profileLabel = profileCount === 1 ? "profile" : "profiles";
  const jobLabel = count === 1 ? "job" : "jobs";
  return `${count} ${jobLabel} ready to review from ${profileCount} ${profileLabel}.`;
}

export default function WorkflowBatchNotifier() {
  const activeBatchIdsRef = useRef(new Set());
  const notifiedBatchIdsRef = useRef(loadNotifiedBatchIds());
  const notificationSessionStartedAtRef = useRef(new Date().toISOString());

  const requestPermission = useCallback(async () => {
    if (typeof window === "undefined" || !("Notification" in window)) {
      return "unsupported";
    }
    if (window.Notification.permission !== "default") {
      return window.Notification.permission;
    }
    try {
      return await window.Notification.requestPermission();
    } catch {
      return window.Notification.permission;
    }
  }, []);

  const markBatchAsNotified = useCallback((batchId) => {
    notifiedBatchIdsRef.current.add(batchId);
    saveNotifiedBatchIds(notifiedBatchIdsRef.current);
  }, []);

  const showBatchNotification = useCallback(
    async (summary) => {
      try {
        const settings = await fetchNotificationSettings();
        if (settings.browser_notifications_enabled === false) {
          return;
        }
      } catch (err) {
        console.error("Failed to load notification settings:", err);
        return;
      }

      if (typeof window === "undefined" || !("Notification" in window)) {
        return;
      }

      let permission = window.Notification.permission;
      if (permission === "default") {
        permission = await requestPermission();
      }
      if (permission !== "granted") {
        return;
      }

      const notification = new window.Notification("LinkedIn batch finished", {
        body: buildBatchNotificationMessage(summary),
        tag: `workflow-batch-${summary.batch_id}`,
      });
      notification.onclick = () => {
        window.focus();
        notification.close();
      };
    },
    [requestPermission]
  );

  const inspectRuns = useCallback(
    async (runs) => {
      const batchStatuses = new Map();

      for (const run of runs) {
        if (!run.batch_id) {
          continue;
        }
        if (!batchStatuses.has(run.batch_id)) {
          batchStatuses.set(run.batch_id, []);
        }
        batchStatuses.get(run.batch_id).push(run.status);
      }

      const nextActiveBatchIds = new Set(
        [...batchStatuses.entries()]
          .filter(([, statuses]) => statuses.some((status) => ACTIVE_RUN_STATUSES.has(status)))
          .map(([batchId]) => batchId)
      );

      const candidateBatchIds = new Set();
      for (const batchId of activeBatchIdsRef.current) {
        if (!nextActiveBatchIds.has(batchId) && !notifiedBatchIdsRef.current.has(batchId)) {
          candidateBatchIds.add(batchId);
        }
      }
      for (const [batchId, statuses] of batchStatuses.entries()) {
        if (
          !nextActiveBatchIds.has(batchId) &&
          !notifiedBatchIdsRef.current.has(batchId) &&
          statuses.length > 0
        ) {
          candidateBatchIds.add(batchId);
        }
      }

      activeBatchIdsRef.current = nextActiveBatchIds;

      for (const batchId of candidateBatchIds) {
        try {
          const summary = await fetchWorkflowBatchNotification(batchId);
          if (!summary?.is_complete) {
            continue;
          }
          if (
            summary.finished_at &&
            summary.finished_at < notificationSessionStartedAtRef.current
          ) {
            markBatchAsNotified(batchId);
            continue;
          }
          await showBatchNotification(summary);
          markBatchAsNotified(batchId);
        } catch (err) {
          console.error("Failed to inspect batch notification:", err);
        }
      }
    },
    [markBatchAsNotified, showBatchNotification]
  );

  useEffect(() => {
    requestPermission();
  }, [requestPermission]);

  useEffect(() => {
    let cancelled = false;

    async function pollRuns() {
      try {
        const result = await fetchWorkflowRuns({ per_page: 50 });
        if (!cancelled) {
          await inspectRuns(result.runs || []);
        }
      } catch (err) {
        console.error("Failed to poll workflow runs for notifications:", err);
      }
    }

    pollRuns();
    const interval = window.setInterval(pollRuns, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [inspectRuns]);

  return null;
}
