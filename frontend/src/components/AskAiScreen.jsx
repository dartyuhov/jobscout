import { useEffect, useRef, useState } from "react";

import { askAi, fetchCvText, fetchJobDetails, fetchJobs } from "../api/jobs";
import { DEFAULT_AI_READINESS } from "../aiReadiness";
import "./AskAiScreen.css";

const MODE_LABELS = {
  general: "General",
  application_question: "Application Answer",
  job: "Job Analysis",
  cv: "CV Review",
};

function createMessage(role, content) {
  const randomId =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  return {
    id: randomId,
    role,
    content,
  };
}

function buildModeDraft(mode, selectedJob) {
  if (mode === "application_question") {
    return [
      `Help me answer this custom application question based on my CV${
        selectedJob ? " and the selected job" : ""
      }.`,
      "",
      "Question:",
    ].join("\n");
  }

  if (mode === "job") {
    const roleText = selectedJob
      ? `${selectedJob.position_name || "this role"} at ${selectedJob.company_name || "this company"}`
      : "the selected job";
    return `Explain ${roleText} for me. What are the most important requirements and what gaps or risks should an applicant watch for?`;
  }

  if (mode === "cv") {
    return "Review my CV and summarize my strongest selling points, likely interview talking points, and any gaps I should explain better.";
  }

  return "";
}

export default function AskAiScreen({
  aiReadiness = DEFAULT_AI_READINESS,
  onGoToSettings,
  requestedJobId = null,
  onRequestedJobHandled,
}) {
  const [cvText, setCvText] = useState("");
  const [cvLoading, setCvLoading] = useState(true);
  const [selectedJob, setSelectedJob] = useState(null);
  const [jobSearch, setJobSearch] = useState("");
  const [jobResults, setJobResults] = useState([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [selectingJobId, setSelectingJobId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("general");
  const [includeCv, setIncludeCv] = useState(false);
  const [error, setError] = useState("");
  const [jobError, setJobError] = useState("");
  const [sending, setSending] = useState(false);
  const composerRef = useRef(null);
  const transcriptEndRef = useRef(null);

  const hasCv = Boolean(cvText.trim());
  const needsCv = includeCv || mode === "cv" || mode === "application_question";

  useEffect(() => {
    let canceled = false;
    setCvLoading(true);

    fetchCvText()
      .then((text) => {
        if (!canceled) {
          setCvText(text || "");
        }
      })
      .catch((err) => {
        if (!canceled) {
          setError(err.message || "Could not load CV context.");
        }
      })
      .finally(() => {
        if (!canceled) {
          setCvLoading(false);
        }
      });

    return () => {
      canceled = true;
    };
  }, []);

  useEffect(() => {
    let canceled = false;
    const timeoutId = window.setTimeout(() => {
      setLoadingJobs(true);
      setJobError("");

      fetchJobs({
        search: jobSearch.trim(),
        match_only: false,
        page: 1,
        per_page: 8,
        sort_by: "posted_date",
        sort_order: "desc",
      })
        .then((payload) => {
          if (!canceled) {
            setJobResults(payload.jobs || []);
          }
        })
        .catch((err) => {
          if (!canceled) {
            setJobError(err.message || "Could not load jobs.");
          }
        })
        .finally(() => {
          if (!canceled) {
            setLoadingJobs(false);
          }
        });
    }, 250);

    return () => {
      canceled = true;
      window.clearTimeout(timeoutId);
    };
  }, [jobSearch]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  function confirmConversationReset() {
    if (messages.length === 0 && !input.trim()) {
      return true;
    }

    return window.confirm(
      "Changing the attached job will clear the current Ask AI conversation. Continue?"
    );
  }

  async function handleSelectJob(jobId, { skipConfirm = false, nextMode = null } = {}) {
    if (selectingJobId || selectedJob?.id === jobId) {
      return;
    }

    if (!skipConfirm && !confirmConversationReset()) {
      return;
    }

    setSelectingJobId(jobId);
    setJobError("");

    try {
      const job = await fetchJobDetails(jobId);
      setSelectedJob(job);
      setMessages([]);
      setInput("");
      if (nextMode) {
        setMode(nextMode);
        setInput(buildModeDraft(nextMode, job));
      } else if (mode === "job") {
        setInput(buildModeDraft("job", job));
      }
    } catch (err) {
      setJobError(err.message || "Could not load the selected job.");
    } finally {
      setSelectingJobId(null);
    }
  }

  useEffect(() => {
    if (requestedJobId === null || requestedJobId === undefined) {
      return;
    }

    handleSelectJob(requestedJobId, {
      skipConfirm: true,
      nextMode: "job",
    }).finally(() => {
      onRequestedJobHandled?.();
    });
  }, [requestedJobId]);

  function handleClearJob() {
    if (!selectedJob) {
      return;
    }

    if (!confirmConversationReset()) {
      return;
    }

    setSelectedJob(null);
    setMessages([]);
    setInput(mode === "job" ? "" : input);
    if (mode === "job") {
      setMode("general");
    }
  }

  function handleQuickAction(nextMode) {
    if (nextMode === "job" && !selectedJob) {
      setError("Select a job first to ask about a specific role.");
      return;
    }

    if (needsCv && !["cv", "application_question"].includes(nextMode)) {
      setMessages([]);
      setInput("");
    }
    setError("");
    setMode(nextMode);
    setIncludeCv(nextMode === "cv" || nextMode === "application_question");
    const nextDraft = buildModeDraft(nextMode, selectedJob);
    if (nextDraft) {
      setInput(nextDraft);
    }
    composerRef.current?.focus();
  }

  function handleClearConversation() {
    setMessages([]);
    setInput("");
    setError("");
    setMode("general");
    setIncludeCv(false);
  }

  async function handleSend() {
    const trimmed = input.trim();
    if (!aiReadiness.ready || !trimmed || sending || (needsCv && (cvLoading || !hasCv))) {
      return;
    }

    const nextUserMessage = createMessage("user", trimmed);
    const nextMessages = [...messages, nextUserMessage];
    setMessages(nextMessages);
    setInput("");
    setError("");
    setSending(true);

    try {
      const payload = await askAi({
        messages: nextMessages.map((message) => ({
          role: message.role,
          content: message.content,
        })),
        jobId: selectedJob?.id ?? null,
        mode,
        includeCv: needsCv,
      });

      setMessages((previous) => [...previous, createMessage("assistant", payload.reply || "")]);
    } catch (err) {
      setError(err.message || "Ask AI request failed.");
    } finally {
      setSending(false);
    }
  }

  function handleComposerKeyDown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="ask-ai-screen">
      <div className="ask-ai-header">
        <div>
          <h2>Ask AI</h2>
          <p className="ask-ai-subtitle">
            Generate answers through your configured AI connection. Attach a job or include your
            CV when needed. Ask AI generates text only; it cannot run commands or change app data.
          </p>
        </div>
      </div>

      {error ? <p className="error-banner">{error}</p> : null}
      {!aiReadiness.ready && (
        <section aria-label="AI setup">
          <p id="ask-ai-guidance" role="status">{aiReadiness.guidance}</p>
          <button type="button" onClick={onGoToSettings}>Open Settings</button>
        </section>
      )}

      <div className="ask-ai-layout">
        <aside className="ask-ai-sidebar">
          <section className="ask-ai-card ask-ai-context-card">
            <div className="ask-ai-card-header">
              <h3>CV Context</h3>
              <span className={`ask-ai-status-pill${hasCv ? " is-ready" : " is-missing"}`}>
                {cvLoading ? "Loading..." : hasCv ? "Ready" : "Missing"}
              </span>
            </div>

            <p className="ask-ai-card-copy">
              {cvLoading
                ? "Loading your saved CV/resume text from settings."
                : hasCv
                  ? `Your saved CV has ${cvText.length.toLocaleString()} characters. It is shared only when you include it or choose a CV-based quick action.`
                  : "General chat and job questions work without a CV. Add one in Settings for CV-based answers."}
            </p>
            <label>
              <input
                type="checkbox"
                checked={needsCv}
                onChange={(event) => {
                  setMessages([]);
                  setInput("");
                  setIncludeCv(event.target.checked);
                  if (!event.target.checked) setMode("general");
                }}
                disabled={sending || cvLoading || !hasCv}
              />
              Include my CV
            </label>

            {!cvLoading && !hasCv ? (
              <button
                type="button"
                className="ask-ai-secondary-button"
                onClick={onGoToSettings}
              >
                Open Settings
              </button>
            ) : null}
          </section>

          <section className="ask-ai-card ask-ai-context-card">
            <div className="ask-ai-card-header ask-ai-card-header-align-top">
              <div>
                <h3>Attached Job</h3>
                <p className="ask-ai-card-copy ask-ai-card-copy-tight">
                  Search by role, company, or LinkedIn job id.
                </p>
              </div>
              {selectedJob ? (
                <button
                  type="button"
                  className="ask-ai-text-button"
                  onClick={handleClearJob}
                  disabled={sending}
                >
                  Clear
                </button>
              ) : null}
            </div>

            {selectedJob ? (
              <div className="ask-ai-selected-job">
                <strong>{selectedJob.position_name || "Untitled position"}</strong>
                <span>{selectedJob.company_name || "Unknown company"}</span>
                <span>
                  {selectedJob.job_location || "Unknown location"}
                  {selectedJob.posted_date ? ` • ${selectedJob.posted_date}` : ""}
                </span>
              </div>
            ) : (
              <p className="ask-ai-card-copy ask-ai-card-copy-tight">
                No job attached. General chat still works.
              </p>
            )}

            <input
              type="text"
              className="ask-ai-job-search"
              value={jobSearch}
              onChange={(event) => setJobSearch(event.target.value)}
              placeholder="Search jobs..."
            />

            {jobError ? <p className="ask-ai-inline-error">{jobError}</p> : null}

            <div className="ask-ai-job-results" role="list">
              {loadingJobs ? (
                <p className="ask-ai-muted-text">Loading jobs...</p>
              ) : jobResults.length === 0 ? (
                <p className="ask-ai-muted-text">No jobs found for the current search.</p>
              ) : (
                jobResults.map((job) => {
                  const isSelected = selectedJob?.id === job.id;
                  const isBusy = selectingJobId === job.id;
                  return (
                    <button
                      key={job.id}
                      type="button"
                      className={`ask-ai-job-result${isSelected ? " is-selected" : ""}`}
                      onClick={() => handleSelectJob(job.id)}
                      disabled={isBusy || sending}
                    >
                      <strong>{job.position_name || "Untitled position"}</strong>
                      <span>{job.company_name || "Unknown company"}</span>
                      <span>
                        {job.linkedin_job_id || "No LinkedIn id"}
                        {job.posted_date ? ` • ${job.posted_date}` : ""}
                      </span>
                      {isBusy ? <span className="ask-ai-job-result-note">Loading...</span> : null}
                    </button>
                  );
                })
              )}
            </div>
          </section>

          <section className="ask-ai-card ask-ai-context-card">
            <div className="ask-ai-card-header">
              <h3>Quick Actions</h3>
            </div>
            <div className="ask-ai-quick-actions">
              <button
                type="button"
                className="ask-ai-secondary-button"
                onClick={() => handleQuickAction("application_question")}
                 disabled={!aiReadiness.ready || !hasCv || sending}
              >
                Answer form question
              </button>
              <button
                type="button"
                className="ask-ai-secondary-button"
                onClick={() => handleQuickAction("job")}
                 disabled={!aiReadiness.ready || !selectedJob || sending}
              >
                Ask about selected job
              </button>
              <button
                type="button"
                className="ask-ai-secondary-button"
                onClick={() => handleQuickAction("cv")}
                 disabled={!aiReadiness.ready || !hasCv || sending}
              >
                Ask about my CV
              </button>
            </div>
          </section>
        </aside>

        <section className="ask-ai-card ask-ai-chat-card">
          <div className="ask-ai-chat-header">
            <div>
              <h3>Conversation</h3>
              <p className="ask-ai-muted-text">
                Mode: <strong>{MODE_LABELS[mode]}</strong>
              </p>
            </div>

            <button
              type="button"
              className="ask-ai-secondary-button"
              onClick={handleClearConversation}
              disabled={sending || (messages.length === 0 && !input.trim())}
            >
              Clear chat
            </button>
          </div>

          <div className="ask-ai-active-context">
            <span className="ask-ai-context-pill">{needsCv ? "CV included" : "CV not included"}</span>
            {selectedJob ? (
              <span className="ask-ai-context-pill is-job">
                Job: {selectedJob.position_name || "Untitled position"}
              </span>
            ) : null}
          </div>

          <div className="ask-ai-transcript">
            {messages.length === 0 ? (
              <div className="ask-ai-empty-state">
                <h4>Start with one of these prompts</h4>
                <ul>
                  <li>Help me answer this custom application question.</li>
                  <li>What are the biggest requirements in this job, and how well do I fit them?</li>
                  <li>What are the strongest parts of my CV for jobs like this?</li>
                </ul>
              </div>
            ) : (
              messages.map((message) => (
                <article
                  key={message.id}
                  className={`ask-ai-message ${message.role === "user" ? "is-user" : "is-assistant"}`}
                >
                  <span className="ask-ai-message-role">
                    {message.role === "user" ? "You" : "Ask AI"}
                  </span>
                  <p>{message.content}</p>
                </article>
              ))
            )}

            {sending ? (
              <article className="ask-ai-message is-assistant is-pending">
                <span className="ask-ai-message-role">Ask AI</span>
                <p>Thinking...</p>
              </article>
            ) : null}

            <div ref={transcriptEndRef} />
          </div>

          <div className="ask-ai-composer">
            <textarea
              ref={composerRef}
              className="ask-ai-textarea"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="Ask about your CV, a selected job, or paste a custom application question..."
              rows={6}
               aria-label="Your message"
               disabled={!aiReadiness.ready || sending || (needsCv && (cvLoading || !hasCv))}
               aria-describedby={!aiReadiness.ready ? "ask-ai-guidance" : undefined}
            />

            <div className="ask-ai-composer-footer">
              <span className="ask-ai-muted-text">
                Your message, recent conversation, and included context are sent to your AI provider.
                Press Cmd/Ctrl + Enter to send.
              </span>

              <div className="ask-ai-composer-actions">
                <button
                  type="button"
                  className="ask-ai-secondary-button"
                  onClick={() => {
                    if (needsCv) {
                      setMessages([]);
                      setInput("");
                    }
                    setMode("general");
                    setIncludeCv(false);
                  }}
                  disabled={sending}
                >
                  Reset mode
                </button>
                <button
                  type="button"
                  className="ask-ai-primary-button"
                  onClick={handleSend}
                   disabled={!aiReadiness.ready || sending || (needsCv && (cvLoading || !hasCv)) || !input.trim()}
                   aria-describedby={!aiReadiness.ready ? "ask-ai-guidance" : undefined}
                >
                  {sending ? "Thinking..." : "Ask AI"}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
