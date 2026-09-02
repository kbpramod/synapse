import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.llm import get_chat_model

def main():
    print("Testing AICredits LLM connection...")
    try:
        llm = get_chat_model()
        print(f"Model initialized: {type(llm).__name__} (model={getattr(llm, 'model_name', getattr(llm, 'model', 'unknown'))})")
        print("Invoking prompt: 'Say hello in 3 words'...")
        response = llm.invoke("Say hello in 3 words")
        print("Response received:")
        print(f"  --> {response.content}")
        print("\nAICredits integration SUCCESSFUL!")
    except Exception as e:
        print(f"\nERROR: Failed to connect to AICredits: {e}")

if __name__ == "__main__":
    main()
