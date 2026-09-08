import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import TestCard from "../TestCard/TestCard";
import {
  addAccount,
  deleteWebsite,
  getOnboardingDetails,
  getTestSchedules,
  getWebsiteRuns,
  onboardingEventsUrl,
  runCronCycle,
  runDiscovery,
  runTestNow,
  type Account,
  type CronRunResult,
  type SuiteResult,
  type TestRun,
  type TestSchedule,
  type Website,
} from "../../api/client";
import "./WebsiteDetail.css";

type Tab = "tests" | "runs" | "settings";

const TABS: { key: Tab; label: string }[] = [
  { key: "tests", label: "Test Cases" },
  { key: "runs", label: "Execution History" },
  { key: "settings", label: "Settings & Access" },
];

function formatTimestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "never";
}

function getRunStatusClass(status: string | null | undefined, exitCode?: number) {
  const s = (status || "").trim().toUpperCase();
  if (
    s === "PASSED" ||
    s === "PASS" ||
    s === "SUCCESS" ||
    (exitCode === 0 && !s.includes("FAIL") && !s.includes("BUG"))
  ) {
    return "pass";
  }
  if (s === "CONFIRMED_BUG" || s === "BUG") {
    return "bug";
  }
  if (s === "PENDING" || s === "RUNNING" || s === "IDLE") {
    return "pending";
  }
  return "fail";
}

function WebsiteDetail() {
  const { websiteId } = useParams();
  const navigate = useNavigate();
  const id = Number(websiteId);

  const [website, setWebsite] = useState<Website | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [schedules, setSchedules] = useState<TestSchedule[]>([]);
  const [dueCount, setDueCount] = useState(0);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [results, setResults] = useState<Record<string, SuiteResult>>({});
  const [lastRun, setLastRun] = useState<CronRunResult | null>(null);

  const [tab, setTab] = useState<Tab>("tests");
  const [openId, setOpenId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");

  const [newAccount, setNewAccount] = useState({
    username: "",
    password: "",
    role: "user",
  });
  const [isAddingAccount, setIsAddingAccount] = useState(false);

  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoveryEvents, setDiscoveryEvents] = useState<string[]>([]);
  const streamRef = useRef<EventSource | null>(null);

  const [runningTestId, setRunningTestId] = useState<string | null>(null);

  const loadTestData = useCallback(
    async (domain: string) => {
      const [schedule, runHistory] = await Promise.all([
        getTestSchedules(domain),
        getWebsiteRuns(id),
      ]);

      setSchedules(schedule.schedules);
      setDueCount(schedule.due_count);
      setRuns(runHistory.runs);
    },
    [id]
  );

  const loadAll = useCallback(async () => {
    if (!Number.isInteger(id)) {
      setError("Invalid website id.");
      setIsLoading(false);
      return;
    }

    try {
      const details = await getOnboardingDetails(id);
      setWebsite(details.website);
      setAccounts(details.accounts);
      await loadTestData(details.website.domain);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load website details."
      );
    } finally {
      setIsLoading(false);
    }
  }, [id, loadTestData]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Close any open progress stream when leaving the page.
  useEffect(() => () => streamRef.current?.close(), []);

  const handleRunDiscovery = async () => {
    setIsDiscovering(true);
    setDiscoveryEvents([]);
    setError("");

    try {
      const started = await runDiscovery(id);
      setDiscoveryEvents([started.message]);

      streamRef.current?.close();
      const stream = new EventSource(onboardingEventsUrl(id));
      streamRef.current = stream;

      stream.onmessage = (event) => {
        setDiscoveryEvents((prev) => [...prev, event.data]);

        // The graph publishes a terminal message when it finishes either way.
        if (event.data.includes("completed") || event.data.includes("failed")) {
          stream.close();
          streamRef.current = null;
          setIsDiscovering(false);
          loadAll();
        }
      };

      stream.onerror = () => {
        stream.close();
        streamRef.current = null;
        setIsDiscovering(false);
      };
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to start discovery."
      );
      setIsDiscovering(false);
    }
  };

  const handleRunNow = async () => {
    if (!website) return;

    setIsRunning(true);
    setError("");

    try {
      const result = await runCronCycle({ domain: website.domain });
      setLastRun(result);
      setResults(
        Object.fromEntries(
          result.suite_summary.map((entry) => [entry.id, entry])
        )
      );
      await loadTestData(website.domain);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to run the test cycle."
      );
    } finally {
      setIsRunning(false);
    }
  };

  const handleRunTestNow = async (testId: string) => {
    if (!website) return;

    setRunningTestId(testId);
    setError("");

    try {
      const { result } = await runTestNow(testId);
      setResults((prev) => ({ ...prev, [testId]: result }));
      await loadTestData(website.domain);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : `Failed to run test '${testId}'.`
      );
    } finally {
      setRunningTestId(null);
    }
  };

  const handleAddAccount = async () => {
    if (!newAccount.username || !newAccount.password) {
      setError("Username and password are required to add an account.");
      return;
    }

    setIsAddingAccount(true);

    try {
      const created = await addAccount(id, newAccount);
      setAccounts((prev) => [...prev, created]);
      setNewAccount({ username: "", password: "", role: "user" });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add the account.");
    } finally {
      setIsAddingAccount(false);
    }
  };

  const handleDelete = async () => {
    if (!website) return;

    const confirmed = window.confirm(
      `Delete ${website.domain}? This also removes its accounts and tests.`
    );
    if (!confirmed) return;

    try {
      await deleteWebsite(id);
      navigate("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to delete the website."
      );
    }
  };

  if (isLoading) {
    return (
      <div className="detail-container">
        <div className="dashboard-loading">
          <div className="loading-spinner"></div>
          <p>Loading application details...</p>
        </div>
      </div>
    );
  }

  if (!website) {
    return (
      <div className="detail-container">
        <button className="breadcrumb-btn" onClick={() => navigate("/dashboard")}>
          ← Back to Dashboard
        </button>
        <div className="dashboard-error">{error || "Website not found."}</div>
      </div>
    );
  }

  return (
    <div className="detail-container">
      {/* Breadcrumb Navigation */}
      <div className="detail-breadcrumb">
        <button className="breadcrumb-btn" onClick={() => navigate("/dashboard")}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          <span>Dashboard</span>
        </button>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">{website.domain}</span>
      </div>

      {/* Detail Header */}
      <div className="detail-header-block">
        <div className="detail-header-info">
          <div className="domain-heading-row">
            <h1>{website.domain}</h1>
            <span className={`status ${website.is_active ? "pass" : "pending"}`}>
              {website.is_active ? "ACTIVE" : "INACTIVE"}
            </span>
          </div>

          <div className="detail-meta-row">
            <a
              href={website.url}
              target="_blank"
              rel="noreferrer"
              className="external-link"
              title="Open Target Application"
            >
              <span>{website.url}</span>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
            </a>
            <span className="meta-sep">·</span>
            <span className="meta-tag">ID #{website.id}</span>
            <span className="meta-sep">·</span>
            <span className="meta-tag">{schedules.length} test suite{schedules.length === 1 ? "" : "s"}</span>
            {dueCount > 0 && (
              <>
                <span className="meta-sep">·</span>
                <span className="due-count-badge">{dueCount} due for run</span>
              </>
            )}
          </div>
        </div>

        <div className="detail-actions-bar">
          <button className="prompt-button" onClick={() => loadAll()} title="Refresh Data">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M23 4v6h-6" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
            <span>Refresh</span>
          </button>

          <button
            className={`discovery-action-btn ${isDiscovering ? "active" : ""}`}
            onClick={handleRunDiscovery}
            disabled={isDiscovering}
          >
            {isDiscovering ? (
              <>
                <span className="discovery-spinner"></span>
                <span>Discovering Paths...</span>
              </>
            ) : (
              <>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <span>Run Discovery</span>
              </>
            )}
          </button>

          <button
            className="save-prompt-button"
            onClick={handleRunNow}
            disabled={isRunning}
          >
            {isRunning ? (
              <>
                <span className="card-spin"></span>
                <span>Executing Cycle...</span>
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>Run Due Tests</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Discovery Live Terminal */}
      {discoveryEvents.length > 0 && (
        <div className="discovery-terminal">
          <div className="terminal-header">
            <div className="terminal-controls">
              <span className="term-dot term-red"></span>
              <span className="term-dot term-yellow"></span>
              <span className="term-dot term-green"></span>
              <span className="terminal-title">discovery.log — autonomous explorer</span>
            </div>
            <button className="term-clear-btn" onClick={() => setDiscoveryEvents([])}>
              clear
            </button>
          </div>

          <div className="terminal-body">
            {discoveryEvents.map((event, index) => (
              <div key={`${index}-${event}`} className="terminal-line">
                <span className="term-prompt">›</span>
                <span className="term-text">{event}</span>
              </div>
            ))}
            {isDiscovering && (
              <div className="terminal-line active-line">
                <span className="term-prompt">›</span>
                <span className="term-cursor"></span>
                <span className="term-hint">Agent exploring DOM hierarchy and user flows...</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="detail-tabs">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            className={`detail-tab ${tab === key ? "active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "tests" && (
              <span className="tab-counter">{schedules.length}</span>
            )}
            {key === "runs" && (
              <span className="tab-counter">{runs.length}</span>
            )}
            {key === "settings" && (
              <span className="tab-counter">{accounts.length}</span>
            )}
          </button>
        ))}
      </div>

      {error && (
        <div className="dashboard-error">
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {lastRun && tab === "tests" && (
        <div className="run-summary-banner">
          <div className="summary-status-icon">✓</div>
          <div>
            <strong>Test Cycle Finished:</strong>{" "}
            {lastRun.status === "idle"
              ? "No test cases were due in this cycle."
              : `${lastRun.executed_count} executed · ${lastRun.passed_count ?? 0} passed · ${lastRun.bug_count ?? 0} bugs confirmed · ${lastRun.failed_count ?? 0} automation failures`}
          </div>
        </div>
      )}

      {/* TAB 1: Tests */}
      {tab === "tests" && (
        <div className="tab-content-area">
          {schedules.length === 0 ? (
            <div className="dashboard-empty-state">
              <div className="empty-icon">🧪</div>
              <h3>No test cases discovered yet</h3>
              <p>
                Click <strong>"Run Discovery"</strong> to allow Forge agent to crawl {website.domain} and autonomously build test cases.
              </p>
              <button
                className="save-prompt-button"
                onClick={handleRunDiscovery}
                disabled={isDiscovering}
              >
                Start Discovery
              </button>
            </div>
          ) : (
            <div className="test-cards-list">
              {schedules.map((schedule) => (
                <TestCard
                  key={schedule.test_id}
                  schedule={schedule}
                  result={results[schedule.test_id]}
                  isOpen={openId === schedule.test_id}
                  isRunning={runningTestId === schedule.test_id}
                  onToggle={() =>
                    setOpenId(openId === schedule.test_id ? null : schedule.test_id)
                  }
                  onRunNow={() => handleRunTestNow(schedule.test_id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: Execution History */}
      {tab === "runs" && (
        <div className="tab-content-area">
          {runs.length === 0 ? (
            <div className="dashboard-empty-state">
              <div className="empty-icon">📋</div>
              <h3>No test executions recorded yet</h3>
              <p>Execution logs and test runs will populate as tests are run manually or by the background scheduler.</p>
            </div>
          ) : (
            <div className="table-responsive-container">
              <table className="forge-data-table">
                <thead>
                  <tr>
                    <th>Test Case</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Executed At</th>
                    <th>Error Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.run_id}>
                      <td className="table-col-title">
                        <strong>{run.title}</strong>
                        <span className="table-test-id">{run.test_id}</span>
                      </td>
                      <td>
                        <span
                          className={`status ${getRunStatusClass(
                            run.status,
                            run.exit_code
                          )}`}
                        >
                          {run.status || (run.exit_code === 0 ? "PASSED" : "FAILED")}
                        </span>
                      </td>
                      <td className="table-duration">{run.duration_s}s</td>
                      <td className="table-timestamp">
                        {formatTimestamp(run.executed_at)}
                      </td>
                      <td className="table-error-summary">
                        {run.error_summary ? (
                          <span className="error-text" title={run.error_summary}>
                            {run.error_summary}
                          </span>
                        ) : (
                          <span className="no-error">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: Settings */}
      {tab === "settings" && (
        <div className="tab-content-area settings-layout">
          <section className="settings-card">
            <div className="card-header">
              <h3>Website Configuration</h3>
              <p>Target domain metadata and indexing status</p>
            </div>
            <div className="specs-list">
              <div className="spec-item">
                <span className="spec-label">Target URL</span>
                <span className="spec-val mono">{website.url}</span>
              </div>
              <div className="spec-item">
                <span className="spec-label">Domain</span>
                <span className="spec-val mono">{website.domain}</span>
              </div>
              <div className="spec-item">
                <span className="spec-label">Lifecycle Status</span>
                <span className="spec-val">{website.is_active ? "Active" : "Inactive"}</span>
              </div>
              <div className="spec-item">
                <span className="spec-label">Registered At</span>
                <span className="spec-val">{formatTimestamp(website.created_at)}</span>
              </div>
              <div className="spec-item">
                <span className="spec-label">Last Discovered</span>
                <span className="spec-val">{formatTimestamp(website.last_discovered_at)}</span>
              </div>
            </div>
          </section>

          <section className="settings-card">
            <div className="card-header">
              <h3>Test Accounts ({accounts.length})</h3>
              <p>Credentials utilized by the agent during authenticated runs</p>
            </div>

            {accounts.length === 0 ? (
              <p className="dashboard-empty-state-text">No accounts registered for this website.</p>
            ) : (
              <div className="accounts-list">
                {accounts.map((account) => (
                  <div key={account.id} className="account-item">
                    <div className="account-user-info">
                      <span className="user-icon-badge">👤</span>
                      <strong className="account-username">{account.username}</strong>
                      <span className="account-role-badge">{account.role}</span>
                    </div>
                    <span className={`status ${account.is_active ? "pass" : "pending"}`}>
                      {account.is_active ? "ACTIVE" : "INACTIVE"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="add-account-form">
              <h4>Add New Account</h4>
              <div className="account-inputs-row">
                <input
                  type="text"
                  placeholder="Username / Email"
                  value={newAccount.username}
                  onChange={(e) =>
                    setNewAccount({ ...newAccount, username: e.target.value })
                  }
                />
                <input
                  type="password"
                  placeholder="Password"
                  value={newAccount.password}
                  onChange={(e) =>
                    setNewAccount({ ...newAccount, password: e.target.value })
                  }
                />
                <input
                  type="text"
                  placeholder="Role (e.g. user, admin)"
                  value={newAccount.role}
                  onChange={(e) =>
                    setNewAccount({ ...newAccount, role: e.target.value })
                  }
                />
                <button
                  className="save-prompt-button"
                  onClick={handleAddAccount}
                  disabled={isAddingAccount}
                >
                  {isAddingAccount ? "Adding..." : "+ Add Account"}
                </button>
              </div>
            </div>
          </section>

          <section className="settings-card danger-zone">
            <div className="card-header">
              <h3 className="danger-title">Danger Zone</h3>
              <p>Irreversible actions for this application</p>
            </div>
            <div className="danger-content">
              <div>
                <strong>Delete this website</strong>
                <p>Permanently remove this application, its registered accounts, and all discovered test suites.</p>
              </div>
              <button className="delete-button" onClick={handleDelete}>
                Delete Website
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default WebsiteDetail;
