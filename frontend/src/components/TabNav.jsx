import "./TabNav.css";

const TABS = [
  { id: "jobs", label: "Jobs" },
  { id: "ai", label: "Ask AI" },
  { id: "profiles", label: "Profiles" },
  { id: "runs", label: "Run Workflow" },
  { id: "settings", label: "Settings" },
];

export default function TabNav({ activeTab, onTabChange }) {
  return (
    <nav className="tab-nav">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
