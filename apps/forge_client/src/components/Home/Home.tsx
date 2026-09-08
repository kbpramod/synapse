import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  submitOnboarding,
  DEFAULT_ONBOARDING_STATE,
  type Environment,
  type LoginOption,
  type AuthType,
  type OnboardingFormState,
} from "../../api/onboarding";
import "./Home.css";

function Home() {
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2>(1);
  const [form, setForm] = useState<OnboardingFormState>(DEFAULT_ONBOARDING_STATE);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toast, setToast] = useState("");

  const showToast = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(""), 3500);
  };

  const handleNextStep = () => {
    if (!form.url.trim()) {
      showToast("Please enter the target Website URL.");
      return;
    }

    // Auto-prepend https:// if protocol is omitted
    let formattedUrl = form.url.trim();
    if (!/^https?:\/\//i.test(formattedUrl)) {
      formattedUrl = `https://${formattedUrl}`;
      setForm((prev) => ({ ...prev, url: formattedUrl }));
    }

    setStep(2);
  };

  const handleStartTesting = async () => {
    if (!form.url.trim()) {
      showToast("Target Website URL is required.");
      setStep(1);
      return;
    }

    if (form.loginOption === "credentials") {
      if (!form.username.trim() || !form.password) {
        showToast("Please provide both Username and Password for the test account.");
        return;
      }
    }

    setIsSubmitting(true);

    try {
      const result = await submitOnboarding(form);
      navigate(`/websites/${result.website.id}`);
    } catch (error) {
      console.error(error);
      showToast(
        error instanceof Error
          ? error.message
          : "Unable to start testing. Please check backend connection."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      {toast && (
        <div className="toast">
          <span>⚠️</span>
          <span>{toast}</span>
        </div>
      )}

      <main className="main">
        <section className="hero">
          <div className="hero-pill">
            <span>⚡</span>
            <span>Next-Gen Autonomous Web Testing</span>
          </div>

          <h1>
            Forge <span className="gradient-text">Testing Agent</span>
          </h1>

          <p>
            Autonomous AI QA that explores your web application, discovers critical user journeys, and catches regressions continuously.
          </p>

          {/* Stepper Progress Bar */}
          <div className="stepper-container">
            <div
              className={`step-indicator ${step === 1 ? "active" : "completed"}`}
              onClick={() => setStep(1)}
              role="button"
              tabIndex={0}
            >
              <span className="step-num">{step > 1 ? "✓" : "1"}</span>
              <span>Step 1: Application</span>
            </div>

            <div className={`stepper-connector ${step === 2 ? "completed" : ""}`}></div>

            <div
              className={`step-indicator ${step === 2 ? "active" : ""}`}
              onClick={() => form.url && setStep(2)}
              role="button"
              tabIndex={0}
            >
              <span className="step-num">2</span>
              <span>Step 2: Credentials</span>
            </div>
          </div>

          {/* STEP 1: Add Application */}
          {step === 1 && (
            <div className="wizard-card">
              <div className="wizard-card-header">
                <h2>Step 1 — Add application</h2>
                <p>Provide your target application details and testing environment.</p>
              </div>

              <div className="input-group">
                <label htmlFor="app-name-input">Application Name</label>
                <div className="input-wrapper">
                  <span className="input-icon">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                      <line x1="8" y1="21" x2="16" y2="21" />
                      <line x1="12" y1="17" x2="12" y2="21" />
                    </svg>
                  </span>
                  <input
                    id="app-name-input"
                    type="text"
                    placeholder="e.g. My SaaS Portal"
                    value={form.appName}
                    onChange={(e) =>
                      setForm({ ...form, appName: e.target.value })
                    }
                  />
                </div>
              </div>

              <div className="input-group">
                <label htmlFor="url-input">
                  Website URL <span style={{ color: "var(--accent-forge)" }}>*</span>
                </label>
                <div className="input-wrapper">
                  <span className="input-icon">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="2" y1="12" x2="22" y2="12" />
                      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                    </svg>
                  </span>
                  <input
                    id="url-input"
                    type="url"
                    placeholder="https://your-app.com"
                    value={form.url}
                    onChange={(e) => setForm({ ...form, url: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="input-group">
                <label>Environment (Optional)</label>
                <div className="env-selector-group">
                  {(["Production", "Staging", "Testing"] as Environment[]).map(
                    (env) => (
                      <button
                        key={env}
                        type="button"
                        className={`env-pill-btn ${form.environment === env ? "active" : ""}`}
                        onClick={() => setForm({ ...form, environment: env })}
                      >
                        <span
                          className={`env-dot ${env === "Production"
                              ? "prod"
                              : env === "Staging"
                                ? "stag"
                                : "dev"
                            }`}
                        ></span>
                        <span>{env}</span>
                      </button>
                    )
                  )}
                </div>
              </div>

              <div className="wizard-actions">
                <button
                  type="button"
                  className="link-button"
                  onClick={() => navigate("/dashboard")}
                >
                  View testing dashboard →
                </button>

                <button
                  type="button"
                  className="forward-step-btn"
                  onClick={handleNextStep}
                >
                  <span>Continue to Step 2</span>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="5" y1="12" x2="19" y2="12" />
                    <polyline points="12 5 19 12 12 19" />
                  </svg>
                </button>
              </div>
            </div>
          )}

          {/* STEP 2: Test Credentials (Optional) */}
          {step === 2 && (
            <div className="wizard-card">
              <div className="wizard-card-header">
                <h2>Step 2 — Test credentials (optional)</h2>
                <p>Configure authentication access for the testing agent.</p>
              </div>

              <div className="input-group">
                <label>Does your application require login?</label>
                <div className="radio-options-list">
                  {/* Option 1: No, public pages */}
                  <div
                    className={`radio-card ${form.loginOption === "public" ? "selected" : ""
                      }`}
                    onClick={() =>
                      setForm({ ...form, loginOption: "public" })
                    }
                    role="button"
                    tabIndex={0}
                  >
                    <div className="custom-radio">
                      {form.loginOption === "public" && (
                        <div className="radio-dot"></div>
                      )}
                    </div>
                    <div className="radio-card-content">
                      <span className="radio-card-title">
                        No, I want to test public pages
                      </span>
                      <span className="radio-card-desc">
                        Crawl landing pages, marketing routes, and unauthenticated public features.
                      </span>
                    </div>
                  </div>

                  {/* Option 2: Yes, add test account */}
                  <div
                    className={`radio-card ${form.loginOption === "credentials" ? "selected" : ""
                      }`}
                    onClick={() =>
                      setForm({ ...form, loginOption: "credentials" })
                    }
                    role="button"
                    tabIndex={0}
                  >
                    <div className="custom-radio">
                      {form.loginOption === "credentials" && (
                        <div className="radio-dot"></div>
                      )}
                    </div>
                    <div className="radio-card-content">
                      <span className="radio-card-title">
                        Yes, I'll add a test account
                      </span>
                      <span className="radio-card-desc">
                        Enable autonomous login to test private dashboards, user settings, and core application flows.
                      </span>
                    </div>
                  </div>

                  {/* Option 3: Skip for now */}
                  <div
                    className={`radio-card ${form.loginOption === "skip" ? "selected" : ""
                      }`}
                    onClick={() => setForm({ ...form, loginOption: "skip" })}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="custom-radio">
                      {form.loginOption === "skip" && (
                        <div className="radio-dot"></div>
                      )}
                    </div>
                    <div className="radio-card-content">
                      <span className="radio-card-title">Skip for now</span>
                      <span className="radio-card-desc">
                        Register application now without credentials. You can add test accounts anytime later.
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Conditional Credentials Inputs when YES is selected */}
              {form.loginOption === "credentials" && (
                <div className="credentials-drawer">
                  <div className="drawer-header">
                    <span>🔑</span>
                    <span>Test Account Credentials</span>
                  </div>

                  <div className="input-group">
                    <label htmlFor="user-input">Username / Email</label>
                    <div className="input-wrapper">
                      <span className="input-icon">
                        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                          <circle cx="12" cy="7" r="4" />
                        </svg>
                      </span>
                      <input
                        id="user-input"
                        type="text"
                        placeholder="testuser@example.com"
                        value={form.username}
                        onChange={(e) =>
                          setForm({ ...form, username: e.target.value })
                        }
                      />
                    </div>
                  </div>

                  <div className="input-group">
                    <label htmlFor="pwd-input">Password</label>
                    <div className="input-wrapper">
                      <span className="input-icon">
                        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                        </svg>
                      </span>
                      <input
                        id="pwd-input"
                        type="password"
                        placeholder="••••••••••••"
                        value={form.password}
                        onChange={(e) =>
                          setForm({ ...form, password: e.target.value })
                        }
                      />
                    </div>
                  </div>

                  <div className="input-group">
                    <label htmlFor="auth-type-select">Authentication Type</label>
                    <select
                      id="auth-type-select"
                      className="select-input"
                      value={form.authType}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          authType: e.target.value as AuthType,
                        })
                      }
                    >
                      <option value="password">Standard Username & Password Form</option>
                      <option value="token">Session Bearer / API Token</option>
                      <option value="sso">Single Sign-On (OAuth / Social Login)</option>
                    </select>
                  </div>
                </div>
              )}

              <div className="wizard-actions">
                <button
                  type="button"
                  className="back-step-btn"
                  onClick={() => setStep(1)}
                  disabled={isSubmitting}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="19" y1="12" x2="5" y2="12" />
                    <polyline points="12 19 5 12 12 5" />
                  </svg>
                  <span>Back to Step 1</span>
                </button>

                <button
                  type="button"
                  className="forward-step-btn"
                  onClick={handleStartTesting}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? (
                    <>
                      <span className="card-spin"></span>
                      <span>Starting Agent...</span>
                    </>
                  ) : (
                    <>
                      <span>Start Autonomous Testing</span>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="5" y1="12" x2="19" y2="12" />
                        <polyline points="12 5 19 12 12 19" />
                      </svg>
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Feature Grid */}
          <div className="feature-grid">
            <div className="feature-card">
              <div className="feature-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
              </div>
              <h4>Autonomous Discovery</h4>
              <p>Crawls your application to map out user paths and generate resilient test suites without manual scripting.</p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
              </div>
              <h4>Self-Healing Tests</h4>
              <p>Intelligently adapts selectors and test steps dynamically when your UI changes, preventing false positives.</p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
              </div>
              <h4>Continuous Schedules</h4>
              <p>Automated periodic runs with detailed execution traces, screenshots, and instant anomaly alerting.</p>
            </div>
          </div>
        </section>
      </main>
    </>
  );
}

export default Home;
