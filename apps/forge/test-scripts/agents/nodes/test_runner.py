import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent.parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.nodes.runner import runner_node


def main():
    state = {
        "test_file_path": r"D:\Pramod\Tzylo\forge\example.com\tests\test_page_smoke.py",
        "config": {
            "test_timeout_s": 45,
            "headless": False,
        },
    }

    print("=" * 80)
    print("FORGE RUNNER TEST")
    print("=" * 80)

    result = runner_node(state)

    print("\n" + "=" * 80)
    print("RUNNER RESULT")
    print("=" * 80)

    print(result)


if __name__ == "__main__":
    main()