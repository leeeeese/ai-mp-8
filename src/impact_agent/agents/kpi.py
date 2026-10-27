"""KPI Agent: PoC 평가. Composer 이후 실행하며 결과는 Composer 에 전달하지 않는다 (LLM 미사용). 설계: docs/SE_kpi-agent-design.md

입력: 정답 노드 목록(AnswerKey) + 실행 로그(agent_runs, 단계별 산출물에서 뽑은 final_nodes). Composer 산출물 문장은 쓰지 않는다.
노드 판정 (5.1):
  - 탐지(TP)  정답 노드가 final_nodes 에 있음 / 미탐(FN) 없음
  - 오탐(FP)  출력 노드가 정답·제외 목록에 없고 유형이 eval_target_labels 안
  - 평가 제외 excluded_nodes (출력돼도 오탐 아님, 분모·분자 제외) / 범위 밖: 유형이 eval_target_labels 밖
핵심 KPI (6.1): 노드 탐지율 = TP/(TP+FN), 미탐률 = FN/(TP+FN), Critical 미탐률, 노드 정밀도 = TP/(TP+FP). 분모 0 이면 해당 없음(생략).
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from .. import config
from ..schemas import (
    AnswerKey,
    AnswerNode,
    Complexity,
    Domain,
    ExcludedNode,
    GroupKpi,
    KpiCategory,
    KpiMetric,
    KpiMetricName,
    KpiOutput,
    NodeJudgement,
    NodeSeverity,
    NodeVerdict,
)
from ..state import AgentState
from .base import BaseAgent

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
    return AnswerKey(
        req_id=gt["req_id"],
        system=gt.get("domain") or infer_system(all_nodes),
        sm_part=gt.get("sm_part"),
        complexity=gt.get("complexity"),
        eval_target_labels=gt.get("eval_target_labels", []),
        answer_nodes=answers,
        excluded_nodes=[ExcludedNode(node_id=n, reason=r) for n, r in excluded.items()],
        distractor_nodes=[d["node_id"] for d in gt.get("distractors", [])],
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
        metrics = quality_metrics(rows) + metrics
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
