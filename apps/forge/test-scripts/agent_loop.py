import os
import sys
import json
import logging
import argparse
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from config import is_headless
from db.migrations import init_db
from agents.graph import create_forge_graph
from agents.state import ForgeState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("forge.cli")


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title.center(76)}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Forge Autonomous Testing Agent Loop")
    parser.add_argument("url", nargs="?", default="https://example.com", help="Target URL to test")
    parser.add_argument("--headed", action="store_true", help="Launch browser visibly (headless=False)")
    parser.add_argument(
        "--headless",
        type=lambda v: str(v).lower() in ("true", "1", "yes"),
        default=None,
        help="Explicit headless setting (true/false)"
    )
    parser.add_argument("--timeout", type=int, default=40, help="Test execution timeout in seconds")
    parser.add_argument("--max-heals", type=int, default=2, help="Max self-healing attempts")
    parser.add_argument(
        "--lang", "--language",
        choices=["python"],
        default=os.getenv("FORGE_TEST_LANGUAGE", "python"),
        help="Test script language: python (default)"
    )
    parser.add_argument(
        "--viewport",
        choices=["desktop", "tablet", "mobile"],
        default="desktop",
        help="Target viewport profile: desktop (1280x800, default), tablet (768x1024), or mobile (375x667)"
    )
    args = parser.parse_args()

    target_url = args.url
    print_banner(f"FORGE AUTONOMOUS TESTING AGENT: {target_url} [viewport: {args.viewport}]")

    if args.headed:
        headless_mode = False
    elif args.headless is not None:
        headless_mode = args.headless
    else:
        headless_mode = is_headless()

    try:
        init_db()
    except Exception as db_err:
        logger.warning(f"Database initialization notice: {db_err}")

    config = {
        "headless": headless_mode,
        "language": args.lang,
        "viewport": args.viewport,
        "timeout_ms": 25000,
        "settle_ms": 1000,
        "test_timeout_s": args.timeout,
        "max_heal_attempts": args.max_heals,
    }

    initial_state: ForgeState = {
        "target_url": target_url,
        "config": config,
        "max_heal_attempts": config["max_heal_attempts"],
        "suite_summary": [],
    }

    logger.info("Compiling Forge Agent StateGraph...")
    graph = create_forge_graph()

    logger.info("Starting Agent execution cycle...")

    step_counter = 1
    final_state = None

    for output in graph.stream(initial_state):
        for node_name, state_update in output.items():
            print(f"\n[{step_counter:02d}] >>> EXECUTED NODE: {node_name.upper()} <<<")
            step_counter += 1

            if node_name == "discover":
                disc = state_update.get("discovery_data", {})
                page = disc.get("page", {})
                elements = disc.get("elements", {})
                print(f"    Target Title : {page.get('title')}")
                print(f"    Elements     : Buttons={len(elements.get('buttons', []))}, "
                      f"Inputs={len(elements.get('inputs', []))}, Links={len(elements.get('links', []))}")

            elif node_name == "understanding":
                und = state_update.get("page_understanding", {})
                print(f"    Page Type    : {und.get('page_type')}")
                print(f"    Purpose      : {und.get('purpose')}")
                print(f"    Actions      : {', '.join(und.get('primary_actions', []))}")

            elif node_name == "planner":
                plan = state_update.get("test_plan", [])
                print(f"    Planned Tests: {len(plan)}")
                for t in plan:
                    print(f"      - [{t.get('priority', '').upper()}] {t.get('id')}: {t.get('title')}")

            elif node_name == "builder":
                print(f"    Script Path  : {state_update.get('test_file_path')}")

            elif node_name == "runner":
                res = state_update.get("execution_result", {})
                status_str = "PASSED" if res.get("passed") else f"FAILED (exit {res.get('exit_code')})"
                print(f"    Run Result   : {status_str} in {res.get('duration_s')}s")

            elif node_name == "observer":
                res = state_update.get("execution_result", {})
                screenshots = res.get("screenshot_paths", [])
                if screenshots:
                    print(f"    Screenshots  : {len(screenshots)} captured")

            elif node_name == "analyzer":
                analysis = state_update.get("analysis", {})
                print(f"    Verdict      : {analysis.get('verdict')}")
                print(f"    Reason       : {analysis.get('reason')}")
                if analysis.get("suggested_fix"):
                    print(f"    Fix Idea     : {analysis.get('suggested_fix')}")

            elif node_name == "healer":
                history = state_update.get("healing_history", [])
                latest = history[-1] if history else {}
                print(f"    Heal Attempt : #{state_update.get('heal_attempt')}")
                print(f"    Diagnosis    : {latest.get('diagnosis')}")
                print(f"    Fix Plan     : {latest.get('fix_plan')}")

            elif node_name == "editor":
                print(f"    Edited Script: {state_update.get('test_file_path')}")

            elif node_name == "advance_test":
                curr = state_update.get("current_test", {})
                print(f"    Next Test    : {curr.get('id')} ({curr.get('title')})")

            final_state = state_update

    print_banner("AGENT EXECUTION COMPLETE")
    suite = final_state.get("suite_summary", []) if final_state else []
    print(f"Total Test Scenarios Completed: {len(suite)}")
    for item in suite:
        status_icon = "[PASS]" if item.get("status") == "PASSED" else "[FAIL]"
        print(f"  {status_icon} [{item.get('status')}] {item.get('id')} (Heals needed: {item.get('heals_needed')})")
    print("\n")


if __name__ == "__main__":
    main()
