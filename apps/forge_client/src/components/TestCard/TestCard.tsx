import type { SuiteResult, TestSchedule } from "../../api/client";
import "./TestCard.css";

type TestCardProps = {
  schedule: TestSchedule;
  result?: SuiteResult;
  isOpen: boolean;
  isRunning: boolean;
  onToggle: () => void;
  onRunNow: () => void;
};

const RESULT_LABELS: Record<SuiteResult["status"], string> = {
  PASSED: "PASS",
  CONFIRMED_BUG: "BUG",
  FAILED_AUTOMATION: "FAIL",
  SUSPECTED_APP_FAILURE: "SUSPECT",
};

function formatTimestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "never";
}

function TestCard({
  schedule,
  result,
  isOpen,
  isRunning,
  onToggle,
  onRunNow,
}: TestCardProps) {
  const isPassed = result?.status === "PASSED";
  const statusLabel = result ? RESULT_LABELS[result.status] : "PENDING";
  const statusClass = result
    ? isPassed
      ? "pass"
      : result.status === "CONFIRMED_BUG"
      ? "bug"
      : "fail"
    : "pending";

  return (
    <div className={`test-card ${isOpen ? "open" : ""}`}>
      <div className="test-card-header" onClick={onToggle} role="button" tabIndex={0}>
        <div className="test-info">
          <div className="test-title-row">
            <span className="test-bullet"></span>
            <h3>{schedule.title}</h3>
          </div>

          <div className="test-meta-sub">
            <span className="cron-tag">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              every {schedule.cron_interval_hours}h
            </span>
            <span className="meta-sep">·</span>
            <span className="run-timestamp">last run {formatTimestamp(schedule.last_run_at)}</span>
          </div>
        </div>

        <div className="test-actions" onClick={(e) => e.stopPropagation()}>
          {schedule.is_due && (
            <span className="due-badge" title="Test is scheduled to run">
              <span>●</span> DUE
            </span>
          )}

          <span className={`status ${statusClass}`}>{statusLabel}</span>

          <button
            className="run-now-button"
            onClick={onRunNow}
            disabled={isRunning}
            title="Execute test now"
          >
            {isRunning ? (
              <>
                <span className="card-spin"></span>
                <span>Running...</span>
              </>
            ) : (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>Run</span>
              </>
            )}
          </button>

          <button
            className={`toggle-button ${isOpen ? "expanded" : ""}`}
            onClick={onToggle}
            aria-label={isOpen ? "Close details" : "Open details"}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </div>
      </div>

      {isOpen && (
        <div className="test-card-details">
          <div className="details-header">
            <h4>Execution Details & Diagnostics</h4>
          </div>

          <div className="details-grid">
            <div className="detail-item">
              <span className="detail-label">Test ID</span>
              <span className="detail-val mono">{schedule.test_id}</span>
            </div>

            <div className="detail-item">
              <span className="detail-label">Domain</span>
              <span className="detail-val mono">{schedule.domain}</span>
            </div>

            <div className="detail-item">
              <span className="detail-label">Next Scheduled Run</span>
              <span className="detail-val">{formatTimestamp(schedule.next_run_at)}</span>
            </div>

            {result && (
              <>
                <div className="detail-item">
                  <span className="detail-label">Outcome</span>
                  <span className={`detail-val status-text ${isPassed ? "pass" : "fail"}`}>
                    {result.status}
                  </span>
                </div>

                {result.duration_s !== undefined && (
                  <div className="detail-item">
                    <span className="detail-label">Duration</span>
                    <span className="detail-val">{result.duration_s}s</span>
                  </div>
                )}

                {result.heals_needed !== undefined && (
                  <div className="detail-item">
                    <span className="detail-label">Self-Heal Attempts</span>
                    <span className="detail-val">{result.heals_needed}</span>
                  </div>
                )}

                {result.incident_id && (
                  <div className="detail-item">
                    <span className="detail-label">Incident ID</span>
                    <span className="detail-val mono">{result.incident_id}</span>
                  </div>
                )}
              </>
            )}
          </div>

          {result?.error && (
            <div className="detail-error-box">
              <div className="error-title">Error Stack / Failure Trace:</div>
              <pre>{result.error}</pre>
            </div>
          )}

          {!result && (
            <p className="no-session-msg">
              This test has not run in the current session yet. Click "Run" to trigger immediate execution.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default TestCard;
