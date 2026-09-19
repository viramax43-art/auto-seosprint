from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.executors.base import BaseExecutor
from src.models import AutomationLevel, ExecutionResult, SeoTask, TaskAnalysis
from src.parser import extract_urls_from_text

if TYPE_CHECKING:
    from playwright.sync_api import Page


class BrowserTaskExecutor(BaseExecutor):
    """Универсальный исполнитель: открывает URL из задания в сессии браузера."""

    name = "browser_task"

    def can_handle(self, analysis: TaskAnalysis) -> bool:
        from src.models import TaskCategory

        if analysis.automation_level == AutomationLevel.SKIP:
            return False
        if analysis.category in {
            TaskCategory.VISIT_URLS,
            TaskCategory.YOUTUBE,
            TaskCategory.TELEGRAM,
            TaskCategory.SOCIAL_SUBSCRIBE,
            TaskCategory.FINANCIAL,
        }:
            return False
        return bool(analysis.urls)

    def execute(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        urls = analysis.urls or extract_urls_from_text(f"{task.description} {task.report_requirements}")
        if not urls and page is not None:
            urls = extract_urls_from_text(page.content())
        if not urls:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="URL для выполнения не найдены",
            )

        if page is None:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="Нужна активная сессия браузера",
            )

        screenshots_dir = self.settings.data_dir / "screenshots" / task.task_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        visited: list[str] = []
        screenshot_paths: list[str] = []
        context = page.context

        work_page = None
        try:
            for index, url in enumerate(urls[:8], start=1):
                if work_page is None or work_page.is_closed():
                    work_page = context.new_page()
                work_page.goto(url, wait_until="domcontentloaded", timeout=60000)
                dwell = max(4, min(analysis.estimated_minutes * 10, 15))
                time.sleep(dwell)
                if work_page.url and not work_page.url.startswith("about:"):
                    visited.append(work_page.url)
                shot = screenshots_dir / f"step_{index}_{datetime.now().strftime('%H%M%S')}.png"
                if self.safe_screenshot(work_page, shot):
                    screenshot_paths.append(str(shot))
        finally:
            if work_page is not None and not work_page.is_closed() and len(context.pages) > 1:
                try:
                    work_page.close()
                except Exception:
                    pass

        report = self._generate_report(task, analysis, visited)
        return ExecutionResult(
            task_id=task.task_id,
            success=True,
            mode=self.name,
            message=f"Посещено {len(visited)} URL",
            collected_data={"visited_urls": visited},
            report_text=report,
            screenshot_paths=screenshot_paths,
        )

    def _generate_report(self, task: SeoTask, analysis: TaskAnalysis, visited: list[str]) -> str:
        return self.generate_ai_report(task, analysis, {"visited_urls": visited})
