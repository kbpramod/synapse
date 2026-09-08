import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getDaemonStatus,
  listWebsites,
  startDaemon,
  stopDaemon,
  type Website,
} from "../../api/client";
import "./Dashboard.css";

function Dashboard() {
  const navigate = useNavigate();
  const [websites, setWebsites] = useState<Website[]>([]);
  const [daemonRunning, setDaemonRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [isTogglingDaemon, setIsTogglingDaemon] = useState(false);

  const loadWebsites = useCallback(async () => {
    try {
      setWebsites(await listWebsites());
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load registered websites."
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWebsites();
    getDaemonStatus()
      .then((status) => setDaemonRunning(status.daemon_running))
      .catch(() => setDaemonRunning(false));
  }, [loadWebsites]);

  const handleToggleDaemon = async () => {
    setIsTogglingDaemon(true);
    try {
      if (daemonRunning) {
        await stopDaemon();
        setDaemonRunning(false);
      } else {
        await startDaemon({ interval_seconds: 60 });
        setDaemonRunning(true);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to change the scheduler state."
      );
    } finally {
      setIsTogglingDaemon(false);
    }
  };

  const activeCount = websites.filter((w) => w.is_active).length;

  return (
    <div className="dashboard-container">
      <div className="dashboard-header-block">
        <div>
          <div className="dashboard-badge">Overview</div>
          <h1 className="dashboard-title">Testing Dashboard</h1>
          <p className="dashboard-subtitle">
            Monitor autonomous testing agents, suite health, and background schedules.
          </p>
        </div>

        <div className="dashboard-header-actions">
          <button
            className={`daemon-toggle-btn ${daemonRunning ? "running" : ""}`}
            onClick={handleToggleDaemon}
            disabled={isTogglingDaemon}
          >
            <span className={`pulse-dot ${daemonRunning ? "active" : ""}`}></span>
            <span>
              {isTogglingDaemon
                ? "Updating..."
                : daemonRunning
                ? "Scheduler Running"
                : "Scheduler Stopped"}
            </span>
          </button>

          <button className="save-prompt-button" onClick={() => navigate("/")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            <span>Register Website</span>
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-label">Registered Applications</div>
          <div className="metric-value">{websites.length}</div>
          <div className="metric-desc">Web apps configured for testing</div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Active Deployments</div>
          <div className="metric-value">{activeCount}</div>
          <div className="metric-desc">Targeting continuous QA cycles</div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Scheduler Daemon</div>
          <div className="metric-value metric-status">
            <span className={`status-indicator ${daemonRunning ? "online" : "offline"}`}>
              {daemonRunning ? "Online" : "Paused"}
            </span>
          </div>
          <div className="metric-desc">
            {daemonRunning ? "Interval: every 60s" : "Click scheduler button to start"}
          </div>
        </div>
      </div>

      {error && (
        <div className="dashboard-error">
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      <div className="section-title-wrap">
        <h2>Registered Websites ({websites.length})</h2>
        <button className="link-button" onClick={() => loadWebsites()}>
          ↻ Refresh
        </button>
      </div>

      {isLoading && (
        <div className="dashboard-loading">
          <div className="loading-spinner"></div>
          <p>Loading registered websites...</p>
        </div>
      )}

      {!isLoading && websites.length === 0 && (
        <div className="dashboard-empty-state">
          <div className="empty-icon">🌐</div>
          <h3>No applications registered yet</h3>
          <p>Register your web app URL and credentials to begin autonomous discovery and testing.</p>
          <button className="save-prompt-button" onClick={() => navigate("/")}>
            Register Application
          </button>
        </div>
      )}

      <div className="website-list">
        {websites.map((website) => (
          <div
            key={website.id}
            className="website-row"
            onClick={() => navigate(`/websites/${website.id}`)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                navigate(`/websites/${website.id}`);
              }
            }}
          >
            <div className="website-row-left">
              <div className="domain-avatar">
                {website.domain.slice(0, 2).toUpperCase()}
              </div>

              <div className="website-row-main">
                <div className="website-domain-title">
                  <h3>{website.domain}</h3>
                  <span className={`status ${website.is_active ? "pass" : "pending"}`}>
                    {website.is_active ? "ACTIVE" : "INACTIVE"}
                  </span>
                </div>
                <span className="website-row-url">{website.url}</span>
              </div>
            </div>

            <div className="website-row-meta">
              <span className="website-row-id">ID #{website.id}</span>
              <span className="website-row-action">
                View Tests
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default Dashboard;
