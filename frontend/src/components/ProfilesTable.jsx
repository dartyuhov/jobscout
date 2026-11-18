import "./ProfilesTable.css";

export default function ProfilesTable({
  profiles,
  loading,
  launchingProfileId,
  restoringProfileId,
  exportingProfileId,
  showArchived,
  onOpenBrowser,
  onEdit,
  onArchive,
  onRestore,
  onExport,
}) {
  if (loading) {
    return <p className="profiles-loading">Loading profiles...</p>;
  }

  if (profiles.length === 0) {
    return (
      <p className="profiles-empty">
        {showArchived
          ? "No profiles found for the current archived view."
          : "No profiles found. Create one or import settings JSON to get started."}
      </p>
    );
  }

  return (
    <div className="profiles-table-wrapper">
      <table className="profiles-table">
        <thead>
          <tr>
            <th>Session Name</th>
            <th>Status</th>
            <th>Job Title</th>
            <th>Location ID</th>
            <th>Max Pages</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((profile) => {
            const rowBusy = launchingProfileId === profile.id || restoringProfileId === profile.id || exportingProfileId === profile.id;

            return (
              <tr key={profile.id} className={profile.is_archived ? "profile-row-archived" : ""}>
                <td className="cell-session">{profile.sessionName}</td>
                <td>
                  <span className={`profile-status-badge${profile.is_archived ? " is-archived" : ""}`}>
                    {profile.is_archived ? "Archived" : "Active"}
                  </span>
                </td>
                <td className="cell-title">{profile.jobTitle || "-"}</td>
                <td>{profile.locationGeoId || "-"}</td>
                <td>{profile.maxPage}</td>
                <td className="cell-actions">
                  <button
                    type="button"
                    className="btn-open-browser"
                    onClick={() => onOpenBrowser(profile)}
                    disabled={rowBusy || profile.is_archived}
                  >
                    {rowBusy && launchingProfileId === profile.id
                      ? "Opening..."
                      : profile.is_archived
                        ? "Archived"
                        : "Open Browser"}
                  </button>
                  <button
                    type="button"
                    className="btn-edit"
                    onClick={() => onEdit(profile)}
                    disabled={rowBusy}
                  >
                    Edit
                  </button>
                  {profile.is_archived ? (
                    <button
                      type="button"
                      className="btn-restore"
                      onClick={() => onRestore(profile)}
                      disabled={rowBusy}
                    >
                      {restoringProfileId === profile.id ? "Restoring..." : "Restore"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-archive"
                      onClick={() => onArchive(profile)}
                      disabled={rowBusy}
                    >
                      Archive
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-edit"
                    onClick={() => onExport(profile)}
                    disabled={rowBusy}
                  >
                    {exportingProfileId === profile.id ? "Exporting..." : "Export settings"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
