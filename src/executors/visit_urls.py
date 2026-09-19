from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from src.executors.base import BaseExecutor
from src.models import AutomationLevel, ExecutionResult, SeoTask, TaskAnalysis, TaskCategory
from src.seosprint.browser_log import attach_page_logging, get_browser_logger, log_goto
from src.seosprint.platforms import PLATFORM_LABELS, is_platform_logged_in, platform_for_url

if TYPE_CHECKING:
    from playwright.sync_api import Page


class VisitUrlsExecutor(BaseExecutor):
    name = "visit_urls"

    def can_handle(self, analysis: TaskAnalysis) -> bool:
        if analysis.automation_level == AutomationLevel.SKIP or not analysis.urls:
            return False
        return analysis.category == TaskCategory.VISIT_URLS

    def execute(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        screenshots_dir = self.settings.data_dir / "screenshots" / task.task_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        visited: list[str] = []
        screenshot_paths: list[str] = []

        step_error = ""
        try:
            if page is not None:
                visited, screenshot_paths, step_error = self._visit_with_page(
                    page, analysis.urls, screenshots_dir
                )
            else:
                visited, screenshot_paths = self._visit_standalone(analysis.urls, screenshots_dir)

            if step_error:
                return ExecutionResult(
                    task_id=task.task_id,
                    success=False,
                    mode=self.name,
                    message=step_error,
                    collected_data={"visited_urls": visited},
                    screenshot_paths=screenshot_paths,
                )

            return ExecutionResult(
                task_id=task.task_id,
                success=True,
                mode=self.name,
                message=f"Посещено {len(visited)} URL",
                collected_data={"visited_urls": visited},
                report_text="",
                screenshot_paths=screenshot_paths,
            )
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message=str(exc),
                collected_data={"visited_urls": visited},
                screenshot_paths=screenshot_paths,
            )

    def _visit_with_page(
        self,
        page: Page,
        urls: list[str],
        screenshots_dir: Path,
    ) -> tuple[list[str], list[str], str]:
        visited: list[str] = []
        screenshot_paths: list[str] = []
        context = page.context

        logger = get_browser_logger(log_dir=self.settings.data_dir / "logs")
        work_page = None
        try:
            for index, url in enumerate(urls, start=1):
                if work_page is None or work_page.is_closed():
                    work_page = context.new_page()
                    if self.settings.browser_debug:
                        attach_page_logging(work_page, logger, label=f"visit-{index}")
                logger.info("VISIT [%d/%d] target=%s", index, len(urls), url)
                log_goto(work_page, url, logger, wait_until="domcontentloaded", timeout=60000)
                work_page.wait_for_timeout(3000)
                logger.info("VISIT [%d/%d] landed=%s", index, len(urls), work_page.url[:200])

                platform = platform_for_url(url) or platform_for_url(work_page.url)
                if platform and not is_platform_logged_in(work_page, platform):
                    label = PLATFORM_LABELS.get(platform, platform)
                    message = f"Нужна сессия {label} (обнаружено при выполнении)"
                    if self.tz_context:
                        self.tz_context.log_action(f"Открыть {url}", message)
                    return visited, screenshot_paths, message

                if work_page.url and not work_page.url.startswith("about:"):
                    visited.append(work_page.url)

                shot = screenshots_dir / f"visit_{index}_{datetime.now().strftime('%H%M%S')}.png"
                if self.safe_screenshot(work_page, shot):
                    screenshot_paths.append(str(shot))

                if self.tz_context:
                    ok, reason = self.tz_context.verify_action(
                        f"Посетить {url}",
                        f"URL: {work_page.url}, скрин: {bool(screenshot_paths)}",
                    )
                    if not ok:
                        return visited, screenshot_paths, reason or "Шаг не соответствует ТЗ"
        finally:
            if work_page is not None and not work_page.is_closed() and len(context.pages) > 1:
                try:
                    logger.info("VISIT close work tab | %s", work_page.url[:120])
                    work_page.close()
                except Exception as exc:
                    logger.warning("VISIT close failed: %s", exc)

        return visited, screenshot_paths, ""

    def _visit_standalone(
        self,
        urls: list[str],
        screenshots_dir: Path,
    ) -> tuple[list[str], list[str]]:
        visited: list[str] = []
        screenshot_paths: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.settings.headless,
                slow_mo=self.settings.browser_slow_mo,
            )
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()

            for index, url in enumerate(urls, start=1):
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                visited.append(page.url)

                shot = screenshots_dir / f"visit_{index}_{datetime.now().strftime('%H%M%S')}.png"
                if self.safe_screenshot(page, shot):
                    screenshot_paths.append(str(shot))

            browser.close()

        return visited, screenshot_paths

