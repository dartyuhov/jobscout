import "./RunHistory.css";

function formatDate(dateStr) {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleString();
}

function getStatusLabel(status) {
  switch (status) {
    case "pending":
      return "Pending";
    case "running":
      return "Running";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    case "queued":
      return "Queued";
    case "interrupted":
      return "Interrupted";
    default:
      return status;
  }
}

function getTriggerSourceLabel(source) {
  switch (source) {
    case "scheduled":
      return "Scheduled";
    case "manual":
      return "Manual";
    default:
      return "Manual"; // Default to manual for backwards compatibility
  }
}

export default function RunHistory({ runs, loading, currentRunId, onViewRun }) {
  if (loading) {
    return (
      <div className="run-history panel">
        <h3>Run History</h3>
        <p className="history-loading">Loading...</p>
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="run-history panel">
        <h3>Run History</h3>
        <p className="history-empty">No workflow runs yet.</p>
      </div>
    );
  }

  return (
    <div className="run-history panel">
      <h3>Run History</h3>
      <div className="history-table-wrapper">
        <table className="history-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Profile</th>
              <th>Source</th>
              <th>Status</th>
              <th>Jobs Found</th>
              <th>Matched</th>
              <th>Parsed</th>
              <th>Started</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                className={run.id === currentRunId ? "current-row" : ""}
              >
                <td className="col-id">{run.id}</td>
                <td className="col-profile">{run.profile_session_name || "-"}</td>
                <td className="col-source">
                  <span className={`source-badge ${run.trigger_source || "manual"}`}>
                    {getTriggerSourceLabel(run.trigger_source)}
                  </span>
                </td>
                <td className="col-status">
                  <span className={`status-pill ${run.status}`}>
                    {getStatusLabel(run.status)}
                  </span>
                </td>
                <td className="col-jobs">{run.job_ids_found ?? "-"}</td>
                <td className="col-matched">{run.matched_jobs_count ?? "-"}</td>
                <td className="col-parsed">
                  {run.parsed_ok != null && run.parsed_failed != null
                    ? `${run.parsed_ok} / ${run.parsed_failed}`
                    : "-"}
                </td>
                <td className="col-started">{formatDate(run.started_at)}</td>
                <td className="col-actions">
                  <button
                    type="button"
                    className="btn-view"
                    onClick={() => onViewRun(run)}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
