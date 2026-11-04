"""시나리오 실행 기록 → report.html (한 장). run_scenarios.py 가 케이스마다 다시 만든다.

질문 중심으로 구성한다 (시간 분석은 scripts/SE 쪽 자료가 다룸):
    1. 한눈에        지표 타일, 기준 실행(--baseline, 같은 E2E 흐름으로 돌린 이전 run) 대비 증감
       (기준 실행이 있으면) 케이스별 변화표 "이전 → 이후" + 산점도 (가로 이전 · 세로 이후, 대각선 위 = 좋아짐)
    2. 판정 흐름     정답 → 1차 판정 → 상세 분석 권장 → 2차 → Verifier → 최종
    3. 정답 손실     정답 경로가 Explorer → Retriever → Simulator 전달 → KPI 탐지 중 어디서 빠지는지 (케이스 × 단계)
    4. 시간·비용·정밀도  케이스별 지연(에이전트별 누적 막대), 입력·출력 토큰, tool·LLM 호출, 재계획, 노드 정밀도 + 에이전트별 합계
    5. 개선 후보     실행 분석 결론 (<run>/findings.json: 무엇이 문제인지 · 근거 측정값 · 케이스 · 개선 방향 · 담당). 없으면 자동 징후를 분류별로
    6. 케이스 카드   흐름·권장 사유·누락·에이전트별 시간, md·log 링크
기준 실행이 없으면 증감 없이 그 run 만 그린다 (v6/v6-1 = E2E 첫 기준선).
색: dataviz 기본 팔레트 (순차 = 파랑 한 색, 에이전트 = 범주 색 고정 순서, 상태 = good·critical 은 아이콘·문구와 함께).
"""

import html
import json
import statistics
from datetime import datetime
from pathlib import Path

SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]  # 순차 (0 → 1)
TODO = {  # 자동 징후 분류 (findings.json 이 없을 때 5장, 케이스 카드)
    "복잡도 판정": "1차 복잡도가 정답과 다름", "권장": "상세 분석 권장 결과", "항목 분리": "단순 요건을 항목 여러 개로 분리",
    "그래프 밖": "단순 요건에 그래프 밖 영역", "경로 과다": "단순 흐름 경로가 많음", "전달 손실": "Simulator 앞 40개 밖 경로",
    "공통 데이터 미탐": "공통코드 등 공통 데이터 미탐", "탐색": "탐색 누락·방해 노드",
}
AGENTS = ["planner", "explorer", "retriever", "simulator", "verifier", "composer", "kpi"]
# 에이전트 범주 색 (기본 팔레트 1~7번 고정 순서. 어떤 케이스에서도 같은 에이전트는 같은 색)
AGENT_COLOR = {"planner": "#2a78d6", "explorer": "#eb6834", "retriever": "#1baf7a", "simulator": "#eda100",
               "verifier": "#e87ba4", "composer": "#008300", "kpi": "#6250d6"}


def load(run_dir: Path) -> list[dict]:
    """영역별 폴더(tos·swing)의 summary.json 을 합쳐 읽는다 (폴더 없이 summary.json 만 있는 run 도 읽음)."""
    files = [run_dir / "summary.json"] + [run_dir / d / "summary.json" for d in ("tos", "swing")]
    return [x for f in files if f.exists() for x in json.loads(f.read_text(encoding="utf-8")) if "error" not in x]


# --- 지표 -----------------------------------------------------------------------------


def _r(s: dict) -> dict:
    return s.get("result") or s.get("react") or {}


def _ratio(text) -> tuple[int, int]:
    a, _, b = str(text or "0/0").partition("/")
    return int(a or 0), int(b or 0)


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


# 하류(Simulator·Verifier·Composer·KPI) 결과에 달린 지표. 기준 실행과 KPI 측정 방식이 다르면 증감을 보이지 않는다
DOWNSTREAM = {"over", "miss", "crec", "latency", "tokens"}


def kpi_changed(rows: list[dict], base_rows: list[dict]) -> bool:
    """KPI 측정 방식이 바뀌었나: 2026-11-03 SE KPI 개편([변경] 노드 정밀도 추가) 전후를 change_node_precision 기록 여부로 가린다."""
    has = lambda rs: any("change_node_precision" in (s.get("kpi") or {}) for s in rs)  # noqa: E731
    return bool(base_rows) and has(rows) != has(base_rows)


def kpi_tiles(rows: list[dict]) -> list[dict]:
    """KPI Agent 지표 (docs/SE_kpi-agent-design.md 1-4): 핵심(quality) · 판정(judgement) · 성능(performance). 케이스 평균, 하한은 통과 수."""
    k = lambda s: s.get("kpi") or {}  # noqa: E731
    vals = lambda name: [k(s)[name] for s in rows if k(s).get(name) is not None]  # noqa: E731
    passed = lambda name, ok: f"{sum(ok(v) for v in vals(name))}/{len(vals(name))}건"  # noqa: E731
    tools = vals("tool_calls")
    # KPI 화면(frontend Kpi.tsx)과 같이 16건 카운트를 합산한 비율. 카운트가 없는 이전 기록은 케이스 평균
    cs = [s["kpi_counts"] for s in rows if s.get("kpi_counts")]
    c = lambda name: sum(x.get(name) or 0 for x in cs)  # noqa: E731
    pool = lambda a, b, name: (c(a) / (c(a) + c(b)) if c(a) + c(b) else None) if cs else _mean(vals(name))  # noqa: E731
    pooled = "16건 합산" if cs else "케이스 평균"
    return [
        {"key": "k_cprec", "group": "핵심", "label": "[변경] 노드 정밀도", "value": pool("change_tp", "change_fp", "change_node_precision"),
         "fmt": "f", "up": True, "note": f"정밀도 주 지표 · {pooled} · 하한 0.5 · 케이스 통과 {passed('change_node_precision', lambda v: v >= 0.5)}"},
        {"key": "k_crec", "group": "핵심", "label": "[변경] 재현율", "value": (c("change_hit") / c("change_total") if c("change_total") else None) if cs else None,
         "fmt": "f", "up": True, "note": f"측정만 · 정답 변경 노드 {c('change_total')}개 중 [변경] {c('change_hit')}" if cs else "카운트 없는 이전 기록"},
        {"key": "k_recall", "group": "핵심", "label": "노드 탐지율", "value": pool("tp", "fn", "node_recall"), "fmt": "f", "up": True,
         "note": f"{pooled} · 하한 0.8 · 케이스 통과 {passed('node_recall', lambda v: v >= 0.8)}"},
        {"key": "k_crit", "group": "참고", "label": "Critical 미탐률",
         "value": (c("critical_missed") / c("critical_total") if c("critical_total") else None) if cs else _mean(vals("critical_miss_rate")),
         "fmt": "f", "up": False,
         "note": f"목표 0 · 0인 케이스 {passed('critical_miss_rate', lambda v: v == 0)}" if vals("critical_miss_rate") else "KPI Agent 기록 없음 (정답 노드에 Critical 표시 없음)"},
        {"key": "k_prec", "group": "참고", "label": "노드 정밀도 (참고)", "value": pool("tp", "fp", "node_precision"), "fmt": "f", "up": True,
         "note": f"측정만 · {pooled} · 결과 경로 위 노드 전체 기준"},
        {"key": "k_cx", "group": "판정", "label": "복잡도 일치", "value": _mean(vals("complexity_match")), "fmt": "pct", "up": True, "note": "재분석 후 값 기준"},
        {"key": "k_tag", "group": "판정", "label": "[변경]/[참조] 표시 정확도", "value": _mean(vals("change_tag_accuracy")), "fmt": "f", "up": True,
         "note": "정답 변경·참조 노드 중 표시가 맞은 비율"},
        {"key": "k_area", "group": "판정", "label": "영역 판정 일치", "value": _mean(vals("area_impact_match")), "fmt": "f", "up": True, "note": "복잡 · 4개 영역"},
        {"key": "k_diff", "group": "판정", "label": "난이도 일치", "value": _mean(vals("difficulty_match")), "fmt": "pct", "up": True, "note": "복잡"},
        {"key": "k_feas", "group": "판정", "label": "구현 가능 여부 일치", "value": _mean(vals("feasibility_match")), "fmt": "pct", "up": True, "note": "복잡"},
        {"key": "k_unres", "group": "판정", "label": "재계획 미해결", "value": sum(vals("verifier_unresolved")), "fmt": "n", "up": False, "note": "복잡 · Verifier 보완 필요 남음"},
        {"key": "k_tools", "group": "성능", "label": "tool 호출 (라운드 최대)", "value": _mean(tools), "fmt": "f", "up": False,
         "note": f"한도 20 · 도달 {sum(v >= 20 for v in tools)}/{len(tools)}건"},
        {"key": "k_lat", "group": "성능", "label": "지연", "value": _mean([v / 1000 for v in vals("latency_ms")]), "fmt": "s", "up": False, "note": "케이스 평균"},
        {"key": "k_tok", "group": "성능", "label": "토큰", "value": _mean(vals("total_tokens")), "fmt": "k", "up": False, "note": "케이스 평균"},
        {"key": "k_replan", "group": "성능", "label": "재계획", "value": sum(vals("replans")), "fmt": "n", "up": False, "note": "16건 합계"},
    ]


def tiles(rows: list[dict]) -> list[dict]:
    """(라벨, 값, 표시 형식, 높을수록 좋은가, 설명)."""
    n = len(rows)
    simple = [s for s in rows if (s.get("flow") or {}).get("expected") == "simple"]
    complex_ = [s for s in rows if (s.get("flow") or {}).get("expected") == "complex"]
    must = [_ratio(_r(s).get("must_find")) for s in rows]
    crit = sum(_r(s).get("critical_missed") or 0 for s in rows), sum(_r(s).get("critical_total") or 0 for s in rows)
    detail = lambda s: (s.get("flow") or {}).get("detail_flow") or _r(s).get("detail_flow")  # noqa: E731
    return [
        {"key": "complexity", "label": "1차 복잡도 일치", "value": sum((s.get("flow") or {}).get("first") == (s.get("flow") or {}).get("expected") for s in rows) / n if n else None,
         "fmt": "pct", "up": True, "note": f"{n}건 중"},
        {"key": "path", "label": "정답 경로 재현율", "value": _mean([_r(s).get("path_recall") for s in rows]), "fmt": "f", "up": True, "note": "평균 (Explorer)"},
        {"key": "must", "label": "must_find", "value": sum(a for a, _ in must) / sum(b for _, b in must) if sum(b for _, b in must) else None,
         "fmt": "pct", "up": True, "note": f"{sum(a for a, _ in must)}/{sum(b for _, b in must)}"},
        {"key": "crit", "label": "Critical 경로 미탐", "value": crit[0], "fmt": "n", "up": False, "note": f"전체 {crit[1]}개 중"},
        {"key": "handoff", "label": "Simulator 전달 경로", "value": _mean([_r(s).get("handoff_path_recall") for s in rows]), "fmt": "f", "up": True, "note": "평균 (앞 40개 안)"},
        {"key": "prec", "label": "노드 정밀도", "value": statistics.median(v) if (v := [_r(s).get("node_precision") for s in rows if _r(s).get("node_precision") is not None]) else None,
         "fmt": "f", "up": True, "note": "중앙값 (Explorer 노드)"},
        {"key": "over", "label": "불필요한 권장", "value": sum(detail(s) == "불필요한 권장" for s in simple), "fmt": "n", "up": False, "note": f"정답 단순 {len(simple)}건 중"},
        {"key": "miss", "label": "놓친 복잡 요건", "value": sum(detail(s) == "신호 없음 (놓침)" for s in complex_), "fmt": "n", "up": False, "note": f"정답 복잡 {len(complex_)}건 중"},
        {"key": "crec", "label": "[변경] 노드 재현율", "value": _mean([_r(s).get("change_recall") for s in rows]), "fmt": "f", "up": True, "note": "평균 (정답 변경 노드 중 [변경] 표시, 평가 스크립트)"},
        {"key": "latency", "label": "평균 지연", "value": _mean([_r(s).get("latency_s") for s in rows]), "fmt": "s", "up": False, "note": "케이스당 (재분석 포함)"},
        {"key": "tokens", "label": "평균 토큰", "value": _mean([(_r(s).get("input_tokens") or 0) + (_r(s).get("output_tokens") or 0) for s in rows]) if rows else None,
         "fmt": "k", "up": False, "note": "케이스당 입력+출력"},
    ]


def _fmt(v, kind: str) -> str:
    if v is None:
        return "-"
    return {"pct": f"{v * 100:.0f}%", "f": f"{v:.2f}", "n": f"{v:.0f}", "s": f"{v:.0f}s", "k": f"{v / 1000:.0f}k"}[kind]


def _delta(cur, base, kind: str, up: bool) -> str:
    if cur is None or base is None:
        return '<span class="delta muted">기준 없음</span>'
    d = cur - base
    if abs(d) < 1e-9:
        return '<span class="delta muted">변화 없음</span>'
    better = (d > 0) == up
    sign = "+" if d > 0 else "−"
    mag = {"pct": f"{abs(d) * 100:.0f}%p", "f": f"{abs(d):.2f}", "n": f"{abs(d):.0f}", "s": f"{abs(d):.0f}s", "k": f"{abs(d) / 1000:.0f}k"}[kind]
    if mag.rstrip("%pks").replace("0", "").replace(".", "") == "":  # 표시 자릿수에서 0 이면 변화 없음
        return '<span class="delta muted">변화 없음</span>'
    return (f'<span class="delta {"good" if better else "bad"}">{"✓" if better else "!"} {sign}{mag} '
            f'{"개선" if better else "악화"}</span>')


def _heat(v) -> str:
    """0~1 값 → 순차 색 셀. 값은 셀 안 글자로 항상 함께 보인다."""
    if not isinstance(v, (int, float)):
        return '<td class="heat na">-</td>'
    i = min(int(v * (len(SEQ) - 1) + 0.5), len(SEQ) - 1)
    ink = "#ffffff" if i >= 3 else "#0b0b0b"
    return f'<td class="heat" style="background:{SEQ[i]};color:{ink}" title="{v:.3f}">{v:.2f}</td>'


def _chip(text: str, tone: str) -> str:
    icon = {"good": "✓", "bad": "✕", "warn": "!", "info": "→", "muted": "·"}[tone]
    return f'<span class="chip {tone}">{icon} {html.escape(text)}</span>'


# --- 섹션 -----------------------------------------------------------------------------


def _flow_row(s: dict) -> str:
    f = s.get("flow") or {}
    exp, first = f.get("expected"), f.get("first")
    d = f.get("detail_flow") or _r(s).get("detail_flow") or "-"
    tone = "bad" if d in ("불필요한 권장", "신호 없음 (놓침)") else "good" if "정상" in d or "가능" in d else "warn" if "참고" in d else "muted"
    second = _chip(f"2차 {f.get('final')}", "info") if f.get("reanalysis") is not None else '<span class="muted">-</span>'
    replans = _r(s).get("replans")
    ver = _chip(f"재계획 {replans}회", "warn") if replans else _chip("재계획 없음", "muted") if f.get("final") == "complex" else '<span class="muted">-</span>'
    final_ok = f.get("final") == exp
    return (f"<tr><th scope=row>{s['case']}</th><td>{exp or '-'}</td><td>{_chip(first or '-', 'good' if first == exp else 'bad')}</td>"
            f"<td>{_chip(d, tone)}</td><td>{second}</td><td>{ver}</td><td>{_chip(f.get('final') or '-', 'good' if final_ok else 'bad')}</td></tr>")


def _loss_row(s: dict, base: dict | None) -> str:
    r, k = _r(s), s.get("kpi") or {}
    vals = [r.get("path_recall"), r.get("evidence_coverage"), r.get("handoff_path_recall"), k.get("node_recall")]
    b = _r(base) if base else {}
    tip = f"기준 실행: 경로 {b.get('path_recall')} · 근거 {b.get('evidence_coverage')} · 전달 {b.get('handoff_path_recall')}" if base else "기준 없음"
    first = s.get("first_round")
    note = f"1차(재분석 전) 경로 {first['path_recall']}" if first else ""
    return (f'<tr title="{html.escape(tip)}"><th scope=row>{s["case"]}</th>' + "".join(_heat(v) for v in vals)
            + f'<td class="num">{r.get("paths_found", "-")}</td><td class="muted small">{html.escape(note)}</td></tr>')


def _findings(run_dir: Path, rows: list[dict]) -> str:
    """실행 분석 결론 (findings.json). 없으면 자동 징후 표."""
    f = run_dir / "findings.json"
    if not f.exists():
        return _flags_table(rows)
    items = json.loads(f.read_text(encoding="utf-8"))
    out = ['<table class="grid"><thead><tr><th>#</th><th>영역</th><th>문제</th><th>근거 (이번 실행 측정값)</th><th>케이스</th><th>개선 방향</th><th>담당</th></tr></thead><tbody>']
    for i, x in enumerate(items, start=1):
        out.append(f"<tr><th scope=row>{i}</th><td>{html.escape(x['area'])}</td><td><b>{html.escape(x['problem'])}</b></td>"
                   f"<td class=small>{html.escape(x['evidence'])}</td><td class=small>{html.escape(', '.join(x['cases']))}</td>"
                   f"<td class=small>{html.escape(x['direction'])}</td><td>{html.escape(x['owner'])}</td></tr>")
    return "".join(out) + "</tbody></table>"


def _cost_table(rows: list[dict], base: dict[str, dict]) -> str:
    """케이스별 시간(에이전트별 누적 막대)·토큰·호출·정밀도 + 에이전트별 합계. 막대 길이는 가장 오래 걸린 케이스 기준."""
    lat = {s["case"]: s.get("agent_latency_s") or _r(s).get("agent_latency_s") or {} for s in rows}
    top = max((sum(v.values()) for v in lat.values()), default=0) or 1
    legend = "".join(f'<span><i style="background:{AGENT_COLOR[a]}"></i>{a}</span>' for a in AGENTS)
    body = []
    for s in rows:
        r, k, b = _r(s), s.get("kpi") or {}, _r(base[s["case"]]) if s["case"] in base else {}
        segs = "".join(f'<span style="width:{lat[s["case"]][a] / top * 100:.2f}%;background:{AGENT_COLOR[a]}" title="{a} {lat[s["case"]][a]:.1f}s"></span>'
                       for a in AGENTS if lat[s["case"]].get(a))
        was = f' title="기준 실행 {b.get("latency_s")}s · 토큰 {(b.get("input_tokens") or 0) + (b.get("output_tokens") or 0):,}"' if b else ""
        kp = k.get("node_precision")
        body.append(f'<tr{was}><th scope=row>{s["case"]}</th><td class=num>{r.get("latency_s", "-")}</td><td><div class="stack">{segs}</div></td>'
                    f'<td class=num>{r.get("input_tokens", 0):,}</td><td class=num>{r.get("output_tokens", 0):,}</td>'
                    f'<td class=num>{r.get("tool_calls", "-")}</td><td class=num>{r.get("llm_calls", "-")}</td><td class=num>{r.get("replans", 0)}</td>'
                    f'<td class=num>{r.get("node_precision", "-")}</td><td class=num>{f"{kp:.3f}" if isinstance(kp, float) else "-"}</td>'
                    f'<td class=num>{_fmt(r.get("change_precision"), "f")}</td><td class=num>{_fmt(r.get("fp_reference_ratio"), "pct")}</td></tr>')
    totals = {a: sum(v.get(a, 0) for v in lat.values()) for a in AGENTS}
    all_s = sum(totals.values()) or 1
    share = " · ".join(f"{a} {totals[a]:.0f}s ({totals[a] / all_s * 100:.0f}%)" for a in AGENTS if totals[a])
    tin, tout = sum(_r(s).get("input_tokens") or 0 for s in rows), sum(_r(s).get("output_tokens") or 0 for s in rows)
    return (f'<div class="legend cat">{legend}</div><table><thead><tr><th>케이스</th><th class=num>지연(s)</th><th>에이전트별 시간</th>'
            f'<th class=num>입력 토큰</th><th class=num>출력 토큰</th><th class=num>tool</th><th class=num>LLM 호출</th><th class=num>재계획</th>'
            f'<th class=num>정밀도 (Explorer)</th><th class=num>정밀도 (KPI)</th><th class=num>[변경] 정밀도</th><th class=num>오탐 중 [참조]</th></tr></thead><tbody>{"".join(body)}</tbody>'
            f'<tfoot><tr><th>합계</th><td class=num>{sum(_r(s).get("latency_s") or 0 for s in rows):.0f}</td><td class=small>{share}</td>'
            f'<td class=num>{tin:,}</td><td class=num>{tout:,}</td><td colspan=7></td></tr></tfoot></table>')


def _flags_table(rows: list[dict]) -> str:
    groups: dict[str, list[tuple[str, str]]] = {}
    for s in rows:
        for fl in s.get("flags") or []:
            groups.setdefault(fl["code"], []).append((s["case"], fl["text"]))
    if not groups:
        return '<p class="muted">징후 없음</p>'
    order = [c for c in TODO if c in groups] + [c for c in groups if c not in TODO]
    out = ['<table class="grid"><thead><tr><th>분류</th><th>무엇</th><th class=num>케이스</th><th>징후 (측정값)</th></tr></thead><tbody>']
    for code in order:
        items = groups[code]
        detail = "<br>".join(f"<b>{c}</b> {html.escape(t)}" for c, t in items)
        out.append(f"<tr><th scope=row>{code}</th><td>{TODO.get(code, '')}</td><td class=num>{len(items)}</td><td class=small>{detail}</td></tr>")
    return "".join(out) + "</tbody></table>"


def _bars(lat: dict) -> str:
    """에이전트별 시간 (한 계열이라 범례 없음, 값은 막대 옆 글자)."""
    top = max(lat.values(), default=0) or 1
    rows = []
    for a in AGENTS:
        if a in lat:
            w = max(lat[a] / top * 100, 1)
            rows.append(f'<div class="bar"><span class="bl">{a}</span><span class="bt"><span style="width:{w:.1f}%"></span></span>'
                        f'<span class="bv">{lat[a]:.1f}s</span></div>')
    return "".join(rows)


def _card(s: dict, base: dict | None) -> str:
    f, r, k = s.get("flow") or {}, _r(s), s.get("kpi") or {}
    lis = []
    if f.get("reasons"):
        lis += [f"<li>권장 사유: {html.escape(x)}</li>" for x in f["reasons"]]
    if f.get("notes"):
        lis += [f"<li>참고 신호: {html.escape(x)}</li>" for x in f["notes"]]
    if r.get("must_missing"):
        lis.append(f"<li>must_find 누락: {html.escape(', '.join(r['must_missing']))}</li>")
    if r.get("distractors_found"):
        lis.append(f"<li>방해 노드 도달: {html.escape(', '.join(r['distractors_found']))}</li>")
    lis += [f"<li><b>{fl['code']}</b> {html.escape(fl['text'])}</li>" for fl in s.get("flags") or []]
    re = f'<pre>{html.escape(f["reanalysis"])}</pre>' if f.get("reanalysis") else ""
    cmp_ = ""
    if base:
        b = _r(base)
        cmp_ = (f'<p class="small muted">기준 실행: 경로 {b.get("path_recall")} · must {b.get("must_find")} · 정밀도 {b.get("node_precision")} · '
                f'지연 {b.get("latency_s")}s</p>')
    head = (f"{s['case']} · {html.escape(s.get('title', ''))} <span class=muted>({s.get('domain')} · {s.get('difficulty')})</span>"
            f"<span class=sum>경로 {r.get('path_recall')} · must {r.get('must_find')} · KPI 탐지율 {_fmt(k.get('node_recall'), 'f')} · "
            f"{r.get('latency_s')}s</span>")
    return (f'<details class="card"><summary>{head}</summary><div class="cbody"><div><p>흐름: {html.escape(f.get("path", "-"))}</p>'
            f'<ul>{"".join(lis) or "<li class=muted>특이 사항 없음</li>"}</ul>{re}{cmp_}'
            f'<p class="small"><a href="{s["domain"]}/{s["case"]}.md">{s["domain"]}/{s["case"]}.md</a> · <a href="{s["domain"]}/{s["case"]}.log">{s["domain"]}/{s["case"]}.log</a></p></div>'
            f'<div><h4>에이전트별 시간</h4>{_bars(s.get("agent_latency_s") or r.get("agent_latency_s") or {})}</div></div></details>')


# 케이스별 변화표 열: (라벨, 값 꺼내기, 형식, 높을수록 좋은가)
CHANGE_COLS = [
    ("1차 판정", lambda s: (s.get("flow") or {}).get("first"), "text", None),
    ("경로 재현율", lambda s: _r(s).get("path_recall"), "f", True),
    ("must_find", lambda s: _r(s).get("must_find"), "ratio", True),
    ("KPI 탐지율", lambda s: (s.get("kpi") or {}).get("node_recall"), "f", True),
    ("KPI 정밀도", lambda s: (s.get("kpi") or {}).get("node_precision"), "f", True),
    ("[변경] 정밀도", lambda s: _r(s).get("change_precision"), "f", True),
    ("Simulator 전달", lambda s: _r(s).get("handoff_path_recall"), "f", True),
    ("경로 수", lambda s: _r(s).get("paths_found"), "n", False),
    ("지연(s)", lambda s: _r(s).get("latency_s"), "n", False),
    ("토큰(k)", lambda s: ((_r(s).get("input_tokens") or 0) + (_r(s).get("output_tokens") or 0)) / 1000 if _r(s) else None, "n", False),
]


DOWNSTREAM_COLS = {"KPI 탐지율", "KPI 정밀도", "[변경] 정밀도", "지연(s)", "토큰(k)"}


def _arrow(old, new, kind: str, up: bool | None, expected: str | None = None) -> str:
    """'이전 → 이후' 칸. 좋아지면 ✓(초록), 나빠지면 !(빨강), 같으면 흐리게."""
    def show(v) -> str:
        return "-" if v is None else f"{v:.2f}" if kind == "f" else f"{v:.0f}" if kind == "n" else str(v)

    text = f"{show(old)} → {show(new)}"
    if kind == "text":
        tone = "" if old == new else "good" if new == expected else "bad" if old == expected else ""
    elif kind == "ratio":
        a, b = _ratio(old), _ratio(new)
        diff = (b[0] / b[1] if b[1] else 0) - (a[0] / a[1] if a[1] else 0)
        tone = "" if abs(diff) < 1e-9 else "good" if diff > 0 else "bad"
    elif old is None or new is None or show(old) == show(new):
        tone = ""
    else:
        tone = "good" if (new > old) == up else "bad"
    mark = {"good": "✓ ", "bad": "! "}.get(tone, "")
    return f'<td class="chg {tone or "same"}">{mark}{text}</td>'


def _change_table(rows: list[dict], base: dict[str, dict], changed: bool = False) -> str:
    cols = [c for c in CHANGE_COLS if not (changed and c[0] in DOWNSTREAM_COLS)]
    body = []
    for s in rows:
        b = base.get(s["case"])
        if b is None:
            continue
        exp = (s.get("flow") or {}).get("expected")
        body.append(f"<tr><th scope=row>{s['case']}</th>" + "".join(_arrow(get(b), get(s), kind, up, exp) for _, get, kind, up in cols) + "</tr>")
    head = "".join(f"<th>{label}</th>" for label, *_ in cols)
    return f'<div class="wide"><table class="chgt"><thead><tr><th>케이스</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _scatter(rows: list[dict], base: dict[str, dict], label: str, get, up: bool, unit: str = "") -> str:
    """가로 = 기준 실행, 세로 = 이번 실행. 대각선 위쪽이 커진 것 (up 이면 좋아짐). 점에 마우스를 올리면 케이스·값."""
    pts = [(s["case"], get(base[s["case"]]), get(s)) for s in rows if s["case"] in base]
    pts = [(c, x, y) for c, x, y in pts if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    if not pts:
        return ""
    top = max(max(x, y) for _, x, y in pts) * 1.08 or 1
    W, P = 300, 48
    sx = lambda v: P + v / top * (W - P - 12)  # noqa: E731
    sy = lambda v: W - P - v / top * (W - P - 12)  # noqa: E731
    dots = []
    for c, x, y in pts:
        tone = "same" if abs(y - x) < top * 0.01 else "good" if (y > x) == up else "bad"
        dots.append(f'<circle class="pt {tone}" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="5"><title>{c}: {x:.3g}{unit} → {y:.3g}{unit}</title></circle>')
        if abs(y - x) > top * 0.08:  # 많이 바뀐 점만 이름 표시
            dots.append(f'<text class="pl" x="{sx(x) + 7:.1f}" y="{sy(y) + 4:.1f}">{c}</text>')
    def num(v: float) -> str:  # 눈금 숫자 (지수 표기 없이)
        return f"{v:.0f}" if top >= 10 else f"{v:.2f}"

    ticks = "".join(f'<text class="tk" x="{sx(v):.1f}" y="{W - P + 14}" text-anchor="middle">{num(v)}</text>'
                    f'<text class="tk" x="{P - 6}" y="{sy(v) + 4:.1f}" text-anchor="end">{num(v)}</text>' for v in (0, top / 2, top))
    better = "위쪽 = 좋아짐" if up else "아래쪽 = 좋아짐"
    return (f'<figure class="sc"><figcaption>{label} <span class="muted">(대각선 {better})</span></figcaption>'
            f'<svg viewBox="0 0 {W} {W}" role="img" aria-label="{label} 이전 대비 이후">'
            f'<line class="ax" x1="{P}" y1="{W - P}" x2="{W - 12}" y2="{W - P}"/><line class="ax" x1="{P}" y1="{W - P}" x2="{P}" y2="12"/>'
            f'<line class="dg" x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(top):.1f}" y2="{sy(top):.1f}"/>{ticks}{"".join(dots)}'
            f'<text class="tk" x="{W / 2}" y="{W - 4}" text-anchor="middle">기준 실행</text>'
            f'<text class="tk" x="9" y="{W / 2}" transform="rotate(-90 9 {W / 2})" text-anchor="middle">이번 실행</text></svg></figure>')


def _changes(rows: list[dict], base: dict[str, dict], base_name: str, changed: bool = False) -> str:
    if not base:
        return ""
    if changed:  # KPI 측정 방식이 달라 하류 지표는 비교하지 않음 → 상류(Planner·Explorer) 지표만
        plots = (_scatter(rows, base, "정답 경로 재현율", lambda s: _r(s).get("path_recall"), True)
                 + _scatter(rows, base, "Simulator 전달 경로", lambda s: _r(s).get("handoff_path_recall"), True)
                 + _scatter(rows, base, "경로 수", lambda s: _r(s).get("paths_found"), False))
        note = (' <b>KPI 측정 방식이 기준 실행과 달라(2026-11-03 SE KPI 개편) 상류(Planner·Explorer) 지표만 비교한다.</b> '
                'KPI·[변경] 판정·지연·토큰은 이번 실행이 새 기준선이다.')
    else:
        plots = (_scatter(rows, base, "KPI 노드 정밀도", lambda s: (s.get("kpi") or {}).get("node_precision"), True)
                 + _scatter(rows, base, "Simulator 전달 경로", lambda s: _r(s).get("handoff_path_recall"), True)
                 + _scatter(rows, base, "토큰 (천)", lambda s: ((_r(s).get("input_tokens") or 0) + (_r(s).get("output_tokens") or 0)) / 1000, False, "k"))
        note = ""
    return (f'<h2>1-2. {html.escape(base_name)} 대비 케이스별 변화</h2><p>칸마다 "기준 실행 → 이번 실행". 좋아지면 ✓, 나빠지면 !. '
            f'산점도는 가로가 기준 실행, 세로가 이번 실행이고 점에 마우스를 올리면 값이 나온다.{note}</p>'
            f'<div class="scs">{plots}</div>{_change_table(rows, base, changed)}')


def write_report(run_dir: Path, baseline_dir: Path | None = None) -> Path:
    rows = load(run_dir)
    base_rows = load(baseline_dir) if baseline_dir and baseline_dir.exists() else []
    base = {s["case"]: s for s in base_rows}
    cur_t, base_t = tiles(rows), {t["key"]: t for t in tiles(base_rows)} if base_rows else {}
    base_k = {t["key"]: t for t in kpi_tiles(base_rows)} if base_rows else {}
    changed = kpi_changed(rows, base_rows)
    new_base = '<span class="delta muted">측정 기준 변경 · 비교 안 함</span>'

    def tile(t: dict, base_v, hide: bool) -> str:
        delta = (new_base if hide else _delta(t["value"], base_v, t["fmt"], t["up"])) if base_rows else ""
        return (f'<div class="tile"><div class="tl">{t["label"]}</div><div class="tv">{_fmt(t["value"], t["fmt"])}</div>'
                f'<div class="tn">{t["note"]}</div>{delta}</div>')

    tile_html = "".join(tile(t, base_t.get(t["key"], {}).get("value"), changed and t["key"] in DOWNSTREAM) for t in cur_t)
    kpi_html = "".join(
        f'<h4 style="margin-top:10px">{g}</h4><div class="tiles">'
        + "".join(tile(t, base_k.get(t["key"], {}).get("value"), changed) for t in kpi_tiles(rows) if t["group"] == g) + "</div>"
        for g in ("핵심", "참고", "판정", "성능"))
    title = f"YJ 시나리오 실행 기록 · {run_dir.name}"
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--ring:rgba(11,11,11,.10);
--good:#006300;--good-bg:#e6f4e6;--bad:#d03b3b;--bad-bg:#fbe9e9;--warn:#8a5a00;--warn-bg:#fff4dc;--info-bg:#e8f1fc;--bar:#3987e5}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--line:#2c2c2a;
--ring:rgba(255,255,255,.10);--good:#0ca30c;--good-bg:#132613;--bad:#e66767;--bad-bg:#2c1414;--warn:#fab219;--warn-bg:#2a2210;--info-bg:#14243a;--bar:#3987e5}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.55 system-ui,-apple-system,"Apple SD Gothic Neo",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}header{{border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:24px}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:36px 0 4px}}h2+p{{margin:0 0 12px;color:var(--ink2)}}h4{{margin:0 0 6px;font-size:12px;color:var(--ink2)}}
.meta{{color:var(--ink2);font-size:13px;margin-top:4px}}.muted{{color:var(--muted)}}.small{{font-size:12px}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}}
.tile{{background:var(--surface);border:1px solid var(--ring);border-left:4px solid var(--ink);padding:10px 12px}}
.tl{{font-size:12px;color:var(--ink2)}}.tv{{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}}.tn{{font-size:11px;color:var(--muted)}}
.delta{{display:inline-block;margin-top:4px;font-size:12px;font-weight:600}}.delta.good{{color:var(--good)}}.delta.bad{{color:var(--bad)}}
table{{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--ring)}}th,td{{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{font-size:12px;color:var(--ink2);font-weight:600;background:var(--page);white-space:nowrap}}tbody th{{font-weight:600;white-space:nowrap}}
td.heat{{text-align:center;font-variant-numeric:tabular-nums;font-weight:600;border:2px solid var(--surface)}}td.heat.na{{color:var(--muted)}}
.chip{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;white-space:nowrap}}.chip.good{{background:var(--good-bg);color:var(--good)}}
.chip.bad{{background:var(--bad-bg);color:var(--bad)}}.chip.warn{{background:var(--warn-bg);color:var(--warn)}}.chip.info{{background:var(--info-bg)}}.chip.muted{{color:var(--muted)}}
.legend{{display:flex;gap:4px;align-items:center;font-size:12px;color:var(--ink2);margin:6px 0}}.legend i{{width:22px;height:10px;display:inline-block}}
.legend.cat{{gap:14px;flex-wrap:wrap}}.legend.cat i{{width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}}
.stack{{display:flex;gap:2px;height:12px;min-width:200px}}.stack span{{display:block;height:12px}}.stack span:last-child{{border-radius:0 4px 4px 0}}
tfoot th,tfoot td{{background:var(--page);font-weight:600}}
.card{{background:var(--surface);border:1px solid var(--ring);margin-bottom:6px}}.card summary{{cursor:pointer;padding:8px 12px;font-weight:600}}
.card .sum{{float:right;font-weight:400;color:var(--ink2);font-size:12px}}.cbody{{display:grid;grid-template-columns:2fr 1fr;gap:18px;padding:4px 14px 12px}}
.cbody ul{{margin:4px 0;padding-left:18px}}pre{{white-space:pre-wrap;background:var(--page);padding:8px;font-size:12px;border:1px solid var(--line)}}
.bar{{display:grid;grid-template-columns:70px 1fr 48px;gap:6px;align-items:center;font-size:12px;margin:3px 0}}
.bt{{background:var(--line);height:8px;border-radius:0 4px 4px 0}}.bt span{{display:block;height:8px;background:var(--bar);border-radius:0 4px 4px 0}}.bv{{text-align:right;color:var(--ink2)}}
.wide{{overflow-x:auto}}table.chgt td.chg{{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}}
td.chg.good{{color:var(--good);font-weight:600}}td.chg.bad{{color:var(--bad);font-weight:600}}td.chg.same{{color:var(--muted)}}
.scs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:12px}}
figure.sc{{margin:0;background:var(--surface);border:1px solid var(--ring);padding:8px}}figure.sc figcaption{{font-size:12px;font-weight:600;margin-bottom:4px}}
.sc svg{{width:100%;height:auto}}.sc .ax{{stroke:var(--muted);stroke-width:1}}.sc .dg{{stroke:var(--line);stroke-width:2;stroke-dasharray:4 4}}
.sc .tk{{font-size:10px;fill:var(--muted)}}.sc .pl{{font-size:10px;fill:var(--ink2)}}
.sc .pt{{stroke:var(--surface);stroke-width:2}}.sc .pt.good{{fill:var(--good)}}.sc .pt.bad{{fill:var(--bad)}}.sc .pt.same{{fill:var(--muted)}}
a{{color:inherit}}@media (max-width:760px){{.cbody{{grid-template-columns:1fr}}.card .sum{{float:none;display:block}}}}
</style></head><body><main>
<header><h1>{html.escape(title)}</h1><div class="meta">정답셋 {len(rows)}건 · UI 와 같은 전체 흐름 (Planner → … → Composer → KPI Agent)
{f" · 기준 실행 <b>{html.escape(baseline_dir.name)}</b> ({len(base_rows)}건)과 비교" if base_rows else ""} · 생성 {datetime.now():%Y-%m-%d %H:%M}</div></header>

<h2>1. 한눈에 — KPI Agent 지표</h2><p>KPI Agent 가 케이스마다 낸 지표의 평균 (정의: <code>docs/SE_kpi-agent-design.md</code>). 핵심 = [변경] 노드 정밀도 · [변경] 재현율 · 노드 탐지율 (16건 합산), 참고 = Critical 미탐률 · 노드 정밀도, 판정 = Planner·Simulator·Composer 판정 일치, 성능 = 실행 로그.{" KPI 측정 방식이 기준 실행과 달라 이 절은 비교하지 않는다." if changed else " 증감은 기준 실행 대비 (✓ 개선 · ! 악화)." if base_rows else ""}</p>
{kpi_html}

<h2>1-1. 탐색 지표 (YJ 평가 스크립트)</h2><p>Planner·Explorer·Retriever 품질과 판정 흐름 (정답 경로 기준).{" 증감은 기준 실행 대비이고, 좋아진 방향은 ✓, 나빠진 방향은 ! 로 표시한다." if base_rows else " 기준 실행 없이 이 run 만 (다음 run 부터 --baseline 으로 비교)."}</p>
<div class="tiles">{tile_html}</div>

{_changes(rows, base, baseline_dir.name if baseline_dir else "", changed)}

<h2>2. 판정 흐름</h2><p>정답 복잡도와 실제 흐름. 1차 simple · 정답 complex 는 "상세 분석 요청"을 눌렀다고 가정해 2차로 간다.</p>
<table><thead><tr><th>케이스</th><th>정답</th><th>1차 판정</th><th>상세 분석 권장</th><th>재분석</th><th>Verifier</th><th>최종</th></tr></thead>
<tbody>{"".join(_flow_row(s) for s in rows)}</tbody></table>

<h2>3. 정답이 어디서 빠지나</h2><p>정답 경로가 단계마다 얼마나 남는지 (1 = 전부). 왼쪽에서 오른쪽으로 값이 떨어지는 칸이 손실 지점이다. 행에 마우스를 올리면 기준 실행 값.</p>
<div class="legend">0 <i style="background:{SEQ[0]}"></i><i style="background:{SEQ[1]}"></i><i style="background:{SEQ[2]}"></i><i style="background:{SEQ[3]}"></i><i style="background:{SEQ[4]}"></i><i style="background:{SEQ[5]}"></i> 1</div>
<table><thead><tr><th>케이스</th><th>Explorer 경로 재현율</th><th>Retriever 근거율</th><th>Simulator 전달</th><th>KPI 노드 탐지율</th><th class=num>Explorer 경로 수</th><th>비고</th></tr></thead>
<tbody>{"".join(_loss_row(s, base.get(s["case"])) for s in rows)}</tbody></table>

<h2>4. 시간 · 비용 · 정밀도</h2><p>케이스별 지연(재분석 포함)을 에이전트별로 쌓은 막대, 토큰·호출 수, 정밀도. KPI 정밀도는 결과 경로 위 노드 전체가 분모라 지나가기만 하는 노드([참조])도 오탐이 된다.
[변경] 정밀도 = 에이전트가 고친다고 표시한 노드 중 정답 비율, 오탐 중 [참조] = KPI 오탐 가운데 에이전트 스스로 [참조]·표시 없음으로 둔 비율. 막대에 마우스를 올리면 에이전트별 초, 행에 올리면 기준 실행 값.</p>
{_cost_table(rows, base)}

<h2>5. 개선 후보</h2><p>이번 실행 기록(케이스 md·실행 로그·KPI)을 분석해 뽑은 개선 후보. 근거는 이번 실행의 측정값이다.</p>
{_findings(run_dir, rows)}

<h2>6. 케이스</h2><p>펼치면 흐름·권장 사유·누락·에이전트별 시간과 md·실행 로그 링크.</p>
{"".join(_card(s, base.get(s["case"])) for s in rows)}
</main></body></html>"""
    out = run_dir / "report.html"
    out.write_text(doc, encoding="utf-8")
    return out
