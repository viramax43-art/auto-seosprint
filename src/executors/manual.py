from __future__ import annotations

import json
from typing import TYPE_CHECKING

from src.executors.base import BaseExecutor
from src.models import AutomationLevel, ExecutionResult, SeoTask, TaskAnalysis

if TYPE_CHECKING:
    from playwright.sync_api import Page


class ManualGuideExecutor(BaseExecutor):
    """Генерирует пошаговую инструкцию и шаблон отчёта через Gemini."""

    name = "manual_guide"

    def can_handle(self, analysis: TaskAnalysis) -> bool:
        return (
            analysis.automation_level == AutomationLevel.MANUAL
            and not analysis.urls
        )

    def execute(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        prompt = f"""Создай пошаговую инструкцию выполнения задания SEOsprint и шаблон отчёта.

Задание:
{json.dumps(task.model_dump(), ensure_ascii=False, indent=2)}

Анализ:
{json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)}

Профиль исполнителя:
- Telegram: {self.settings.executor_telegram or 'не указан'}
- Email: {self.settings.executor_email or 'не указан'}

Верни JSON:
{{
  "steps": ["шаг 1", "..."],
  "report_template": "готовый текст для вставки в отчёт SEOsprint",
  "tips": ["совет 1"],
  "warnings": ["предупреждение"]
}}
"""
        try:
            data = self.client.chat_json(
                [
                    {
                        "role": "system",
                        "content": "Ты помощник исполнителя SEOsprint. Отвечай только JSON на русском.",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            steps = data.get("steps", analysis.required_actions)
            report = data.get("report_template", "")
            tips = data.get("tips", [])
            warnings = data.get("warnings", [])

            guide_lines = [
                f"# Инструкция: T-{task.task_id} — {task.title}",
                "",
                "## Шаги",
                *[f"{i}. {s}" for i, s in enumerate(steps, 1)],
                "",
                "## Шаблон отчёта",
                report,
            ]
            if tips:
                guide_lines.extend(["", "## Советы", *[f"- {t}" for t in tips]])
            if warnings:
                guide_lines.extend(["", "## Внимание", *[f"- {w}" for w in warnings]])

            guide_path = self.settings.data_dir / "guides" / f"{task.task_id}.md"
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            guide_path.write_text("\n".join(guide_lines), encoding="utf-8")

            return ExecutionResult(
                task_id=task.task_id,
                success=True,
                mode=self.name,
                message=f"Инструкция сохранена: {guide_path}",
                collected_data=data,
                report_text=report,
            )
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                success=False,
                mode=self.name,
                message=str(exc),
            )
