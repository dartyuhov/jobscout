import { useRef, useState } from "react";

import {
  archiveProfile,
  createProfile,
  exportProfile,
  fetchProfiles,
  openProfileBrowser,
  restoreProfile,
  updateProfile,
  uploadProfile,
} from "../api/jobs";
import DeleteConfirmModal from "./DeleteConfirmModal";
import ProfileFormModal from "./ProfileFormModal";
import ProfilesTable from "./ProfilesTable";
import "./ProfilesScreen.css";

export default function ProfilesScreen({ profiles, loading, onRefresh }) {
  const [formProfile, setFormProfile] = useState(null); // null = closed, {} = new, {id,...} = edit
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [restoringProfileId, setRestoringProfileId] = useState(null);
  const [launchingProfileId, setLaunchingProfileId] = useState(null);
  const [exportingProfileId, setExportingProfileId] = useState(null);
  const [showArchived, setShowArchived] = useState(false);
  const [allProfiles, setAllProfiles] = useState([]);
  const [loadingAllProfiles, setLoadingAllProfiles] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [archiveError, setArchiveError] = useState("");
  const fileInputRef = useRef(null);

  const displayedProfiles = showArchived ? allProfiles : profiles;
  const displayLoading = showArchived ? loadingAllProfiles : loading;

  async function refreshVisibleProfiles() {
    await onRefresh();

    if (!showArchived) {
      return;
    }

    const items = await fetchProfiles({ includeArchived: true });
    setAllProfiles(items);
  }

  function handleCreateNew() {
    setFormProfile({});
    setError("");
  }

  function handleEdit(profile) {
    setFormProfile(profile);
    setError("");
  }

  function getDuplicateSessionName(sessionName) {
    const trimmedName = String(sessionName || "").trim();
    const baseName = trimmedName.replace(/\s*\(\d+\)$/, "") || trimmedName;
    const existingNames = new Set(
      displayedProfiles
        .map((profile) => String(profile.sessionName || "").trim())
        .filter(Boolean)
    );

    let suffix = 2;
    let candidate = `${baseName} (${suffix})`;
    while (existingNames.has(candidate)) {
      suffix += 1;
      candidate = `${baseName} (${suffix})`;
    }

    return candidate;
  }

  function handleDuplicate(draftProfile) {
    setFormProfile({
      ...draftProfile,
      sessionName: getDuplicateSessionName(draftProfile.sessionName),
    });
    setError("");
  }

  function handleArchiveClick(profile) {
    setArchiveTarget(profile);
    setArchiveError("");
  }

  function handleCloseForm() {
    if (!saving) {
      setFormProfile(null);
      setError("");
    }
  }

  function handleCloseDelete() {
    if (!archiving) {
      setArchiveTarget(null);
      setArchiveError("");
    }
  }

  async function loadArchivedProfiles() {
    setLoadingAllProfiles(true);

    try {
      const items = await fetchProfiles({ includeArchived: true });
      setAllProfiles(items);
    } catch (err) {
      setError(err.message || "Failed to load archived profiles.");
    } finally {
      setLoadingAllProfiles(false);
    }
  }

  async function handleSave(payload) {
    setSaving(true);
    setError("");

    try {
      if (formProfile.id) {
        await updateProfile(formProfile.id, payload);
      } else {
        await createProfile(payload);
      }
      setFormProfile(null);
      await refreshVisibleProfiles();
    } catch (err) {
      setError(err.message || "Failed to save profile.");
    } finally {
      setSaving(false);
    }
  }

  async function handleConfirmArchive(profileId) {
    setArchiving(true);
    setArchiveError("");

    try {
      const archivedProfile = await archiveProfile(profileId);
      setArchiveTarget(null);
      await refreshVisibleProfiles();
      setSuccess(`Archived '${archivedProfile.sessionName}'.`);
    } catch (err) {
      setArchiveError(err.message || "Failed to archive profile.");
    } finally {
      setArchiving(false);
    }
  }

  async function handleRestore(profile) {
    setRestoringProfileId(profile.id);
    setError("");
    setSuccess("");

    try {
      const restoredProfile = await restoreProfile(profile.id);
      await refreshVisibleProfiles();
      setSuccess(`Restored '${restoredProfile.sessionName}'.`);
    } catch (err) {
      setError(err.message || "Failed to restore profile.");
    } finally {
      setRestoringProfileId(null);
    }
  }

  async function handleOpenBrowser(profile) {
    setLaunchingProfileId(profile.id);
    setError("");
    setSuccess("");

    try {
      const payload = await openProfileBrowser(profile.id);
      setSuccess(payload.message || `Opened browser for '${profile.sessionName}'.`);
    } catch (err) {
      setError(err.message || "Failed to open browser for this profile.");
    } finally {
      setLaunchingProfileId(null);
    }
  }

  function handleUploadClick() {
    fileInputRef.current?.click();
  }

  async function handleExport(profile) {
    setExportingProfileId(profile.id);
    setError("");
    setSuccess("");
    try {
      const settings = await exportProfile(profile.id);
      const url = URL.createObjectURL(new Blob([JSON.stringify(settings, null, 2) + "\n"], {
        type: "application/json",
      }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "search-settings.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSuccess("Exported search settings. Review preference text for personal details before sharing.");
    } catch (err) {
      setError(err.message || "Failed to export search settings.");
    } finally {
      setExportingProfileId(null);
    }
  }

  async function handleFileChange(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    // Reset input so same file can be selected again
    event.target.value = "";

    setSaving(true);
    setError("");

    try {
      const profile = await uploadProfile(file);
      await refreshVisibleProfiles();
      setSuccess(`Imported settings as '${profile.sessionName}'.`);
    } catch (err) {
      setError(err.message || "Failed to import search settings.");
    } finally {
      setSaving(false);
    }
  }

  async function handleShowArchivedChange(event) {
    const nextValue = event.target.checked;
    setShowArchived(nextValue);
    setError("");

    if (nextValue) {
      await loadArchivedProfiles();
    }
  }

  return (
    <div className="profiles-screen">
      <div className="profiles-header">
        <div>
          <h2>Profiles</h2>
          <p className="profiles-subtitle">
            Manage your LinkedIn search profiles. Each profile defines search
            criteria and preferences. Import settings as a new local search or
            export settings to share them.
          </p>
          <p className="profiles-subtitle">
            Preference text can contain personal details. Review it before sharing.
            Exports omit CV text, jobs, conversations, credentials, and runtime settings.
          </p>
        </div>
        <div className="profiles-actions">
          <label className="profiles-toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={handleShowArchivedChange}
              disabled={saving || archiving || Boolean(restoringProfileId)}
            />
            <span>Show archived</span>
          </label>
          <button
            type="button"
            className="btn-upload"
            onClick={handleUploadClick}
            disabled={saving}
          >
            {saving ? "Importing..." : "Import settings JSON"}
          </button>
          <button
            type="button"
            className="btn-create"
            onClick={handleCreateNew}
            disabled={saving}
          >
            Create Profile
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            onChange={handleFileChange}
            style={{ display: "none" }}
          />
        </div>
      </div>

      {error && <p className="profiles-error" role="alert">{error}</p>}
      {success && <p className="profiles-success" role="status">{success}</p>}

      <ProfilesTable
        profiles={displayedProfiles}
        loading={displayLoading}
        launchingProfileId={launchingProfileId}
        restoringProfileId={restoringProfileId}
        exportingProfileId={exportingProfileId}
        showArchived={showArchived}
        onOpenBrowser={handleOpenBrowser}
        onEdit={handleEdit}
        onArchive={handleArchiveClick}
        onRestore={handleRestore}
        onExport={handleExport}
      />

      {formProfile !== null && (
        <ProfileFormModal
          profile={formProfile}
          onClose={handleCloseForm}
          onDuplicate={handleDuplicate}
          onSave={handleSave}
          saving={saving}
        />
      )}

      {archiveTarget && (
        <DeleteConfirmModal
          profile={archiveTarget}
          onClose={handleCloseDelete}
          onConfirm={handleConfirmArchive}
          deleting={archiving}
          error={archiveError}
          title="Archive Profile"
          warning="Jobs and run history will be kept, but this profile will stop appearing in active workflows until you restore it."
          confirmLabel="Archive"
          pendingLabel="Archiving..."
        />
      )}
    </div>
  );
}
