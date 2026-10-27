"""ReAct 루프 (Yao et al., 2023). Explorer·Retriever 가 규칙 단계 뒤에 쓴다.

한 단계 = LLM 이 지금까지의 조회 결과(Observation)를 보고 판단(Thought)과 다음 행동(Action)을 고름 → tool 실행 → 결과 요약을 다음 단계 입력에 붙임.
finish 를 고르거나, tool 호출 한도·단계 상한에 닿거나, 잘못된 행동이 연속 MAX_ERRORS 번 나오면 끝낸다.
LLM 호출이 실패하면(API 오류·응답 검증 실패) 그때까지의 단계만 남기고 끝낸다 → 호출한 에이전트는 규칙 단계 결과를 그대로 쓴다.
"""

import json
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from ..llm import LLM_FAILURES, LLMClient, Usage
from ..schemas import ReactStep

D = TypeVar("D", bound=BaseModel)

MAX_ERRORS = 2
MAX_OBSERVATION_CHARS = 2500  # 단계마다 다음 입력에 붙일 결과 길이 (토큰 보호)


class InvalidAction(ValueError):
    """LLM 이 고른 행동의 인자가 잘못됨 (tool 은 호출하지 않음). 메시지를 Observation 으로 돌려준다."""


def run_react(
    llm: LLMClient,
    system: str,
    context: str,
    decision: type[D],
    execute: Callable[[D], str],
    budget: Callable[[], int],
    max_steps: int | None = None,
    max_observation: Callable[[D], int] | None = None,
) -> tuple[list[ReactStep], Usage]:
    """decision 은 thought·action 필드를 가진 모델. execute 는 행동을 실행하고 Observation 문자열을 돌려준다.

    max_steps 는 finish 를 포함한 단계 상한, max_observation 은 행동별 Observation 길이 (없으면 MAX_OBSERVATION_CHARS).
    """
    trace: list[ReactStep] = []
    usage = Usage(0, 0)
    errors = 0
    while True:
        left = budget()
        last = max_steps is not None and len(trace) + 1 >= max_steps
        user = f"{context}\n\n{render_trace(trace)}\n\n남은 tool 호출: {left}회. 다음 행동을 고르세요."
        if left <= 0 or last:
            user += " (한도 소진: finish 만 가능)" if left <= 0 else " (마지막 단계: finish 로 판단을 정리)"
        try:
            d, u = llm.parse(system, user, decision)
        except LLM_FAILURES as e:
            trace.append(ReactStep(step=len(trace) + 1, thought="-", action="finish",
                                   observation=f"LLM 오류로 보완 중단 (규칙 결과 유지): {type(e).__name__}"))
            return trace, usage
        usage.input_tokens += u.input_tokens or 0
        usage.output_tokens += u.output_tokens or 0
        args = {k: v for k, v in d.model_dump(mode="json").items() if k not in ("thought", "action") and v not in (None, [])}
        if d.action == "finish" or left <= 0 or last:
            reason = "종료" if d.action == "finish" else "tool 호출 한도 소진으로 종료" if left <= 0 else "단계 상한으로 종료"
            trace.append(ReactStep(step=len(trace) + 1, thought=d.thought, action="finish", args=args, observation=reason))
            return trace, usage
        try:
            observation = execute(d)
            errors = 0
        except InvalidAction as e:
            observation = f"오류: {e}"
            errors += 1
        trace.append(ReactStep(step=len(trace) + 1, thought=d.thought, action=d.action, args=args,
                               observation=observation[:max_observation(d) if max_observation else MAX_OBSERVATION_CHARS]))
        if errors >= MAX_ERRORS:
            trace.append(ReactStep(step=len(trace) + 1, thought="-", action="finish",
                                   observation=f"잘못된 행동 {MAX_ERRORS}회 연속으로 종료"))
            return trace, usage


def render_trace(trace: list[ReactStep]) -> str:
    if not trace:
        return "[지금까지의 단계] 없음 (첫 단계)"
    lines = ["[지금까지의 단계]"]
    for s in trace:
        lines += [f"Thought {s.step}: {s.thought}",
                  f"Action {s.step}: {s.action} {json.dumps(s.args, ensure_ascii=False)}",
                  f"Observation {s.step}: {s.observation}"]
    return "\n".join(lines)
