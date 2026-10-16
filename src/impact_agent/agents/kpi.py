"""KPI Agent: 분석 결과의 품질 지표와 실행 성능 지표를 측정한다 (LLM 미사용).

단순: Retriever 이후, 복잡: Verifier 이후 실행되며 결과는 Composer 로 전달된다.
"""

from .. import config
from ..schemas import (
    Area,
    Complexity,
    Confidence,
    KpiCategory,
    KpiMetric,
    KpiMetricName,
    KpiOutput,
    VerifyResult,
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


class KpiAgent(BaseAgent[KpiOutput]):
    name = "kpi"
    output_model = KpiOutput

    def run(self, state: AgentState) -> KpiOutput:
        complexity = state.planner.complexity if state.planner else Complexity.SIMPLE
        metrics = self._quality(state, complexity) + self._performance(state)
        failed = [m for m in metrics if m.passed is False]
        return KpiOutput(
            complexity=complexity,
            metrics=metrics,
            agent_runs=list(state.agent_runs),
            all_passed=not failed,
            warnings=[f"{m.name.value}={m.value:g} (기준 {m.threshold:g}) 미달" for m in failed],
        )

    def _quality(self, state: AgentState, complexity: Complexity) -> list[KpiMetric]:
        q = KpiCategory.QUALITY
        metrics: list[KpiMetric] = []

        if state.explorer and state.retriever:
            total = len(state.explorer.nodes)
            missing = len(set(state.retriever.nodes_without_evidence))
            coverage = (total - missing) / total if total else 0.0
            metrics.append(
                _metric(KpiMetricName.EVIDENCE_COVERAGE, q, coverage, "ratio", config.KPI_MIN_EVIDENCE_COVERAGE)
            )

        if complexity is Complexity.COMPLEX and state.simulation:
            hops = [h for a in state.simulation.area_impacts for p in a.paths for h in p.hops]
            confirmed = sum(h.confidence is Confidence.CONFIRMED for h in hops)
            ratio = confirmed / len(hops) if hops else 0.0
            areas = {a.area for a in state.simulation.area_impacts}
            metrics += [
                _metric(KpiMetricName.CONFIRMED_RATIO, q, ratio, "ratio", config.KPI_MIN_CONFIRMED_RATIO),
                _metric(KpiMetricName.AREA_COVERAGE, q, len(areas) / len(Area), "ratio"),
                _metric(KpiMetricName.UNVERIFIED_SEGMENTS, q, len(state.simulation.unverified_segments), "count"),
            ]
        if complexity is Complexity.COMPLEX and state.verifier:
            passed = float(state.verifier.result is VerifyResult.PASS)
            metrics.append(_metric(KpiMetricName.VERIFY_PASSED, q, passed, "bool", 1.0))

        return metrics

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
