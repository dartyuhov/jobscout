import { useEffect } from "react";

import "./DeleteConfirmModal.css";

export default function DeleteConfirmModal({
  profile,
  onClose,
  onConfirm,
  deleting,
  error,
  title = "Delete Profile",
  warning = "This action cannot be undone.",
  confirmLabel = "Delete",
  pendingLabel = "Deleting...",
}) {
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleEscape(event) {
      if (event.key === "Escape" && !deleting) {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleEscape);
    };
  }, [onClose, deleting]);

  if (!profile) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={deleting ? undefined : onClose}>
      <div
        className="modal-panel delete-confirm-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="delete-icon">
          <svg
            width="48"
            height="48"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>

        <h2>{title}</h2>
        <p className="delete-message">
          Are you sure you want to {confirmLabel.toLowerCase()} the profile{" "}
          <strong>{profile.sessionName}</strong>?
        </p>
        <p className="delete-warning">{warning}</p>

        {error && <p className="delete-error">{error}</p>}

        <div className="delete-actions">
          <button
            type="button"
            className="btn-cancel"
            onClick={onClose}
            disabled={deleting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-delete-confirm"
            onClick={() => onConfirm(profile.id)}
            disabled={deleting}
          >
            {deleting ? pendingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
