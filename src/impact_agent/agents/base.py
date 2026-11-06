"""에이전트 공통 베이스."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel

from ..state import AgentState

OutputT = TypeVar("OutputT", bound=BaseModel)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class BaseAgent(ABC, Generic[OutputT]):
    name: ClassVar[str]
    output_model: ClassVar[type[BaseModel]]

    @property
    def system_prompt(self) -> str:
        """시스템 공통 페르소나(_common.md) + 에이전트별 프롬프트."""
        common = (PROMPT_DIR / "_common.md").read_text(encoding="utf-8")
        return common + "\n" + (PROMPT_DIR / f"{self.name}.md").read_text(encoding="utf-8")

    @abstractmethod
    def run(self, state: AgentState) -> OutputT:
        """state 를 읽어 output_model 형식의 결과를 반환한다."""
