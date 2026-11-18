// Shared by the portal and Node API. Keep only search-related parameters.
export function parseSearchUrl(value) {
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
  const normalized = `https://www.linkedin.com/jobs/search/${params.size ? `?${params}` : ""}`;
  return {
    searchUrl: normalized,
    jobTitle: params.get("keywords") || "",
    locationGeoId: params.get("geoId") || "",
    f_TPR: params.get("f_TPR") || "",
    f_WT: params.get("f_WT") || "",
    filters: [...params.entries()],
  };
}
