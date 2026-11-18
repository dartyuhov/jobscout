export async function fetchAiReadiness() {
  const response = await fetch("/api/health");
  const payload = await parseResponse(response);
  return payload.ai;
}

export async function fetchAiSettings() {
  return parseResponse(await fetch("/api/settings/ai"));
}

export async function saveAiSettings(settings) {
  return parseResponse(await fetch("/api/settings/ai", {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(settings),
  }));
}

export async function testAiSettings(revision) {
  return parseResponse(await fetch("/api/settings/ai/test", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ revision }),
  }));
}

export async function saveAiAutomation(settings) {
  return parseResponse(await fetch("/api/settings/ai/automation", {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(settings),
  }));
}

export async function analyzeJob(id) {
  return parseResponse(await fetch(`/api/jobs/${id}/analyze`, { method: "POST" }));
}

function buildQuery(params) {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    if (Array.isArray(value)) {
      if (value.length > 0) {
        query.set(key, value.join(","));
      }
      return;
    }

    query.set(key, String(value));
  });

  return query.toString();
}

async function parseResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      payload && typeof payload.error === "string"
        ? payload.error
        : "Request failed.";
    throw new Error(message);
  }
  return payload;
}

// ============ Profiles API ============

export async function fetchProfiles(options = {}) {
  const query = buildQuery({ include_archived: options.includeArchived || false });
  const response = await fetch(`/api/profiles${query ? `?${query}` : ""}`);
  const payload = await parseResponse(response);
  return payload.profiles || [];
}

export async function fetchProfileById(id) {
  const response = await fetch(`/api/profiles/${id}`);
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function createProfile(profileData) {
  const response = await fetch("/api/profiles", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(profileData),
  });
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function updateProfile(id, profileData) {
  const response = await fetch(`/api/profiles/${id}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(profileData),
  });
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function deleteProfile(id) {
  const response = await fetch(`/api/profiles/${id}`, {
    method: "DELETE",
  });
  const payload = await parseResponse(response);
  return payload;
}

export async function archiveProfile(id) {
  const response = await fetch(`/api/profiles/${id}/archive`, {
    method: "POST",
  });
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function restoreProfile(id) {
  const response = await fetch(`/api/profiles/${id}/restore`, {
    method: "POST",
  });
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function openProfileBrowser(id) {
  const response = await fetch(`/api/profiles/${id}/open-browser`, {
    method: "POST",
  });
  return parseResponse(response);
}

export async function uploadProfile(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/profiles/upload", {
    method: "POST",
    body: formData,
  });
  const payload = await parseResponse(response);
  return payload.profile;
}

export async function exportProfile(id) {
  const response = await fetch(`/api/profiles/${id}/export`);
  return parseResponse(response);
}

// ============ Jobs API ============

export async function fetchJobs(params) {
  const query = buildQuery(params);
  const response = await fetch(`/api/jobs${query ? `?${query}` : ""}`);
  return parseResponse(response);
}

export async function fetchJobStats() {
  const response = await fetch("/api/jobs/stats");
  const payload = await parseResponse(response);
  return payload.stats;
}

export async function fetchJobDetails(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const payload = await parseResponse(response);
  return payload.job;
}

export async function updateJobStatus(jobId, status) {
  const response = await fetch(`/api/jobs/${jobId}/status`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ status }),
  });
  const payload = await parseResponse(response);
  return payload.job;
}

// ============ Workflow Runs API ============

export async function fetchWorkflowRuns(params = {}) {
  const query = buildQuery(params);
  const response = await fetch(`/api/runs${query ? `?${query}` : ""}`);
  return parseResponse(response);
}

export async function fetchWorkflowRunById(id) {
  const response = await fetch(`/api/runs/${id}`);
  const payload = await parseResponse(response);
  return payload.run;
}

export async function fetchWorkflowBatchNotification(batchId) {
  const response = await fetch(`/api/run-batches/${encodeURIComponent(batchId)}/notification`);
  const payload = await parseResponse(response);
  return payload.summary;
}

export async function startWorkflowRun(profileId) {
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ profile_id: profileId }),
  });
  const payload = await parseResponse(response);
  return payload.run;
}

export async function cancelWorkflowRun(id) {
  const response = await fetch(`/api/runs/${id}/cancel`, {
    method: "POST",
  });
  const payload = await parseResponse(response);
  return payload.run;
}

export async function queueMultipleRuns(profileIds) {
  const response = await fetch("/api/runs/queue-multiple", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ profile_ids: profileIds }),
  });
  const payload = await parseResponse(response);
  return payload.runs;
}

export async function fetchSchedules() {
  const response = await fetch("/api/schedules");
  const payload = await parseResponse(response);
  return payload.schedules || [];
}

export async function updateProfilesSchedule(profileIds, cron) {
  const response = await fetch("/api/schedules/profiles", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ profile_ids: profileIds, cron }),
  });
  const payload = await parseResponse(response);
  return payload.profiles || [];
}

// ============ Settings API ============

export async function fetchCvText() {
  const response = await fetch("/api/settings/cv");
  const payload = await parseResponse(response);
  return payload.cv_text;
}

export async function fetchNotificationSettings() {
  const response = await fetch("/api/settings/notifications");
  return parseResponse(response);
}

export async function fetchThemeSettings() {
  const response = await fetch("/api/settings/theme");
  return parseResponse(response);
}

export async function fetchIgnoredCompaniesSettings() {
  const response = await fetch("/api/settings/ignored-companies");
  return parseResponse(response);
}

export async function fetchChromeProfileSettings() {
  const response = await fetch("/api/settings/chrome-profiles");
  const payload = await parseResponse(response);
  return payload.chrome_profiles || [];
}

export async function saveCvText(cvText) {
  const response = await fetch("/api/settings/cv", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ cv_text: cvText }),
  });
  return parseResponse(response);
}

export async function saveNotificationSettings(browserNotificationsEnabled) {
  const response = await fetch("/api/settings/notifications", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      browser_notifications_enabled: browserNotificationsEnabled,
    }),
  });
  return parseResponse(response);
}

export async function saveThemeSettings(themePreference) {
  const response = await fetch("/api/settings/theme", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      theme_preference: themePreference,
    }),
  });
  return parseResponse(response);
}

export async function saveIgnoredCompaniesSettings(ignoredCompanies) {
  const response = await fetch("/api/settings/ignored-companies", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ignored_companies: ignoredCompanies,
    }),
  });
  return parseResponse(response);
}

export async function killChromeProfile(chromeProfilePath) {
  const response = await fetch("/api/settings/chrome-profiles/kill", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ chrome_profile_path: chromeProfilePath }),
  });
  return parseResponse(response);
}

export async function killAllActiveChromeProfiles() {
  const response = await fetch("/api/settings/chrome-profiles/kill-all", {
    method: "POST",
  });
  return parseResponse(response);
}

// ============ Cover Letter API ============

export async function generateCoverLetter(jobId) {
  const response = await fetch("/api/cover-letter/generate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ job_id: jobId }),
  });
  return parseResponse(response);
}

// ============ Job Summarization API ============

export async function summarizeJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}/summarize`, {
    method: "POST",
  });
  return parseResponse(response);
}

// ============ Ask AI API ============

export async function askAi({ messages, jobId = null, mode = "general", includeCv = false }) {
  const response = await fetch("/api/ai/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messages,
      job_id: jobId,
      mode,
      include_cv: includeCv,
    }),
  });
  return parseResponse(response);
}
