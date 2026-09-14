import os
import sys
import runpy
import re
import json
import logging
from pathlib import Path
from typing import List

# Ensure src/ is on sys.path for intra-repo imports
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

logger = logging.getLogger("forge.runner.harness")

# Semantic error selector: explicitly targets error banners/validation feedback,
# avoiding generic .alert, [role='alert'], .alert-info, and .alert-success.
SEMANTIC_ERROR_SELECTOR = (
    ".alert-danger, .error-message, .error-msg, "
    ".error:not(input):not(form):not(.alert), "
    ".invalid-feedback, [data-test='error'], [data-testid='error'], "
    "[aria-invalid='true'], .text-danger"
)

# Text patterns that indicate false positives (success banners, info messages)
FALSE_POSITIVE_TEXT_RE = re.compile(
    r"\b(success|successfully|subscribed|contact us|get in touch|feedback for us|subscription|automationexercise)\b",
    re.I,
)


def install_playwright_harness(test_file_path: str):
    """
    Hooks Playwright BrowserContext.close to manage lifecycle telemetry:
    - On Success: Automatically persists context.storage_state.
    - On Failure: Intercepts active exception unwinding to record [FAILURE_URL],
      capture failure screenshot, and inspect semantic error elements.
    """
    try:
        from playwright.sync_api import BrowserContext
    except ImportError:
        return

    test_stem = os.path.splitext(os.path.abspath(test_file_path))[0]
    screenshot_path = f"{test_stem}_failure.png"
    storage_path = f"{test_stem}.storage_state.json"

    orig_close = BrowserContext.close

    def hooked_close(self, *args, **kwargs):
        exc_type, exc_val, exc_tb = sys.exc_info()

        if exc_type is not None:
            # An active exception is unwinding through finally block
            for page in getattr(self, "pages", []):
                try:
                    current_url = page.url
                    if current_url:
                        print(f"[FAILURE_URL] {current_url}")

                    # Semantic error extraction: strictly target real errors
                    raw_errors: List[str] = []
                    try:
                        raw_errors = [
                            t.strip() for t in page.locator(SEMANTIC_ERROR_SELECTOR).all_inner_texts()
                            if t and t.strip()
                        ]
                    except Exception:
                        pass

                    # Filter out any lingering false-positive text (e.g. success banners)
                    clean_errors = [
                        err for err in raw_errors
                        if not FALSE_POSITIVE_TEXT_RE.search(err)
                    ]
                    if clean_errors:
                        print(f"[VISIBLE_ERRORS] {json.dumps(clean_errors[:5])}")

                    # Capture failure screenshot
                    page.screenshot(path=screenshot_path)
                except Exception:
                    pass
        else:
            # Passing test run: capture storage state if possible
            try:
                self.storage_state(path=storage_path)
            except Exception:
                pass

        return orig_close(self, *args, **kwargs)

    BrowserContext.close = hooked_close


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m runner.harness <test_script.py>")
        sys.exit(1)

    test_file = sys.argv[1]
    if not os.path.exists(test_file):
        print(f"Test script not found: {test_file}", file=sys.stderr)
        sys.exit(1)

    install_playwright_harness(test_file)

    # Set __file__ and run as __main__
    sys.argv = [test_file] + sys.argv[2:]
    runpy.run_path(test_file, run_name="__main__")


if __name__ == "__main__":
    main()
