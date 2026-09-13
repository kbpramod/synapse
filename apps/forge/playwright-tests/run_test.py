"""
Manual Test Runner for Playwright Tests in Forge.

Usage:
    uv run python playwright-tests/run_test.py
    uv run python playwright-tests/run_test.py playwright-tests/test_001.py
    uv run python playwright-tests/run_test.py playwright-tests/test_001.py --headless
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run a Playwright test script directly.")
    parser.add_argument(
        "test_path",
        nargs="?",
        default="playwright-tests/test_001.py",
        help="Path to the test script (default: playwright-tests/test_001.py).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: headed/visible window).",
    )
    args = parser.parse_args()

    target_file = Path(args.test_path)
    if not target_file.exists():
        print(f"[ERROR] Test script not found: {target_file}")
        sys.exit(1)

    env = os.environ.copy()
    env["HEADLESS"] = "true" if args.headless else "false"

    print(f"==================================================")
    print(f" Running: {target_file}")
    print(f" Mode   : {'HEADLESS' if args.headless else 'HEADED (visible browser)'}")
    print(f"==================================================\n")

    result = subprocess.run(
        [sys.executable, str(target_file)],
        env=env,
    )

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
