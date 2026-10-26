"""영역별 목업 소스를 파싱해 하나의 Neo4j 영향도 그래프 시드를 생성한다.

영역별 파서(scripts/graph/parse_<domain>.py) → 공통 검증(scripts/graph/model.py) → data/graph/{nodes.csv, edges.csv, seed.cypher}
소스 루트는 영역별로 .env 의 SOURCE_ROOT_<DOMAIN> (없으면 data/source/<domain>).
용어사전은 data/glossary/<domain>.csv (term, synonyms(| 구분), node_id, note) 를 노드 속성 terms 와 검색 인덱스에 넣는다.

usage:
    python scripts/build_graph.py            # data/graph/ 갱신
    python scripts/build_graph.py --strict   # 끊긴 참조 / 규약 외 패턴 / ID 규칙 위반이 있으면 실패(exit 1)
    python scripts/build_graph.py --load     # 생성 후 .env 의 Neo4j 에 적재 (기존 :Node 그래프 삭제 후 재적재)
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

from graph import model, parse_swing, parse_tos

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data" / "graph"
PARSERS = {"swing": parse_swing.parse, "tos": parse_tos.parse}


def env_path(key: str, default: str) -> Path:
    return (ROOT / os.environ.get(key, default)).resolve()


def load_glossary(domain: str) -> list[dict]:
    path = env_path("GLOSSARY_DIR", "data/glossary") / f"{domain}.csv"
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("term") and r.get("node_id"):
                syn = [s.strip() for s in (r.get("synonyms") or "").split("|") if s.strip()]
                rows.append({"term": r["term"].strip(), "synonyms": syn, "node_id": r["node_id"].strip(), "domain": domain})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true", help="생성 후 Neo4j 에 적재")
    ap.add_argument("--strict", action="store_true", help="오류가 있으면 exit 1")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    results, glossary = [], []
    for domain, parse in PARSERS.items():
        root = env_path(f"SOURCE_ROOT_{domain.upper()}", f"data/source/{domain}")
        if not root.is_dir():
            print(f"[INFO] {domain}: 소스 루트 없음 ({root}) - 건너뜀", file=sys.stderr)
            continue
        results.append(parse(root, domain))
        glossary += load_glossary(domain)
    graph = model.finalize(results)
    model.enrich(graph, glossary)

    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    model.write_csv(GRAPH_DIR, graph.nodes, graph.edges)
    model.write_cypher(GRAPH_DIR, graph.nodes, graph.edges)

    for domain, cnt in sorted(Counter(n["domain"] for n in graph.nodes).items()):
        labels = Counter(n["label"] for n in graph.nodes if n["domain"] == domain)
        print(f"{domain} nodes {cnt}: " + ", ".join(f"{k} {v}" for k, v in sorted(labels.items())))
    print(f"glossary terms {len(glossary)}, common nodes {sum(n['properties']['is_common'] for n in graph.nodes)}")
    print(f"edges {len(graph.edges)}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(e['type'] for e in graph.edges).items())))
    for msg in graph.infos:
        print(f"[INFO] {msg}", file=sys.stderr)
    for err in graph.errors:
        print(f"[WARN] {err}", file=sys.stderr)

    if args.load:
        model.load_neo4j(graph.nodes, graph.edges)
        print("Neo4j 적재 완료")
    return 1 if args.strict and graph.errors else 0


if __name__ == "__main__":
    sys.exit(main())
