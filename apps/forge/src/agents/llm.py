import os
import logging
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded regardless of current working directory
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
load_dotenv()

logger = logging.getLogger("forge.llm")


def get_chat_model(
    model_name: Optional[str] = None,
    temperature: float = 0.1,
    provider: Optional[str] = None,
):
    """
    Returns an initialized LangChain BaseChatModel based on available API keys or configuration.
    Supports:
      - OpenAI (default: gpt-4o-mini or gpt-4o)
      - Anthropic (default: claude-3-5-sonnet-20241022)
      - Google GenAI (default: gemini-2.0-flash or gemini-1.5-pro)
    """
    chosen_provider = provider or os.getenv("FORGE_LLM_PROVIDER")

    # If provider is explicitly specified
    if chosen_provider:
        chosen_provider = chosen_provider.lower()
        if chosen_provider in ("aicredits", "ai_credits"):
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name or os.getenv("AICREDITS_MODEL", "gpt-4o-mini"),
                api_key=os.getenv("AICREDITS_API_KEY"),
                base_url=os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1"),
                temperature=temperature,
            )
        elif chosen_provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                temperature=temperature,
            )
        elif chosen_provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=model_name or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                temperature=temperature,
            )
        elif chosen_provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=model_name or os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
                temperature=temperature,
            )

    # Auto-detect: Prioritize AI Credits if configured
    if os.getenv("AICREDITS_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name or os.getenv("AICREDITS_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            api_key=os.getenv("AICREDITS_API_KEY"),
            base_url=os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1"),
            temperature=temperature,
        )

    # Auto-detect other standard API keys
    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=temperature,
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model_name or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            temperature=temperature,
        )

    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return ChatGoogleGenerativeAI(
            model=model_name or os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
            temperature=temperature,
            google_api_key=api_key,
        )

    # Fallback to OpenAI default attempt (will error only when invoked if key is missing)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name or "gpt-4o-mini",
        temperature=temperature,
    )