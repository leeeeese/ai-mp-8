"""상세 실행 로그. 에이전트·tool·LLM 이 진행 중에 남기고, 받는 곳(터미널·세션 로그 파일·화면)은 API 실행기(api/runner.py)가 붙인다.

받는 곳이 없으면(CLI·평가 스크립트) 출력하지 않는다. 표시 구조는 api/agent_log.py:
    section  블록 안 묶음 (없으면 앞 묶음에 이어 붙음. 동작은 "동작 · …"), label  행 머리, kind  row(행) / route(라우팅)
"""

import logging

logger = logging.getLogger("impact_agent.trace")
logger.propagate = False
MAX_TEXT = 300  # 로그 한 행의 기본 본문 길이 (Thought·원문 인용 등 한 덩어리 기준)


def trace(text: str, agent: str | None = None, section: str | None = None, label: str = "", kind: str = "row") -> None:
    logger.info(text, extra={"agent": agent, "section": section, "label": label, "kind": kind})


def clip(text: object, limit: int = MAX_TEXT) -> str:
    """줄바꿈·연속 공백을 없애고 limit 자로 자른다."""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[:limit - 1] + "…"
