"""LLM 호출 (OpenAI Structured Outputs). 모델은 .env 의 LLM_MODEL (기본 gpt-6.1-sol).

Planner 는 항상, Explorer·Retriever 는 ReAct 보완 단계에서 사용한다 (react_llm).
Simulator·Verifier·Composer 는 판정·문장 작성에 사용하고, 없으면 규칙 결과만 낸다 (analysis_llm).

AZURE_OPENAI_ENDPOINT 가 있으면 Azure OpenAI 호환 게이트웨이로 호출한다 (api_version = OPENAI_API_VERSION).
샘플링: 같은 요청의 응답 편차를 줄이려고 seed 를 고정한다 (LLM_SEED, 기본 7, 빈 값이면 끔). 이 모델은 temperature 를 기본값(1)만
받아서 고정하지 않는다. seed 를 받지 않는 모델이면 한 번 오류 난 뒤 seed 없이 다시 부르고 이후로는 넣지 않는다.
"""

import os
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .trace import clip, trace

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_SEED = "7"


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


def _llm_failures() -> tuple[type[Exception], ...]:
    try:
        from openai import OpenAIError
    except ImportError:
        return (LLMError, ValidationError)
    return (LLMError, OpenAIError, ValidationError)


# 규칙 결과로 대체할 LLM 실패 (거부·빈 응답, API·네트워크 오류, 응답 검증 실패)
LLM_FAILURES = _llm_failures()


def react_llm() -> "LLMClient | None":
    """Explorer·Retriever ReAct 단계용 LLM. 키가 없거나 REACT=0 이면 None (규칙 단계만 실행)."""
    if os.environ.get("REACT", "1") == "0" or not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAIClient()


def analysis_llm() -> "LLMClient | None":
    """Simulator·Verifier·Composer 판정·문장용 LLM. 키가 없거나 ANALYSIS_LLM=0 이면 None (규칙 결과만)."""
    if os.environ.get("ANALYSIS_LLM", "1") == "0" or not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAIClient()


class OpenAIClient:
    def __init__(self, model: str | None = None) -> None:
        from openai import AzureOpenAI, OpenAI

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY 가 비어 있습니다 (.env). LLM 없이 확인하려면 CLI 의 --plan 으로 PlannerOutput JSON 을 넘기세요")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        seed = os.environ.get("LLM_SEED", DEFAULT_SEED).strip()
        self.seed: int | None = int(seed) if seed else None
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if endpoint:
            self.client = AzureOpenAI(azure_endpoint=endpoint, api_key=key,
                                      api_version=os.environ.get("OPENAI_API_VERSION", "2024-12-01-preview"))
        else:
            self.client = OpenAI(api_key=key)

    def parse(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]:
        start = time.perf_counter()
        try:
            parsed, usage = self._parse(system, user, schema)
        except Exception as e:
            trace(f"{schema.__name__} 실패 {time.perf_counter() - start:.1f}s · {type(e).__name__}: {clip(e, 120)}", label="LLM 오류")
            raise
        trace(f"{self.model} {schema.__name__} {time.perf_counter() - start:.1f}s · "
              f"입력 {usage.input_tokens or 0:,} · 출력 {usage.output_tokens or 0:,} 토큰", label="LLM")
        return parsed, usage

    def _parse(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]:
        completions = getattr(self.client.chat.completions, "parse", None) or self.client.beta.chat.completions.parse
        kwargs = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                  "response_format": schema}
        try:
            completion = completions(**kwargs, **({"seed": self.seed} if self.seed is not None else {}))
        except Exception as e:  # seed 를 받지 않는 모델: seed 없이 다시 (이후로는 넣지 않음)
            if self.seed is None or "seed" not in str(e).lower():
                raise
            self.seed = None
            completion = completions(**kwargs)
        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise LLMError(f"LLM 응답을 {schema.__name__} 로 받지 못했습니다: {message.refusal or '빈 응답'}")
        usage = completion.usage
        return message.parsed, Usage(usage.prompt_tokens if usage else None, usage.completion_tokens if usage else None)
