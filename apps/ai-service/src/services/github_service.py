import json
import os
import time
import urllib.request
import jwt


def get_github_app_jwt() -> str:
    """Generates RS256 signed JWT for GitHub App authentication."""
    app_id = os.getenv("GITHUB_APP_ID")
    pem_path = os.getenv("GITHUB_PRIVATE_KEY_PATH", "tzylo-synapse.2026-04-26.private-key.pem")

    # If pem_path is relative, resolve from current working directory or ai-service root
    if not os.path.isabs(pem_path):
        possible_paths = [
            pem_path,
            os.path.join(os.getcwd(), pem_path),
            os.path.join(os.path.dirname(__file__), "..", "..", pem_path)
        ]
        for p in possible_paths:
            if os.path.exists(p):
                pem_path = p
                break

    if not os.path.exists(pem_path):
        raise FileNotFoundError(f"GitHub App private key not found at {pem_path}")

    with open(pem_path, "r", encoding="utf-8") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": str(app_id) if app_id else "1"
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_access_token(installation_id: str) -> str:
    """Obtains temporary Installation Access Token from GitHub API."""
    app_jwt = get_github_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tzylo-App"
        }
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["token"]
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not get installation token for {installation_id}: {exc}")
        return ""


def fetch_installation_repositories(installation_id: str) -> list[dict]:
    """Fetches list of all repositories granted to this GitHub installation."""
    token = get_installation_access_token(installation_id)
    if not token:
        return []

    url = "https://api.github.com/installation/repositories"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tzylo-App"
        }
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("repositories", [])
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not fetch installation repositories: {exc}")
        return []
