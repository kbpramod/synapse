import {
    onboardWebsite,
    type OnboardingResponse,
    type Website,
    type Account,
} from "./client";

export type Environment = "Production" | "Staging" | "Testing";

export type LoginOption = "public" | "credentials" | "skip";

export type AuthType = "password" | "token" | "sso";

export interface OnboardingFormState {
    // Step 1: Application Details
    appName: string;
    url: string;
    environment: Environment;

    // Step 2: Credentials
    loginOption: LoginOption;
    username: string;
    password: string;
    authType: AuthType;
}

export const DEFAULT_ONBOARDING_STATE: OnboardingFormState = {
    appName: "",
    url: "",
    environment: "Production",
    loginOption: "credentials",
    username: "",
    password: "",
    authType: "password",
};

/**
 * Executes onboarding based on multi-step wizard state.
 * If credentials option is chosen, accounts are registered with the website.
 * If public or skip is chosen, the website is onboarded without initial credentials.
 */
export async function submitOnboarding(
    state: OnboardingFormState
): Promise<OnboardingResponse> {
    const accounts: {
        username: string;
        password: string;
        role?: string;
        credentials?: Record<string, unknown>;
    }[] = [];

    if (state.loginOption === "credentials") {
        if (!state.username || !state.password) {
            throw new Error("Username and password are required when adding a test account.");
        }

        accounts.push({
            username: state.username.trim(),
            password: state.password,
            role: "user",
            credentials: {
                app_name: state.appName.trim() || undefined,
                environment: state.environment,
                auth_type: state.authType,
            },
        });
    }

    return onboardWebsite({
        url: state.url.trim(),
        accounts,
    });
}

export type { OnboardingResponse, Website, Account };
