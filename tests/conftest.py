import pytest


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """테스트는 실제 LLM 을 부르지 않는다 (Explorer·Retriever 는 규칙 단계만, ReAct 는 가짜 LLM 을 넘겨 확인)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
