import { useEffect, useMemo, useState } from "react";

import "./ProfileFormModal.css";

const EMPTY_FORM = {
  sessionName: "",
  jobTitle: "",
  locationGeoId: "",
  f_TPR: "",
  f_WT: "",
  want: "",
  not_want: "",
  maxPage: "5",
  cron: "",
};

function buildFormState(profile) {
  if (!profile) {
    return EMPTY_FORM;
  }

  return {
    sessionName: profile.sessionName || "",
    jobTitle: profile.jobTitle || "",
    locationGeoId: profile.locationGeoId || "",
    f_TPR: profile.f_TPR || "",
    f_WT: profile.f_WT || "",
    want: profile.want || "",
    not_want: profile.not_want || "",
    maxPage: String(profile.maxPage ?? 5),
    cron: profile.cron || "",
  };
}

export default function ProfileFormModal({
  profile,
  onClose,
  onDuplicate,
  onSave,
  saving,
}) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false);

  const isEdit = Boolean(profile?.id);
  const initialForm = useMemo(() => buildFormState(profile), [profile]);
  const hasUnsavedChanges = JSON.stringify(form) !== JSON.stringify(initialForm);

  useEffect(() => {
    setForm(initialForm);
    setErrors({});
    setShowUnsavedConfirm(false);
  }, [initialForm]);

  function requestClose() {
    if (saving) {
      return;
    }

    if (hasUnsavedChanges) {
      setShowUnsavedConfirm(true);
      return;
    }

    onClose();
  }

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleEscape(event) {
      if (event.key !== "Escape" || saving) {
        return;
      }

      if (showUnsavedConfirm) {
        setShowUnsavedConfirm(false);
        return;
      }

      requestClose();
    }

    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleEscape);
    };
  }, [saving, showUnsavedConfirm, hasUnsavedChanges, onClose]);

  function handleChange(event) {
    const { name, value, type, checked } = event.target;
    setForm((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
    // Clear field error on change
    if (errors[name]) {
      setErrors((prev) => ({ ...prev, [name]: undefined }));
    }
  }

  function validate() {
    const newErrors = {};

    if (!form.sessionName.trim()) {
      newErrors.sessionName = "Session name is required";
    }
    if (!form.locationGeoId.trim()) {
      newErrors.locationGeoId = "Location Geo ID is required";
    }
    if (!form.f_TPR.trim()) {
      newErrors.f_TPR = "Time posted range (f_TPR) is required";
    }

    // Validate maxPage
    const maxPageNum = parseInt(form.maxPage, 10);
    if (isNaN(maxPageNum) || maxPageNum < 1) {
      newErrors.maxPage = "Must be a positive number";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  function handleSubmit(event) {
    event.preventDefault();

    if (!validate()) {
      return;
    }

    const payload = {
      sessionName: form.sessionName.trim(),
      jobTitle: form.jobTitle.trim(),
      locationGeoId: form.locationGeoId.trim(),
      f_TPR: form.f_TPR.trim(),
      f_WT: form.f_WT.trim(),
      want: form.want.trim(),
      not_want: form.not_want.trim(),
      maxPage: parseInt(form.maxPage, 10),
      cron: form.cron.trim() || null,
    };

    onSave(payload);
  }

  function handleDuplicateClick() {
    if (!isEdit || saving) {
      return;
    }

    onDuplicate({
      sessionName: form.sessionName.trim(),
      jobTitle: form.jobTitle.trim(),
      locationGeoId: form.locationGeoId.trim(),
      f_TPR: form.f_TPR.trim(),
      f_WT: form.f_WT.trim(),
      want: form.want,
      not_want: form.not_want,
      maxPage: parseInt(form.maxPage, 10) || 5,
      cron: form.cron.trim(),
    });
  }

  return (
    <div className="modal-overlay" onClick={saving ? undefined : requestClose}>
      <div
        className="modal-panel profile-form-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>{isEdit ? "Edit Profile" : "Create Profile"}</h2>
          <button
            type="button"
            className="close-modal"
            onClick={requestClose}
            disabled={saving}
          >
            Close
          </button>
        </div>

        <form onSubmit={handleSubmit} className="profile-form">
          <div className="form-section">
            <h3>Required Fields</h3>

            <div className="form-row">
              <label htmlFor="sessionName">
                Session Name <span className="required">*</span>
              </label>
              <input
                type="text"
                id="sessionName"
                name="sessionName"
                value={form.sessionName}
                onChange={handleChange}
                disabled={saving}
                placeholder="e.g., engineering-search"
              />
              {errors.sessionName && (
                <span className="field-error">{errors.sessionName}</span>
              )}
            </div>

            <p>All searches share the app-owned visible Chrome login.</p>

            <div className="form-row">
              <label htmlFor="locationGeoId">
                Location Geo ID <span className="required">*</span>
              </label>
              <input
                type="text"
                id="locationGeoId"
                name="locationGeoId"
                value={form.locationGeoId}
                onChange={handleChange}
                disabled={saving}
                placeholder="e.g., 105088894"
              />
              {errors.locationGeoId && (
                <span className="field-error">{errors.locationGeoId}</span>
              )}
            </div>

            <div className="form-row">
              <label htmlFor="f_TPR">
                Time Posted Range (f_TPR) <span className="required">*</span>
              </label>
              <input
                type="text"
                id="f_TPR"
                name="f_TPR"
                value={form.f_TPR}
                onChange={handleChange}
                disabled={saving}
                placeholder="e.g., r604800"
              />
              {errors.f_TPR && (
                <span className="field-error">{errors.f_TPR}</span>
              )}
            </div>

          </div>

          <div className="form-section">
            <h3>Optional Fields</h3>

            <div className="form-row">
              <label htmlFor="jobTitle">Job Title</label>
              <input
                type="text"
                id="jobTitle"
                name="jobTitle"
                value={form.jobTitle}
                onChange={handleChange}
                disabled={saving}
                placeholder="e.g., Software Engineer"
              />
            </div>

            <div className="form-row">
              <label htmlFor="f_WT">Work Type (f_WT)</label>
              <input
                type="text"
                id="f_WT"
                name="f_WT"
                value={form.f_WT}
                onChange={handleChange}
                disabled={saving}
                placeholder="e.g., 2"
              />
            </div>

            <div className="form-row">
              <label htmlFor="maxPage">Max Pages</label>
              <input
                type="number"
                id="maxPage"
                name="maxPage"
                value={form.maxPage}
                onChange={handleChange}
                disabled={saving}
                min="1"
              />
              {errors.maxPage && (
                <span className="field-error">{errors.maxPage}</span>
              )}
            </div>

          </div>

          <div className="form-section">
            <h3>Search Criteria</h3>
            <p className="section-hint">
              Plain text criteria for filtering jobs.
            </p>

            <div className="form-row">
              <label htmlFor="want">Want (criteria to match)</label>
              <textarea
                id="want"
                name="want"
                value={form.want}
                onChange={handleChange}
                disabled={saving}
                rows={4}
                placeholder="e.g., remote, senior, python"
              />
              {errors.want && (
                <span className="field-error">{errors.want}</span>
              )}
            </div>

            <div className="form-row">
              <label htmlFor="not_want">Not Want (criteria to exclude)</label>
              <textarea
                id="not_want"
                name="not_want"
                value={form.not_want}
                onChange={handleChange}
                disabled={saving}
                rows={4}
                placeholder="e.g., intern, junior, onsite"
              />
              {errors.not_want && (
                <span className="field-error">{errors.not_want}</span>
              )}
            </div>
          </div>

          <div className="form-actions">
            {isEdit && (
              <button
                type="button"
                className="btn-duplicate"
                onClick={handleDuplicateClick}
                disabled={saving}
              >
                Duplicate
              </button>
            )}
            <button
              type="button"
              className="btn-cancel"
              onClick={requestClose}
              disabled={saving}
            >
              Cancel
            </button>
            <button type="submit" className="btn-save" disabled={saving}>
              {saving ? "Saving..." : isEdit ? "Update Profile" : "Create Profile"}
            </button>
          </div>
        </form>

        {showUnsavedConfirm && (
          <div className="unsaved-confirm-backdrop" onClick={() => setShowUnsavedConfirm(false)}>
            <div
              className="unsaved-confirm-panel"
              onClick={(event) => event.stopPropagation()}
            >
              <h3>Discard unsaved changes?</h3>
              <p>
                You have unsaved profile changes. Close this form and lose them?
              </p>
              <div className="unsaved-confirm-actions">
                <button
                  type="button"
                  className="btn-cancel"
                  onClick={() => setShowUnsavedConfirm(false)}
                  disabled={saving}
                >
                  Keep editing
                </button>
                <button
                  type="button"
                  className="btn-discard"
                  onClick={onClose}
                  disabled={saving}
                >
                  Discard changes
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
