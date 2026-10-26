"""ReAct 루프 (Yao et al., 2023). Explorer·Retriever 가 규칙 단계 뒤에 쓴다.

한 단계 = LLM 이 지금까지의 조회 결과(Observation)를 보고 판단(Thought)과 다음 행동(Action)을 고름 → tool 실행 → 결과 요약을 다음 단계 입력에 붙임.
finish 를 고르거나, tool 호출 한도가 다 되거나, 잘못된 행동이 연속 MAX_ERRORS 번 나오면 끝낸다.
"""

import json
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from ..llm import LLMClient, Usage
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
) -> tuple[list[ReactStep], Usage]:
    """decision 은 thought·action 필드를 가진 모델. execute 는 행동을 실행하고 Observation 문자열을 돌려준다."""
    trace: list[ReactStep] = []
    usage = Usage(0, 0)
    errors = 0
    while True:
        left = budget()
        user = f"{context}\n\n{render_trace(trace)}\n\n남은 tool 호출: {left}회. 다음 행동을 고르세요."
        if left <= 0:
            user += " (한도 소진: finish 만 가능)"
        d, u = llm.parse(system, user, decision)
        usage.input_tokens += u.input_tokens or 0
        usage.output_tokens += u.output_tokens or 0
        args = {k: v for k, v in d.model_dump(mode="json").items() if k not in ("thought", "action") and v not in (None, [])}
        if d.action == "finish" or left <= 0:
            trace.append(ReactStep(step=len(trace) + 1, thought=d.thought, action="finish", args=args,
                                   observation="종료" if d.action == "finish" else "tool 호출 한도 소진으로 종료"))
            return trace, usage
        try:
            observation = execute(d)
            errors = 0
        except InvalidAction as e:
            observation = f"오류: {e}"
            errors += 1
        trace.append(ReactStep(step=len(trace) + 1, thought=d.thought, action=d.action, args=args,
                               observation=observation[:MAX_OBSERVATION_CHARS]))
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
