import { useEffect } from "react";

import "./CvRequiredModal.css";

export default function CvRequiredModal({ onClose, onGoToSettings }) {
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleEscape(event) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleEscape);
    };
  }, [onClose]);

  return (
    <div className="cv-modal-overlay" onClick={onClose}>
      <div className="cv-modal-panel" onClick={(event) => event.stopPropagation()}>
        <h3>CV Required</h3>
        <p>
          To generate a personalized cover letter, you need to add your CV/resume text first.
        </p>
        <p className="cv-modal-hint">
          Go to the Settings tab to paste your CV text, then come back to generate cover letters.
        </p>
        <div className="cv-modal-actions">
          <button type="button" className="cv-modal-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="cv-modal-primary" onClick={onGoToSettings}>
            Go to Settings
          </button>
        </div>
      </div>
    </div>
  );
}
