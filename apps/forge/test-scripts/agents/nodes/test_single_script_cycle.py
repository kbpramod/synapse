import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent.parent.parent
src_dir = root_dir / "src"

if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.nodes.runner import runner_node
from agents.nodes.observer import observer_node
from agents.nodes.analyzer import analyzer_node
from agents.nodes.healer import healer_node
from agents.nodes.editor import editor_node


def main():
    # ---------------------------------------------------------------
    # TEST CONFIGURATION
    # ---------------------------------------------------------------

    test_file_path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(
            r"D:\Pramod\Tzylo\forge\wecatchai.com\tests\test_page_smoke.py"
        )
    )

    if not test_file_path.exists():
        raise FileNotFoundError(
            f"Test file does not exist: {test_file_path}"
        )

    target_url = "https://wecatchai.com"

    state = {
        "test_file_path": str(test_file_path),

        "target_url": target_url,

        "current_test": {
            "id": "test_page_smoke",
            "title": "WeCatchAI Smoke Test",
        },

        "config": {
            "test_timeout_s": 45,
            "headless": False,
        },

        "heal_attempt": 0,
        "max_heal_attempts": 3,
        "healing_history": [],
        "suite_summary": [],

        # Editor needs the existing source code.
        "test_code": test_file_path.read_text(encoding="utf-8"),
    }

    print("=" * 80)
    print("FORGE SINGLE TEST HEALING CYCLE")
    print("=" * 80)

    # ---------------------------------------------------------------
    # 1. RUNNER
    # ---------------------------------------------------------------

    print("\n" + "=" * 80)
    print("1. RUNNER")
    print("=" * 80)

    result = runner_node(state)
    state.update(result)

    print("\nRunner result:")
    print(state["execution_result"])

    # ---------------------------------------------------------------
    # 2. OBSERVER
    # ---------------------------------------------------------------

    print("\n" + "=" * 80)
    print("2. OBSERVER")
    print("=" * 80)

    result = observer_node(state)
    state.update(result)

    print("\nObserver result:")
    print(state["execution_result"])

    # ---------------------------------------------------------------
    # 3. ANALYZER
    # ---------------------------------------------------------------

    print("\n" + "=" * 80)
    print("3. ANALYZER")
    print("=" * 80)

    result = analyzer_node(state)
    state.update(result)

    print("\nAnalyzer result:")
    print(state["analysis"])

    # ---------------------------------------------------------------
    # 4. HEALER
    # ---------------------------------------------------------------

    if state["analysis"].get("verdict") == "NEED_HEAL":

        print("\n" + "=" * 80)
        print("4. HEALER")
        print("=" * 80)

        result = healer_node(state)
        state.update(result)

        print("\nHealing history:")
        print(state.get("healing_history"))

        print("\nHealing plan:")
        print(state.get("healing_plan"))

        # -----------------------------------------------------------
        # 5. EDITOR
        # -----------------------------------------------------------

        print("\n" + "=" * 80)
        print("5. EDITOR")
        print("=" * 80)

        result = editor_node(state)
        state.update(result)

        print("\nEditor result:")
        print(f"Test file: {state.get('test_file_path')}")

        # Keep state in sync with the edited file.
        state["test_code"] = state.get("test_code", "")

        # -----------------------------------------------------------
        # 6. RUNNER AFTER HEAL
        # -----------------------------------------------------------

        print("\n" + "=" * 80)
        print("6. RUNNER - AFTER HEAL")
        print("=" * 80)

        result = runner_node(state)
        state.update(result)

        print("\nRunner result:")
        print(state["execution_result"])

        # -----------------------------------------------------------
        # 7. OBSERVER AFTER HEAL
        # -----------------------------------------------------------

        print("\n" + "=" * 80)
        print("7. OBSERVER - AFTER HEAL")
        print("=" * 80)

        result = observer_node(state)
        state.update(result)

        print("\nObserver result:")
        print(state["execution_result"])

        # -----------------------------------------------------------
        # 8. ANALYZER AFTER HEAL
        # -----------------------------------------------------------

        print("\n" + "=" * 80)
        print("8. ANALYZER - AFTER HEAL")
        print("=" * 80)

        result = analyzer_node(state)
        state.update(result)

        print("\nAnalyzer result:")
        print(state["analysis"])

    # ---------------------------------------------------------------
    # FINAL REPORT
    # ---------------------------------------------------------------

    print("\n" + "=" * 80)
    print("FINAL REPORT")
    print("=" * 80)

    analysis = state.get("analysis", {})
    execution = state.get("execution_result", {})

    print(f"Test       : {state['current_test']['title']}")
    print(f"Target     : {state['target_url']}")
    print(f"Test file  : {state['test_file_path']}")
    print(f"Passed     : {execution.get('passed')}")
    print(f"Exit code  : {execution.get('exit_code')}")
    print(f"Duration   : {execution.get('duration_s')}s")
    print(f"Verdict    : {analysis.get('verdict')}")
    print(f"Reason     : {analysis.get('reason')}")
    print(f"Failure    : {analysis.get('failure_type')}")
    print(f"Fix        : {analysis.get('suggested_fix')}")
    print(f"Heal count : {state.get('heal_attempt', 0)}")

    print("\n" + "=" * 80)
    print("SINGLE TEST HEALING CYCLE COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()