"""SE 반복 실험 결과(experiment.json)를 한 페이지 HTML 로 만든다 (run_experiment.py 가 끝날 때도 부른다).

    python3 scripts/SE/experiment_html.py --exp-id 20261103-base
    python3 scripts/SE/experiment_html.py --exp-id 20261104-ops --compare 20261103-base

출력: outputs/runs/SE/experiments/<exp_id>/experiment.html
구성: 핵심 지표 타일 · 회차 간 편차(범위 막대) · (--compare) 이전 실험 대비 · 놓친 [변경] 노드 분류 · 케이스별 회차 값 · 모든 회차에서 놓친 노드 · [변경] 오탐
"""

import argparse
import json
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "runs" / "SE" / "experiments"

PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SE 반복 실험</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f4f2; --surface: #fcfcfb; --border: #e3e2de; --grid: #ecebe7;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #7a7974;
  --accent: #2a78d6; --neutral-mark: #b9b8b2; --range: #d6d5d0;
  --warn-bg: #fff4dc; --warn-border: #eda100;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #111110; --surface: #1a1a19; --border: #2e2e2c; --grid: #262624;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
    --accent: #3987e5; --neutral-mark: #6b6a64; --range: #3a3a37;
    --warn-bg: #2a2210; --warn-border: #c98500;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #111110; --surface: #1a1a19; --border: #2e2e2c; --grid: #262624;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
  --accent: #3987e5; --neutral-mark: #6b6a64; --range: #3a3a37;
  --warn-bg: #2a2210; --warn-border: #c98500;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text-primary);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Pretendard", "Segoe UI", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 17px; margin: 0 0 4px; }
h3 { font-size: 14px; margin: 16px 0 6px; color: var(--text-secondary); font-weight: 600; }
.sub { color: var(--text-secondary); margin: 0 0 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.card .desc { color: var(--text-secondary); margin: 0 0 16px; font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.tile .label { color: var(--text-secondary); font-size: 12px; }
.tile .value { font-size: 28px; font-weight: 650; letter-spacing: -0.02em; margin: 2px 0; font-variant-numeric: tabular-nums; }
.tile .note { color: var(--text-muted); font-size: 12px; }
.findings { border-left: 4px solid var(--warn-border); background: var(--warn-bg); }
.findings ol { margin: 8px 0 0; padding-left: 20px; }
.findings li { margin-bottom: 8px; }
.findings li:last-child { margin-bottom: 0; }
.findings b { font-weight: 650; }
code { font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-bottom: 8px; font-size: 13px; color: var(--text-secondary); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend svg { width: 14px; height: 14px; display: inline-block; flex: none; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { fill: var(--text-secondary); font-size: 12px; }
svg .axis { stroke: var(--grid); stroke-width: 1; }
#tip { position: fixed; pointer-events: none; z-index: 10; background: var(--surface); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.15);
  max-width: 320px; display: none; }
#tip b { font-weight: 650; }
#tip .muted { color: var(--text-secondary); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 600; background: var(--surface); }
td:first-child, th:first-child, td.l, th.l { text-align: left; }
td.var { font-weight: 700; }
.badge { display: inline-block; font-size: 11px; padding: 0 6px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-secondary); margin-left: 6px; }
ul.plain { margin: 0; padding-left: 18px; }
ul.plain li { margin-bottom: 3px; font-size: 13px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.foot { color: var(--text-muted); font-size: 12px; }
@media (max-width: 760px) { .tiles { grid-template-columns: repeat(2, 1fr); } .cols { grid-template-columns: 1fr; } .tile .value { font-size: 22px; } }
</style>
</head>
<body>
<main>
  <h1 id="title"></h1>
  <p class="sub" id="sub"></p>
  <div class="tiles" id="tiles"></div>
  <section class="card findings"><h2>이번 실험에서 확인된 것</h2><ol id="findings"></ol></section>

  <section class="card">
    <h2>회차 간 편차</h2>
    <p class="desc">지표마다 회차 값(회색 점)과 범위(옅은 막대), 평균(파란 세로선)입니다. 다음 실험에서 이 범위보다 작은 변화는 개선으로 보기 어렵습니다. 비율 지표는 0~1 같은 축입니다.</p>
    <div class="legend"><span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="var(--neutral-mark)"/></svg>회차 값</span>
      <span><svg width="14" height="14"><rect x="0" y="4" width="14" height="6" rx="3" fill="var(--range)"/></svg>범위 (최소~최대)</span>
      <span><svg width="14" height="14"><rect x="6" y="0" width="2.5" height="14" fill="var(--accent)"/></svg>평균</span></div>
    <div id="spread"></div>
    <div class="table-wrap" style="margin-top:12px"><table id="metricTbl"></table></div>
  </section>

  <section class="card" id="cmpSec" hidden>
    <h2>이전 실험 대비</h2>
    <p class="desc" id="cmpDesc"></p>
    <div class="table-wrap"><table id="cmpTbl"></table></div>
  </section>

  <section class="card">
    <h2>놓친 [변경] 노드</h2>
    <p class="desc">정답 변경 노드(impacted 중 modify·add)가 [변경] 으로 표시되지 않은 이유. 막대 = 회차 평균, 숫자 = 회차별 값.</p>
    <div id="miss"></div>
    <div class="cols">
      <div><h3 id="alwaysTitle"></h3><ul class="plain" id="always"></ul></div>
      <div><h3>[변경] 오탐 (회차 수)</h3><ul class="plain" id="fps"></ul></div>
    </div>
  </section>

  <section class="card">
    <h2>케이스별 회차 값</h2>
    <p class="desc">회차마다 값이 다른 칸은 굵게, 케이스 이름 옆에 "변동" 표시. [변경] 재현 = 맞힌 수/정답 변경 노드 수, 오탐 = [변경] 으로 표시했지만 정답이 아닌 노드 수.</p>
    <div class="table-wrap"><table id="caseTbl"></table></div>
  </section>

  <p class="foot" id="foot"></p>
</main>
<div id="tip"></div>
<script>
const D = __DATA__;
const META = D.meta, REPS = D.repeats, BASE = D.base;
const M = [
  { k: "change_precision", n: "[변경] 노드 정밀도", ratio: true, s: 1, main: true },
  { k: "change_recall", n: "[변경] 재현율", ratio: true, s: 1 },
  { k: "node_recall", n: "노드 탐지율", ratio: true, s: 1 },
  { k: "tag_complex", n: "태그 정확도 complex", ratio: true, s: 1 },
  { k: "tag_simple", n: "태그 정확도 simple", ratio: true, s: 1 },
  { k: "area_match", n: "영역 일치", ratio: true, s: 1 },
  { k: "node_precision", n: "노드 정밀도 (참고)", ratio: true, s: 1 },
  { k: "difficulty_match", n: "난이도 일치 (건)", s: 1 },
  { k: "unneeded", n: "불필요한 권장 (건)", s: -1 },
  { k: "se_seconds", n: "SE 구간 시간 (초)", s: -1 },
  { k: "se_tokens", n: "SE 토큰", s: -1 },
  { k: "total_seconds", n: "전체 시간 (초)", s: -1 },
  { k: "result_paths", n: "결과 경로 수 (합)", s: -1 },
];
const E2E = META.from === "e2e";
const upstreamText = m => m.from === "e2e" ? "e2e (회차마다 전체 흐름)" : `상류 고정 ${m.from}`;
const KINDS = ["경로에 없음 (상류)", "LLM 입력 밖 (경로 상한)", "판정 누락 (기본값 [참조])", "LLM 이 [참조] 판정", "표시 없음"];
const NS = "http://www.w3.org/2000/svg";
const el = (t, a = {}, p) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); if (p) p.appendChild(e); return e; };
const text = (p, x, y, s, a = {}) => { const t = el("text", { x, y, ...a }, p); t.textContent = s; return t; };
const tip = document.getElementById("tip");
function bindTip(node, html) {
  node.addEventListener("mousemove", ev => {
    tip.innerHTML = html; tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight; let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14; if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  });
  node.addEventListener("mouseleave", () => tip.style.display = "none");
}
const fmt = (v, m) => v == null ? "-" : m && m.ratio ? v.toFixed(3)
  : Number.isInteger(v) || Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("ko-KR") : (Math.round(v * 10) / 10).toLocaleString("ko-KR");
const stat = xs => { xs = xs.filter(x => x != null); return xs.length ? { mean: xs.reduce((a, b) => a + b, 0) / xs.length, lo: Math.min(...xs), hi: Math.max(...xs) } : null; };
const vals = (reps, k) => reps.map(r => r.metrics[k]);
const cases = Object.keys(REPS[0].cases);
const sumc = (r, f) => Object.values(r.cases).reduce((a, c) => a + f(c), 0);

document.getElementById("title").textContent = `SE 반복 실험 ${META.exp_id}` + (META.label ? ` · ${META.label}` : "");
document.getElementById("sub").textContent = `${upstreamText(META)} · ${REPS.length}회 반복 · 케이스 ${cases.length}건 · 코드 ${META.commit} · ${META.updated}`;

// --- 타일 ---
const T = k => stat(vals(REPS, k));
const hits = REPS.map(r => sumc(r, c => c.change_hit)), tot = sumc(REPS[0], c => c.change_total);
const tiles = [
  ["[변경] 노드 정밀도 (주 지표)", T("change_precision").mean.toFixed(3), `범위 ${T("change_precision").lo.toFixed(3)} ~ ${T("change_precision").hi.toFixed(3)}`],
  ["[변경] 재현율", T("change_recall").mean.toFixed(3), `맞힌 수 ${Math.min(...hits)}~${Math.max(...hits)} / ${tot}`],
  ["노드 탐지율", T("node_recall").mean.toFixed(3), `범위 ${T("node_recall").lo.toFixed(3)} ~ ${T("node_recall").hi.toFixed(3)}`],
  E2E && T("total_seconds")
    ? ["전체 시간 (16건 합)", `${Math.round(T("total_seconds").mean)}초`, `범위 ${Math.round(T("total_seconds").lo)}~${Math.round(T("total_seconds").hi)}초 · 경로 수 ${Math.round(T("result_paths").lo)}~${Math.round(T("result_paths").hi)}`]
    : ["SE 구간 시간", `${Math.round(T("se_seconds").mean)}초`, `범위 ${Math.round(T("se_seconds").lo)}~${Math.round(T("se_seconds").hi)}초 · 토큰 평균 ${Math.round(T("se_tokens").mean).toLocaleString("ko-KR")}`],
];
document.getElementById("tiles").innerHTML = tiles.map(([l, v, n]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div><div class="note">${n}</div></div>`).join("");

// --- 확인된 것 (데이터에서 자동 작성) ---
const varying = cases.filter(c => { const vs = REPS.map(r => r.cases[c]); return new Set(vs.map(v => `${v.change_hit}|${v.change_fp.length}|${v.tag}`)).size > 1; });
const missMean = KINDS.map(k => [k, REPS.reduce((a, r) => a + (r.metrics.miss_kinds[k] || 0), 0) / REPS.length]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
const always = D.always, fps = D.fps;
const cp = T("change_precision"), cr = T("change_recall");
const F = [
  `<b>회차 간 편차: [변경] 노드 정밀도 ${cp.lo.toFixed(3)} ~ ${cp.hi.toFixed(3)} (폭 ${(cp.hi - cp.lo).toFixed(3)}), [변경] 재현율 ${cr.lo.toFixed(3)} ~ ${cr.hi.toFixed(3)} (폭 ${(cr.hi - cr.lo).toFixed(3)}).</b> 다음 실험에서 이 폭보다 작은 변화는 LLM 응답 편차 안입니다.`,
  `<b>케이스 ${cases.length}건 중 ${cases.length - varying.length}건은 ${REPS.length}회 모두 같은 결과입니다.</b> ` + (varying.length ? `회차마다 달라진 케이스: ${varying.join(", ")}. 편차는 이 케이스들에서만 나옵니다.` : "회차마다 달라진 케이스가 없습니다."),
  `<b>놓친 [변경] 노드 (회차 평균): ${missMean.map(([k, v]) => `${k} ${Math.round(v * 10) / 10}`).join(", ") || "없음"}.</b> 모든 회차에서 놓친 노드는 ${always.length}개이고, 그중 ${always.filter(a => a.kind === "LLM 이 [참조] 판정").length}개가 LLM 이 근거를 대고 [참조] 로 판정한 것, ${always.filter(a => a.kind.startsWith("경로에 없음")).length}개가 상류에서 경로에 없는 것입니다.`,
  fps.length ? `<b>[변경] 오탐 ${fps.length}종.</b> ` + fps.slice(0, 4).map(f => `${f.case} <code>${f.node}</code> ${f.n}/${REPS.length}회`).join(", ") + "." : "<b>[변경] 오탐이 없습니다.</b>",
];
document.getElementById("findings").innerHTML = F.map(s => `<li>${s}</li>`).join("");

// --- 회차 간 편차: 범위 막대 (비율 지표, 0~1 공통 축) ---
(function () {
  const box = document.getElementById("spread"), ms = M.filter(m => m.ratio);
  const W = 1000, L = 170, R = 70, row = 30, T0 = 8, B = 26, H = T0 + ms.length * row + B;
  const x = v => L + v * (W - L - R);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "지표별 회차 간 편차" }, box);
  for (let t = 0; t <= 1.0001; t += 0.2) { el("line", { x1: x(t), x2: x(t), y1: T0, y2: H - B, class: "axis" }, svg); text(svg, x(t), H - B + 18, t.toFixed(1), { "text-anchor": "middle" }); }
  ms.forEach((m, i) => {
    const y = T0 + i * row + row / 2, s = stat(vals(REPS, m.k));
    text(svg, 0, y + 4, m.n, { fill: "var(--text-primary)", "font-weight": m.main ? 650 : 400 });
    if (!s) return;
    el("rect", { x: x(s.lo) - 5, y: y - 5, width: Math.max(x(s.hi) - x(s.lo), 0) + 10, height: 10, rx: 5, fill: "var(--range)" }, svg);
    REPS.forEach(r => {
      const v = r.metrics[m.k]; if (v == null) return;
      const c = el("circle", { cx: x(v), cy: y, r: 5, fill: "var(--neutral-mark)", stroke: "var(--surface)", "stroke-width": 2 }, svg);
      bindTip(c, `<b>${m.n} · r${r.repeat}</b><br>${v.toFixed(3)}`);
    });
    el("rect", { x: x(s.mean) - 1.25, y: y - 9, width: 2.5, height: 18, fill: "var(--accent)" }, svg);
    const right = x(s.hi) + 12 + 130 <= W;  // 오른쪽에 공간이 없으면 범위 왼쪽에
    text(svg, right ? x(s.hi) + 12 : x(s.lo) - 12, y + 4, `${s.mean.toFixed(3)} · 폭 ${(s.hi - s.lo).toFixed(3)}`,
         { fill: "var(--text-primary)", "text-anchor": right ? "start" : "end" });
  });
})();
document.getElementById("metricTbl").innerHTML = `<thead><tr><th class="l">지표</th><th>평균</th><th>범위</th>${REPS.map(r => `<th>r${r.repeat}</th>`).join("")}</tr></thead><tbody>` +
  M.map(m => { const s = stat(vals(REPS, m.k)); return `<tr><td>${m.n}</td><td>${s ? fmt(s.mean, m) : "-"}</td><td>${s ? `${fmt(s.lo, m)} ~ ${fmt(s.hi, m)}` : "-"}</td>${REPS.map(r => `<td>${fmt(r.metrics[m.k], m)}</td>`).join("")}</tr>`; }).join("") + "</tbody>";

// --- 이전 실험 대비 ---
if (BASE && BASE.repeats.length) {
  document.getElementById("cmpSec").hidden = false;
  document.getElementById("cmpDesc").textContent = `${BASE.meta.exp_id}${BASE.meta.label ? " · " + BASE.meta.label : ""} (${upstreamText(BASE.meta)}, ${BASE.repeats.length}회) 대비. 판정: 평균 차이가 두 실험의 회차 범위(최대-최소) 중 큰 값보다 크면 개선·악화, 아니면 편차 안.` +
    (BASE.meta.from !== META.from ? (BASE.meta.from === "e2e" || E2E ? " ⚠️ 한쪽은 e2e, 한쪽은 상류 고정이라 탐지율·경로 수·시간 차이에는 상류 변동이 섞입니다. SE 판정 지표 위주로 보세요." : " ⚠️ 상류가 달라 직접 비교가 어렵습니다.") : "");
  document.getElementById("cmpTbl").innerHTML = `<thead><tr><th class="l">지표</th><th>이전 평균 (범위)</th><th>이번 평균 (범위)</th><th>차이</th><th class="l">판정</th></tr></thead><tbody>` +
    M.map(m => {
      const b = stat(vals(BASE.repeats, m.k)), a = stat(vals(REPS, m.k));
      if (!a || !b) return `<tr><td>${m.n}</td><td>-</td><td>-</td><td>-</td><td class="l">-</td></tr>`;
      const d = a.mean - b.mean, spread = Math.max(b.hi - b.lo, a.hi - a.lo);
      const v = Math.abs(d) <= spread ? "편차 안" : d * m.s > 0 ? "개선" : "악화";
      return `<tr><td>${m.n}</td><td>${fmt(b.mean, m)} (${fmt(b.lo, m)}~${fmt(b.hi, m)})</td><td>${fmt(a.mean, m)} (${fmt(a.lo, m)}~${fmt(a.hi, m)})</td>` +
        `<td>${d > 0 ? "+" : ""}${fmt(d, m)}</td><td class="l ${v === "악화" ? "var" : ""}">${v}${v === "악화" ? " ↓" : v === "개선" ? " ↑" : ""}</td></tr>`;
    }).join("") + "</tbody>";
}

// --- 놓친 [변경] 노드 분류 ---
(function () {
  const box = document.getElementById("miss");
  const rows = KINDS.map(k => [k, REPS.map(r => r.metrics.miss_kinds[k] || 0)]);
  const max = Math.max(1, ...rows.flatMap(r => r[1]));
  const W = 1000, L = 210, R = 140, row = 26, barH = 14, H = rows.length * row + 4;
  const x = v => L + (v / max) * (W - L - R);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "놓친 변경 노드 분류" }, box);
  rows.forEach(([k, vs], i) => {
    const y = i * row + (row - barH) / 2, mean = vs.reduce((a, b) => a + b, 0) / vs.length;
    text(svg, 0, y + barH - 3, k, { fill: "var(--text-primary)" });
    const r = el("rect", { x: L, y, width: Math.max(x(mean) - L, mean ? 2 : 0), height: barH, rx: 3, fill: "var(--accent)" }, svg);
    bindTip(r, `<b>${k}</b><br>회차 평균 ${Math.round(mean * 10) / 10}<br><span class="muted">${vs.map((v, j) => `r${j + 1} ${v}`).join(" · ")}</span>`);
    text(svg, x(mean) + 8, y + barH - 3, `${Math.round(mean * 10) / 10}  (${vs.join(" · ")})`, { fill: "var(--text-primary)" });
  });
})();
document.getElementById("alwaysTitle").textContent = `모든 회차에서 놓친 노드 (${always.length}개)`;
const byKind = KINDS.map(k => [k, always.filter(a => a.kind === k)]).filter(x => x[1].length);
document.getElementById("always").innerHTML = byKind.map(([k, xs]) => `<li><b>${k}</b> (${xs.length}): ${xs.map(a => `${a.case} <code>${a.node}</code>`).join(", ")}</li>`).join("") || "<li>없음</li>";
document.getElementById("fps").innerHTML = fps.map(f => `<li>${f.case} <code>${f.node}</code> — ${f.n}/${REPS.length}회</li>`).join("") || "<li>없음</li>";

// --- 케이스별 회차 값 ---
(function () {
  const cols = [["[변경] 재현", c => `${c.change_hit}/${c.change_total}`], ["[변경] 오탐", c => `${c.change_fp.length}`],
                ["태그 정확도", c => c.tag == null ? "-" : c.tag.toFixed(2)], ["탐지율", c => c.node_recall == null ? "-" : c.node_recall.toFixed(2)],
                ["SE(s)", c => `${Math.round(c.se_seconds)}`]];
  const reps = REPS.map(r => `r${r.repeat}`).join(" · ");
  const head = `<thead><tr><th class="l">케이스</th><th class="l">흐름</th>${cols.map(([n]) => `<th>${n} <span style="font-weight:400">(${reps})</span></th>`).join("")}</tr></thead>`;
  const body = cases.map(cs => {
    const vs = REPS.map(r => r.cases[cs]);
    const cells = cols.map(([n, f], ci) => { const xs = vs.map(f); const diff = ci < 4 && new Set(xs).size > 1; return `<td class="${diff ? "var" : ""}">${xs.join(" · ")}</td>`; }).join("");
    return `<tr><td>${cs}${varying.includes(cs) ? '<span class="badge">변동</span>' : ""}</td><td class="l">${vs[0].complexity}</td>${cells}</tr>`;
  }).join("");
  document.getElementById("caseTbl").innerHTML = head + `<tbody>${body}</tbody>`;
})();

document.getElementById("foot").textContent = `scripts/SE/run_experiment.py 결과 (outputs/runs/SE/experiments/${META.exp_id}/experiment.json). ` +
  (E2E ? "회차마다 전체 흐름(Planner → Explorer ⇄ Retriever → Simulator → Verifier → Composer → KPI, 재계획 포함)을 새로 실행 (run_scenarios.py 와 같음)."
       : `상류(Planner·Explorer·Retriever)는 ${META.from} 그대로, SE 구간(Simulator·Verifier·Composer·KPI)만 회차마다 다시 실행 (재계획 미실행).`) + ` [변경] 노드 정밀도·재현율은 케이스별 TP·FP·맞힌 수를 합산한 비율, 그 밖의 비율 지표는 케이스 평균.`;
</script>
</body>
</html>
"""


def page_data(exp: dict, base: dict | None) -> dict:
    reps = exp["repeats"]
    cases = list(reps[0]["cases"]) if reps else []
    first = reps[0]["cases"] if reps else {}
    always = [{"case": c, "node": n, "kind": k} for c in cases for n, k in first.get(c, {}).get("missed_change", {}).items()
              if all(n in r["cases"].get(c, {}).get("missed_change", {}) for r in reps)]
    counts: dict[tuple[str, str], int] = {}
    for r in reps:
        for c, v in r["cases"].items():
            for n in v["change_fp"]:
                counts[(c, n)] = counts.get((c, n), 0) + 1
    fps = [{"case": c, "node": n, "n": k} for (c, n), k in sorted(counts.items(), key=lambda x: -x[1])]
    return {"meta": exp["meta"], "repeats": reps, "always": always, "fps": fps,
            "base": {"meta": base["meta"], "repeats": base["repeats"]} if base else None}


def write_html(exp_id: str, compare: str | None = None) -> Path:
    exp = json.loads((EXP_ROOT / exp_id / "experiment.json").read_text(encoding="utf-8"))
    base_path = EXP_ROOT / compare / "experiment.json" if compare else None
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path and base_path.exists() else None
    out = EXP_ROOT / exp_id / "experiment.html"
    data = json.dumps(page_data(exp, base), ensure_ascii=False).replace("</", "<\\/")
    out.write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--compare", help="비교할 이전 실험 exp id")
    args = ap.parse_args()
    print(f"기록: {write_html(args.exp_id, args.compare)}")


if __name__ == "__main__":
    main()
