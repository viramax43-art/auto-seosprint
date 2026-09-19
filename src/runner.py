from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from src.analyzer import TaskAnalyzer
from src.config import Settings
from src.executors.registry import ExecutorRegistry
from src.models import ExecutionResult, SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient
from src.parser import parse_task_list_html

if TYPE_CHECKING:
    from playwright.sync_api import Page

console = Console()


class TaskRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenRouterClient(settings)
        self.analyzer = TaskAnalyzer(self.client)
        self.registry = ExecutorRegistry(settings, self.client)

    def close(self) -> None:
        self.client.close()

    def load_tasks_from_html(self, html: str) -> list[SeoTask]:
        return parse_task_list_html(html)

    def load_tasks_from_file(self, path: Path) -> list[SeoTask]:
        return self.load_tasks_from_html(path.read_text(encoding="utf-8"))

    def analyze_tasks(self, tasks: list[SeoTask]) -> list[TaskAnalysis]:
        return self.analyzer.analyze_batch(tasks)

    def filter_automatable(self, analyses: list[TaskAnalysis]) -> list[TaskAnalysis]:
        return [a for a in analyses if a.automation_level.value != "skip"]

    def print_summary(self, tasks: list[SeoTask], analyses: list[TaskAnalysis]) -> None:
        by_id = {t.task_id: t for t in tasks}
        table = Table(title="Задания SEOsprint")
        table.add_column("ID", style="cyan")
        table.add_column("Название", max_width=40)
        table.add_column("Цена", justify="right")
        table.add_column("Категория")
        table.add_column("Авто")
        table.add_column("Сложн.", justify="center")

        for analysis in sorted(analyses, key=lambda a: by_id[a.task_id].price_usd, reverse=True):
            task = by_id[analysis.task_id]
            auto = "✓" if analysis.can_automate else analysis.automation_level.value
            table.add_row(
                task.task_id,
                task.title[:40],
                f"${task.price_usd:.4f}",
                analysis.category.value,
                auto,
                str(analysis.difficulty),
            )
        console.print(table)

    def save_analysis(self, analyses: list[TaskAnalysis]) -> Path:
        out = self.settings.data_dir / "analysis.json"
        payload = [a.model_dump() for a in analyses]
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def run_task(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        page: Page | None = None,
    ) -> ExecutionResult:
        console.print(f"\n[bold]T-{task.task_id}[/bold]: {task.title}")
        console.print(f"Режим: {analysis.automation_level.value} | {analysis.category.value}")
        result = self.registry.run(task, analysis, page=page)

        if result.success:
            console.print(f"[green]OK[/green] {result.message}")
        else:
            console.print(f"[yellow]{result.message}[/yellow]")

        if result.report_text:
            report_path = self.settings.data_dir / "reports" / f"{task.task_id}.txt"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(result.report_text, encoding="utf-8")
            console.print(f"Отчёт: {report_path}")

        return result

    def run_batch(
        self,
        tasks: list[SeoTask],
        analyses: list[TaskAnalysis],
        *,
        only_automatable: bool = False,
        task_ids: list[str] | None = None,
        page: Page | None = None,
    ) -> list[ExecutionResult]:
        analysis_map = {a.task_id: a for a in analyses}
        results: list[ExecutionResult] = []

        for task in tasks:
            if task_ids and task.task_id not in task_ids:
                continue

            analysis = analysis_map[task.task_id]
            if only_automatable and not analysis.can_automate:
                continue

            results.append(self.run_task(task, analysis, page=page))

        return results
