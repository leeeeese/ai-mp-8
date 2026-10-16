"""CLI: python -m impact_agent "<요청 문장>" --sm-part product"""

import argparse

from .agents import Supervisor
from .schemas import SmPart, UserInput


def main() -> None:
    parser = argparse.ArgumentParser(prog="impact_agent")
    parser.add_argument("request_text")
    parser.add_argument("--sm-part", choices=[p.value for p in SmPart], required=True)
    args = parser.parse_args()

    result = Supervisor().run(UserInput(request_text=args.request_text, sm_part=SmPart(args.sm_part)))
    print(result.markdown)


if __name__ == "__main__":
    main()
