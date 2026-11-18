import { USER_STATUS_LABELS, USER_STATUSES } from "../constants";
import "./FilterBar.css";

export default function FilterBar({ filters, profiles, onChange, onClear }) {
  function toggleStatus(status) {
    const hasStatus = filters.statuses.includes(status);
    const nextStatuses = hasStatus
      ? filters.statuses.filter((item) => item !== status)
      : [...filters.statuses, status];
    onChange({ statuses: nextStatuses });
  }

  return (
    <div className="filter-grid">
      <label className="filter-field search-field">
        <span>Search (ID, company, position)</span>
        <input
          type="search"
          placeholder="Type to search jobs"
          value={filters.search}
          onChange={(event) => onChange({ search: event.target.value })}
        />
      </label>

      <label className="filter-field">
        <span>Profile</span>
        <select
          value={filters.profile_id}
          onChange={(event) => onChange({ profile_id: event.target.value })}
        >
          <option value="">All profiles</option>
          {profiles
            .filter((profile) => profile.sessionName && profile.sessionName.trim())
            .map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.sessionName}
              </option>
            ))}
        </select>
      </label>

      <label className="filter-field">
        <span>Location</span>
        <input
          type="search"
          placeholder="Filter by location"
          value={filters.location}
          onChange={(event) => onChange({ location: event.target.value })}
        />
      </label>

      <label className="filter-field">
        <span>Date from</span>
        <input
          type="date"
          value={filters.date_from}
          onChange={(event) => onChange({ date_from: event.target.value })}
        />
      </label>

      <label className="filter-field">
        <span>Date to</span>
        <input
          type="date"
          value={filters.date_to}
          onChange={(event) => onChange({ date_to: event.target.value })}
        />
      </label>

      <fieldset className="status-fieldset">
        <legend>Status filter</legend>
        <div className="status-options">
          {USER_STATUSES.map((status) => (
              <label key={status} className={`filter-status-pill status-${status}`}>
                <input
                  type="checkbox"
                  checked={filters.statuses.includes(status)}
                  onChange={() => toggleStatus(status)}
                />
                <span className="filter-status-pill-label">{USER_STATUS_LABELS[status]}</span>
              </label>
          ))}
        </div>
      </fieldset>

      <div className="filter-actions">
        <div className="filter-actions-left">
          <label className="match-toggle">
            <input
              type="checkbox"
              checked={filters.match_only}
              onChange={(event) => onChange({ match_only: event.target.checked })}
            />
            <span>Show only AI-matched jobs</span>
          </label>

          <button type="button" className="clear-button" onClick={onClear}>
            Clear filters
          </button>
        </div>

        <label className="rows-control">
          <span>Rows</span>
          <select
            value={filters.per_page}
            onChange={(event) =>
              onChange({ per_page: Number.parseInt(event.target.value, 10) || 25 })
            }
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
      </div>
    </div>
  );
}
