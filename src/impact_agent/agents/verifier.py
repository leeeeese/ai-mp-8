"""Verifier: (복잡) Simulator 결과의 영역 누락·근거를 검증하고, 보완이 필요하면 재계획을 요청한다. 설계: docs/SE_agent_design.md 2장

점검 (VerifyCheck.check)
  area_path_missing  요건 항목의 변경 유형이 가리키는 영역에 그 항목의 경로가 없음 / 항목의 경로가 하나도 없음
  evidence_missing   confirmed hop 의 근거 위치가 Retriever 근거에 없음 / 변경(add·modify) 영역의 hop 이 전부 inferred
  cross_domain_link  경로 노드의 domain 이 섞였는데 cross_domain=false
  redundant_path     중복·포함 경로 (경고만, 재계획 사유 아님)
  alternative_basis  hard 영역에 대안이 없음 / 대안이 근거를 가리키지 않음 (LLM 이 있을 때)
판정: 보완 필요가 하나라도 있으면 needs_supplement + reason + additional_scope(node: / area: / keyword:).
이전 라운드 replan_feedback 에 같은 사유가 있으면 재계획으로 해결되지 않는 것으로 보고 pass 로 바꾸고 note 에 남긴다.
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..llm import LLM_FAILURES, LLMClient, Usage, analysis_llm
from ..rules import evidence_ref
from ..schemas import (
    Area,
    ChangeType,
    Confidence,
    Feasibility,
    ImpactPath,
    ImpactType,
    ReplanFeedback,
    VerifierOutput,
    VerifyCheck,
    VerifyResult,
)
from ..schemas.verifier import CheckKind
from ..state import AgentState
from .base import BaseAgent

# 변경 유형 → 그 항목의 경로(또는 그래프 밖 표시)가 반드시 있어야 할 영역 (설계서 2장, rules.CHANGE_TYPE_RULES 목표 역할 기준)
REQUIRED_AREAS: dict[ChangeType, list[Area]] = {
    ChangeType.SCREEN_CHANGE: [Area.SCREEN],
    ChangeType.QUERY_FIELD_ADD: [Area.SCREEN, Area.DB],
    ChangeType.DATA_ADD: [Area.DB],
    ChangeType.COLUMN_SPEC_CHANGE: [Area.DB],
    ChangeType.BATCH_CHANGE: [Area.BATCH],
    ChangeType.INTERFACE_FIELD_ADD: [Area.INTERFACE],
}
CHECK_ORDER: list[CheckKind] = ["area_path_missing", "evidence_missing", "cross_domain_link", "redundant_path", "alternative_basis"]
MAX_SCOPE = 5  # 점검 하나가 additional_scope 에 넣는 항목 수
REPEATED = "(반복) 재계획으로 해결되지 않음: "
WARNING = "(경고) "


@dataclass
class Issue:
    check: CheckKind
    note: str
    area: Area | None = None
    scope: list[str] = field(default_factory=list)
    blocking: bool = True  # False = 경고 (재계획 사유 아님)


# --- LLM 응답 형식 (Structured Outputs 호환: 기본값 없이 모든 필드 명시) ---------------------


class DraftAltJudgement(BaseModel):
    area: Area
    index: int = Field(description="그 영역 대안 목록의 순번 (0부터)")
    has_basis: bool = Field(description="대안 설명이 입력의 근거(노드·원문)로 뒷받침되면 true")
    note: str = Field(description="판단 근거 한 문장")


class VerifierDraft(BaseModel):
    judgements: list[DraftAltJudgement]


# --- 에이전트 ------------------------------------------------------------------------------------


class VerifierAgent(BaseAgent[VerifierOutput]):
    name = "verifier"
    output_model = VerifierOutput

    def __init__(self, llm: LLMClient | None = None, use_llm: bool = True) -> None:
        self._llm = llm
        self._llm_checked = llm is not None or not use_llm
        self.last_usage: Usage | None = None

    @property
    def llm(self) -> LLMClient | None:
        """대안 근거 판단 LLM. 주입하지 않았으면 .env 설정으로 한 번 만든다 (키가 없으면 None → 규칙 점검만)."""
        if not self._llm_checked:
            self._llm, self._llm_checked = analysis_llm(), True
        return self._llm

    def run(self, state: AgentState) -> VerifierOutput:
        self.last_usage = None
        if state.simulation is None:
            return decide([Issue("area_path_missing", "Simulator 결과가 없어 점검하지 못함", blocking=False)], [])
        issues = (area_path_missing(state) + evidence_missing(state) + cross_domain_link(state)
                  + redundant_path(state) + self._alternative_basis(state))
        return decide(issues, state.replan_feedback)

    def _alternative_basis(self, state: AgentState) -> list[Issue]:
        areas = state.simulation.area_impacts
        issues = [Issue("alternative_basis", f"[{a.area.value}] hard 판정인데 대안이 없음", a.area, [f"area:{a.area.value}"])
                  for a in areas if a.feasibility is Feasibility.HARD and not a.alternatives]
        valid = {(a.area, i) for a in areas for i in range(len(a.alternatives))}
        if not valid or self.llm is None:
            return issues
        try:
            draft, self.last_usage = self.llm.parse(self.system_prompt, alternatives_message(state), VerifierDraft)
        except LLM_FAILURES as exc:  # LLM 실패 시 대안 유무만 규칙 점검
            return issues + [Issue("alternative_basis", f"LLM 판단 실패로 대안 근거 점검 생략: {exc}", blocking=False)]
        for j in draft.judgements:
            if not j.has_basis and (j.area, j.index) in valid:
                issues.append(Issue("alternative_basis", f"[{j.area.value}] 대안 {j.index + 1} 근거 부족: {j.note}", j.area,
                                    [f"area:{j.area.value}"]))
        return issues


# --- 점검 (규칙) ---------------------------------------------------------------------------------


def item_of(path_id: str) -> int | None:
    """Simulator 경로 id (P<item_id>-<n>) → 요건 항목 id."""
    m = re.match(r"P(\d+)-", path_id)
    return int(m.group(1)) if m else None


def unique_paths(state: AgentState) -> list[ImpactPath]:
    """영역 여러 곳에 걸친 경로는 한 번만."""
    return list({p.path_id: p for a in state.simulation.area_impacts for p in a.paths}.values())


def area_path_missing(state: AgentState) -> list[Issue]:
    if state.planner is None:
        return []
    area_items = {a.area: {item_of(p.path_id) for p in a.paths} for a in state.simulation.area_impacts}
    with_paths = set().union(*area_items.values()) if area_items else set()
    exp = state.explorer
    unresolved = exp.unresolved if exp else []
    results = {r.item_id: r for r in exp.item_results} if exp else {}
    issues = []
    for it in state.planner.items:
        keywords = [f"keyword:{m.phrase}" for m in it.mentions][:MAX_SCOPE]
        if it.item_id not in with_paths:
            frontier = [f"node:{n}" for n in results[it.item_id].frontier] if it.item_id in results else []
            issues.append(Issue("area_path_missing", f"[항목 {it.item_id}] 영향 경로 없음 ({it.text})",
                                scope=(frontier + keywords)[:MAX_SCOPE]))
            continue
        for area in REQUIRED_AREAS.get(it.change_type, []):
            if it.item_id in area_items.get(area, set()):
                continue
            if area is Area.INTERFACE and any(u.startswith(f"[항목 {it.item_id}]") and "그래프 밖" in u for u in unresolved):
                continue  # 그래프 밖 채널·전문으로 표시됨 (분석 한계로 넘어감)
            issues.append(Issue("area_path_missing", f"[항목 {it.item_id}] {it.change_type.value} 인데 {area.value} 영역 경로 없음",
                                area, [f"area:{area.value}"] + keywords[:MAX_SCOPE - 1]))
    return issues


def evidence_missing(state: AgentState) -> list[Issue]:
    refs = {evidence_ref(e) for e in state.retriever.evidence} if state.retriever else set()
    issues = []
    bad = list(dict.fromkeys(h.node for p in unique_paths(state) for h in p.hops
                             if h.confidence is Confidence.CONFIRMED and h.evidence_ref not in refs))
    if bad:  # 경로 단위로 한 번만 (여러 영역에 걸친 경로가 중복 보고되지 않게)
        issues.append(Issue("evidence_missing", f"confirmed 인데 근거 위치가 Retriever 근거에 없음: {', '.join(bad)}",
                            scope=[f"node:{n}" for n in bad[:MAX_SCOPE]]))
    for a in state.simulation.area_impacts:
        hops = [h for p in a.paths for h in p.hops]
        if a.impact_type in (ImpactType.ADD, ImpactType.MODIFY) and hops and all(
                h.confidence is Confidence.INFERRED for h in hops):
            nodes = list(dict.fromkeys(h.node for h in hops))
            issues.append(Issue("evidence_missing", f"[{a.area.value}] 변경 영역인데 원문 근거가 하나도 없음", a.area,
                                [f"node:{n}" for n in nodes[:MAX_SCOPE]]))
    return issues


def cross_domain_link(state: AgentState) -> list[Issue]:
    domains = {n.node_id: n.domain for n in state.explorer.nodes} if state.explorer else {}
    issues = []
    for p in unique_paths(state):
        found = sorted({domains[h.node].value for h in p.hops if domains.get(h.node)})
        if len(found) > 1 and not p.cross_domain:
            nodes = [h.node for h in p.hops]
            issues.append(Issue("cross_domain_link", f"{p.path_id} 경로가 {'·'.join(found)} 에 걸치는데 도메인 연계로 표시되지 않음",
                                scope=[f"node:{n}" for n in nodes[:MAX_SCOPE]]))
    return issues


def redundant_path(state: AgentState) -> list[Issue]:
    issues, first, distinct = [], {}, []
    for p in unique_paths(state):
        nodes = tuple(h.node for h in p.hops)
        if nodes in first:
            issues.append(Issue("redundant_path", f"{p.path_id} 가 {first[nodes]} 와 같은 경로", blocking=False))
        else:
            first[nodes] = p.path_id
            distinct.append((p.path_id, list(nodes)))
    for pid, nodes in distinct:
        container = next((qid for qid, other in distinct if len(nodes) < len(other) and _contains(other, nodes)), None)
        if container:
            issues.append(Issue("redundant_path", f"{pid} 가 {container} 에 포함됨", blocking=False))
    return issues


def _contains(seq: list[str], sub: list[str]) -> bool:
    return any(seq[i:i + len(sub)] == sub for i in range(len(seq) - len(sub) + 1))


# --- 판정 ----------------------------------------------------------------------------------------


def decide(issues: list[Issue], feedback: list[ReplanFeedback]) -> VerifierOutput:
    """보완 필요(재계획) / 경고 / 반복 사유를 나눠 VerifierOutput 을 만든다. 문제가 없는 점검은 '이상 없음' 으로 남긴다."""
    past = [fb.reason for fb in feedback]
    checks, reasons, scope = [], [], []
    for i in issues:
        repeated = i.blocking and any(i.note in r for r in past)
        if i.blocking and not repeated:
            checks.append(VerifyCheck(check=i.check, area=i.area, result=VerifyResult.NEEDS_SUPPLEMENT, note=i.note))
            reasons.append(i.note)
            scope += i.scope
        else:
            note = (REPEATED if repeated else WARNING) + i.note
            checks.append(VerifyCheck(check=i.check, area=i.area, result=VerifyResult.PASS, note=note))
    seen = {c.check for c in checks}
    checks += [VerifyCheck(check=k, result=VerifyResult.PASS, note="이상 없음") for k in CHECK_ORDER if k not in seen]
    checks.sort(key=lambda c: CHECK_ORDER.index(c.check))
    return VerifierOutput(
        result=VerifyResult.NEEDS_SUPPLEMENT if reasons else VerifyResult.PASS,
        checks=checks,
        reason="; ".join(reasons) or None,
        additional_scope=list(dict.fromkeys(scope)),
    )


# --- LLM 입력 ------------------------------------------------------------------------------------


def alternatives_message(state: AgentState) -> str:
    sim = state.simulation
    lines = ["[요청]", state.user_input.request_text.strip(), "", "[영역별 대안] area / 순번: 설명 (장단점)"]
    for a in sim.area_impacts:
        for i, alt in enumerate(a.alternatives):
            lines.append(f"- {a.area.value} / {i}: {alt.description} ({alt.pros_cons})")
    if sim.requested_vs_alternative:
        lines += ["", "[요청안 / 대안 비교]", sim.requested_vs_alternative]
    lines += ["", "[영역 경로와 근거] 노드 (근거 위치)"]
    for a in sim.area_impacts:
        if a.alternatives:
            nodes = dict.fromkeys(f"{h.node} ({h.evidence_ref or '근거 없음'}): {h.impact}" for p in a.paths for h in p.hops)
            lines += [f"### {a.area.value}"] + [f"- {n}" for n in list(nodes)[:30]]
    evidence = state.retriever.evidence if state.retriever else []
    if evidence:
        lines += ["", "[근거 요약]"] + [f"- {e.node_id} {evidence_ref(e)}: {e.summary}" for e in evidence[:40]]
    return "\n".join(lines)
