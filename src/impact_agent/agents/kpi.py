"""KPI Agent: PoC 평가. Composer 이후 실행하며 결과는 Composer 에 전달하지 않는다 (LLM 미사용).

입력: 정답 경로 목록(AnswerKey) + 실행 로그(agent_runs) + 분석 결과
지표: 재현율 · 미탐율 · 정밀도 (AnswerKey 가 있을 때) / 시간 등 실행 성능
"""

import json
from pathlib import Path

from .. import config
from ..schemas import (
    AnswerKey,
    Complexity,
    KpiCategory,
    KpiMetric,
    KpiMetricName,
    KpiOutput,
)
from ..state import AgentState
from .base import BaseAgent

# threshold 가 상한인 지표 (value <= threshold 이면 충족). 나머지는 하한.
_UPPER_BOUND = {KpiMetricName.TOOL_CALLS}


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


def load_answer_key(path: str | Path) -> AnswerKey:
    """data/ground_truth/<요건ID>.json → AnswerKey.

    expected: impacted 중 must_find / optional: 나머지 impacted + reference_only / distractor: distractors
    """
    gt = json.loads(Path(path).read_text(encoding="utf-8"))
    exp = gt["expected"]
    return AnswerKey(
        req_id=gt["req_id"],
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
        matched: list[str] = []
        missed: list[str] = []
        extra: list[str] = []

        if key is not None:
            predicted = self.predicted_nodes(state)
            expected = set(key.expected_nodes)
            allowed = expected | set(key.optional_nodes)
            matched = sorted(predicted & expected)
            missed = sorted(expected - predicted)
            extra = sorted(predicted - allowed)
            metrics = self._quality(expected, predicted, allowed) + metrics

        failed = [m for m in metrics if m.passed is False]
        return KpiOutput(
            complexity=complexity,
            req_id=key.req_id if key else None,
            metrics=metrics,
            agent_runs=list(state.agent_runs),
            matched_nodes=matched,
            missed_nodes=missed,
            extra_nodes=extra,
            all_passed=not failed,
            warnings=[f"{m.name.value}={m.value:g} (기준 {m.threshold:g}) 미달" for m in failed],
        )

    @staticmethod
    def predicted_nodes(state: AgentState) -> set[str]:
        """에이전트가 영향으로 낸 노드. 복잡: Simulator 경로의 노드 / 단순: Retriever 근거 노드.

        TODO: Composer 산출물 기준으로 바꿀지 팀 내 확정 필요.
        """
        if state.simulation:
            return {h.node for a in state.simulation.area_impacts for p in a.paths for h in p.hops}
        if state.retriever:
            return {e.node_id for e in state.retriever.evidence if e.node_id}
        return set()

    def _quality(self, expected: set[str], predicted: set[str], allowed: set[str]) -> list[KpiMetric]:
        q = KpiCategory.QUALITY
        recall = len(predicted & expected) / len(expected) if expected else 0.0
        precision = len(predicted & allowed) / len(predicted) if predicted else 0.0
        return [
            _metric(KpiMetricName.RECALL, q, recall, "ratio", config.KPI_MIN_RECALL),
            _metric(KpiMetricName.MISS_RATE, q, 1 - recall, "ratio"),
            _metric(KpiMetricName.PRECISION, q, precision, "ratio", config.KPI_MIN_PRECISION),
        ]

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
