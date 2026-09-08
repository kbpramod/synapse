const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type Website = {
  id: number;
  url: string;
  domain: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_discovered_at: string | null;
};

export type Account = {
  id: number;
  website_id: number;
  username: string;
  role: string;
  credentials: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type OnboardingResponse = {
  status: string;
  message: string;
  website: Website;
  accounts: Account[];
};

export type TestSchedule = {
  test_id: string;
  title: string;
  domain: string;
  cron_interval_hours: number;
  cron_expression: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  is_due: boolean;
  status: string | null;
};

export type ScheduleResponse = {
  total_active: number;
  due_count: number;
  schedules: TestSchedule[];
};

export type SuiteResultStatus =
  | "PASSED"
  | "CONFIRMED_BUG"
  | "FAILED_AUTOMATION"
  | "SUSPECTED_APP_FAILURE";

export type SuiteResult = {
  id: string;
  title: string | null;
  status: SuiteResultStatus;
  error?: string;
  incident_id?: string | null;
  heals_needed?: number;
  duration_s?: number;
};

export type CronRunResult = {
  status: "completed" | "idle";
  due_count: number;
  executed_count: number;
  passed_count?: number;
  bug_count?: number;
  failed_count?: number;
  duration_seconds?: number;
  started_at: string;
  completed_at: string;
  suite_summary: SuiteResult[];
};

export type OnboardingDetails = {
  website: Website;
  accounts: Account[];
  account_count: number;
};

export type TestRun = {
  run_id: string;
  test_id: string;
  title: string;
  status: string;
  exit_code: number;
  duration_s: number;
  error_summary: string | null;
  executed_at: string;
};

export type WebsiteRuns = {
  website_id: number;
  domain: string;
  count: number;
  runs: TestRun[];
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(`Cannot reach the API at ${BASE_URL}.`, 0);
  }

  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }

  return response.json() as Promise<T>;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;

    if (typeof detail === "string") {
      return detail;
    }

    // FastAPI validation errors arrive as a list of {loc, msg, type}
    if (Array.isArray(detail)) {
      return detail.map((d) => d?.msg).filter(Boolean).join(", ");
    }
  } catch {
    // fall through to the generic message
  }

  return `Request failed with status ${response.status}.`;
}

export function onboardWebsite(payload: {
  url: string;
  accounts: { username: string; password: string; role?: string }[];
}): Promise<OnboardingResponse> {
  return request<OnboardingResponse>("/api/onboarding/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listWebsites(activeOnly = false): Promise<Website[]> {
  return request<Website[]>(`/api/website/?active_only=${activeOnly}`);
}

export function getWebsite(websiteId: number): Promise<Website> {
  return request<Website>(`/api/website/${websiteId}`);
}

export function getWebsiteRuns(websiteId: number, limit = 50): Promise<WebsiteRuns> {
  return request<WebsiteRuns>(`/api/website/${websiteId}/runs?limit=${limit}`);
}

export function deleteWebsite(websiteId: number): Promise<{ status: string; website_id: number }> {
  return request<{ status: string; website_id: number }>(`/api/website/${websiteId}`, {
    method: "DELETE",
  });
}

export function getOnboardingDetails(websiteId: number): Promise<OnboardingDetails> {
  return request<OnboardingDetails>(`/api/onboarding/${websiteId}`);
}

export function addAccount(
  websiteId: number,
  account: { username: string; password: string; role?: string }
): Promise<Account> {
  return request<Account>(`/api/onboarding/${websiteId}/accounts`, {
    method: "POST",
    body: JSON.stringify(account),
  });
}

export function runDiscovery(websiteId: number): Promise<{
  status: string;
  website_id: number;
  url: string;
  message: string;
}> {
  return request(`/api/onboarding/${websiteId}/discover`, { method: "POST" });
}

/** SSE stream of onboarding/discovery progress, consumed with EventSource. */
export function onboardingEventsUrl(websiteId: number): string {
  return `${BASE_URL}/api/onboarding/${websiteId}/events`;
}

export function getTestSchedules(domain?: string): Promise<ScheduleResponse> {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return request<ScheduleResponse>(`/api/cron/schedule${query}`);
}

export type RunTestResult = {
  status: "completed" | "not_found";
  test_id: string;
  result: SuiteResult;
  incident_reports: unknown[];
  started_at: string;
  completed_at: string;
  duration_seconds: number;
};

export function runTestNow(testId: string, headless = false): Promise<RunTestResult> {
  return request<RunTestResult>(`/api/cron/run/${encodeURIComponent(testId)}`, {
    method: "POST",
    body: JSON.stringify({ headless }),
  });
}

export function runCronCycle(payload: {
  domain?: string;
  headless?: boolean;
  limit?: number;
}): Promise<CronRunResult> {
  return request<CronRunResult>("/api/cron/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getDaemonStatus(): Promise<{ daemon_running: boolean }> {
  return request<{ daemon_running: boolean }>("/api/cron/daemon/status");
}

export function startDaemon(payload: {
  interval_seconds?: number;
  domain?: string;
  headless?: boolean;
}): Promise<{ status: string }> {
  return request<{ status: string }>("/api/cron/daemon/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function stopDaemon(): Promise<{ status: string; message: string }> {
  return request<{ status: string; message: string }>("/api/cron/daemon/stop", {
    method: "POST",
  });
}
