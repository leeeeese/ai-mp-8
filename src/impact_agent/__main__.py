"""CLI: python -m impact_agent "<요청 문장>" --sm-part product [--answer-key data/ground_truth/L-1.json]"""

import argparse

from .agents import Supervisor
from .agents.kpi import load_answer_key
from .schemas import SmPart, UserInput


def main() -> None:
    parser = argparse.ArgumentParser(prog="impact_agent")
    parser.add_argument("request_text")
    parser.add_argument("--sm-part", choices=[p.value for p in SmPart], required=True)
    parser.add_argument("--answer-key", help="PoC 평가용 정답 파일 (data/ground_truth/<요건ID>.json)")
    args = parser.parse_args()

    supervisor = Supervisor()
    answer_key = load_answer_key(args.answer_key) if args.answer_key else None
    result = supervisor.run(UserInput(request_text=args.request_text, sm_part=SmPart(args.sm_part)), answer_key)
    print(result.markdown)

    kpi = supervisor.last_state.kpi
    if kpi is not None:
        print("\n## KPI")
        for m in kpi.metrics:
            print(f"- {m.name.value}: {m.value:g} {m.unit or ''}".rstrip())


if __name__ == "__main__":
    main()
