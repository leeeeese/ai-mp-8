"""AgentState → UI 응답 모양 (frontend/src/api/types.ts) 변환.

UI 타입은 대부분 camelCase 이고, 상세 분석 권장(detail_analysis_*)·KPI 기록은 백엔드와 같은 snake_case 를 쓴다.
UI 에만 있는 값(plannerVerdict·process·Report 전체 판정·일정 불확실성)은 여기서 만든다 (가안, docs/ui_design.md 7장).
"""

from ..agents.simulator import CHANGE_TAG, requirement_difficulty
from ..schemas import AreaImpact, Complexity, Feasibility, ImpactType, NodeSeverity, NodeVerdict, VerifyResult
from ..state import AgentState

AGENT_LABEL = {
    "planner": "Planner", "explorer": "Explorer", "retriever": "Retriever", "simulator": "Simulator",
    "verifier": "Verifier", "composer": "Composer",
}
IMPACT_LABEL = {ImpactType.ADD: "신규", ImpactType.MODIFY: "수정", ImpactType.REFERENCE_ONLY: "참조",
                ImpactType.NONE: "변경 없음"}
FEASIBILITY_ORDER = [Feasibility.FEASIBLE, Feasibility.REVIEW, Feasibility.HARD]


def overall_feasibility(areas: list[AreaImpact]) -> Feasibility:
    """변경 영역 중 가장 나쁜 가능 여부 (Composer 요약·KPI 와 같은 규칙). 변경 영역이 없으면 feasible."""
    changed = [a.feasibility for a in areas if a.impact_type in (ImpactType.ADD, ImpactType.MODIFY)]
    return max(changed, key=FEASIBILITY_ORDER.index) if changed else Feasibility.FEASIBLE


def area_view(a: AreaImpact) -> dict:
    hops = [h for p in a.paths for h in p.hops]
    changed = next((h.impact for h in hops if h.impact.startswith(CHANGE_TAG)), None)
    alt = a.alternatives[0] if a.alternatives else None
    return {
        "area": a.area.value,
        "impact": f"{IMPACT_LABEL[a.impact_type]} — {changed or a.difficulty_basis}",
        "path": [h.node for h in a.paths[0].hops] if a.paths else [],
        "requested": {"feasibility": a.feasibility.value, "difficulty": a.difficulty.value},
        "alternative": {"feasibility": alt.feasibility.value, "difficulty": alt.difficulty.value} if alt else None,
        "confidence": a.confidence.value,
    }


def answer_view(state: AgentState) -> dict:
    """Composer 결과 → AgentAnswer."""
    out = state.composer
    simple = out.simple_answer
    complex_ = out.complex_report
    planner = state.planner
    word = "단순" if out.complexity is Complexity.SIMPLE else "복잡"
    answer = {
        "complexity": out.complexity.value,
        "plannerVerdict": f"{word} — {planner.complexity_reason}" if planner else word,
        "process": [f"{AGENT_LABEL.get(r.agent, r.agent)}: {r.latency_ms / 1000:.1f}s · 도구 {r.tool_calls}회"
                    for r in state.agent_runs if r.agent in AGENT_LABEL],
        "summary": simple.conclusion_lines if simple else [complex_.summary_conclusion] if complex_ else [],
        "areas": [area_view(a) for a in state.simulation.area_impacts if a.paths or a.impact_type is not ImpactType.NONE]
        if complex_ and state.simulation else [],
        "openItemCount": len(complex_.checklist) if complex_ else len(planner.structured_request.open_items) if planner else 0,
    }
    if simple:
        answer |= {
            "evidence": [{"claim": e.claim, "evidence": e.evidence, "location": e.location or "", "confidence": e.confidence.value}
                         for e in simple.evidence_table],
            "checkQueries": [{"title": q.purpose, "sql": q.sql} for q in simple.check_queries],
            "detail_analysis_recommended": simple.detail_analysis_recommended,
            "detail_analysis_reasons": simple.detail_analysis_reasons,
        }
    return answer


def report_view(state: AgentState) -> dict | None:
    """복잡 요건 결과물 → Report (버전은 v1 하나. 백엔드는 버전 대신 stage)."""
    r = state.composer.complex_report if state.composer else None
    if r is None or state.simulation is None:
        return None
    areas = state.simulation.area_impacts
    level, _ = requirement_difficulty(areas)
    feas = overall_feasibility(areas)
    unresolved = state.verifier is not None and state.verifier.result is VerifyResult.NEEDS_SUPPLEMENT
    uncertainty = "높음" if feas is not Feasibility.FEASIBLE or unresolved else "중간" if len(r.schedule_factors) >= 3 else "낮음"
    return {
        "version": 1,
        "versions": [{"version": 1, "label": f"v1 ({r.stage})"}],
        "feasibility": feas.value,
        "difficulty": level.value,
        "scheduleUncertainty": uncertainty,
        "areas": [area_view(a) for a in areas if a.paths or a.impact_type is not ImpactType.NONE],
        "scheduleFactors": r.schedule_factors,
        "limits": r.analysis_limits,
        "checklist": [{"id": c.item_id, "owner": c.owner.value, "question": c.question, "status": c.status.value,
                       **({"answer": c.answer} if c.answer else {})} for c in r.checklist],
    }


def kpi_record(meta: dict, state: AgentState) -> dict:
    """세션 KPI 기록 (frontend KpiRecord, 백엔드 KpiOutput 과 같은 필드명 + 세션 정보·합산용 counts)."""
    k = state.kpi
    rows = k.judgements
    crit = [r for r in rows if r.severity is NodeSeverity.CRITICAL and r.verdict in (NodeVerdict.DETECTED, NodeVerdict.MISSED)]
    return {
        "session_id": meta["id"], "title": meta["title"], "sm_part": meta["smPart"], "complexity": k.complexity.value,
        "created_at": meta["finishedAt"], "agent_version": meta["agentVersion"], "req_id": k.req_id,
        "evaluable": k.evaluable, "metrics": {m.name.value: m.value for m in k.metrics},
        "counts": {
            "tp": sum(r.verdict is NodeVerdict.DETECTED for r in rows), "fn": sum(r.verdict is NodeVerdict.MISSED for r in rows),
            "fp": sum(r.verdict is NodeVerdict.FALSE_POSITIVE for r in rows),
            "critical_missed": sum(r.verdict is NodeVerdict.MISSED for r in crit), "critical_total": len(crit),
        } if k.evaluable else None,
        "missed_nodes": k.missed_nodes, "false_positive_nodes": k.false_positive_nodes,
        "reanalyzed": k.reanalyzed, "judgement_notes": k.judgement_notes,
    }
