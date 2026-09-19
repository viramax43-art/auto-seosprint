from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.executors.base import BaseExecutor
from src.models import AutomationLevel, ExecutionResult, SeoTask, TaskAnalysis, TaskCategory
from src.parser import extract_urls_from_text

if TYPE_CHECKING:
    from playwright.sync_api import Page

SOCIAL_CATEGORIES = {
    TaskCategory.SOCIAL_SUBSCRIBE,
    TaskCategory.YOUTUBE,
    TaskCategory.TELEGRAM,
}

INTERACTIVE_ACTIONS = (
    "подпис",
    "лайк",
    "like",
    "коммент",
    "repost",
    "репост",
    "subscribe",
    "join",
    "вступ",
)


class SocialSessionExecutor(BaseExecutor):
    """Выполнение через браузерную сессию (YouTube, VK, Telegram и др.)."""

    name = "social_session"

    def can_handle(self, analysis: TaskAnalysis) -> bool:
        if analysis.automation_level == AutomationLevel.SKIP:
            return False
        if analysis.category == TaskCategory.VISIT_URLS:
            return False
        if analysis.category in SOCIAL_CATEGORIES:
            return True
        if not analysis.required_platforms:
            return False
        actions = " ".join(analysis.required_actions).lower()
        return any(word in actions for word in INTERACTIVE_ACTIONS)

    def execute(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        if page is None:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="Нужна активная сессия браузера SEOsprint",
            )

        screenshots_dir = self.settings.data_dir / "screenshots" / task.task_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        urls = analysis.urls or extract_urls_from_text(f"{task.description} {task.report_requirements}")
        if not urls:
            try:
                urls = extract_urls_from_text(page.content())
            except Exception:
                urls = []
        if not urls:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="URL не найдены в ТЗ задания",
            )

        visited: list[str] = []
        screenshot_paths: list[str] = []
        errors: list[str] = []

        context = page.context
        work_page = None
        try:
            for index, url in enumerate(urls[:5], start=1):
                if work_page is None or work_page.is_closed():
                    work_page = context.new_page()
                try:
                    work_page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    work_page.wait_for_timeout(2000)
                    if work_page.url and not work_page.url.startswith("about:"):
                        visited.append(work_page.url)
                    shot = screenshots_dir / f"social_{index}_{datetime.now().strftime('%H%M%S')}.png"
                    if self.safe_screenshot(work_page, shot):
                        screenshot_paths.append(str(shot))
                    else:
                        errors.append(f"Скриншот не сделан: {url}")
                except Exception as exc:
                    errors.append(f"{url}: {exc}")
        finally:
            if work_page is not None and not work_page.is_closed() and len(context.pages) > 1:
                try:
                    work_page.close()
                except Exception:
                    pass

        if not visited:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="; ".join(errors) or "Не удалось открыть URL",
            )

        report = ""
        message = f"Выполнено через сессию ({', '.join(analysis.required_platforms) or 'social'})"
        if errors:
            message = f"{message}. Предупреждения: {'; '.join(errors[:2])}"

        return ExecutionResult(
            task_id=task.task_id,
            success=True,
            mode=self.name,
            message=message,
            collected_data={"visited_urls": visited, "platforms": analysis.required_platforms},
            report_text=report,
            screenshot_paths=screenshot_paths,
        )

