"""LLM 호출 (OpenAI Structured Outputs). 모델은 .env 의 LLM_MODEL (기본 gpt-6.1-sol).

Planner 는 항상, Explorer·Retriever 는 ReAct 보완 단계에서 사용한다 (react_llm).

AZURE_OPENAI_ENDPOINT 가 있으면 Azure OpenAI 호환 게이트웨이로 호출한다 (api_version = OPENAI_API_VERSION).
"""

import os
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "gpt-6.1-sol"


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(Protocol):
    def parse(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]:
        """system/user 프롬프트로 schema 형식의 결과를 받는다."""
        ...


class LLMError(RuntimeError):
    pass


def react_llm() -> "LLMClient | None":
    """Explorer·Retriever ReAct 단계용 LLM. 키가 없거나 REACT=0 이면 None (규칙 단계만 실행)."""
    if os.environ.get("REACT", "1") == "0" or not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAIClient()


class OpenAIClient:
    def __init__(self, model: str | None = None) -> None:
        from openai import AzureOpenAI, OpenAI

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY 가 비어 있습니다 (.env). LLM 없이 확인하려면 CLI 의 --plan 으로 PlannerOutput JSON 을 넘기세요")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if endpoint:
            self.client = AzureOpenAI(azure_endpoint=endpoint, api_key=key,
                                      api_version=os.environ.get("OPENAI_API_VERSION", "2024-12-01-preview"))
        else:
            self.client = OpenAI(api_key=key)

    def parse(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]:
        completions = getattr(self.client.chat.completions, "parse", None) or self.client.beta.chat.completions.parse
        completion = completions(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=schema,
        )
        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise LLMError(f"LLM 응답을 {schema.__name__} 로 받지 못했습니다: {message.refusal or '빈 응답'}")
        usage = completion.usage
        return message.parsed, Usage(usage.prompt_tokens if usage else None, usage.completion_tokens if usage else None)
