import { useEffect, useMemo, useState } from "react";

import StatusDropdown from "./StatusDropdown";
import "./JobsTable.css";

const COLUMN_WIDTHS_STORAGE_KEY = "jobsPortal_columnWidths";

const COLUMN_CONFIGS = [
  {
    id: "jobId",
    label: "Job ID",
    sortColumn: "linkedin_job_id",
    defaultWidth: 136,
    minWidth: 116,
  },
  {
    id: "company",
    label: "Company",
    sortColumn: "company_name",
    defaultWidth: 160,
    minWidth: 136,
  },
  {
    id: "position",
    label: "Position",
    sortColumn: "position_name",
    defaultWidth: 200,
    minWidth: 160,
  },
  {
    id: "location",
    label: "Location",
    sortColumn: "job_location",
    defaultWidth: 170,
    minWidth: 136,
  },
  {
    id: "profile",
    label: "Profile",
    sortColumn: "profile_session_name",
    defaultWidth: 150,
    minWidth: 130,
  },
  {
    id: "date",
    label: "Date Posted",
    sortColumn: "posted_date",
    defaultWidth: 118,
    minWidth: 110,
  },
  {
    id: "description",
    label: "Description",
    defaultWidth: 100,
    minWidth: 92,
  },
  {
    id: "status",
    label: "Status",
    sortColumn: "user_status",
    defaultWidth: 150,
    minWidth: 132,
  },
  {
    id: "apply",
    label: "Apply",
    defaultWidth: 96,
    minWidth: 88,
  },
];

function getDefaultColumnWidths() {
  return Object.fromEntries(COLUMN_CONFIGS.map((column) => [column.id, column.defaultWidth]));
}

function loadColumnWidths() {
  const defaults = getDefaultColumnWidths();

  try {
    const stored = window.localStorage.getItem(COLUMN_WIDTHS_STORAGE_KEY);
    if (!stored) {
      return defaults;
    }

    const parsed = JSON.parse(stored);
    return COLUMN_CONFIGS.reduce((accumulator, column) => {
      const candidate = parsed?.[column.id];
      accumulator[column.id] =
        typeof candidate === "number" && Number.isFinite(candidate)
          ? Math.max(column.minWidth, candidate)
          : defaults[column.id];
      return accumulator;
    }, {});
  } catch {
    return defaults;
  }
}

function SortHeader({ label, column, filters, onSort }) {
  const isActive = filters.sort_by === column;

  return (
    <button
      type="button"
      className={`sort-button ${isActive ? "active" : ""}`}
      onClick={() => onSort(column)}
    >
      {label}
    </button>
  );
}

function openInNewTab(url) {
  if (!url) {
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function getHumanizedPostedDate(value) {
  if (!value) {
    return "-";
  }

  const parsedDate = new Date(value);
  if (Number.isNaN(parsedDate.getTime())) {
    return value;
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(parsedDate.getFullYear(), parsedDate.getMonth(), parsedDate.getDate());
  const diffInDays = Math.round((today.getTime() - target.getTime()) / 86400000);

  if (diffInDays <= 0) {
    return "today";
  }

  if (diffInDays === 1) {
    return "yesterday";
  }

  if (diffInDays < 7) {
    return `${diffInDays} days ago`;
  }

  if (diffInDays < 14) {
    return "week ago";
  }

  if (diffInDays < 30) {
    return `${Math.floor(diffInDays / 7)} weeks ago`;
  }

  if (diffInDays < 60) {
    return "month ago";
  }

  if (diffInDays < 365) {
    return `${Math.floor(diffInDays / 30)} months ago`;
  }

  const years = Math.floor(diffInDays / 365);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

export default function JobsTable({
  jobs,
  loading,
  filters,
  updatingJobIds,
  onSort,
  onStatusChange,
  onOpenDescription,
}) {
  const [columnWidths, setColumnWidths] = useState(loadColumnWidths);

  useEffect(() => {
    window.localStorage.setItem(COLUMN_WIDTHS_STORAGE_KEY, JSON.stringify(columnWidths));
  }, [columnWidths]);

  const totalTableWidth = useMemo(
    () =>
      COLUMN_CONFIGS.reduce(
        (total, column) => total + (columnWidths[column.id] || column.defaultWidth),
        0
      ),
    [columnWidths]
  );

  function handleOpenJob(jobId) {
    onOpenDescription(jobId);
  }

  function handleRowKeyDown(event, jobId) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleOpenJob(jobId);
    }
  }

  function stopRowClick(event) {
    event.stopPropagation();
  }

  function handleResizeStart(event, columnId) {
    event.preventDefault();
    event.stopPropagation();

    const column = COLUMN_CONFIGS.find((item) => item.id === columnId);
    if (!column) {
      return;
    }

    const startX = event.clientX;
    const startWidth = columnWidths[columnId] || column.defaultWidth;
    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;

    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    function handleMouseMove(moveEvent) {
      const nextWidth = Math.max(column.minWidth, startWidth + moveEvent.clientX - startX);
      setColumnWidths((previous) => ({ ...previous, [columnId]: nextWidth }));
    }

    function handleMouseUp() {
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    }

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  }

  function renderHeaderCell(column) {
    return (
      <th key={column.id} style={{ width: `${columnWidths[column.id]}px` }}>
        <div className="table-header-cell">
          {column.sortColumn ? (
            <SortHeader
              label={column.label}
              column={column.sortColumn}
              filters={filters}
              onSort={onSort}
            />
          ) : (
            <span className="header-label">{column.label}</span>
          )}
          <button
            type="button"
            className="column-resize-handle"
            onMouseDown={(event) => handleResizeStart(event, column.id)}
            aria-label={`Resize ${column.label} column`}
            tabIndex={-1}
          />
        </div>
      </th>
    );
  }

  return (
    <>
      <div className="desktop-table-wrap">
        <table className="jobs-table" style={{ minWidth: `${totalTableWidth}px` }}>
          <colgroup>
            {COLUMN_CONFIGS.map((column) => (
              <col key={column.id} style={{ width: `${columnWidths[column.id]}px` }} />
            ))}
          </colgroup>
          <thead>
            <tr>{COLUMN_CONFIGS.map((column) => renderHeaderCell(column))}</tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="empty-cell">
                  Loading jobs...
                </td>
              </tr>
            ) : jobs.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty-cell">
                  No jobs match the current filters.
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr
                  key={job.id}
                  className="job-row"
                  onClick={() => handleOpenJob(job.id)}
                  onKeyDown={(event) => handleRowKeyDown(event, job.id)}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open job details for ${job.position_name || "job"}`}
                >
                  <td className="mono">{job.linkedin_job_id || "-"}</td>
                  <td>{job.company_name || "-"}</td>
                  <td>{job.position_name || "-"}</td>
                  <td>{job.job_location || "-"}</td>
                  <td>{job.profile_session_name || "-"}</td>
                  <td title={job.posted_date || ""}>{getHumanizedPostedDate(job.posted_date)}</td>
                  <td onClick={stopRowClick}>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => handleOpenJob(job.id)}
                    >
                      View
                    </button>
                  </td>
                  <td onClick={stopRowClick}>
                    <StatusDropdown
                      value={job.user_status}
                      disabled={updatingJobIds.has(job.id)}
                      onChange={(status) => onStatusChange(job.id, status)}
                    />
                  </td>
                  <td onClick={stopRowClick}>
                    <button
                      type="button"
                      className="apply-button"
                      onClick={() => openInNewTab(job.apply_link)}
                      disabled={!job.apply_link}
                    >
                      Apply
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mobile-cards">
        {loading ? <p className="empty-cell">Loading jobs...</p> : null}
        {!loading && jobs.length === 0 ? (
          <p className="empty-cell">No jobs match the current filters.</p>
        ) : null}

        {!loading
          ? jobs.map((job) => (
              <article
                className="job-card"
                key={job.id}
                onClick={() => handleOpenJob(job.id)}
                onKeyDown={(event) => handleRowKeyDown(event, job.id)}
                tabIndex={0}
                role="button"
                aria-label={`Open job details for ${job.position_name || "job"}`}
              >
                <h3>{job.position_name || "Untitled position"}</h3>
                <p className="card-company">{job.company_name || "Unknown company"}</p>
                <p>
                  <strong>Job ID:</strong> {job.linkedin_job_id || "-"}
                </p>
                <p>
                  <strong>Profile:</strong> {job.profile_session_name || "-"}
                </p>
                <p>
                  <strong>Location:</strong> {job.job_location || "-"}
                </p>
                <p>
                  <strong>Date Posted:</strong>{" "}
                  <span title={job.posted_date || ""}>{getHumanizedPostedDate(job.posted_date)}</span>
                </p>
                <div className="card-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={(event) => {
                      stopRowClick(event);
                      handleOpenJob(job.id);
                    }}
                  >
                    View
                  </button>
                  <button
                    type="button"
                    className="apply-button"
                    onClick={(event) => {
                      stopRowClick(event);
                      openInNewTab(job.apply_link);
                    }}
                    disabled={!job.apply_link}
                  >
                    Apply
                  </button>
                </div>
                <div onClick={stopRowClick}>
                  <StatusDropdown
                    value={job.user_status}
                    disabled={updatingJobIds.has(job.id)}
                    onChange={(status) => onStatusChange(job.id, status)}
                  />
                </div>
              </article>
            ))
          : null}
      </div>
    </>
  );
}
