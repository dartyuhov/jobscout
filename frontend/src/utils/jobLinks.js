const LINKEDIN_JOB_VIEW_BASE_URL = "https://www.linkedin.com/jobs/view/";

export function buildLinkedInJobViewUrl(linkedinJobId) {
  const normalizedJobId = String(linkedinJobId ?? "").trim();
  if (!normalizedJobId) {
    return "";
  }

  return `${LINKEDIN_JOB_VIEW_BASE_URL}${normalizedJobId}/`;
}

export function getOpenJobUrl(job) {
  const jobUrl = buildLinkedInJobViewUrl(job?.linkedin_job_id);
  if (jobUrl) {
    return jobUrl;
  }

  return String(job?.apply_link ?? "").trim();
}
