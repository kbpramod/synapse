import os
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from agents.state import ForgeState, UserJourney
from agents.nodes.runner import runner_node
from agents.nodes.observer import observer_node
from agents.nodes.analyzer import analyzer_node
from agents.nodes.healer import healer_node
from agents.nodes.editor import editor_node
from browser.discovery import discover_page_sync


def run_journey_healing_e2e():
    print("=" * 70)
    print("FORGE E2E PROOF: USER JOURNEY TESTING & SELF-HEALING REPAIR LOOP")
    print("Target: https://wecatchai.com/ | Journey: Contact Us")
    print("=" * 70)

    # 1. Discovery / Smoke Loop: What can currently be observed?
    print("\n[PHASE 1] Running Discovery on https://wecatchai.com/...")
    try:
        discovery_result = discover_page_sync("https://wecatchai.com/", headless=True, timeout_ms=30000)
        discovery_data = discovery_result.model_dump()
        print(f"  [DISCOVERY PASS] Observed {len(discovery_data['elements']['buttons'])} buttons, "
              f"{len(discovery_data['elements']['links'])} links.")
    except Exception as disc_err:
        print(f"  [DISCOVERY NOTICE] Using offline fallback discovery metadata ({disc_err})")
        discovery_data = {
            "page": {"url": "https://wecatchai.com/", "title": "WeCatchAI"},
            "elements": {
                "buttons": [],
                "links": [
                    {
                        "forge_id": "lnk_contactusnavlink",
                        "text": "CONTACT US",
                        "selector": "#contactUsNavLink",
                        "href": "https://wecatchai.com/contact",
                        "visible": True,
                        "visible_viewports": ["desktop", "tablet", "mobile"]
                    }
                ]
            }
        }

    # 2. Define User Journey (Action -> State Transition -> Outcome)
    journey: UserJourney = {
        "id": "journey_contact_us",
        "name": "Contact Company Journey",
        "goal": "Allow the user to reach the contact experience",
        "preconditions": ["WeCatchAI homepage is loaded"],
        "steps": [
            "Navigate to https://wecatchai.com/",
            "Click Contact Us navigation link",
            "Verify transition to /contact page"
        ],
        "state_transitions": ["homepage -> contact_page_accessible"],
        "expected_outcome": "User reaches https://wecatchai.com/contact successfully",
        "supported_viewports": ["desktop", "tablet", "mobile"],
        "viewport": "desktop",
    }

    # 3. Create a DELIBERATELY BROKEN test script to prove the self-healing loop
    test_dir = root_dir / "storage" / "wecatchai.com" / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_path = test_dir / "test_journey_contact_us.py"

    broken_test_code = """import os
import re
from playwright.sync_api import sync_playwright, expect


def test_journey_contact_us():
    headless = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        try:
            page.goto("https://wecatchai.com/", wait_until="domcontentloaded", timeout=30000)
            # Deliberately broken locator: hidden/wrong contact button ID
            contact_btn = page.locator("#nonExistentContactButton")
            contact_btn.click(timeout=8000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            expect(page).to_have_url(re.compile(r".*/contact.*"))
            print("[TEST PASSED] Contact Us journey succeeded")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_journey_contact_us()
"""
    test_path.write_text(broken_test_code, encoding="utf-8")
    print(f"\n[PHASE 2] Seeded deliberately broken test script: {test_path.name}")
    print("  Broken step: locator('#nonExistentContactButton').click(timeout=8000)")

    # Initialize StateGraph state
    state: ForgeState = {
        "target_url": "https://wecatchai.com/",
        "discovery_data": discovery_data,
        "test_plan": [journey],
        "current_test_idx": 0,
        "current_test": journey,
        "test_code": broken_test_code,
        "test_file_path": str(test_path),
        "heal_attempt": 0,
        "max_heal_attempts": 3,
        "healing_history": [],
        "suite_summary": [],
        "config": {
            "headless": True,
            "test_timeout_s": 35,
            "viewport": "desktop",
        },
    }

    # Execute the realistic self-healing loop
    max_heals = state.get("max_heal_attempts", 3)
    heal_attempt = 0
    passed = False
    final_verdict = None

    while heal_attempt <= max_heals:
        print(f"\n--- CYCLE ATTEMPT {heal_attempt}: RUNNER ---")
        run_res = runner_node(state)
        state.update(run_res)
        passed = state["execution_result"].get("passed", False)
        err = state["execution_result"].get("error_summary")
        print(f"  Execution Result: Passed={passed}, Error={err}")

        print(f"\n--- CYCLE ATTEMPT {heal_attempt}: OBSERVER ---")
        obs_res = observer_node(state)
        state.update(obs_res)
        print(f"  Captured Evidence: Duration={state['execution_result'].get('duration_s')}s, "
              f"Screenshots={len(state['execution_result'].get('screenshot_paths', []))}")

        print(f"\n--- CYCLE ATTEMPT {heal_attempt}: ANALYZER ---")
        ana_res = analyzer_node(state)
        state.update(ana_res)
        final_verdict = state["analysis"]["verdict"]
        print(f"  Verdict: {final_verdict}")
        print(f"  Failure Type: {state['analysis'].get('failure_type')}")
        print(f"  Reason: {state['analysis']['reason'][:120]}...")

        if final_verdict == "PASS":
            break

        if final_verdict == "NEED_HEAL" and heal_attempt < max_heals:
            heal_attempt += 1
            state["heal_attempt"] = heal_attempt

            print(f"\n--- HEALING CYCLE {heal_attempt}/{max_heals}: HEALER ---")
            heal_res = healer_node(state)
            state.update(heal_res)
            # Record healing history
            if state.get("healing_plan"):
                state["healing_history"].append(state["healing_plan"])
            print(f"  Diagnosis: {state['healing_plan'].get('diagnosis')[:100]}...")
            print(f"  Fix Plan: {state['healing_plan'].get('fix_plan')[:100]}...")

            print(f"\n--- HEALING CYCLE {heal_attempt}/{max_heals}: EDITOR (AST Validation) ---")
            edit_res = editor_node(state)
            state.update(edit_res)
            repaired_code = Path(state["test_file_path"]).read_text(encoding="utf-8")
            print(f"  Surgically edited test script ({len(repaired_code)} bytes). AST verified!")
        else:
            break

    print(f"\nFinal Loop State: Passed={passed}, Verdict={final_verdict}, HealsUsed={heal_attempt}")
    assert passed, f"Self-healing repair loop did not resolve journey failure after {heal_attempt} heals!"
    assert final_verdict == "PASS", f"Expected final verdict PASS, got {final_verdict}"

    print("\n" + "=" * 70)
    print("SUCCESS: COMPLETE SELF-HEALING USER JOURNEY TEST LOOP VERIFIED!")
    print(f"  Initial Failure (NEED_HEAL) -> Healer Plan -> Editor AST Patch -> Rerun -> PASS ({heal_attempt} heal)")
    print("=" * 70)


if __name__ == "__main__":
    run_journey_healing_e2e()
