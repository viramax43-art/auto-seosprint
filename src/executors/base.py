from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import Settings
from src.models import ExecutionResult, SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient
from src.report_builder import build_execution_payload, generate_report_text

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from src.task_context import TaskTzContext


class BaseExecutor(ABC):
    name: str = "base"

    def __init__(self, settings: Settings, client: OpenRouterClient) -> None:
        self.settings = settings
        self.client = client
        self.tz_context: TaskTzContext | None = None

    @abstractmethod
    def can_handle(self, analysis: TaskAnalysis) -> bool:
        raise NotImplementedError

    def safe_screenshot(
        self,
        page: Page,
        path: str | Path,
        *,
        timeout: int = 10_000,
    ) -> bool:
        """Скриншот видимой области — full_page на соцсетях часто зависает."""
        try:
            page.screenshot(
                path=str(path),
                full_page=False,
                timeout=timeout,
                animations="disabled",
            )
            return True
        except Exception:
            try:
                page.screenshot(path=str(path), full_page=False, timeout=5_000)
                return True
            except Exception:
                return False

    @abstractmethod
    def execute(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        raise NotImplementedError

    def generate_ai_report(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        collected_data: dict,
        *,
        screenshot_paths: list[str] | None = None,
        session_cache: dict[str, bool] | None = None,
        fix_instructions: str = "",
    ) -> str:
        action_log = self.tz_context.action_log if self.tz_context else []
        payload = build_execution_payload(
            self.settings,
            task,
            analysis,
            collected_data=collected_data,
            screenshot_paths=screenshot_paths,
            action_log=action_log,
            session_cache=session_cache,
        )
        system = self.tz_context.system_prompt() if self.tz_context else ""
        return generate_report_text(
            self.client,
            self.settings,
            task,
            analysis,
            payload,
            system_prompt=system,
            fix_instructions=fix_instructions,
        )
