import os
import pytest
from langchain_openai import ChatOpenAI
from agents.llm import get_chat_model


def test_get_chat_model_aicredits_explicit(monkeypatch):
    monkeypatch.setenv("AICREDITS_API_KEY", "test-aicredits-key")
    monkeypatch.setenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")

    model = get_chat_model(provider="aicredits", model_name="gpt-4o-mini", temperature=0.2)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"
    assert model.temperature == 0.2
    assert "api.aicredits.in" in str(model.openai_api_base)
    assert model.openai_api_key.get_secret_value() == "test-aicredits-key"


def test_get_chat_model_aicredits_autodetect(monkeypatch):
    monkeypatch.setenv("AICREDITS_API_KEY", "sk-live-autodetect-test")
    monkeypatch.setenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")
    monkeypatch.delenv("FORGE_LLM_PROVIDER", raising=False)

    model = get_chat_model()

    assert isinstance(model, ChatOpenAI)
    assert "api.aicredits.in" in str(model.openai_api_base)
    assert model.openai_api_key.get_secret_value() == "sk-live-autodetect-test"


def test_get_chat_model_custom_model_and_temp(monkeypatch):
    monkeypatch.setenv("AICREDITS_API_KEY", "test-key")
    monkeypatch.setenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")

    model = get_chat_model(model_name="gpt-4o", temperature=0.7)

    assert model.model_name == "gpt-4o"
    assert model.temperature == 0.7


def test_get_chat_model_openai_explicit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

    model = get_chat_model(provider="openai", model_name="gpt-4o-mini")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"
    # When openai is explicitly selected without a custom base url, it should not point to aicredits
    if model.openai_api_base:
        assert "api.aicredits.in" not in str(model.openai_api_base)


@pytest.mark.skipif(
    not os.getenv("AICREDITS_API_KEY"),
    reason="AICREDITS_API_KEY is not set in environment or .env"
)
def test_get_chat_model_live_aicredits_invocation():
    """Live integration test: verifies real inference through AICredits."""
    model = get_chat_model()
    response = model.invoke("Say 'PONG' and nothing else.")
    
    assert response is not None
    assert isinstance(response.content, str)
    assert "pong" in response.content.lower().strip()
