"""KPI Agent: PoC 평가. Composer 이후 실행하며 결과는 Composer 에 전달하지 않는다 (LLM 미사용). 설계: docs/SE_kpi-agent-design.md

입력: 정답 노드 목록(AnswerKey) + 실행 로그(agent_runs, 단계별 산출물에서 뽑은 final_nodes). Composer 산출물 문장은 쓰지 않는다.
노드 판정 (5.1):
  - 탐지(TP)  정답 노드가 final_nodes 에 있음 / 미탐(FN) 없음
  - 오탐(FP)  출력 노드가 정답·제외 목록에 없고 유형이 eval_target_labels 안
  - 평가 제외 excluded_nodes (출력돼도 오탐 아님, 분모·분자 제외) / 범위 밖: 유형이 eval_target_labels 밖
핵심 KPI (6.1): 노드 탐지율 = TP/(TP+FN), 미탐률 = FN/(TP+FN), Critical 미탐률, 노드 정밀도 = TP/(TP+FP). 분모 0 이면 해당 없음(생략).
판정 품질 (judgement, 측정만): 복잡도·난이도·가능 여부·영역 영향 유형 일치, [변경]/[참조] 판정 정확도, 오답 경로 채택,
  (단순) 상세 분석 권장, (복잡) complex 인데 Minor·Verifier 미해결. 비교값이 없거나 해당 flow 가 아니면 생략.
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from .. import config
from ..schemas import (
    AnswerKey,
    AnswerNode,
    Complexity,
    Difficulty,
    Domain,
    ExcludedNode,
    Feasibility,
    GroupKpi,
    ImpactType,
    KpiCategory,
    KpiMetric,
    KpiMetricName,
    KpiOutput,
    NodeJudgement,
    NodeSeverity,
    NodeVerdict,
    VerifyResult,
)
from ..state import AgentState
from .base import BaseAgent
from .simulator import CHANGE_TAG, REFERENCE_TAG, reanalysis_request, requirement_difficulty

# threshold 가 상한인 지표 (value <= threshold 이면 충족). 나머지는 하한.
_UPPER_BOUND = {KpiMetricName.TOOL_CALLS}

# 노드 ID 접두어 → 노드 유형 / 그래프 단계(0~5). DBIO 단계는 경로 맥락으로 정한다 (stage_of).
_TYPE_BY_PREFIX = {
    "SCR": "Screen", "CTL": "Controller", "TP": "TP", "BAT": "Batch", "SVC": "Service",
    "MOD": "Module", "DBIO": "DBIO", "SQL": "SQL", "TBL": "Table", "COL": "Column",
}
_STAGE_BY_PREFIX = {"SCR": 0, "CTL": 1, "TP": 1, "BAT": 1, "SVC": 2, "MOD": 2, "SQL": 3, "TBL": 4, "COL": 5}
_LOGIC_PREFIXES = {"MOD", "SVC"}
_SYSTEM_PREFIXES = {Domain.SWING: {"TP", "MOD"}, Domain.TOS: {"CTL", "SVC", "SQL"}}
TYPE_ORDER = list(_TYPE_BY_PREFIX.values())


def _prefix(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def node_type(node_id: str) -> str:
    return _TYPE_BY_PREFIX.get(_prefix(node_id), _prefix(node_id))


def stage_of(path: list[str], index: int) -> int | None:
    """path[index] 노드의 단계. DBIO 는 직전 노드가 Module/Service 이면 3(경유), 아니면 2(직접 호출)."""
    prefix = _prefix(path[index])
    if prefix == "DBIO":
        return 3 if index > 0 and _prefix(path[index - 1]) in _LOGIC_PREFIXES else 2
    return _STAGE_BY_PREFIX.get(prefix)


def stage_in_paths(node_id: str, paths: Iterable[list[str]]) -> int | None:
    """정답 경로에서 노드의 단계를 찾는다. 경로에 없으면 접두어 기준 (DBIO 는 알 수 없음 → None)."""
    for path in paths:
        if node_id in path:
            return stage_of(path, path.index(node_id))
    return _STAGE_BY_PREFIX.get(_prefix(node_id))


def infer_system(node_ids: Iterable[str]) -> Domain | None:
    """노드 접두어로 시스템 추정 (TP/MOD → swing, CTL/SVC/SQL → tos). 섞이거나 없으면 None."""
    prefixes = {_prefix(n) for n in node_ids}
    found = [d for d, ps in _SYSTEM_PREFIXES.items() if prefixes & ps]
    return found[0] if len(found) == 1 else None


def load_answer_key(path: str | Path) -> AnswerKey:
    """eval/ground_truth/<요건ID>.json → AnswerKey.

    정답: impacted 중 must_find (severity 는 impacted[].severity, 없으면 Major / stage 는 impacted[].stage, 없으면 정답 경로로 산출)
    평가 제외: must_find=false · reference_only · 정답 경로 위 노드(impacted 아님) · 미확정(pending) 경로 노드
    판정 품질 비교값: difficulty, expected.feasibility·areas, 변경 노드(impacted modify·add)·참조 노드(reference_only),
      오답 경로(wrong_paths)의 고유 노드 (정답 경로·영향 노드와 겹치는 노드는 뺌)
    """
    gt = json.loads(Path(path).read_text(encoding="utf-8"))
    exp = gt["expected"]
    confirmed = [p["nodes"] for p in exp.get("paths", []) if p.get("status", "confirmed") == "confirmed"]
    pending = [p["nodes"] for p in exp.get("paths", []) if p.get("status") == "pending"]

    answers = [
        AnswerNode(
            node_id=i["node_id"],
            type=node_type(i["node_id"]),
            stage=i.get("stage", stage_in_paths(i["node_id"], confirmed + pending)),
            severity=i.get("severity", NodeSeverity.MAJOR),
        )
        for i in exp["impacted"] if i["must_find"]
    ]
    answer_ids = {a.node_id for a in answers}
    excluded: dict[str, str] = {}
    for nid, reason in (
        [(i["node_id"], "must_find=false") for i in exp["impacted"] if not i["must_find"]]
        + [(n, "reference_only") for n in exp.get("reference_only", [])]
        + [(n, "정답 경로 노드 (impacted 아님)") for p in confirmed for n in p]
        + [(n, "미확정 경로 노드") for p in pending for n in p]
    ):
        if nid not in answer_ids:
            excluded.setdefault(nid, reason)

    all_nodes = answer_ids | excluded.keys()
    correct = {n for p in confirmed + pending for n in p} | {i["node_id"] for i in exp["impacted"]}
    wrong = [[n for n in w.get("nodes", []) if n not in correct] for w in gt.get("wrong_paths", [])]
    change = [i["node_id"] for i in exp["impacted"] if i.get("impact_type") in ("modify", "add")]
    return AnswerKey(
        req_id=gt["req_id"],
        system=gt.get("domain") or infer_system(all_nodes),
        sm_part=gt.get("sm_part"),
        complexity=gt.get("complexity"),
        eval_target_labels=gt.get("eval_target_labels", []),
        answer_nodes=answers,
        excluded_nodes=[ExcludedNode(node_id=n, reason=r) for n, r in excluded.items()],
        distractor_nodes=[d["node_id"] for d in gt.get("distractors", [])],
        expected_difficulty=gt.get("difficulty"),
        expected_feasibility=exp.get("feasibility"),
        expected_areas=exp.get("areas", {}),
        change_nodes=change,
        reference_nodes=[n for n in dict.fromkeys(exp.get("reference_only", []) + [  # 변경 노드와 겹치면 변경이 우선
            i["node_id"] for i in exp["impacted"] if i.get("impact_type") == "reference_only"]) if n not in change],
        wrong_paths=[w for w in wrong if w],
    )


def judge(key: AnswerKey, output: Iterable[str]) -> list[NodeJudgement]:
    """노드 판정표 (정답 노드 → 평가 제외 노드 → 그 밖의 출력 노드 순)."""
    out, labels = set(output), set(key.eval_target_labels)

    def in_scope(t: str) -> bool:
        return not labels or t in labels

    rows = []
    for a in key.answer_nodes:
        verdict = NodeVerdict.OUT_OF_SCOPE if not in_scope(a.type) else (
            NodeVerdict.DETECTED if a.node_id in out else NodeVerdict.MISSED)
        rows.append(NodeJudgement(node_id=a.node_id, type=a.type, stage=a.stage, severity=a.severity,
                                  verdict=verdict, in_output=a.node_id in out))
    seen = {a.node_id for a in key.answer_nodes}
    for e in key.excluded_nodes:
        if e.node_id not in seen:
            seen.add(e.node_id)
            rows.append(NodeJudgement(node_id=e.node_id, type=node_type(e.node_id), stage=_STAGE_BY_PREFIX.get(
                _prefix(e.node_id)), verdict=NodeVerdict.EXCLUDED, in_output=e.node_id in out))
    for n in dict.fromkeys(output):
        if n not in seen:
            t = node_type(n)
            rows.append(NodeJudgement(node_id=n, type=t, stage=_STAGE_BY_PREFIX.get(_prefix(n)), in_output=True,
                                      verdict=NodeVerdict.FALSE_POSITIVE if in_scope(t) else NodeVerdict.OUT_OF_SCOPE))
    return rows


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def group_kpis(rows: Iterable[NodeJudgement], key_of: Callable[[NodeJudgement], str]) -> dict[str, GroupKpi]:
    """판정표를 축 값별 TP/FN/FP 로 모은다 (평가 제외·범위 밖은 세지 않음)."""
    field = {NodeVerdict.DETECTED: "tp", NodeVerdict.MISSED: "fn", NodeVerdict.FALSE_POSITIVE: "fp"}
    groups: dict[str, GroupKpi] = {}
    for r in rows:
        if r.verdict in field:
            g = groups.setdefault(key_of(r), GroupKpi(key=key_of(r)))
            setattr(g, field[r.verdict], getattr(g, field[r.verdict]) + 1)
    for g in groups.values():
        g.recall, g.precision = ratio(g.tp, g.tp + g.fn), ratio(g.tp, g.tp + g.fp)
    return groups


def by_type(rows: Iterable[NodeJudgement]) -> list[GroupKpi]:
    groups = group_kpis(rows, lambda r: r.type)
    return sorted(groups.values(), key=lambda g: TYPE_ORDER.index(g.key) if g.key in TYPE_ORDER else len(TYPE_ORDER))


def by_stage(rows: Iterable[NodeJudgement]) -> list[GroupKpi]:
    groups = group_kpis(rows, lambda r: "?" if r.stage is None else str(r.stage))
    return [groups[k] for k in sorted(groups)]


def _metric(
    name: KpiMetricName,
    category: KpiCategory,
    value: float,
    unit: str,
    threshold: float | None = None,
) -> KpiMetric:
    passed = None
    if threshold is not None:
        passed = value <= threshold if name in _UPPER_BOUND else value >= threshold
    return KpiMetric(name=name, category=category, value=value, unit=unit, threshold=threshold, passed=passed)


def quality_metrics(rows: list[NodeJudgement]) -> list[KpiMetric]:
    q = KpiCategory.QUALITY
    count = {v: sum(r.verdict is v for r in rows) for v in NodeVerdict}
    tp, fn, fp = count[NodeVerdict.DETECTED], count[NodeVerdict.MISSED], count[NodeVerdict.FALSE_POSITIVE]
    critical = [r for r in rows if r.severity is NodeSeverity.CRITICAL and r.verdict in (NodeVerdict.DETECTED, NodeVerdict.MISSED)]
    metrics = []
    if tp + fn:
        metrics += [
            _metric(KpiMetricName.NODE_RECALL, q, tp / (tp + fn), "ratio", config.KPI_MIN_RECALL),
            _metric(KpiMetricName.NODE_MISS_RATE, q, fn / (tp + fn), "ratio"),
        ]
    if critical:
        missed = sum(r.verdict is NodeVerdict.MISSED for r in critical)
        metrics.append(_metric(KpiMetricName.CRITICAL_MISS_RATE, q, missed / len(critical), "ratio"))
    if tp + fp:
        metrics.append(_metric(KpiMetricName.NODE_PRECISION, q, tp / (tp + fp), "ratio", config.KPI_MIN_PRECISION))
    metrics += [
        _metric(KpiMetricName.EXCLUDED_NODES, q, count[NodeVerdict.EXCLUDED], "count"),
        _metric(KpiMetricName.OUT_OF_SCOPE_NODES, q, count[NodeVerdict.OUT_OF_SCOPE], "count"),
    ]
    return metrics


_FEASIBILITY_ORDER = [Feasibility.FEASIBLE, Feasibility.REVIEW, Feasibility.HARD]


def overall_feasibility(state: AgentState) -> Feasibility | None:
    """변경(add·modify) 영역의 종합 가능 여부 (가장 나쁜 값, Composer 요약과 같은 규칙). 변경 영역이 없으면 None."""
    changed = [a.feasibility for a in state.simulation.area_impacts if a.impact_type in (ImpactType.ADD, ImpactType.MODIFY)]
    return max(changed, key=_FEASIBILITY_ORDER.index) if changed else None


def hop_tags(state: AgentState) -> dict[str, str]:
    """Simulator 경로 노드별 판정: [변경] 이 한 번이라도 있으면 change, 아니면 [참조] 면 reference (표시 없으면 빠짐)."""
    tags: dict[str, str] = {}
    for a in state.simulation.area_impacts:
        for p in a.paths:
            for h in p.hops:
                if h.impact.startswith(CHANGE_TAG):
                    tags[h.node] = "change"
                elif h.impact.startswith(REFERENCE_TAG):
                    tags.setdefault(h.node, "reference")
    return tags


def judgement_metrics(state: AgentState, key: AnswerKey, complexity: Complexity) -> tuple[list[KpiMetric], list[str]]:
    """판정 품질 지표와 불일치 설명 (측정만, threshold 없음). 비교값이 없거나 해당 flow 가 아니면 그 지표를 생략한다."""
    j = KpiCategory.JUDGEMENT
    metrics, notes = [], []

    def flag(name: KpiMetricName, ok: bool, note: str | None = None) -> None:
        metrics.append(_metric(name, j, 1.0 if ok else 0.0, "flag"))
        if note and not ok:
            notes.append(note)

    if key.complexity is not None:
        flag(KpiMetricName.COMPLEXITY_MATCH, complexity is key.complexity,
             f"복잡도: 정답 {key.complexity.value} / 실행 {complexity.value}")

    if complexity is Complexity.SIMPLE:
        answer = state.composer.simple_answer if state.composer else None
        if answer is not None:
            recommended = answer.detail_analysis_recommended
            metrics.append(_metric(KpiMetricName.DETAIL_RECOMMENDED, j, 1.0 if recommended else 0.0, "flag"))
            if key.complexity is Complexity.COMPLEX and not recommended:
                notes.append("정답 complex 인데 단순 처리됐고 상세 분석 권장도 없음 (놓친 복잡 요건)")
            if key.complexity is Complexity.SIMPLE and recommended:
                notes.append(f"정답 simple 인데 상세 분석 권장 ({'; '.join(answer.detail_analysis_reasons)})")
        return metrics, notes

    sim = state.simulation
    if sim is None:
        return metrics, notes
    level, _ = requirement_difficulty(sim.area_impacts)
    changed = any(a.impact_type in (ImpactType.ADD, ImpactType.MODIFY) for a in sim.area_impacts)
    if key.expected_difficulty is not None:
        flag(KpiMetricName.DIFFICULTY_MATCH, level is key.expected_difficulty,
             f"요건 전체 난이도: 정답 {key.expected_difficulty.value} / 실행 {level.value}")
    feas = overall_feasibility(state)
    if key.expected_feasibility is not None and feas is not None:
        flag(KpiMetricName.FEASIBILITY_MATCH, feas is key.expected_feasibility,
             f"구현 가능 여부: 정답 {key.expected_feasibility.value} / 실행 {feas.value}")
    if key.expected_areas:
        actual = {a.area: a.impact_type for a in sim.area_impacts}
        wrong = [f"{ar.value} 정답 {t.value}/실행 {actual.get(ar, ImpactType.NONE).value}"
                 for ar, t in key.expected_areas.items() if actual.get(ar, ImpactType.NONE) is not t]
        metrics.append(_metric(KpiMetricName.AREA_IMPACT_MATCH, j,
                               (len(key.expected_areas) - len(wrong)) / len(key.expected_areas), "ratio"))
        if wrong:
            notes.append(f"영역 영향 유형 불일치: {', '.join(wrong)}")
    tags = hop_tags(state)
    if tags:  # Simulator 가 [변경]/[참조] 표시를 했을 때만
        expected = {n: "change" for n in key.change_nodes} | {n: "reference" for n in key.reference_nodes}
        judged = {n: t for n, t in expected.items() if n in tags}
        if judged:
            off = [f"{n}(정답 {'변경' if t == 'change' else '참조'})" for n, t in judged.items() if tags[n] != t]
            metrics.append(_metric(KpiMetricName.CHANGE_TAG_ACCURACY, j, (len(judged) - len(off)) / len(judged), "ratio"))
            if off:
                notes.append(f"[변경]/[참조] 판정 불일치: {', '.join(off)}")
        if key.wrong_paths:
            adopted = [w for w in key.wrong_paths if any(tags.get(n) == "change" for n in w)]
            metrics.append(_metric(KpiMetricName.WRONG_PATH_ADOPTED, j, len(adopted), "count"))
            if adopted:
                notes.append(f"오답 경로 채택: {'; '.join(', '.join(w) for w in adopted)} 을 [변경] 으로 판정")
    metrics.append(_metric(KpiMetricName.COMPLEX_MINOR, j, 1.0 if changed and level is Difficulty.MINOR else 0.0, "flag"))
    unresolved = state.verifier is not None and state.verifier.result is VerifyResult.NEEDS_SUPPLEMENT
    metrics.append(_metric(KpiMetricName.VERIFIER_UNRESOLVED, j, 1.0 if unresolved else 0.0, "flag"))
    if unresolved:
        notes.append(f"Verifier 보완 필요 미해결: {state.verifier.reason}")
    return metrics, notes


class KpiAgent(BaseAgent[KpiOutput]):
    name = "kpi"
    output_model = KpiOutput

    def run(self, state: AgentState) -> KpiOutput:
        complexity = state.planner.complexity if state.planner else Complexity.SIMPLE
        key = state.answer_key
        metrics = self._performance(state)
        base = {"complexity": complexity, "req_id": key.req_id if key else None, "agent_runs": list(state.agent_runs)}

        nodes = self.final_nodes(state) if key else None
        if key is None or nodes is None:
            reason = "AnswerKey 없음 (성능 지표만)" if key is None else f"final_nodes 없음: {complexity.value} flow 의 결과 노드가 없음"
            warnings = [] if key is None else [f"평가 불가 - {reason}"]
            return KpiOutput(**base, evaluable=False, skip_reason=reason, metrics=metrics,
                             all_passed=not [m for m in metrics if m.passed is False], warnings=warnings)

        rows = judge(key, nodes)
        judged, notes = judgement_metrics(state, key, complexity)
        metrics = quality_metrics(rows) + judged + metrics
        failed = [m for m in metrics if m.passed is False]
        return KpiOutput(
            **base,
            metrics=metrics,
            final_nodes=nodes,
            judgements=rows,
            by_type=by_type(rows),
            by_stage=by_stage(rows),
            missed_nodes=[r.node_id for r in rows if r.verdict is NodeVerdict.MISSED],
            false_positive_nodes=[r.node_id for r in rows if r.verdict is NodeVerdict.FALSE_POSITIVE],
            distractors_found=[n for n in key.distractor_nodes if n in set(nodes)],
            all_passed=not failed,
            warnings=[f"{m.name.value}={m.value:g} (기준 {m.threshold:g}) 미달" for m in failed],
            reanalyzed=reanalysis_request(state) is not None,
            judgement_notes=notes,
        )

    @staticmethod
    def final_nodes(state: AgentState) -> list[str] | None:
        """평가할 Agent 출력 노드 (설계서 2장, 중복 제거). 결과가 없으면 None (평가 불가).

        - 복잡 (Retriever → Simulator → Verifier → Composer): Simulator 영향 경로의 노드 (재계획이 있었으면 마지막 라운드 = 현재 상태)
        - 단순 (Retriever → Composer): Retriever 최종 결과 = 근거 노드 + 근거 없음 노드 (재탐색이 있었으면 마지막 라운드)
        """
        if state.planner is not None and state.planner.complexity is Complexity.COMPLEX:
            if state.simulation is None:
                return None
            nodes = [h.node for a in state.simulation.area_impacts for p in a.paths for h in p.hops]
        else:
            if state.retriever is None:
                return None
            nodes = [e.node_id for e in state.retriever.evidence if e.node_id] + state.retriever.nodes_without_evidence
        return list(dict.fromkeys(nodes))

    def _performance(self, state: AgentState) -> list[KpiMetric]:
        p = KpiCategory.PERFORMANCE
        runs = state.agent_runs
        metrics = [
            _metric(KpiMetricName.LATENCY_MS, p, sum(r.latency_ms for r in runs), "ms"),
            _metric(KpiMetricName.TOOL_CALLS, p, sum(r.tool_calls for r in runs), "count", config.MAX_TOOL_CALLS),
            _metric(KpiMetricName.EXPLORE_LOOPS, p, state.explore_loop_count, "count"),
            _metric(KpiMetricName.REPLANS, p, state.replan_count, "count"),
        ]
        tokens = [(r.input_tokens or 0) + (r.output_tokens or 0) for r in runs if r.input_tokens or r.output_tokens]
        if tokens:
            metrics.append(_metric(KpiMetricName.TOTAL_TOKENS, p, sum(tokens), "tokens"))
        return metrics
