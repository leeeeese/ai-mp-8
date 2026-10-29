"""Planner: 요청 구조화, simple/complex 판정, Explorer 탐색 명세(items) 작성.

LLM 은 요건 항목 분리·변경 유형·언급 표현(동의어·의미 힌트)만 고른다 (PlannerDraft, Structured Output).
복잡도는 프롬프트의 판정 예시로 기준을 준다 (Few-shot, Brown et al. 2020).
재계획이면 이전 탐색 명세와 누적된 누락 사유(replan_feedback)를 함께 넘긴다 (Reflexion, Shinn et al. 2023).
목표 역할·방향·hop 상한(rules.CHANGE_TYPE_RULES), 실행 계획, 탐색 영역(rules.SM_PART_DOMAINS)은 코드가 정한다.
노드 ID 는 정하지 않는다 (그래프를 모르는 상태에서 판단). 복잡도는 요건 문장에서 확인되는 것만으로 판정한다.

사용자 상세 분석 요청: 마지막 후속 입력이 reanalysis(결과 화면의 상세 분석 권장을 사용자가 확인)면
LLM 판정과 관계없이 complex 로 정하고 실행 계획에 Simulator·Verifier 를 넣는다 (코드 규칙, docs/planner_detail_analysis_to-be.md).
Verifier 재계획 라운드(이전 판정 complex + replan_feedback)도 complex 를 유지한다 (복잡 흐름 도중 단순 응답으로 빠지지 않게).
"""

from pydantic import BaseModel, Field

from ..llm import LLMClient, OpenAIClient, Usage
from ..rules import CHANGE_TYPE_RULES, SM_PART_DOMAINS, hop_cap
from ..schemas import (
    AgentName,
    ChangeType,
    Complexity,
    ExcludeOption,
    ExploreItem,
    FollowupType,
    Mention,
    MentionKind,
    OpenItem,
    PlannerOutput,
    PlanStep,
    RequestType,
    StructuredRequest,
)
from ..state import AgentState
from .base import BaseAgent

# --- LLM 응답 형식 (Structured Outputs 호환: 기본값 없이 모든 필드 명시) ---------------------


class DraftMention(BaseModel):
    kind: MentionKind
    phrase: str = Field(description="요건에 나온 표현 그대로")
    maybe_new: bool = Field(description="아직 시스템에 없을 수 있는 대상이면 true")
    synonyms: list[str] = Field(description="같은 뜻의 다른 표현 (현업·시스템 용어). 없으면 []")
    meaning: str | None = Field(description="의미 힌트. 모르면 null")
    area: str | None = Field(description="업무 영역 힌트. 모르면 null")


class DraftItem(BaseModel):
    text: str = Field(description="요건 항목 문장")
    change_type: ChangeType
    mentions: list[DraftMention]
    out_of_graph: list[str] = Field(description="소스 그래프 밖 영역 (외부 채널 전문, 수신 시스템 등). 없으면 []")


class DraftOpenItem(BaseModel):
    item: str
    reason: str


class PlannerDraft(BaseModel):
    target: str
    action: str
    condition: str | None
    policy: str | None
    scope: str | None
    request_type: RequestType
    business_terms: list[str]
    open_items: list[DraftOpenItem]
    complexity: Complexity
    complexity_reason: str
    items: list[DraftItem]
    exclude_common: bool = Field(description="요청이 공통 모듈·공통코드 자체를 다루지 않으면 true")


PLAN_STEPS = {
    Complexity.SIMPLE: [(AgentName.EXPLORER, "언급 표현으로 시작 노드를 찾고 목표 역할까지 탐색"),
                        (AgentName.RETRIEVER, "경로 노드의 소스·정의서 원문 확보")],
    Complexity.COMPLEX: [(AgentName.EXPLORER, "언급 표현으로 시작 노드를 찾고 목표 역할까지 탐색"),
                         (AgentName.RETRIEVER, "경로 노드의 소스·정의서 원문 확보"),
                         (AgentName.SIMULATOR, "영역별 영향·가능성·대안 판단"),
                         (AgentName.VERIFIER, "영역 누락·근거 검증")],
}


def build_output(draft: PlannerDraft, state: AgentState) -> PlannerOutput:
    """LLM 초안에 판단 기준표를 적용해 PlannerOutput 을 만든다."""
    items = []
    for i, d in enumerate(draft.items, start=1):
        role, direction = CHANGE_TYPE_RULES[d.change_type]
        items.append(ExploreItem(
            item_id=i, text=d.text, change_type=d.change_type,
            mentions=[Mention(**m.model_dump()) for m in d.mentions],
            target_role=role, direction=direction, hop_cap=hop_cap(direction), out_of_graph=d.out_of_graph,
        ))
    return PlannerOutput(
        structured_request=StructuredRequest(
            target=draft.target, action=draft.action, condition=draft.condition, policy=draft.policy, scope=draft.scope,
            request_type=draft.request_type, business_terms=draft.business_terms,
            open_items=[OpenItem(item=o.item, reason=o.reason) for o in draft.open_items],
        ),
        complexity=draft.complexity,
        complexity_reason=draft.complexity_reason,
        domains=SM_PART_DOMAINS[state.user_input.sm_part],
        items=items,
        exclude=ExcludeOption(is_common=draft.exclude_common),
        plan=[PlanStep(agent=a, purpose=p) for a, p in PLAN_STEPS[draft.complexity]],
    )


REANALYSIS_REASON = "사용자 상세 분석 요청"
REPLAN_REASON = "재계획 (복잡 흐름 유지)"


def apply_reanalysis(out: PlannerOutput, state: AgentState) -> PlannerOutput:
    """사용자 상세 분석 요청(마지막 후속 입력 reanalysis) 또는 Verifier 재계획이면 complex 로 정하고 Simulator·Verifier 를 넣는다."""
    last = state.followups[-1] if state.followups else None
    if last is not None and last.type is FollowupType.REANALYSIS:
        prefix = f"{REANALYSIS_REASON}: {last.content.strip()}"
    elif state.replan_feedback and state.planner is not None and state.planner.complexity is Complexity.COMPLEX:
        prefix = f"{REPLAN_REASON} {len(state.replan_feedback)}차"
    else:
        return out
    if out.complexity is not Complexity.COMPLEX:
        out.complexity_reason = f"{prefix} / (LLM 판단 {out.complexity.value}) {out.complexity_reason}"
    else:
        out.complexity_reason = f"{prefix} / {out.complexity_reason}"
    out.complexity = Complexity.COMPLEX
    out.plan = [PlanStep(agent=a, purpose=p) for a, p in PLAN_STEPS[Complexity.COMPLEX]]
    return out


def user_message(state: AgentState) -> str:
    lines = [f"[SM 선택] {state.user_input.sm_part.value}", "[요청]", state.user_input.request_text.strip()]
    if state.followups:
        lines += ["", "[후속 입력]"] + [f"- ({f.type.value}) {f.content}" for f in state.followups]
    if state.replan_feedback:
        # Reflexion: 이전 시도(탐색 명세)와 누적된 실패 사유를 함께 주어 같은 누락을 반복하지 않게 한다
        if state.planner is not None:
            lines += ["", "[이전 탐색 명세]"]
            for it in state.planner.items:
                mentions = ", ".join(f"{m.kind.value}:{m.phrase}" for m in it.mentions)
                lines.append(f"- 항목 {it.item_id} ({it.change_type.value}) {it.text} | {mentions}")
        lines += ["", "[재계획 요청] 누적된 누락 사유입니다. 사유마다 메우는 항목·언급 표현을 추가하세요."]
        lines += [f"- {fb.round}차: {fb.reason} / 추가 범위: {', '.join(fb.additional_scope)}" for fb in state.replan_feedback]
    return "\n".join(lines)


class PlannerAgent(BaseAgent[PlannerOutput]):
    name = "planner"
    output_model = PlannerOutput

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm
        self.last_usage: Usage | None = None

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = OpenAIClient()
        return self._llm

    def run(self, state: AgentState) -> PlannerOutput:
        draft, self.last_usage = self.llm.parse(self.system_prompt, user_message(state), PlannerDraft)
        return apply_reanalysis(build_output(draft, state), state)
