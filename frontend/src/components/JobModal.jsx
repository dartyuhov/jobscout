import { useEffect, useState } from "react";

import { fetchJobDetails, generateCoverLetter, summarizeJob, analyzeJob } from "../api/jobs";
import { DEFAULT_AI_READINESS } from "../aiReadiness";
import { getOpenJobUrl } from "../utils/jobLinks";
import StatusDropdown from "./StatusDropdown";
import "./JobModal.css";

function openInNewTab(url) {
  if (!url) {
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function downloadTextFile(content, filename) {
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Section header keywords to detect inline (case-insensitive)
const SECTION_KEYWORDS = [
  "About The Company",
  "About The Role",
  "About The Position",
  "About The Job",
  "About The Team",
  "About Us",
  "Position Summary",
  "Job Summary",
  "Job Description",
  "Job Overview",
  "What You'll Do",
  "What You Will Do",
  "What We're Looking For",
  "Who You Are",
  "Your Role",
  "Your Responsibilities",
  "Your Impact",
  "The Role",
  "The Team",
  "Responsibilities",
  "Key Responsibilities",
  "Requirements",
  "Qualifications",
  "Required Skills",
  "Required Qualifications",
  "Required Experience",
  "Preferred Skills",
  "Preferred Qualifications",
  "Preferred Experience",
  "Minimum Qualifications",
  "Basic Qualifications",
  "Nice To Have",
  "Nice-To-Haves",
  "Must-Haves",
  "Skills",
  "Experience",
  "Education",
  "Benefits",
  "What We Offer",
  "What You'll Get",
  "Why Join Us",
  "Why Work With Us",
  "Perks",
  "Compensation",
  "Salary",
  "Our Mission",
  "Our Vision",
  "Our Values",
  "Our Culture",
  "How To Apply",
  "Key Outcomes",
  "Key Outcomes & Metrics",
];

function formatDescription(text) {
  if (!text) {
    return null;
  }

  // Normalize whitespace
  let normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();

  // Check if text already has line breaks
  const hasLineBreaks = normalized.includes("\n");

  if (!hasLineBreaks) {
    // Insert line breaks before section headers
    // Sort by length descending to match longer phrases first
    const sortedKeywords = [...SECTION_KEYWORDS].sort((a, b) => b.length - a.length);

    for (const keyword of sortedKeywords) {
      // Create regex that matches the keyword with word boundary, case-insensitive
      // But NOT at the very start of the string (we'll handle that separately)
      const escapedKeyword = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const regex = new RegExp(`(?<!^)\\s*(${escapedKeyword})\\s*`, "gi");
      normalized = normalized.replace(regex, "\n\n$1\n");
    }
  }

  // Detect inline bullet points and split them
  // Match patterns like "* Item text" or "• Item text" that appear inline (preceded by space or sentence end)
  // This handles cases where bullets are embedded in continuous text without line breaks
  normalized = normalized.replace(/([.!?])\s*\*\s+/g, "$1\n* ");
  normalized = normalized.replace(/([.!?])\s*•\s+/g, "$1\n• ");
  normalized = normalized.replace(/([.!?])\s*-\s+/g, "$1\n- ");

  // Also handle bullet points that come after a colon (common pattern: "Responsibilities: * Do this * Do that")
  normalized = normalized.replace(/:\s*\*\s+/g, ":\n* ");
  normalized = normalized.replace(/:\s*•\s+/g, ":\n• ");
  normalized = normalized.replace(/:\s*-\s+(?=[A-Z])/g, ":\n- ");

  // Handle consecutive bullet points separated by spaces (e.g., "* Item one * Item two * Item three")
  // This pattern matches "end of sentence/item * next item" - where sentence ends but no newline
  normalized = normalized.replace(/([a-z0-9)"])\s+\*\s+(?=[A-Z])/g, "$1\n* ");
  normalized = normalized.replace(/([a-z0-9)"])\s+•\s+(?=[A-Z])/g, "$1\n• ");

  // Split into segments
  const segments = normalized
    .split(/\n+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  if (segments.length === 0) {
    return null;
  }

  // Check if a segment is a section header
  function isSectionHeader(segment) {
    const lower = segment.toLowerCase();
    // Check exact match or match with trailing colon
    for (const keyword of SECTION_KEYWORDS) {
      const keywordLower = keyword.toLowerCase();
      if (lower === keywordLower || lower === keywordLower + ":") {
        return true;
      }
    }
    // Also check for short ALL CAPS text
    if (segment.length < 40 && segment === segment.toUpperCase() && /[A-Z]/.test(segment)) {
      return true;
    }
    // Short text ending with colon
    if (segment.length < 50 && segment.endsWith(":")) {
      return true;
    }
    return false;
  }

  // Build elements
  const elements = [];
  let currentList = [];

  function flushList() {
    if (currentList.length > 0) {
      elements.push(
        <ul key={`list-${elements.length}`} className="desc-list">
          {currentList.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      );
      currentList = [];
    }
  }

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];

    // Check if it's a bullet point
    const bulletMatch = segment.match(/^[\u2022\u2023\u25E6\u2043\u2219•\-\*]\s*(.+)/);
    if (bulletMatch) {
      currentList.push(bulletMatch[1]);
      continue;
    }

    // Flush any pending list
    flushList();

    // Check if it's a header
    if (isSectionHeader(segment)) {
      const headerText = segment.replace(/:$/, "");
      elements.push(
        <h5 key={`h-${i}`} className="desc-section-header">{headerText}</h5>
      );
    } else {
      elements.push(
        <p key={`p-${i}`} className="desc-paragraph">{segment}</p>
      );
    }
  }

  flushList();

  return <div className="formatted-description">{elements}</div>;
}

export default function JobModal({
  aiReadiness = DEFAULT_AI_READINESS,
  jobId,
  jobIds = [],
  onClose,
  onPrevious,
  onNext,
  onStatusChange,
  onCvRequired,
  onAskAi,
}) {
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [updating, setUpdating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [summary, setSummary] = useState(null);
  const [aboutExpanded, setAboutExpanded] = useState(false);
  const openJobUrl = getOpenJobUrl(job);

  // Navigation state
  const currentIndex = jobIds.indexOf(jobId);
  const hasPrevious = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < jobIds.length - 1;
  const positionText = jobIds.length > 0 && currentIndex >= 0
    ? `${currentIndex + 1} of ${jobIds.length}`
    : null;

  useEffect(() => {
    if (!jobId) {
      return undefined;
    }

    let canceled = false;
    setLoading(true);
    setError("");
    setSummary(null);
    setAboutExpanded(false);

    fetchJobDetails(jobId)
      .then((payload) => {
        if (!canceled) {
          setJob(payload);
          // Initialize summary from cached data if available
          if (payload.ai_summary) {
            setSummary(payload.ai_summary);
          }
        }
      })
      .catch((err) => {
        if (!canceled) {
          setError(err.message || "Could not load job details.");
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
  }, [jobId]);

  useEffect(() => {
    if (!jobId) {
      return undefined;
    }

    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowLeft" && onPrevious) {
        onPrevious();
      } else if (event.key === "ArrowRight" && onNext) {
        onNext();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [jobId, onClose, onPrevious, onNext]);

  async function handleStatusChange(status) {
    if (!job) {
      return;
    }

    setUpdating(true);
    setError("");
    try {
      await onStatusChange(job.id, status);
      setJob((previous) =>
        previous
          ? {
              ...previous,
              user_status: status,
            }
          : previous
      );
    } catch (err) {
      setError(err.message || "Could not update job status.");
    } finally {
      setUpdating(false);
    }
  }

  async function handleGenerateCoverLetter() {
    if (!job || !aiReadiness.ready) {
      return;
    }

    setGenerating(true);
    setError("");

    try {
      const result = await generateCoverLetter(job.id);
      downloadTextFile(result.cover_letter, result.filename);
    } catch (err) {
      // Check if CV is required
      if (err.message && err.message.includes("CV")) {
        if (onCvRequired) {
          onCvRequired();
        } else {
          setError("Please add your CV in the Settings tab first.");
        }
      } else {
        setError(err.message || "Could not generate cover letter.");
      }
    } finally {
      setGenerating(false);
    }
  }

  async function handleSummarize() {
    if (!job || !aiReadiness.ready) {
      return;
    }

    setSummarizing(true);
    setError("");

    try {
      const result = await summarizeJob(job.id);
      setSummary(result.summary);
      // Update local job state to include summary for future renders
      setJob((previous) =>
        previous
          ? {
              ...previous,
              ai_summary: result.summary,
            }
          : previous
      );
    } catch (err) {
      setError(err.message || "Could not summarize job description.");
    } finally {
      setSummarizing(false);
    }
  }

  async function handleAnalyze() {
    if (!job || !aiReadiness.ready) return;
    setAnalyzing(true);
    setError("");
    try {
      await analyzeJob(job.id);
      setJob(await fetchJobDetails(job.id));
    } catch (err) { setError(err.message || "Could not analyze job."); }
    finally { setAnalyzing(false); }
  }

  async function handleAppliedAndNext() {
    if (!job) {
      return;
    }

    try {
      await handleStatusChange("applied");
      if (hasNext && onNext) {
        onNext();
      } else {
        onClose();
      }
    } catch {
      // Error already handled in handleStatusChange
    }
  }

  async function handleSkipAndNext() {
    if (!job) {
      return;
    }

    try {
      await handleStatusChange("not_suitable");
      if (hasNext && onNext) {
        onNext();
      } else {
        onClose();
      }
    } catch {
      // Error already handled in handleStatusChange
    }
  }

  function handleAskAi() {
    if (!job || !onAskAi) {
      return;
    }

    onAskAi(job.id);
  }

  if (!jobId) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
        <div className="modal-floating-controls">
          <div className="modal-nav-controls modal-nav-controls-floating">
            <button
              type="button"
              className="nav-arrow nav-arrow-inline"
              onClick={onPrevious}
              aria-label="Previous job"
              disabled={!hasPrevious}
            >
              &#8249;
            </button>
            {positionText && <span className="position-indicator">{positionText}</span>}
            <button
              type="button"
              className="nav-arrow nav-arrow-inline"
              onClick={onNext}
              aria-label="Next job"
              disabled={!hasNext}
            >
              &#8250;
            </button>
          </div>

          <button
            type="button"
            className="close-modal close-modal-floating"
            onClick={onClose}
            aria-label="Close job details"
          >
            <span aria-hidden="true">&times;</span>
          </button>
        </div>

        {loading ? <p>Loading job details...</p> : null}
        {error ? <p className="error-banner">{error}</p> : null}

        {!loading && job ? (
          <div className="modal-content">
            <div className="modal-header-row">
              <div className="modal-header-left">
                <span className="modal-header-profile">{job.profile_session_name || "No profile"}</span>
              </div>
              <div className="modal-header-right" />
            </div>

            <section className="modal-hero">
              <div className="modal-hero-grid">
                <div className="modal-hero-main">
                  <p className="modal-kicker">LinkedIn Job</p>
                  <h2>{job.position_name || "Untitled position"}</h2>
                  <h3>{job.company_name || "Unknown company"}</h3>
                </div>

                <div className="modal-meta">
                  <p>
                    <strong>Job ID:</strong> {job.linkedin_job_id || "-"}
                  </p>
                  <p>
                    <strong>Location:</strong> {job.job_location || "-"}
                  </p>
                  <p>
                    <strong>Posted:</strong> {job.posted_date || "-"}
                  </p>
                  <p>
                    <strong>Easy apply:</strong> {job.easy_apply ? "Yes" : "No"}
                  </p>
                </div>
              </div>
            </section>

            <div className="modal-actions">
              <div className="modal-actions-group modal-actions-left">
                <button type="button" className="ask-ai-button" onClick={handleAnalyze}
                  disabled={analyzing || !aiReadiness.ready || !job.about_text}
                  aria-describedby={!aiReadiness.ready ? "job-ai-guidance" : undefined}>
                  {analyzing ? "Analyzing…" : "Analyze Job"}
                </button>
                <button
                  type="button"
                  className="apply-button"
                  onClick={() => openInNewTab(openJobUrl)}
                  disabled={!openJobUrl}
                >
                  Open Job
                </button>
                <button
                  type="button"
                  className="ask-ai-button"
                  onClick={handleAskAi}
                   disabled={!onAskAi || !aiReadiness.ready}
                   aria-describedby={!aiReadiness.ready ? "job-ai-guidance" : undefined}
                >
                  Ask AI
                </button>
                <button
                  type="button"
                  className="cover-letter-button"
                  onClick={handleGenerateCoverLetter}
                   disabled={generating || !aiReadiness.ready}
                   aria-describedby={!aiReadiness.ready ? "job-ai-guidance" : undefined}
                >
                  {generating ? "Generating..." : "Cover Letter"}
                </button>
              </div>

              <div className="modal-actions-group modal-actions-right">
                <StatusDropdown
                  value={job.user_status}
                  disabled={updating}
                  onChange={handleStatusChange}
                />
                <button
                  type="button"
                  className="applied-next-button"
                  onClick={handleAppliedAndNext}
                  disabled={updating || job.user_status === "applied"}
                >
                  {updating ? "Saving..." : "Applied"}
                </button>
                <button
                  type="button"
                  className="skip-button"
                  onClick={handleSkipAndNext}
                  disabled={updating || job.user_status === "not_suitable"}
                >
                  Skip
                </button>
              </div>
            </div>

            {!aiReadiness.ready && <p id="job-ai-guidance" role="status">{aiReadiness.guidance}</p>}
            {/* AI Summary Section */}
            {summary ? (
              <section className="summary-block">
                <h4>Quick Summary</h4>
                <div className="summary-grid">
                  <div className="summary-section">
                    <h5>Must Haves</h5>
                    {summary.must_haves && summary.must_haves.length > 0 ? (
                      <ul className="summary-list">
                        {summary.must_haves.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="summary-empty">None specified</p>
                    )}
                  </div>
                  <div className="summary-section">
                    <h5>Nice to Haves</h5>
                    {summary.nice_to_haves && summary.nice_to_haves.length > 0 ? (
                      <ul className="summary-list">
                        {summary.nice_to_haves.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="summary-empty">None specified</p>
                    )}
                  </div>
                </div>
                <div className="summary-meta">
                  <p>
                    <strong>Company:</strong>{" "}
                    {summary.company?.name || "Unknown"}
                    {summary.company?.industry ? ` (${summary.company.industry})` : ""}
                  </p>
                  <p>
                    <strong>Application Type:</strong>{" "}
                    <span className="app-type-badge">{summary.application_type || "other"}</span>
                  </p>
                </div>
              </section>
            ) : (
              <div className="summary-prompt">
                <button
                  type="button"
                  className="summarize-button"
                  onClick={handleSummarize}
                   disabled={summarizing || !job.about_text || !aiReadiness.ready}
                   aria-describedby={!aiReadiness.ready ? "job-ai-guidance" : undefined}
                >
                  {summarizing ? "Summarizing..." : "Summarize"}
                </button>
                {!job.about_text && (
                  <span className="summary-hint">No description to summarize</span>
                )}
              </div>
            )}

            {job.ai_reasoning ? (
              <section className="description-block reasoning-block">
                <h4>AI reasoning</h4>
                {formatDescription(job.ai_reasoning)}
              </section>
            ) : null}

            <section className="description-block">
              <button
                type="button"
                className="section-toggle"
                onClick={() => setAboutExpanded((previous) => !previous)}
                aria-expanded={aboutExpanded}
              >
                <span>About this role</span>
                <span className={`section-toggle-icon${aboutExpanded ? " expanded" : ""}`} aria-hidden="true">
                  &#8250;
                </span>
              </button>
              {aboutExpanded ? (
                formatDescription(job.about_text) || (
                  <p className="no-description">No description available.</p>
                )
              ) : (
                <p className="collapsed-hint">Collapsed by default. Expand to read the full job description.</p>
              )}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
