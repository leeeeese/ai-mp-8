"""SE 반복 실험. 두 가지 모드로 같은 실험을 N회 반복하고 회차 평균·범위로 본다.

- 상류 고정 (--from <run>): Planner·Explorer·Retriever 를 한 run 결과로 고정하고 SE 구간(Simulator → Verifier → Composer → KPI)만
  반복한다. SE 변경 효과를 상류 변동 없이 본다
- e2e (--e2e): 회차마다 전체 흐름(Planner → … → Composer → KPI, run_scenarios.py 와 같음)을 새로 실행한다. 에이전트 전체의
  실제 성능과 상류까지 포함한 실행 편차를 본다

한 번 실행으로는 변경 효과(±0.05 수준)가 LLM 응답 편차에 묻힌다. 3~5회 돌려 평균·범위를 보고,
다른 실험과 비교할 때는 "평균 차이가 두 실험의 회차 범위보다 큰가"로 판단한다.

    PYTHONPATH=src python3 scripts/SE/run_experiment.py --from 20261103-v4 --repeat 3 --exp-id 20261104-base --label "기준"
    PYTHONPATH=src python3 scripts/SE/run_experiment.py --from 20261103-v4 --repeat 3 --exp-id 20261104-ops --label "ops 판정" \
        --compare 20261104-base
    PYTHONPATH=src python3 scripts/SE/run_experiment.py --e2e --repeat 3 --exp-id 20261104-e2e --label "e2e 기준"
    PYTHONPATH=src python3 scripts/SE/run_experiment.py --exp-id 20261104-ops --compare 20261104-base --report-only   (집계만 다시)

끊겨도 같은 명령이면 끝난 회차·케이스는 건너뛴다. 비교할 실험끼리는 모드·상류를 같게 둔다 (다르면 경고).
출력 (outputs/runs/SE/experiments/<exp_id>/)
    r1 … rN/            회차별 기록 (run_scenarios.py 와 같은 형식: <case>.md · <case>.state.json · summary.json)
    experiment.json     설정(상류 run · 회차 · git 커밋) · 회차별 지표 · 케이스별 회차 값 · 놓친 [변경] 노드 분류
    experiment.md       지표 평균·범위 · 케이스 안정성 · 놓친 [변경] 노드 분류 · (--compare) 이전 실험 대비
    experiment.html     같은 내용을 한 페이지로 (scripts/SE/experiment_html.py)
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from experiment_html import write_html  # noqa: E402
from replay_run import RUNS, replay_all  # noqa: E402
from run_scenarios import GT_DIR, ROOT, run_all  # noqa: E402

from impact_agent.agents.kpi import change_judgements, hop_tags, load_answer_key  # noqa: E402
from impact_agent.agents.simulator import MAX_CONTEXT_PATHS, REFERENCE_IMPACT  # noqa: E402
from impact_agent.rules import build_impact_paths  # noqa: E402
from impact_agent.schemas import NodeVerdict  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

EXP_ROOT = RUNS / "experiments"
# 실험 지표: (키, 이름, 좋아지는 방향 +1/-1). 정밀도 주 지표는 [변경] 노드 정밀도 (KPI 설계서 v0.5)
METRICS = [
    ("change_precision", "[변경] 노드 정밀도 (합산)", 1), ("change_recall", "[변경] 재현율 (합산)", 1),
    ("node_recall", "노드 탐지율 (평균)", 1), ("tag_complex", "태그 정확도 complex (평균)", 1),
    ("tag_simple", "태그 정확도 simple (평균)", 1), ("difficulty_match", "난이도 일치 (건)", 1),
    ("area_match", "영역 일치 (평균)", 1), ("unneeded", "불필요한 권장 (건)", -1),
    ("node_precision", "노드 정밀도 (평균, 참고)", 1), ("se_seconds", "SE 구간 시간 (초)", -1), ("se_tokens", "SE 토큰", -1),
    ("total_seconds", "전체 시간 (초)", -1), ("result_paths", "결과 경로 수 (합)", -1),
]
E2E = "e2e"  # meta.from 값: 상류를 고정하지 않고 회차마다 전체 흐름 실행
CASES = ["SC-001", "SC-002", "SC-003", "SC-004", "SC-005", "SC-006", "L-1", "L-2", "L-3", "L-4",
         "M-1", "M-2", "M-3", "M-4", "H-1", "H-2"]  # e2e 기본 케이스 (정답셋 16건)
MISS_KINDS = ["경로에 없음 (상류)", "LLM 입력 밖 (경로 상한)", "판정 누락 (기본값 [참조])", "LLM 이 [참조] 판정", "표시 없음"]


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return sha + (" (src 수정 있음)" if dirty else "")
    except OSError:
        return "-"


def load_state(path: Path, case: str) -> AgentState:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("answer_key", None)
    state = AgentState.model_validate(data)
    state.answer_key = load_answer_key(GT_DIR / f"{case}.json")
    return state


def miss_kind(state: AgentState, node: str) -> str:
    """정답 변경 노드가 [변경] 으로 표시되지 않은 이유."""
    if state.simulation is not None:
        paths = [p for a in state.simulation.area_impacts for p in a.paths]
    else:
        paths = state.composer.simple_answer.impact_paths if state.composer and state.composer.simple_answer else []
    impact = next((h.impact for p in paths for h in p.hops if h.node == node), None)
    if impact is None:
        return MISS_KINDS[0]
    if state.simulation is not None and state.explorer is not None:
        built = build_impact_paths(state.explorer.paths, state.explorer.edges, state.retriever.evidence if state.retriever else [])
        if node not in {h.node for p, _ in built[:MAX_CONTEXT_PATHS] for h in p.hops}:
            return MISS_KINDS[1]
    if impact.endswith(REFERENCE_IMPACT):
        return MISS_KINDS[2]
    return MISS_KINDS[3] if impact.startswith("[참조]") else MISS_KINDS[4]


def case_values(state: AgentState, kpi: dict) -> dict:
    key = state.answer_key
    tags = hop_tags(state)
    changed = {n for n, t in tags.items() if t == "change"}
    rows = change_judgements(state, key)
    se = [r for r in state.agent_runs if r.agent in ("simulator", "verifier", "composer")]
    missed = [n for n in key.change_nodes if n not in changed]
    if state.simulation is not None:
        paths = {p.path_id for a in state.simulation.area_impacts for p in a.paths}
    else:
        paths = {p.path_id for p in (state.composer.simple_answer.impact_paths if state.composer and state.composer.simple_answer else [])}
    return {
        "complexity": key.complexity.value if key.complexity else "-",
        "change_hit": len(changed & set(key.change_nodes)), "change_total": len(key.change_nodes),
        "change_tp": sum(r.verdict is NodeVerdict.DETECTED for r in rows),
        "change_fp": [r.node_id for r in rows if r.verdict is NodeVerdict.FALSE_POSITIVE],
        "node_recall": kpi.get("node_recall"), "node_precision": kpi.get("node_precision"),
        "tag": kpi.get("change_tag_accuracy"), "difficulty_match": kpi.get("difficulty_match"),
        "area_match": kpi.get("area_impact_match"),
        "se_seconds": round(sum(r.latency_ms for r in se) / 1000, 1),
        "se_tokens": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in se),
        "total_seconds": round(sum(r.latency_ms for r in state.agent_runs) / 1000, 1), "result_paths": len(paths),
        "missed_change": {n: miss_kind(state, n) for n in missed},
    }


def repeat_metrics(cases: dict[str, dict], flows: dict[str, str]) -> dict:
    vals = list(cases.values())

    def mean(k, only=None):
        xs = [v[k] for c, v in cases.items() if v[k] is not None and (only is None or v["complexity"] == only)]
        return sum(xs) / len(xs) if xs else None

    tp, fp = sum(v["change_tp"] for v in vals), sum(len(v["change_fp"]) for v in vals)
    hit, total = sum(v["change_hit"] for v in vals), sum(v["change_total"] for v in vals)
    return {
        "change_precision": tp / (tp + fp) if tp + fp else None, "change_recall": hit / total if total else None,
        "node_recall": mean("node_recall"), "tag_complex": mean("tag", "complex"), "tag_simple": mean("tag", "simple"),
        "difficulty_match": sum(v["difficulty_match"] or 0 for v in vals), "area_match": mean("area_match"),
        "unneeded": sum(f == "불필요한 권장" for f in flows.values()), "node_precision": mean("node_precision"),
        "se_seconds": sum(v["se_seconds"] for v in vals), "se_tokens": sum(v["se_tokens"] for v in vals),
        "total_seconds": sum(v.get("total_seconds", 0) for v in vals), "result_paths": sum(v.get("result_paths", 0) for v in vals),
        "miss_kinds": dict(Counter(k for v in vals for k in v["missed_change"].values())),
    }


def collect(exp_dir: Path, repeat: int) -> dict:
    """회차 폴더들에서 케이스별 값과 회차 지표를 모은다 (끝난 회차만)."""
    repeats = []
    for n in range(1, repeat + 1):
        run_dir = exp_dir / f"r{n}"
        if not (run_dir / "summary.json").exists():
            continue
        summary = [s for s in json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) if "error" not in s]
        cases = {s["case"]: case_values(load_state(run_dir / f"{s['case']}.state.json", s["case"]), s["result"]["kpi"])
                 for s in summary}
        flows = {s["case"]: s["flow"]["detail_flow"] for s in summary}
        repeats.append({"repeat": n, "cases": cases, "metrics": repeat_metrics(cases, flows)})
    return {"repeats": repeats}


def stat(xs: list[float | None]) -> tuple[float | None, float | None, float | None]:
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs), min(xs), max(xs)) if xs else (None, None, None)


def fmt(v, key: str = "") -> str:
    if v is None:
        return "-"
    if key in ("difficulty_match", "unneeded", "se_seconds", "se_tokens", "total_seconds", "result_paths"):
        return f"{v:,.1f}" if isinstance(v, float) and not float(v).is_integer() else f"{v:,.0f}"
    return f"{v:.3f}"


def report(meta: dict, data: dict, base: dict | None) -> str:
    reps = data["repeats"]
    L = [f"# SE 반복 실험 {meta['exp_id']}" + (f" · {meta['label']}" if meta.get("label") else ""), "",
         ("- e2e: 회차마다 전체 흐름(상류 포함)을 새로 실행" if meta["from"] == E2E else f"- 상류 고정: `{meta['from']}`")
         + f" · 회차 {len(reps)}/{meta['repeat']} · 코드 {meta['commit']} · {meta['updated']}",
         "- 지표는 회차마다 실행한 케이스 전체로 계산한 뒤 회차 사이의 평균과 범위(최소~최대)로 낸다. 정밀도 주 지표는 [변경] 노드 정밀도", ""]
    if not reps:
        return "\n".join(L + ["끝난 회차가 없습니다."]) + "\n"
    L += ["## 지표 (회차 평균 · 범위)", "", "| 지표 | 평균 | 범위 | " + " | ".join(f"r{r['repeat']}" for r in reps) + " |",
          "|---|---|---|" + "---|" * len(reps)]
    for k, name, _ in METRICS:
        m, lo, hi = stat([r["metrics"].get(k) for r in reps])
        L.append(f"| {name} | {fmt(m, k)} | {fmt(lo, k)} ~ {fmt(hi, k)} | " + " | ".join(fmt(r["metrics"].get(k), k) for r in reps) + " |")

    if base and base["repeats"]:
        L += ["", f"## 이전 실험 대비 ({base['meta']['exp_id']}" + (f" · {base['meta'].get('label')}" if base["meta"].get("label") else "") + ")", "",
              "판정: 평균 차이가 두 실험의 회차 범위(최대-최소) 중 큰 값보다 크면 '개선'·'악화', 아니면 '편차 안'.", "",
              "| 지표 | 이전 평균 (범위) | 이번 평균 (범위) | 차이 | 판정 |", "|---|---|---|---|---|"]
        for k, name, sign in METRICS:
            mb, lb, hb = stat([r["metrics"].get(k) for r in base["repeats"]])
            ma, la, ha = stat([r["metrics"].get(k) for r in reps])
            if mb is None or ma is None:
                L.append(f"| {name} | {fmt(mb, k)} | {fmt(ma, k)} | - | - |")
                continue
            diff, spread = ma - mb, max(hb - lb, ha - la)
            verdict = "편차 안" if abs(diff) <= spread else ("개선" if diff * sign > 0 else "악화")
            L.append(f"| {name} | {fmt(mb, k)} ({fmt(lb, k)}~{fmt(hb, k)}) | {fmt(ma, k)} ({fmt(la, k)}~{fmt(ha, k)}) | "
                     f"{'+' if diff > 0 else ''}{fmt(diff, k)} | {verdict} |")

    cases = list(reps[0]["cases"])
    L += ["", "## 케이스별 회차 값", "", "[변경] 은 맞힌 수/정답 변경 노드 수, 오탐은 [변경] 으로 표시했지만 정답이 아닌 노드 수.", "",
          "| 케이스 | 흐름 | [변경] 재현 | [변경] 오탐 | 탐지율 | 태그 정확도 |", "|---|---|---|---|---|---|"]
    for c in cases:
        vs = [r["cases"].get(c) for r in reps if c in r["cases"]]
        if not vs:
            continue
        hits = " · ".join(f"{v['change_hit']}/{v['change_total']}" for v in vs)
        fps = " · ".join(str(len(v["change_fp"])) for v in vs)
        recalls, tags = " · ".join(fmt(v["node_recall"]) for v in vs), " · ".join(fmt(v["tag"]) for v in vs)
        L.append(f"| {c} | {vs[0]['complexity']} | {hits} | {fps} | {recalls} | {tags} |")

    L += ["", "## 놓친 [변경] 노드 분류 (회차 합계)", "", "| 분류 | " + " | ".join(f"r{r['repeat']}" for r in reps) + " |",
          "|---|" + "---|" * len(reps)]
    L += [f"| {k} | " + " | ".join(str(r["metrics"]["miss_kinds"].get(k, 0)) for r in reps) + " |" for k in MISS_KINDS]
    always = sorted({(c, n, k) for c in cases for n, k in reps[0]["cases"].get(c, {}).get("missed_change", {}).items()
                     if all(n in r["cases"].get(c, {}).get("missed_change", {}) for r in reps)})
    L += ["", f"### 모든 회차에서 놓친 노드 ({len(always)}개, 첫 회차 분류)", ""]
    L += [f"- {c} `{n}` — {k}" for c, n, k in always] or ["- 없음"]
    fps = Counter((c, n) for r in reps for c, v in r["cases"].items() for n in v["change_fp"])
    L += ["", "### [변경] 오탐 (회차 수)", ""]
    L += [f"- {c} `{n}` — {cnt}/{len(reps)}회" for (c, n), cnt in fps.most_common()] or ["- 없음"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="생략하면 상류 run 의 전체 케이스")
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--from", dest="source", help="상류를 고정할 run id (outputs/runs/SE/<run>). 처음 실행 때 --from 또는 --e2e 필수")
    ap.add_argument("--e2e", action="store_true", help="상류를 고정하지 않고 회차마다 전체 흐름을 새로 실행")
    ap.add_argument("--repeat", type=int, default=3, help="반복 횟수 (기본 3)")
    ap.add_argument("--label", default="", help="실험 설명 (예: 'ops 판정 모드')")
    ap.add_argument("--compare", help="비교할 이전 실험 exp id")
    ap.add_argument("--report-only", action="store_true", help="실행 없이 저장된 회차로 집계만 다시")
    args = ap.parse_args()
    exp_dir = EXP_ROOT / args.exp_id
    meta_path = exp_dir / "experiment.json"
    old = json.loads(meta_path.read_text(encoding="utf-8"))["meta"] if meta_path.exists() else {}
    if args.e2e and args.source:
        ap.error("--e2e 와 --from 은 함께 쓸 수 없습니다 (e2e 는 상류를 고정하지 않음)")
    source = E2E if args.e2e else args.source
    meta = {**old, "exp_id": args.exp_id, "from": source or old.get("from"), "repeat": args.repeat or old.get("repeat"),
            "label": args.label or old.get("label", ""),
            # 집계만 다시 할 때는 회차를 만든 코드의 커밋을 그대로 둔다
            "commit": old.get("commit", "-") if args.report_only else git_commit(), "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if not meta["from"]:
        ap.error("--from <run> (상류 고정) 또는 --e2e 가 필요합니다")
    if old.get("from") and source and source != old["from"]:
        ap.error(f"이 실험은 {'e2e' if old['from'] == E2E else '상류 ' + old['from']} 로 시작했습니다. 다르게 하려면 새 --exp-id 를 쓰세요")
    exp_dir.mkdir(parents=True, exist_ok=True)

    if not args.report_only:
        for n in range(1, meta["repeat"] + 1):
            if meta["from"] == E2E:
                print(f"=== {args.exp_id} 회차 {n}/{meta['repeat']} (e2e, 전체 흐름) ===", flush=True)
                run_all(args.cases or CASES, exp_dir / f"r{n}")
            else:
                print(f"=== {args.exp_id} 회차 {n}/{meta['repeat']} (상류 {meta['from']}) ===", flush=True)
                replay_all(meta["from"], exp_dir / f"r{n}", args.cases or None)

    data = collect(exp_dir, meta["repeat"])
    base = None
    if args.compare:
        base_path = EXP_ROOT / args.compare / "experiment.json"
        base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None
        if base and base["meta"].get("from") != meta["from"]:
            print(f"주의: 비교 실험의 상류·모드({base['meta'].get('from')})가 이번({meta['from']})과 다릅니다", flush=True)
    meta_path.write_text(json.dumps({"meta": meta, **data}, ensure_ascii=False, indent=2), encoding="utf-8")
    (exp_dir / "experiment.md").write_text(report(meta, data, base), encoding="utf-8")
    html = write_html(args.exp_id, args.compare if base else None)
    print(f"기록: {exp_dir / 'experiment.md'} · {html}")


if __name__ == "__main__":
    main()
