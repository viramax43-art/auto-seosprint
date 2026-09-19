from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Console

from src.analytics.journal import TaskJournal
from src.config import Settings
from src.models import SeoTask
from src.openrouter import OpenRouterClient
from src.seosprint.client import SeosprintClient

console = Console()


class AnalyticsReviewer:
    def __init__(
        self,
        settings: Settings,
        client: OpenRouterClient,
        seosprint: SeosprintClient,
        journal: TaskJournal,
    ) -> None:
        self.settings = settings
        self.client = client
        self.seosprint = seosprint
        self.journal = journal

    def run_startup_review(self) -> tuple[list[dict], list[dict]]:
        """Проверяет отклонённые и требующие доработки задания."""
        fail_items = self.seosprint.fetch_review_tasks("fail")
        fix_items = self.seosprint.fetch_review_tasks("fix")

        console.print(f"[dim]Аналитика: отклонено {len(fail_items)}, доработка {len(fix_items)}[/dim]")

        analyzed_fail: list[dict] = []
        for item in fail_items:
            review = self._analyze_item(item, kind="fail")
            analyzed_fail.append(review)
            path = self.journal.save_review(item["task_id"], review)
            console.print(f"[yellow]Отказ T-{item['task_id']}[/yellow] → {path.name}")

        analyzed_fix: list[dict] = []
        for item in fix_items:
            review = self._analyze_item(item, kind="fix")
            analyzed_fix.append(review)
            path = self.journal.save_review(item["task_id"], review)
            console.print(f"[cyan]Доработка T-{item['task_id']}[/cyan] → {path.name}")

        return analyzed_fail, analyzed_fix

    def _analyze_item(self, item: dict[str, Any], *, kind: str) -> dict[str, Any]:
        task_id = item["task_id"]
        execution = self.journal.load_execution(task_id)

        task_page, detail = self.seosprint.read_task_page(task_id)
        try:
            tz_description = detail.description if detail else ""
            tz_report_req = detail.report_requirements if detail else ""
        finally:
            self.seosprint.close_page(task_page, reason=f"review T-{task_id}")

        prompt = self._build_prompt(
            item,
            kind=kind,
            execution=execution,
            tz_description=tz_description,
            tz_report_req=tz_report_req,
        )

        try:
            ai_data = self.client.chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "Ты аналитик SEOsprint. Сравни ТЗ, выполненную работу и замечание модератора. "
                            "Дай конкретные рекомендации для исправления отчёта/выполнения."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as exc:
            ai_data = {
                "summary": f"AI недоступен: {exc}",
                "issues": [],
                "recommendations": [],
                "report_template": item.get("submitted_report", ""),
            }

        return {
            "task_id": task_id,
            "title": item.get("title") or (detail.title if detail else ""),
            "kind": kind,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "submitted_report": item.get("submitted_report", ""),
            "rejection_reason": item.get("rejection_reason", ""),
            "fix_required": item.get("fix_required", ""),
            "execution_journal": execution,
            "tz_description": tz_description,
            "tz_report_requirements": tz_report_req,
            "ai_summary": str(ai_data.get("summary", "")),
            "ai_issues": ai_data.get("issues", []),
            "ai_recommendations": ai_data.get("recommendations", []),
            "ai_report_template": str(ai_data.get("report_template", "")),
            "apply_instructions": str(ai_data.get("apply_instructions", ai_data.get("recommendations", ""))),
        }

    def _build_prompt(
        self,
        item: dict[str, Any],
        *,
        kind: str,
        execution: dict[str, Any] | None,
        tz_description: str,
        tz_report_req: str,
    ) -> str:
        if kind == "fail":
            moderator = item.get("rejection_reason", "")
            mod_label = "Причина отказа"
        else:
            moderator = item.get("fix_required", "")
            mod_label = "Что нужно исправить"

        exec_block = "Журнал выполнения не найден."
        if execution:
            exec_block = (
                f"Действия: {execution.get('actions', [])}\n"
                f"Отчёт: {execution.get('report_text', '')}\n"
                f"Скриншоты: {execution.get('screenshot_paths', [])}\n"
                f"Payload: {execution.get('execution_payload', {})}"
            )

        return f"""Задание T-{item['task_id']}: {item.get('title', '')}

ТЗ (описание):
{tz_description}

Требования к отчёту:
{tz_report_req}

Отправленный отчёт исполнителя:
{item.get('submitted_report', '')}

{mod_label}:
{moderator}

{exec_block}

JSON:
{{
  "summary": "краткий вывод",
  "issues": ["проблема 1", "проблема 2"],
  "recommendations": ["рекомендация 1"],
  "report_template": "готовый улучшенный текст отчёта",
  "apply_instructions": "что сделать при повторном выполнении"
}}"""

    def build_fix_task_overrides(self, fix_reviews: list[dict]) -> dict[str, str]:
        """task_id → инструкции для повторного выполнения."""
        overrides: dict[str, str] = {}
        for review in fix_reviews:
            parts = []
            if review.get("fix_required"):
                parts.append(f"Исправить: {review['fix_required']}")
            if review.get("ai_recommendations"):
                recs = review["ai_recommendations"]
                if isinstance(recs, list):
                    parts.extend(str(r) for r in recs)
                else:
                    parts.append(str(recs))
            if review.get("apply_instructions"):
                parts.append(str(review["apply_instructions"]))
            if review.get("ai_report_template"):
                parts.append(f"Шаблон отчёта: {review['ai_report_template']}")
            overrides[review["task_id"]] = "\n".join(parts)
        return overrides
