import { useEffect, useState } from "react";

import "./CronInput.css";

const CRON_PRESETS = [
  { label: "No schedule", value: "" },
  { label: "Daily at 2:00 AM", value: "0 2 * * *" },
  { label: "Daily at 6:00 AM", value: "0 6 * * *" },
  { label: "Every 6 hours", value: "0 */6 * * *" },
  { label: "Weekdays at 6:00 AM", value: "0 6 * * 1-5" },
  { label: "Weekly on Monday at 6:00 AM", value: "0 6 * * 1" },
  { label: "Custom...", value: "__custom__" },
];

export default function CronInput({ value, onChange, disabled, error }) {
  const [mode, setMode] = useState("preset");
  const [customValue, setCustomValue] = useState("");
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);

  // Determine initial mode based on value
  useEffect(() => {
    if (!value) {
      setMode("preset");
      return;
    }

    const isPreset = CRON_PRESETS.some(
      (p) => p.value === value && p.value !== "__custom__"
    );

    if (isPreset) {
      setMode("preset");
    } else {
      setMode("custom");
      setCustomValue(value);
    }
  }, [value]);

  // Validate custom cron expression
  useEffect(() => {
    if (mode !== "custom" || !customValue.trim()) {
      setValidationResult(null);
      return;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(async () => {
      setValidating(true);
      try {
        const response = await fetch(
          `/api/cron/validate?expression=${encodeURIComponent(customValue)}`,
          { signal: controller.signal }
        );
        const data = await response.json();
        setValidationResult(data);
      } catch (err) {
        if (err.name !== "AbortError") {
          setValidationResult({ valid: false, error: "Validation failed" });
        }
      } finally {
        setValidating(false);
      }
    }, 500); // Debounce

    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [customValue, mode]);

  function handlePresetChange(event) {
    const selected = event.target.value;

    if (selected === "__custom__") {
      setMode("custom");
      setCustomValue(value || "");
    } else {
      setMode("preset");
      onChange(selected);
    }
  }

  function handleCustomChange(event) {
    const newValue = event.target.value;
    setCustomValue(newValue);
    onChange(newValue);
  }

  function handleBackToPreset() {
    setMode("preset");
    setCustomValue("");
    onChange("");
  }

  const currentPreset =
    mode === "preset"
      ? CRON_PRESETS.find((p) => p.value === value)?.value || ""
      : "__custom__";

  return (
    <div className="cron-input">
      <div className="cron-select-row">
        <select
          value={currentPreset}
          onChange={handlePresetChange}
          disabled={disabled}
          className="cron-preset-select"
        >
          {CRON_PRESETS.map((preset) => (
            <option key={preset.value} value={preset.value}>
              {preset.label}
            </option>
          ))}
        </select>
      </div>

      {mode === "custom" && (
        <div className="cron-custom-row">
          <input
            type="text"
            value={customValue}
            onChange={handleCustomChange}
            disabled={disabled}
            placeholder="e.g., 0 2 * * * (minute hour day month weekday)"
            className={`cron-custom-input ${
              validationResult && !validationResult.valid ? "invalid" : ""
            }`}
          />
          <button
            type="button"
            className="cron-back-btn"
            onClick={handleBackToPreset}
            disabled={disabled}
            title="Back to presets"
          >
            Back
          </button>
        </div>
      )}

      {mode === "custom" && validating && (
        <span className="cron-validating">Validating...</span>
      )}

      {mode === "custom" && validationResult && !validating && (
        <div
          className={`cron-validation ${
            validationResult.valid ? "valid" : "invalid"
          }`}
        >
          {validationResult.valid ? (
            <span className="cron-next-run">
              Next run:{" "}
              {new Date(validationResult.nextRuns?.[0]).toLocaleString()}
            </span>
          ) : (
            <span className="cron-error">{validationResult.error}</span>
          )}
        </div>
      )}

      {error && <span className="field-error">{error}</span>}

      {mode === "preset" && value && (
        <span className="cron-expression-hint">
          Expression: <code>{value}</code>
        </span>
      )}
    </div>
  );
}
