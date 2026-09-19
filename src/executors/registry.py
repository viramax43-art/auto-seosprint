from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import Settings
from src.executors.base import BaseExecutor
from src.task_context import TaskTzContext
from src.executors.ai_browser import AiBrowserExecutor
from src.executors.browser_task import BrowserTaskExecutor
from src.executors.manual import ManualGuideExecutor
from src.executors.social import SocialSessionExecutor
from src.executors.visit_urls import VisitUrlsExecutor
from src.models import ExecutionResult, SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient

if TYPE_CHECKING:
    from playwright.sync_api import Page


class ExecutorRegistry:
    def __init__(self, settings: Settings, client: OpenRouterClient) -> None:
        self.ai_browser = AiBrowserExecutor(settings, client)
        self.executors: list[BaseExecutor] = [
            VisitUrlsExecutor(settings, client),
            SocialSessionExecutor(settings, client),
            BrowserTaskExecutor(settings, client),
            ManualGuideExecutor(settings, client),
        ]
        self._tz_context: TaskTzContext | None = None

    def set_tz_context(self, ctx: TaskTzContext | None) -> None:
        self._tz_context = ctx
        self.ai_browser.tz_context = ctx
        for executor in self.executors:
            executor.tz_context = ctx

    def run(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        if analysis.automation_level.value == "skip":
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode="skip",
                message=analysis.skip_reason or "Задание пропущено (депозит/казино)",
            )

        last_result: ExecutionResult | None = None
        for executor in self.executors:
            if not executor.can_handle(analysis):
                continue
            try:
                result = executor.execute(task, analysis, page=page)
            except Exception as exc:
                result = ExecutionResult(
                    task_id=task.task_id,
                    success=False,
                    mode=executor.name,
                    message=str(exc),
                )
            if result.success:
                return result
            last_result = result

        if page is not None and task.description.strip():
            try:
                ai_result = self.ai_browser.execute(task, analysis, page=page)
            except Exception as exc:
                ai_result = ExecutionResult(
                    task_id=task.task_id,
                    success=False,
                    mode=self.ai_browser.name,
                    message=str(exc),
                )
            if ai_result.success:
                return ai_result
            last_result = ai_result

        if last_result is not None:
            return last_result

        return ExecutionResult(
            task_id=task.task_id,
            success=False,
            mode="none",
            message="Нет подходящего исполнителя",
        )
