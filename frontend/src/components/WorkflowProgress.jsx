import { useState } from "react";
import "./WorkflowProgress.css";

function formatDuration(startedAt, finishedAt) {
  if (!startedAt) return "-";

  const start = new Date(startedAt);
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const diffMs = end - start;

  if (diffMs < 0) return "-";

  const seconds = Math.floor(diffMs / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
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
    default:
      return status;
  }
}

function extractErrorDetails(run) {
  const details = {
    errorMessage: null,
    fatalErrors: [],
    failedJobIds: [],
    steps: [],
    exitCode: run.exit_code,
    stderrLog: null,
  };

  // Extract error_message if set
  if (run.error_message) {
    details.errorMessage = run.error_message;
  }

  // Try to extract detailed info from stdout_log
  if (run.stdout_log) {
    try {
      const parsed = JSON.parse(run.stdout_log);
      if (parsed.fatal_errors && Array.isArray(parsed.fatal_errors)) {
        details.fatalErrors = parsed.fatal_errors;
      }
      if (parsed.failed_job_ids && Array.isArray(parsed.failed_job_ids)) {
        details.failedJobIds = parsed.failed_job_ids;
      }
      if (parsed.steps && Array.isArray(parsed.steps)) {
        details.steps = parsed.steps;
      }
    } catch {
      // Not JSON or parsing failed
    }
  }

  // Include stderr if available
  if (run.stderr_log) {
    details.stderrLog = run.stderr_log;
  }

  return details;
}

function buildErrorPrompt(run, errorDetails) {
  const lines = [
    "# Workflow Error Investigation",
    "",
    "## Context",
    `- **Profile:** ${run.profile_session_name || "Unknown"}`,
    `- **Run ID:** ${run.id}`,
    `- **Status:** ${run.status}`,
    `- **Exit Code:** ${errorDetails.exitCode ?? "N/A"}`,
    `- **Started:** ${run.started_at ? new Date(run.started_at).toISOString() : "N/A"}`,
    `- **Finished:** ${run.finished_at ? new Date(run.finished_at).toISOString() : "N/A"}`,
    `- **Duration:** ${formatDuration(run.started_at, run.finished_at)}`,
    "",
  ];

  // Results summary
  if (run.job_ids_found !== null || run.parsed_ok !== null || run.parsed_failed !== null) {
    lines.push("## Results Summary");
    lines.push(`- **Jobs Found:** ${run.job_ids_found ?? "N/A"}`);
    lines.push(`- **Matched Jobs:** ${run.matched_jobs_count ?? "N/A"}`);
    lines.push(`- **Parsed OK:** ${run.parsed_ok ?? "N/A"}`);
    lines.push(`- **Parse Failed:** ${run.parsed_failed ?? "N/A"}`);
    lines.push("");
  }

  // Fatal errors
  if (errorDetails.fatalErrors.length > 0) {
    lines.push("## Fatal Errors");
    errorDetails.fatalErrors.forEach((err, idx) => {
      lines.push(`${idx + 1}. ${err}`);
    });
    lines.push("");
  }

  // Failed job IDs
  if (errorDetails.failedJobIds.length > 0) {
    lines.push("## Failed Job IDs");
    lines.push("```");
    lines.push(errorDetails.failedJobIds.join(", "));
    lines.push("```");
    lines.push("");
  }

  // Step details
  if (errorDetails.steps.length > 0) {
    lines.push("## Step Details");
    errorDetails.steps.forEach((step) => {
      const statusIcon = step.success ? "[OK]" : "[FAILED]";
      lines.push(`### ${statusIcon} ${step.step}`);
      if (step.error) {
        lines.push("**Error:**");
        lines.push("```");
        lines.push(step.error);
        lines.push("```");
      }
      if (step.output) {
        lines.push("**Output:**");
        lines.push("```");
        lines.push(typeof step.output === "string" ? step.output : JSON.stringify(step.output, null, 2));
        lines.push("```");
      }
      lines.push("");
    });
  }

  // Error message
  if (errorDetails.errorMessage) {
    lines.push("## Error Message");
    lines.push("```");
    lines.push(errorDetails.errorMessage);
    lines.push("```");
    lines.push("");
  }

  // Stderr
  if (errorDetails.stderrLog) {
    lines.push("## Stderr Log");
    lines.push("```");
    lines.push(errorDetails.stderrLog);
    lines.push("```");
    lines.push("");
  }

  lines.push("---");
  lines.push("Please analyze this error and suggest possible causes and solutions.");

  return lines.join("\n");
}

export default function WorkflowProgress({ run, onCancel, cancelling }) {
  const [copied, setCopied] = useState(false);

  if (!run) return null;

  const isActive = run.status === "pending" || run.status === "running";
  const showCancel = isActive && onCancel;
  const errorDetails = extractErrorDetails(run);
  const hasError = run.status === "failed" || errorDetails.fatalErrors.length > 0 || errorDetails.errorMessage;

  async function handleCopyPrompt() {
    const prompt = buildErrorPrompt(run, errorDetails);
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement("textarea");
      textarea.value = prompt;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  return (
    <div className={`workflow-progress panel status-${run.status}`}>
      <div className="progress-header">
        <h3>
          {isActive ? "Workflow In Progress" : "Workflow Result"}
        </h3>
        <span className={`status-badge ${run.status}`}>
          {getStatusLabel(run.status)}
        </span>
      </div>

      <div className="progress-details">
        <div className="detail-row">
          <span className="detail-label">Profile:</span>
          <span className="detail-value">{run.profile_session_name || "-"}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Started:</span>
          <span className="detail-value">
            {run.started_at ? new Date(run.started_at).toLocaleString() : "-"}
          </span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Duration:</span>
          <span className="detail-value">
            {formatDuration(run.started_at, run.finished_at)}
          </span>
        </div>
        {run.finished_at && (
          <div className="detail-row">
            <span className="detail-label">Finished:</span>
            <span className="detail-value">
              {new Date(run.finished_at).toLocaleString()}
            </span>
          </div>
        )}
      </div>

      {(run.status === "completed" || run.status === "failed") && (
        <div className="progress-results">
          <h4>Results</h4>
          <div className="results-grid">
            <div className="result-item">
              <span className="result-label">Jobs Found</span>
              <span className="result-value">{run.job_ids_found ?? "-"}</span>
            </div>
            <div className="result-item">
              <span className="result-label">Matched Jobs</span>
              <span className="result-value success">{run.matched_jobs_count ?? "-"}</span>
            </div>
            <div className="result-item">
              <span className="result-label">Parsed OK</span>
              <span className="result-value success">{run.parsed_ok ?? "-"}</span>
            </div>
            <div className="result-item">
              <span className="result-label">Parse Failed</span>
              <span className="result-value error">{run.parsed_failed ?? "-"}</span>
            </div>
            <div className="result-item">
              <span className="result-label">Exit Code</span>
              <span className={`result-value ${run.exit_code === 0 ? "success" : "error"}`}>
                {run.exit_code ?? "-"}
              </span>
            </div>
          </div>
        </div>
      )}

      {hasError && (
        <div className="progress-error">
          <div className="error-header">
            <h4>Error Details</h4>
            <button
              type="button"
              className="btn-copy-prompt"
              onClick={handleCopyPrompt}
              title="Copy error details as markdown prompt for AI investigation"
            >
              {copied ? "Copied!" : "Copy Prompt"}
            </button>
          </div>

          {errorDetails.fatalErrors.length > 0 && (
            <div className="error-section">
              <span className="error-section-label">Fatal Errors:</span>
              <ul className="error-list">
                {errorDetails.fatalErrors.map((err, idx) => (
                  <li key={idx}>{err}</li>
                ))}
              </ul>
            </div>
          )}

          {errorDetails.failedJobIds.length > 0 && (
            <div className="error-section">
              <span className="error-section-label">Failed Job IDs ({errorDetails.failedJobIds.length}):</span>
              <code className="error-job-ids">{errorDetails.failedJobIds.join(", ")}</code>
            </div>
          )}

          {errorDetails.steps.filter(s => !s.success).length > 0 && (
            <div className="error-section">
              <span className="error-section-label">Failed Steps:</span>
              <div className="error-steps">
                {errorDetails.steps.filter(s => !s.success).map((step, idx) => (
                  <div key={idx} className="error-step">
                    <span className="step-name">{step.step}</span>
                    {step.error && <pre className="step-error">{step.error}</pre>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {errorDetails.errorMessage && (
            <div className="error-section">
              <span className="error-section-label">Error Message:</span>
              <pre className="error-message-pre">{errorDetails.errorMessage}</pre>
            </div>
          )}

          {errorDetails.stderrLog && (
            <div className="error-section">
              <span className="error-section-label">Stderr Log:</span>
              <pre className="error-stderr">{errorDetails.stderrLog}</pre>
            </div>
          )}
        </div>
      )}

      {isActive && (
        <div className="progress-indicator">
          <div className="spinner" />
          <span>
            {run.status === "pending" ? "Initializing..." : "Running workflow..."}
          </span>
        </div>
      )}

      {showCancel && (
        <div className="progress-actions">
          <button
            type="button"
            className="btn-cancel"
            onClick={onCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling..." : "Cancel Workflow"}
          </button>
        </div>
      )}
    </div>
  );
}
