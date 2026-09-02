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
from db.repository import ForgeRepository
from browser.discovery import discover_page_sync
from schemas.discovery import StateInfo
from agents.nodes.understanding import understanding_node
from agents.nodes.planner import planner_node
from agents.nodes.builder import builder_node
from runner.playwright_runner import run_test_script
from storage.local import sanitize_domain, get_website_storage_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("forge.stage")


def run_discover(url: str, headed: bool = False):
    print(f"\n[STAGE: DISCOVER] Running browser discovery for: {url}")
    init_db()
    domain = sanitize_domain(url)

    result = discover_page_sync(
        url=url,
        state_info=StateInfo(name="default", role="guest"),
        headless=not headed,
        save_to_storage=True,
    )
    data = result.model_dump()
    ForgeRepository.upsert_website(domain, url)
    ForgeRepository.record_page_discovery(domain, data.get("page", {}))

    # Flatten and index elements
    all_elements = []
    for category in ("buttons", "inputs", "links", "textareas", "selects"):
        all_elements.extend(data.get("elements", {}).get(category, []))
    ForgeRepository.record_elements(url, all_elements)

    print(f"[DISCOVER COMPLETE] Buttons={len(data['elements']['buttons'])}, Inputs={len(data['elements']['inputs'])}, Links={len(data['elements']['links'])}")
    print(f"Indexed into Neon PostgreSQL (schema: forge)")
    return data


def run_understand(url: str):
    print(f"\n[STAGE: UNDERSTAND] Synthesizing page understanding for: {url}")
    domain = sanitize_domain(url)
    disc_file = get_website_storage_dir(url) / "discovery" / "discovery.json"
    if not disc_file.exists():
        print(f"Discovery data not found on disk. Running discovery first...")
        disc_data = run_discover(url)
    else:
        with open(disc_file, "r", encoding="utf-8") as f:
            disc_data = json.load(f)

    state = {"target_url": url, "discovery_data": disc_data}
    res = understanding_node(state)
    und = res.get("page_understanding", {})

    ForgeRepository.record_page_discovery(domain, disc_data.get("page", {}), understanding=und)
    print(f"[UNDERSTAND COMPLETE] Page Type: '{und.get('page_type')}', Purpose: '{und.get('purpose')}'")
    return und


def run_plan(url: str):
    print(f"\n[STAGE: PLAN] Generating test scenarios for: {url}")
    domain = sanitize_domain(url)
    und = run_understand(url)
    disc_file = get_website_storage_dir(url) / "discovery" / "discovery.json"
    with open(disc_file, "r", encoding="utf-8") as f:
        disc_data = json.load(f)

    state = {"target_url": url, "discovery_data": disc_data, "page_understanding": und}
    res = planner_node(state)
    plan = res.get("test_plan", [])

    for scenario in plan:
        ForgeRepository.save_test(
            test_id=scenario["id"],
            domain=domain,
            page_url=url,
            title=scenario.get("title", scenario["id"]),
            description=scenario.get("description", ""),
            category=scenario.get("category", "regression"),
            priority=scenario.get("priority", "medium"),
            steps=scenario.get("steps", []),
            expected_outcome=scenario.get("expected_outcome", ""),
        )

    print(f"[PLAN COMPLETE] Generated {len(plan)} test scenario(s):")
    for t in plan:
        print(f"  - [{t.get('priority', '').upper()}] {t['id']}: {t.get('title')}")
    return plan


def run_build(url: str, lang: str = "python"):
    print(f"\n[STAGE: BUILD] Synthesizing Playwright {lang.upper()} test scripts for: {url}")
    domain = sanitize_domain(url)
    active_tests = ForgeRepository.get_active_tests(domain=domain)
    if not active_tests:
        print(f"No active tests found for {domain}. Running planner first...")
        run_plan(url)
        active_tests = ForgeRepository.get_active_tests(domain=domain)

    disc_file = get_website_storage_dir(url) / "discovery" / "discovery.json"
    with open(disc_file, "r", encoding="utf-8") as f:
        disc_data = json.load(f)

    for test_record in active_tests:
        state = {
            "target_url": url,
            "discovery_data": disc_data,
            "current_test": test_record,
            "heal_attempt": 0,
            "config": {"language": lang},
        }
        res = builder_node(state)
        print(f"  [BUILT] {test_record['test_id']} -> {res.get('test_file_path')}")


def run_tests_stage(url: str, headed: bool = False):
    domain = sanitize_domain(url)
    print(f"\n[STAGE: RUN] Executing Playwright tests for domain: {domain} (headed={headed})")
    tests = ForgeRepository.get_active_tests(domain=domain)
    if not tests:
        print(f"No tests found for domain {domain}. Build tests first.")
        return

    for t in tests:
        path = t.get("script_path")
        if not path or not Path(path).exists():
            print(f"  [MISSING SCRIPT] {t['test_id']}: {path}")
            continue
        print(f"  Executing {t['test_id']}...")
        res = run_test_script(path, headed=headed)
        status = "PASSED" if res["passed"] else "FAILED"
        print(f"    --> {status} ({res['duration_s']}s) Exit={res['exit_code']}")


def main():
    parser = argparse.ArgumentParser(description="Forge Stage CLI - Run Individual Pipeline Stages")
    parser.add_argument("--stage", required=True, choices=["discover", "understand", "plan", "build", "run", "all"], help="Stage to execute")
    parser.add_argument("--url", default="https://example.com", help="Target URL")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible/headed mode")
    parser.add_argument(
        "--lang",
        choices=["python", "typescript"],
        default=os.getenv("FORGE_TEST_LANGUAGE", "python"),
        help="Test script language (default: python)"
    )
    args = parser.parse_args()

    init_db()

    is_headed = args.headed or (not is_headless())

    if args.stage == "discover":
        run_discover(args.url, headed=is_headed)
    elif args.stage == "understand":
        run_understand(args.url)
    elif args.stage == "plan":
        run_plan(args.url)
    elif args.stage == "build":
        run_build(args.url, lang=args.lang)
    elif args.stage == "run":
        run_tests_stage(args.url, headed=is_headed)
    elif args.stage == "all":
        run_discover(args.url, headed=is_headed)
        run_understand(args.url)
        run_plan(args.url)
        run_build(args.url, lang=args.lang)
        run_tests_stage(args.url, headed=is_headed)


if __name__ == "__main__":
    main()
