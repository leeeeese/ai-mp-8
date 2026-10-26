"""저장된 실행 결과(<case>.state.json)를 정답셋과 대조해 KPI 를 배치로 계산한다 (KPI 설계서 7·8장, 워크플로와 분리된 사후 평가).

usage:
    python scripts/eval_kpi.py outputs/runs/YJ/<run_id>                       # 폴더 안 *.state.json 전체
    python scripts/eval_kpi.py a/SC-001.state.json b/H-1.state.json --agent-version v0.3 --out outputs/kpi/v0.3

사례 ID 는 파일명(<case>.state.json)에서 읽고, 정답은 eval/ground_truth/<case>.json 을 쓴다.
출력 (--out, 기본: 입력 폴더의 kpi/)
    judgements.csv  사례별 노드 판정표
    cases.csv       사례별 KPI (시스템 · SM 파트 · flow · Agent 버전)
    summary.json    합산 · 시스템 · SM 파트 · flow · 노드 유형 · 단계별 KPI (TP/FN/FP 를 합산해 계산)
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from impact_agent.agents import KpiAgent
from impact_agent.agents.kpi import by_stage, by_type, group_kpis, load_answer_key, ratio
from impact_agent.schemas import NodeSeverity, NodeVerdict
from impact_agent.state import AgentState

GT_DIR = ROOT / "eval" / "ground_truth"


def state_files(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
        files += sorted(p.glob("*.state.json")) if p.is_dir() else [p]
    return files


def load_state(path: Path) -> AgentState:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("kpi", None)  # 이전 버전 KPI 결과는 다시 계산
    data.pop("answer_key", None)
    return AgentState.model_validate(data)


def critical_counts(rows) -> tuple[int, int]:
    crit = [r for r in rows if r.severity is NodeSeverity.CRITICAL and r.verdict in (NodeVerdict.DETECTED, NodeVerdict.MISSED)]
    return sum(r.verdict is NodeVerdict.MISSED for r in crit), len(crit)


def summarize(cases: list[dict], axis: str | None) -> dict:
    """axis 값별로 사례들의 판정을 합쳐 KPI 계산 (axis=None 이면 전체)."""
    out = {}
    for case in cases:
        k = "all" if axis is None else str(case[axis])
        out.setdefault(k, []).append(case)
    result = {}
    for k, group in out.items():
        rows = [r for c in group for r in c["rows"]]
        g = group_kpis(rows, lambda r: "all").get("all")
        tp, fn, fp = (g.tp, g.fn, g.fp) if g else (0, 0, 0)
        crit_missed, crit_total = critical_counts(rows)
        result[k] = {
            "cases": len(group), "tp": tp, "fn": fn, "fp": fp,
            "node_recall": ratio(tp, tp + fn), "node_miss_rate": ratio(fn, tp + fn),
            "critical_miss_rate": ratio(crit_missed, crit_total), "critical_total": crit_total,
            "node_precision": ratio(tp, tp + fp),
            "excluded": sum(r.verdict is NodeVerdict.EXCLUDED for r in rows),
            "out_of_scope": sum(r.verdict is NodeVerdict.OUT_OF_SCOPE for r in rows),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path, help="*.state.json 파일 또는 폴더")
    parser.add_argument("--agent-version", default="-", help="리포트에 기록할 Agent 버전")
    parser.add_argument("--out", type=Path, help="출력 폴더 (기본: 첫 입력 폴더의 kpi/)")
    args = parser.parse_args()

    files = state_files(args.inputs)
    if not files:
        parser.error("*.state.json 이 없습니다")
    out_dir = args.out or (args.inputs[0] if args.inputs[0].is_dir() else args.inputs[0].parent) / "kpi"
    out_dir.mkdir(parents=True, exist_ok=True)

    cases, skipped = [], []
    for f in files:
        case_id = f.name.removesuffix(".state.json")
        gt = GT_DIR / f"{case_id}.json"
        if not gt.exists():
            skipped.append((case_id, "정답셋 없음"))
            continue
        state = load_state(f)
        state.answer_key = load_answer_key(gt)
        kpi = KpiAgent().run(state)
        if not kpi.evaluable:
            skipped.append((case_id, kpi.skip_reason))
            continue
        key = state.answer_key
        cases.append({
            "case_id": case_id, "system": key.system.value if key.system else "-",
            "sm_part": key.sm_part.value if key.sm_part else "-", "flow": kpi.complexity.value,
            "kpi": kpi, "rows": kpi.judgements,
        })

    with open(out_dir / "judgements.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["case_id", "agent_version", "node_id", "type", "stage", "severity", "verdict", "in_output"])
        for c in cases:
            for r in c["rows"]:
                w.writerow([c["case_id"], args.agent_version, r.node_id, r.type, "" if r.stage is None else r.stage,
                            r.severity.value if r.severity else "", r.verdict.value, r.in_output])

    names = ["node_recall", "node_miss_rate", "critical_miss_rate", "node_precision", "excluded_nodes",
             "out_of_scope_nodes", "latency_ms", "tool_calls"]
    with open(out_dir / "cases.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["case_id", "system", "sm_part", "flow", "agent_version", *names, "missed_nodes", "false_positive_nodes"])
        for c in cases:
            v = {m.name.value: m.value for m in c["kpi"].metrics}
            w.writerow([c["case_id"], c["system"], c["sm_part"], c["flow"], args.agent_version,
                        *[v.get(n, "") for n in names], " ".join(c["kpi"].missed_nodes), " ".join(c["kpi"].false_positive_nodes)])

    all_rows = [r for c in cases for r in c["rows"]]
    summary = {
        "agent_version": args.agent_version,
        "skipped": [{"case_id": k, "reason": r} for k, r in skipped],
        "overall": summarize(cases, None).get("all"),
        "by_system": summarize(cases, "system"),
        "by_sm_part": summarize(cases, "sm_part"),
        "by_flow": summarize(cases, "flow"),
        "by_type": [g.model_dump() for g in by_type(all_rows)],
        "by_stage": [g.model_dump() for g in by_stage(all_rows)],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    o = summary["overall"] or {}
    pct = lambda x: "-" if x is None else f"{x * 100:.1f}%"
    print(f"평가 {len(cases)}건 · 제외 {len(skipped)}건 → {out_dir}")
    if o:
        print(f"탐지율 {pct(o['node_recall'])} · 미탐률 {pct(o['node_miss_rate'])} · "
              f"Critical 미탐률 {pct(o['critical_miss_rate'])} · 정밀도 {pct(o['node_precision'])}")
    for k, r in skipped:
        print(f"  [제외] {k}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
