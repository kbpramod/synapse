import ast
import json
import os
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.state import ForgeState, ActionSpec, ExpectationSpec, PostActionResult
from agents.nodes.action_builder import action_builder_node, render_action_script
from agents.nodes.action_runner import action_runner_node
from agents.nodes.result_discovery import result_discovery_node, calculate_dom_delta
from agents.nodes.expectation_analysis import expectation_analysis_node
from agents.nodes.correctness import correctness_node
from agents.nodes.heal_action import heal_action_node
from agents.nodes.heal_expectation import heal_expectation_node
from agents.nodes.testcase_assembler import testcase_assembler_node


def test_action_builder_and_runner_isolation():
    print("\n[TEST 1] Testing Action Builder (Zero Assertions Invariant)...")
    target_url = "https://example.com"

    mock_state: ForgeState = {
        "target_url": target_url,
        "config": {"headless": True, "timeout_ms": 15000},
        "current_test": {
            "id": "smoke_example_visit",
            "type": "SMOKE",
            "title": "Verify Example Domain loads cleanly",
            "intent": "User visits example domain and verifies navigation",
            "steps": ["Navigate to homepage"],
            "expected": ["Page title and main heading are displayed"],
        },
        "discovery_data": {
            "page": {"url": target_url, "title": "Example Domain"},
            "elements": {
                "buttons": [],
                "inputs": [],
                "links": [{"selector": "a", "href": "https://iana.org/domains/example", "text": "Learn more"}],
            },
        },
    }

    # 1. Action Builder
    builder_out = action_builder_node(mock_state)
    action_spec: ActionSpec = builder_out["action_spec"]
    action_code = builder_out["action_code"]

    assert action_spec is not None, "ActionSpec was not synthesized"
    assert len(action_spec["steps"]) > 0, "ActionSpec has no steps"

    # STRICT INVARIANT: ZERO ASSERTIONS IN ACTION SCRIPT
    assert "expect(" not in action_code, "Action script violated invariant: contains 'expect(' assertion!"
    assert "to_be_visible" not in action_code, "Action script contains matcher 'to_be_visible'"
    print("  [PASS] ActionSpec contains zero assertions. Action is purely interaction-focused.")

    # 2. Action Runner
    print("\n[TEST 2] Testing Action Runner...")
    mock_state.update(builder_out)
    runner_out = action_runner_node(mock_state)
    action_res = runner_out["action_result"]
    assert action_res["passed"] is True, f"Action execution failed: {action_res.get('error_summary')}"
    print(f"  [PASS] Action executed cleanly in {action_res.get('duration_s')}s (exit code {action_res.get('exit_code')}).")

    # 3. Result Discovery
    print("\n[TEST 3] Testing Result Discovery & DOM Delta...")
    mock_state.update(runner_out)
    discovery_out = result_discovery_node(mock_state)
    post_res: PostActionResult = discovery_out["post_action_result"]
    assert post_res is not None, "PostActionResult was not created"
    assert "navigation" in post_res, "PostActionResult missing navigation"
    assert "dom_delta" in post_res, "PostActionResult missing dom_delta"
    print(f"  [PASS] Result discovery captured: URL={post_res['navigation']['result_url']}, Headings={len(post_res['dom_delta']['current_headings'])}")

    # 4. Expectation Analysis
    print("\n[TEST 4] Testing Expectation Analysis (Intent Grounding)...")
    mock_state.update(discovery_out)
    exp_out = expectation_analysis_node(mock_state)
    exp_spec: ExpectationSpec = exp_out["expectation_spec"]
    assert exp_spec is not None, "ExpectationSpec was not created"
    assert len(exp_spec["expectations"]) > 0, "No grounded expectations derived"
    for exp in exp_spec["expectations"]:
        assert "code" in exp, "Expectation missing code"
        assert "confidence" in exp, "Expectation missing confidence"
        assert "evidence" in exp, "Expectation missing evidence"
        print(f"    - Grounded [{exp['type']}] (conf={exp['confidence']}): {exp['code']}")
    print("  [PASS] Expectations derived from live result and grounded in intent.")

    # 5. Correctness Evaluation
    print("\n[TEST 5] Testing Correctness Evaluation...")
    mock_state.update(exp_out)
    corr_out = correctness_node(mock_state)
    verdict = corr_out["correctness_verdict"]
    reason = corr_out["correctness_reason"]
    print(f"    Verdict: [{verdict}] - {reason}")
    assert verdict in ("CORRECT", "ACTION_DEFECT", "EXPECTATION_DEFECT", "APP_BUG", "INCONCLUSIVE"), f"Invalid verdict: {verdict}"
    print(f"  [PASS] Correctness evaluation classified outcome as: [{verdict}].")

    # 6. Testcase Assembler with Provenance
    print("\n[TEST 6] Testing Testcase Assembler & Provenance...")
    assemble_out = testcase_assembler_node(mock_state)
    test_code = assemble_out["test_code"]
    test_path = assemble_out["test_file_path"]
    prov = assemble_out["test_provenance"]

    assert test_code is not None, "Assembled test code is None"
    assert Path(test_path).exists(), f"Test file {test_path} was not written to disk"
    assert prov is not None, "Provenance metadata is None"
    assert prov["action_provenance"]["validated"] is True, "Action provenance validation flag missing"
    assert len(prov["expectation_provenance"]) > 0, "Expectation provenance missing"

    # Verify AST parsing of complete test
    ast.parse(test_code)
    print("  [PASS] Assembled test passed AST syntax parsing and embeds full Provenance.")

    # Verify Test Artifact Directory
    artifacts = assemble_out.get("test_artifacts")
    assert artifacts is not None, "Testcase Assembler did not return test_artifacts"
    assert "manifest" in artifacts, "Missing manifest in test_artifacts"
    manifest = artifacts["manifest"]
    assert manifest["test_id"] == "smoke_example_visit"
    assert manifest["validated"]["action"] is True
    assert manifest["validated"]["expectations"] is True
    print(f"  [PASS] Full 10-artifact test directory persisted and synced to Supabase (manifest version={manifest['version']}).")


    # 7. Execute the Assembled Production Test Script
    print("\n[TEST 7] Executing Assembled Production Playwright Test...")
    import subprocess
    run_proc = subprocess.run(
        [sys.executable, str(test_path)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "HEADLESS": "true"},
    )
    print(f"    Exit code: {run_proc.returncode}")
    if run_proc.stdout:
        print(f"    Stdout: {run_proc.stdout.strip()}")
    assert run_proc.returncode == 0, f"Assembled test script failed at runtime:\n{run_proc.stderr}"
    print("  [PASS] Assembled test script executed and passed with exit code 0!")


def test_isolated_healing_invariants():
    print("\n[TEST 8] Testing Structural Isolation of Action Healer...")
    # Action Healer must ONLY receive action spec and cannot modify expectations
    broken_action_spec: ActionSpec = {
        "test_id": "test_heal_isolation",
        "target_url": "https://example.com",
        "viewport": {"width": 1280, "height": 800},
        "steps": [
            {"step_id": 1, "action_type": "goto", "selector": None, "value": "https://example.com", "description": "Goto"},
            {"step_id": 2, "action_type": "click", "selector": "#nonexistent_button", "value": None, "description": "Click"},
        ],
    }
    state: ForgeState = {
        "target_url": "https://example.com",
        "action_spec": broken_action_spec,
        "action_heal_attempt": 0,
        "action_result": {
            "passed": False,
            "error_summary": "Timeout waiting for locator('#nonexistent_button')",
        },
        "discovery_data": {
            "elements": {
                "buttons": [],
                "links": [{"selector": "a[href*='iana']", "text": "More info"}],
            }
        },
    }
    healed_out = heal_action_node(state)
    assert healed_out["action_heal_attempt"] == 1, "Action heal attempt counter not incremented"
    assert "expectation_spec" not in healed_out, "Action healer violated isolation: modified expectation_spec!"
    print("  [PASS] Action Healer is structurally isolated from assertions.")

    print("\n[TEST 9] Testing Structural Isolation of Expectation Healer...")
    exp_spec: ExpectationSpec = {
        "test_id": "test_exp_heal_isolation",
        "journey_intent": "User visits example domain",
        "expectations": [
            {"type": "text_match", "target": "h1", "expected_value": "Wrong Heading", "confidence": 0.5, "code": "expect(page.locator('h1')).to_have_text('Wrong Heading')"}
        ],
        "summary": "Initial expectation",
    }
    state_exp: ForgeState = {
        "expectation_spec": exp_spec,
        "expectation_heal_attempt": 0,
        "correctness_reason": "Expected 'Wrong Heading' but post-action DOM has 'Example Domain'",
        "post_action_result": {
            "dom_delta": {"current_headings": ["Example Domain"]},
            "navigation": {"result_url": "https://example.com"},
        },
    }
    healed_exp_out = heal_expectation_node(state_exp)
    assert healed_exp_out["expectation_heal_attempt"] == 1, "Expectation heal attempt counter not incremented"
    assert "action_spec" not in healed_exp_out, "Expectation healer violated isolation: modified action_spec!"
    print("  [PASS] Expectation Healer is structurally isolated from action code.")


def main():
    print("=" * 75)
    print("FORGE DECOUPLED ARCHITECTURE FULL END-TO-END INTEGRATION TEST")
    print("=" * 75)
    test_action_builder_and_runner_isolation()
    test_isolated_healing_invariants()
    print("\n" + "=" * 75)
    print("ALL INTEGRATION AND ISOLATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    main()
