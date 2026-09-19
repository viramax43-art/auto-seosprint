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

PLAN_PROMPT = """Ты планируешь выполнение задания SEOsprint в браузере.

Изучи полное ТЗ и составь план. Верни JSON:
{
  "urls_to_visit": ["https://..."],
  "browser_steps": ["шаг 1", "шаг 2"],
  "dwell_seconds": 5,
  "report_text": "готовый текст отчёта строго по report_requirements",
  "needs_screenshot": true
}

Правила:
- urls_to_visit: все ссылки из ТЗ, по которым нужно перейти
- report_text: только то, что просит рекламодатель (ник, номер подписки, ссылки и т.д.)
- если данных для отчёта нет — используй Telegram/email исполнителя или напиши что скриншот приложен
- browser_steps: кратко, что делать на каждом URL
"""


class AiBrowserExecutor(BaseExecutor):
    """AI-планировщик + браузерное выполнение (fallback для сложных заданий)."""

    name = "ai_browser"

    def can_handle(self, analysis: TaskAnalysis) -> bool:
        return False

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
                message="Нужна страница задания SEOsprint",
            )

        if not task.description.strip():
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="ТЗ задания пустое — нечего выполнять",
            )

        plan = self._build_plan(task, analysis)
        urls = plan.get("urls_to_visit") or analysis.urls or extract_urls_from_text(
            f"{task.description} {task.report_requirements} {page.content()}"
        )
        if not urls:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message="AI не нашёл URL для выполнения",
            )

        screenshots_dir = self.settings.data_dir / "screenshots" / task.task_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        visited: list[str] = []
        screenshot_paths: list[str] = []
        dwell = int(plan.get("dwell_seconds") or 5)
        context = page.context

        work_page = None
        try:
            for index, url in enumerate(urls[:8], start=1):
                if work_page is None or work_page.is_closed():
                    work_page = context.new_page()
                work_page.goto(url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(max(3, dwell))
                if work_page.url and not work_page.url.startswith("about:"):
                    visited.append(work_page.url)
                if plan.get("needs_screenshot", True):
                    shot = screenshots_dir / f"ai_{index}_{datetime.now().strftime('%H%M%S')}.png"
                    if self.safe_screenshot(work_page, shot):
                        screenshot_paths.append(str(shot))
        finally:
            if work_page is not None and not work_page.is_closed() and len(context.pages) > 1:
                try:
                    work_page.close()
                except Exception:
                    pass

        report = str(plan.get("report_text") or "").strip()
        if not report:
            report = self.generate_ai_report(
                task,
                analysis,
                {
                    "visited_urls": visited,
                    "steps": plan.get("browser_steps", []),
                },
            )

        return ExecutionResult(
            task_id=task.task_id,
            success=True,
            mode=self.name,
            message=f"AI-план: {len(visited)} URL, {len(plan.get('browser_steps', []))} шагов",
            collected_data={"plan": plan, "visited_urls": visited},
            report_text=report,
            screenshot_paths=screenshot_paths,
        )

    def _build_plan(self, task: SeoTask, analysis: TaskAnalysis) -> dict:
        payload = {
            "task": task.model_dump(),
            "analysis": analysis.model_dump(),
            "executor_telegram": self.settings.executor_telegram,
            "executor_email": self.settings.executor_email,
        }
        try:
            return self.client.chat_json(
                [
                    {"role": "system", "content": PLAN_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
        except Exception:
            return {
                "urls_to_visit": analysis.urls or extract_urls_from_text(
                    f"{task.description} {task.report_requirements}"
                ),
                "browser_steps": analysis.required_actions,
                "dwell_seconds": 5,
                "needs_screenshot": "скрин" in task.report_requirements.lower(),
                "report_text": "",
            }
