import { USER_STATUS_LABELS, USER_STATUSES } from "../constants";
import "./StatusDropdown.css";

export default function StatusDropdown({ value, onChange, disabled = false }) {
  return (
    <select
      className={`status-dropdown status-${value || "to_be_reviewed"}`}
      value={value || "to_be_reviewed"}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      aria-label="Job status"
    >
      {USER_STATUSES.map((status) => (
        <option key={status} value={status}>
          {USER_STATUS_LABELS[status]}
        </option>
      ))}
    </select>
  );
}
