"""KPI Agent: PoC 평가. Composer 이후 실행하며 결과는 Composer 에 전달하지 않는다 (LLM 미사용).

입력: 정답 경로 목록(AnswerKey) + 실행 로그(agent_runs) + Agent 가 제시한 영향 경로
핵심 KPI (경로 단위):
  - 영향 경로 재현율   = 정확 탐지 경로 / 확정 정답 경로
  - Critical 미탐율    = Critical 확정 경로 중 정확 탐지하지 못한 경로 (부분·초기 포함) / Critical 확정 경로
  - 경로 정밀도        = 정확 경로 / 제시 경로 (분모에 부분·초기 포함 = 가안, 제외 = 보조 지표)

탐지 판정: 정답 경로 A→B→…→끝 의 앞에서부터 Agent 경로가 순서대로 따라간 노드 수 k 로 판정한다.
  k = 전체 → exact (중간 노드 추가·끝 이후 연장 허용) / k ≥ 2 → partial / k = 1 → initial / k = 0 → missed
미확정(pending) 정답 경로와 그에 대응하는 제시 경로는 재현율·정밀도 계산에서 모두 제외하고 건수만 공개한다.
"""

import json
from pathlib import Path

from .. import config
from ..schemas import (
    AnswerKey,
    AnswerPath,
    AnswerPathStatus,
    Complexity,
    KpiCategory,
    KpiMetric,
    KpiMetricName,
    KpiOutput,
    PathDetection,
    PathResult,
)
from ..state import AgentState
from .base import BaseAgent

# threshold 가 상한인 지표 (value <= threshold 이면 충족). 나머지는 하한.
_UPPER_BOUND = {KpiMetricName.TOOL_CALLS}

# 노드 ID 접두어 → 그래프 단계(A~F = 0~5). DBIO 는 직전 노드가 Module/Service 이면 D(3), 아니면 C(2).
_STAGE_BY_PREFIX = {"SCR": "A", "TP": "B", "BAT": "B", "CTL": "B", "MOD": "C", "SVC": "C", "SQL": "D", "TBL": "E", "COL": "F"}
_LOGIC_PREFIXES = {"MOD", "SVC"}


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


def _prefix(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def stage_of(path: list[str], index: int) -> str | None:
    """path[index] 노드의 단계 문자 (A~F)."""
    prefix = _prefix(path[index])
    if prefix == "DBIO":
        return "D" if index > 0 and _prefix(path[index - 1]) in _LOGIC_PREFIXES else "C"
    return _STAGE_BY_PREFIX.get(prefix)


def matched_prefix_len(answer: list[str], predicted: list[str]) -> int:
    """정답 경로의 앞에서부터 predicted 안에 순서대로 등장하는 노드 수."""
    k = 0
    for node in predicted:
        if k < len(answer) and node == answer[k]:
            k += 1
    return k


def detection_of(k: int, path_len: int) -> PathDetection:
    if k == path_len:
        return PathDetection.EXACT
    if k >= 2:
        return PathDetection.PARTIAL
    if k == 1:
        return PathDetection.INITIAL
    return PathDetection.MISSED


def load_answer_key(path: str | Path) -> AnswerKey:
    """eval/ground_truth/<요건ID>.json → AnswerKey.

    paths: expected.paths / expected: impacted 중 must_find / optional: 나머지 impacted + reference_only
    distractor: distractors
    """
    gt = json.loads(Path(path).read_text(encoding="utf-8"))
    exp = gt["expected"]
    return AnswerKey(
        req_id=gt["req_id"],
        paths=[AnswerPath(**p) for p in exp.get("paths", [])],
        expected_nodes=[i["node_id"] for i in exp["impacted"] if i["must_find"]],
        optional_nodes=[i["node_id"] for i in exp["impacted"] if not i["must_find"]] + exp.get("reference_only", []),
        distractor_nodes=[d["node_id"] for d in gt.get("distractors", [])],
    )


class KpiAgent(BaseAgent[KpiOutput]):
    name = "kpi"
    output_model = KpiOutput

    def run(self, state: AgentState) -> KpiOutput:
        complexity = state.planner.complexity if state.planner else Complexity.SIMPLE
        key = state.answer_key
        metrics = self._performance(state)
        out = {}

        if key is not None:
            predicted = self.predicted_paths(state)
            path_results, false_positives, path_metrics = self._paths(key.paths, predicted)
            node_metrics = self._nodes(key, {n for p in predicted for n in p})
            metrics = path_metrics + metrics
            out = {"path_results": path_results, "false_positive_paths": false_positives, **node_metrics}

        failed = [m for m in metrics if m.passed is False]
        return KpiOutput(
            complexity=complexity,
            req_id=key.req_id if key else None,
            metrics=metrics,
            agent_runs=list(state.agent_runs),
            all_passed=not failed,
            warnings=[f"{m.name.value}={m.value:g} (기준 {m.threshold:g}) 미달" for m in failed],
            **out,
        )

    @staticmethod
    def predicted_paths(state: AgentState) -> list[list[str]]:
        """Agent 가 제시한 영향 경로 (노드 ID 목록). 같은 경로는 한 번만 센다.

        우선순위: Composer 결과(복잡 path_details / 단순 impact_paths) → Simulator 경로.
        """
        paths = []
        composer = state.composer
        if composer and composer.complex_report:
            paths = composer.complex_report.path_details
        elif composer and composer.simple_answer:
            paths = composer.simple_answer.impact_paths
        if not paths and state.simulation:
            paths = [p for a in state.simulation.area_impacts for p in a.paths]
        unique = dict.fromkeys(tuple(h.node for h in p.hops) for p in paths if p.hops)
        return [list(p) for p in unique]

    def _paths(
        self, answers: list[AnswerPath], predicted: list[list[str]]
    ) -> tuple[list[PathResult], list[list[str]], list[KpiMetric]]:
        q = KpiCategory.QUALITY

        # 1) 정답 경로별 판정 (재현율 · Critical 미탐율)
        results = []
        for a in answers:
            k = max((matched_prefix_len(a.nodes, p) for p in predicted), default=0)
            detection = PathDetection.PENDING if a.status is AnswerPathStatus.PENDING else detection_of(k, len(a.nodes))
            results.append(PathResult(
                path_id=a.path_id, critical=a.critical, detection=detection, matched_len=k, path_len=len(a.nodes),
                reached_node=a.nodes[k - 1] if k else None, reached_stage=stage_of(a.nodes, k - 1) if k else None,
            ))
        confirmed = [r for r in results if r.detection is not PathDetection.PENDING]
        critical = [r for r in confirmed if r.critical]
        count = {d: sum(r.detection is d for r in results) for d in PathDetection}

        # 2) 제시 경로별 판정 (정밀도): 확정 정답과 정확 일치 → exact.
        #    아니면 가장 많이 따라간 정답 기준 (동률이면 확정 우선): 미확정 → 제외 / 확정 → 부분·초기 / 없음 → 오탐
        exact_n = partial_n = 0
        false_positives = []
        for p in predicted:
            ks = [(a, matched_prefix_len(a.nodes, p)) for a in answers]
            if any(a.status is AnswerPathStatus.CONFIRMED and k == len(a.nodes) for a, k in ks):
                exact_n += 1
                continue
            best_confirmed = max((k for a, k in ks if a.status is AnswerPathStatus.CONFIRMED), default=0)
            best_pending = max((k for a, k in ks if a.status is AnswerPathStatus.PENDING), default=0)
            if best_pending > best_confirmed:
                continue
            if best_confirmed > 0:
                partial_n += 1
            else:
                false_positives.append(p)
        fp_n = len(false_positives)

        metrics = []
        if confirmed:
            recall = count[PathDetection.EXACT] / len(confirmed)
            metrics.append(_metric(KpiMetricName.PATH_RECALL, q, recall, "ratio", config.KPI_MIN_RECALL))
        if critical:
            missed = sum(r.detection is not PathDetection.EXACT for r in critical)
            metrics.append(_metric(KpiMetricName.CRITICAL_MISS_RATE, q, missed / len(critical), "ratio"))
        if predicted:
            with_partial = exact_n + partial_n + fp_n
            exact_only = exact_n + fp_n
            metrics += [
                _metric(KpiMetricName.PATH_PRECISION, q, exact_n / with_partial if with_partial else 0.0, "ratio",
                        config.KPI_MIN_PRECISION),
                _metric(KpiMetricName.PATH_PRECISION_EXCL_PARTIAL, q, exact_n / exact_only if exact_only else 0.0, "ratio"),
            ]
        metrics += [
            _metric(KpiMetricName.PARTIAL_PATHS, q, count[PathDetection.PARTIAL], "count"),
            _metric(KpiMetricName.INITIAL_PATHS, q, count[PathDetection.INITIAL], "count"),
            _metric(KpiMetricName.PENDING_PATHS, q, count[PathDetection.PENDING], "count"),
        ]
        return results, false_positives, metrics

    @staticmethod
    def _nodes(key: AnswerKey, predicted: set[str]) -> dict[str, list[str]]:
        """노드 단위 보조 집계 (경로 KPI 해석용)."""
        expected = set(key.expected_nodes)
        allowed = expected | set(key.optional_nodes)
        return {
            "matched_nodes": sorted(predicted & expected),
            "missed_nodes": sorted(expected - predicted),
            "extra_nodes": sorted(predicted - allowed),
        }

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
