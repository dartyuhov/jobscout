function parseSearchUrl(value) {
  if (typeof value !== "string") throw new Error("Search URL must be a string.");
  let url;
  try { url = new URL(value.trim()); } catch { throw new Error("Paste a complete LinkedIn Jobs search URL."); }
  if (url.protocol !== "https:" || !/^(?:[a-z]{2,3}\.)?linkedin\.com$/.test(url.hostname)
      || url.port || url.username || url.password) {
    throw new Error("Use an HTTPS LinkedIn URL containing your search parameters.");
  }
  const allowed = new Set(["keywords", "geoId", "location", "distance", "sortBy"]);
  const params = new URLSearchParams();
  for (const [key, val] of url.searchParams) {
    if (allowed.has(key) || /^f_[A-Za-z0-9_]+$/.test(key)) params.append(key, val);
  }
  return {
    searchUrl: `https://www.linkedin.com/jobs/search/${params.size ? `?${params}` : ""}`,
    jobTitle: params.get("keywords") || "", locationGeoId: params.get("geoId") || "",
    f_TPR: params.get("f_TPR") || "", f_WT: params.get("f_WT") || "",
  };
}
module.exports = { parseSearchUrl };
