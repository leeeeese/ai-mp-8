"""저장된 실행 결과(<case>.state.json)를 정답셋과 대조해 KPI 를 배치로 계산한다 (KPI 설계서 7·8장, 워크플로와 분리된 사후 평가).

usage:
    python scripts/eval_kpi.py outputs/runs/YJ/<run_id>                       # 폴더 안 *.state.json 전체
    python scripts/eval_kpi.py a/SC-001.state.json b/H-1.state.json --agent-version v0.3 --out outputs/kpi/v0.3
    python scripts/eval_kpi.py outputs/runs/v0.4 --agent-version v0.4 --compare outputs/kpi/v0.3/summary.json   # 버전 비교

사례 ID 는 파일명(<case>.state.json)에서 읽고, 정답은 eval/ground_truth/<case>.json 을 쓴다.
출력 (--out, 기본: 입력 폴더의 kpi/)
    judgements.csv  사례별 노드 판정표
    cases.csv       사례별 KPI (시스템 · SM 파트 · flow · Agent 버전) + 판정 품질 지표·재분석 여부·불일치 설명
    summary.json    합산 · 시스템 · SM 파트 · flow · 노드 유형 · 단계별 KPI (TP/FN/FP 를 합산해 계산) + 판정 품질 집계
    report.md       요약 수치 · 판정 품질 · 미탐 노드(Critical 우선) · 오탐 · 판정 불일치 사례 · (--compare) 이전 버전 비교
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
from impact_agent.schemas import Complexity, NodeSeverity, NodeVerdict
from impact_agent.state import AgentState

GT_DIR = ROOT / "eval" / "ground_truth"
# 판정 품질 지표 (KpiMetricName judgement). 사례 열·집계·리포트·비교에 같은 순서로 쓴다
JUDGEMENT = ["complexity_match", "difficulty_match", "feasibility_match", "area_impact_match", "change_tag_accuracy",
             "wrong_path_adopted", "detail_recommended", "complex_minor", "verifier_unresolved"]
# 버전 비교표에 올리는 값 (summary 의 overall · judgement 키)
COMPARE = [("overall", "node_recall"), ("overall", "critical_miss_rate"), ("overall", "node_precision"),
           ("judgement", "complexity_match_rate"), ("judgement", "difficulty_match_rate"),
           ("judgement", "feasibility_match_rate"), ("judgement", "area_impact_match_avg"),
           ("judgement", "change_tag_accuracy_avg"), ("judgement", "detail_catch_rate"),
           ("judgement", "detail_over_rate"), ("judgement", "wrong_path_adopted"), ("judgement", "complex_minor"),
           ("judgement", "verifier_unresolved"), ("judgement", "reanalyzed")]


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


def metric_values(kpi) -> dict[str, float]:
    return {m.name.value: m.value for m in kpi.metrics}


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def judgement_summary(cases: list[dict]) -> dict:
    """판정 품질 집계. 일치율·평균은 그 지표가 있는 사례만으로, 건수는 합계로 낸다.

    상세 분석 권장: catch = 정답 complex 인데 단순 처리된 사례 중 권장한 비율 (놓친 복잡 요건을 붙잡았는가),
    over = 정답 simple 이고 단순 처리된 사례 중 권장한 비율 (과한 권장).
    """
    vals = [metric_values(c["kpi"]) for c in cases]

    def collect(name: str) -> list[float]:
        return [v[name] for v in vals if name in v]

    missed_complex = [v for c, v in zip(cases, vals) if c["flow"] == "simple" and c["gt_complexity"] == "complex"]
    simple_ok = [v for c, v in zip(cases, vals) if c["flow"] == "simple" and c["gt_complexity"] == "simple"]
    recommended = lambda vs: [v.get("detail_recommended", 0.0) for v in vs]  # noqa: E731
    return {
        "complexity_match_rate": mean(collect("complexity_match")),
        "difficulty_match_rate": mean(collect("difficulty_match")),
        "feasibility_match_rate": mean(collect("feasibility_match")),
        "area_impact_match_avg": mean(collect("area_impact_match")),
        "change_tag_accuracy_avg": mean(collect("change_tag_accuracy")),
        "wrong_path_adopted": sum(collect("wrong_path_adopted")),
        "detail_catch_cases": len(missed_complex), "detail_catch_rate": mean(recommended(missed_complex)),
        "detail_over_cases": len(simple_ok), "detail_over_rate": mean(recommended(simple_ok)),
        "complex_minor": sum(collect("complex_minor")),
        "verifier_unresolved": sum(collect("verifier_unresolved")),
        "reanalyzed": sum(c["kpi"].reanalyzed for c in cases),
    }


def compare_rows(previous: dict, current: dict) -> list[tuple[str, float | None, float | None, float | None]]:
    """이전 / 현재 summary 의 비교 행: (지표, 이전, 현재, 차이). 한쪽에 값이 없으면 차이는 None."""
    rows = []
    for section, name in COMPARE:
        before = (previous.get(section) or {}).get(name)
        after = (current.get(section) or {}).get(name)
        rows.append((f"{section}.{name}", before, after,
                     None if before is None or after is None else after - before))
    return rows


def is_ratio(name: str) -> bool:
    """비율(0~1) 지표인지. 그 밖(…_cases, wrong_path_adopted, complex_minor 등)은 건수."""
    return name.rsplit(".", 1)[-1].endswith(("_rate", "_avg", "recall", "precision"))


def fmt(x: float | None, name: str, signed: bool = False) -> str:
    if x is None:
        return "-"
    sign = "+" if signed and x > 0 else ""
    return f"{sign}{x * 100:.1f}%" if is_ratio(name) else f"{sign}{x:g}"


def report_markdown(summary: dict, cases: list[dict], previous: dict | None = None) -> str:
    """사람이 읽는 평가 리포트 (KPI 설계서 2장)."""
    o, j = summary.get("overall") or {}, summary["judgement"]
    lines = [f"# KPI 평가 리포트 ({summary['agent_version']})", "",
             f"평가 {len(cases)}건 · 제외 {len(summary['skipped'])}건 · 재분석 {j['reanalyzed']}건", "",
             "## 노드 탐지", "", "| 지표 | 값 |", "|---|---|"]
    lines += [f"| {k} | {fmt(o.get(k), k)} |" for k in ("node_recall", "node_miss_rate", "critical_miss_rate", "node_precision")]
    lines += ["", "## 판정 품질 (측정만)", "", "| 지표 | 값 |", "|---|---|"]
    lines += [f"| {k} | {fmt(v, k)} |" for k, v in j.items()]
    missed = [(c["case_id"], r) for c in cases for r in c["rows"] if r.verdict is NodeVerdict.MISSED]
    missed.sort(key=lambda x: x[1].severity is not NodeSeverity.CRITICAL)
    lines += ["", "## 미탐 노드 (Critical 우선)", ""]
    lines += [f"- {cid} {r.node_id} ({r.severity.value if r.severity else '-'})" for cid, r in missed] or ["- 없음"]
    lines += ["", "## 오탐 노드", ""]
    lines += [f"- {c['case_id']}: {', '.join(c['kpi'].false_positive_nodes)}" for c in cases if c["kpi"].false_positive_nodes] \
        or ["- 없음"]
    lines += ["", "## 판정 불일치", ""]
    lines += [f"- {c['case_id']}: {n}" for c in cases for n in c["kpi"].judgement_notes] or ["- 없음"]
    if summary["skipped"]:
        lines += ["", "## 평가 제외", ""] + [f"- {s['case_id']}: {s['reason']}" for s in summary["skipped"]]
    if previous is not None:
        lines += ["", f"## 버전 비교 ({previous.get('agent_version', '-')} → {summary['agent_version']})", "",
                  "| 지표 | 이전 | 현재 | 차이 |", "|---|---|---|---|"]
        for name, before, after, diff in compare_rows(previous, summary):
            lines.append(f"| {name} | {fmt(before, name)} | {fmt(after, name)} | {fmt(diff, name, signed=True)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path, help="*.state.json 파일 또는 폴더")
    parser.add_argument("--agent-version", default="-", help="리포트에 기록할 Agent 버전")
    parser.add_argument("--out", type=Path, help="출력 폴더 (기본: 첫 입력 폴더의 kpi/)")
    parser.add_argument("--compare", type=Path, help="비교할 이전 버전 summary.json (report.md 에 버전 비교표)")
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
            "gt_complexity": key.complexity.value if isinstance(key.complexity, Complexity) else "-",
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
        w.writerow(["case_id", "system", "sm_part", "flow", "gt_complexity", "agent_version", *names, *JUDGEMENT, "reanalyzed",
                    "missed_nodes", "false_positive_nodes", "judgement_notes"])
        for c in cases:
            v = metric_values(c["kpi"])
            w.writerow([c["case_id"], c["system"], c["sm_part"], c["flow"], c["gt_complexity"], args.agent_version,
                        *[v.get(n, "") for n in names], *[v.get(n, "") for n in JUDGEMENT], c["kpi"].reanalyzed,
                        " ".join(c["kpi"].missed_nodes), " ".join(c["kpi"].false_positive_nodes),
                        " | ".join(c["kpi"].judgement_notes)])

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
        "judgement": judgement_summary(cases),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    previous = json.loads(args.compare.read_text(encoding="utf-8")) if args.compare else None
    (out_dir / "report.md").write_text(report_markdown(summary, cases, previous), encoding="utf-8")

    o = summary["overall"] or {}
    pct = lambda x: "-" if x is None else f"{x * 100:.1f}%"
    print(f"평가 {len(cases)}건 · 제외 {len(skipped)}건 → {out_dir}")
    if o:
        print(f"탐지율 {pct(o['node_recall'])} · 미탐률 {pct(o['node_miss_rate'])} · "
              f"Critical 미탐률 {pct(o['critical_miss_rate'])} · 정밀도 {pct(o['node_precision'])}")
    jd = summary["judgement"]
    print(f"복잡도 일치 {pct(jd['complexity_match_rate'])} · 난이도 일치 {pct(jd['difficulty_match_rate'])} · "
          f"영역 일치 {pct(jd['area_impact_match_avg'])} · 상세 분석 권장 catch {pct(jd['detail_catch_rate'])}")
    for k, r in skipped:
        print(f"  [제외] {k}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
