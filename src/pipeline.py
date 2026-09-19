from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from src.analytics.journal import TaskJournal
from src.analytics.reviewer import AnalyticsReviewer
from src.config import Settings
from src.models import AutomationLevel, PipelineResult, SeoTask, TaskAnalysis
from src.parser import merge_tasks
from src.recon import ReconRunner
from src.report_builder import build_execution_payload, generate_report_text
from src.runner import TaskRunner
from src.seosprint.browser import SeosprintBrowser
from src.seosprint.client import SeosprintClient
from src.seosprint.platforms import (
    check_sessions,
    filter_checkable_platforms,
    format_platforms,
    missing_sessions,
    required_platforms_for_task,
)
from src.task_context import TaskTzContext

console = Console()


class SeosprintPipeline:
    """Цикл: разведка → выполнение → взять → сдать отчёт."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser = SeosprintBrowser(settings)
        self.client = SeosprintClient(self.browser)
        self.runner = TaskRunner(settings)
        self.recon = ReconRunner(self.runner.client)
        self.journal = TaskJournal(settings.data_dir)
        self.reviewer = AnalyticsReviewer(
            settings,
            self.runner.client,
            self.client,
            self.journal,
        )
        self._session_cache: dict[str, bool] = {}
        self._fix_overrides: dict[str, str] = {}

    def close(self) -> None:
        self.runner.close()
        self.browser.stop()

    def __enter__(self) -> SeosprintPipeline:
        self.browser.start()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def ensure_logged_in(self) -> bool:
        if self.browser.is_logged_in():
            return True
        console.print("[yellow]Сессия SEOsprint не найдена. Запустите: python main.py login[/yellow]")
        return False

    def _analyze_task(self, task: SeoTask) -> TaskAnalysis:
        return self.runner.analyzer.analyze(task)

    def _load_full_task_spec(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        page,
    ) -> tuple[SeoTask, TaskAnalysis]:
        detail = self.client.parse_task_from_page(page, task.task_id)
        if detail is None:
            console.print("[yellow]![/yellow] Не удалось прочитать ТЗ со страницы задания")
            return task, analysis

        task = merge_tasks(task, detail)
        if not task.description.strip():
            console.print("[yellow]![/yellow] Описание задания пустое")
            return task, analysis

        console.print(
            f"[dim]ТЗ: {len(task.description)} симв., "
            f"отчёт: {len(task.report_requirements)} симв.[/dim]"
        )
        analysis = self._analyze_task(task)
        if analysis.required_actions:
            console.print(f"[dim]Шаги: {len(analysis.required_actions)}[/dim]")
        return task, analysis

    def _collect_required_platforms(self, task: SeoTask, analysis: TaskAnalysis) -> list[str]:
        required = list(analysis.required_platforms)
        for platform in required_platforms_for_task(task.title, task.description, site=task.site):
            if platform not in required:
                required.append(platform)
        return required

    def _obvious_platforms(self, task: SeoTask, analysis: TaskAnalysis) -> list[str]:
        return filter_checkable_platforms(self._collect_required_platforms(task, analysis))

    def warm_session_cache(self, platforms: list[str]) -> None:
        checkable = filter_checkable_platforms(platforms)
        if not checkable:
            return
        self._session_cache = check_sessions(
            self.browser.context,
            checkable,
            cache=self._session_cache,
        )

    def _ensure_sessions(self, platforms: list[str]) -> tuple[bool, str]:
        checkable = filter_checkable_platforms(platforms)
        if not checkable:
            return True, ""

        self.browser._ensure_keeper_page()
        self._session_cache = check_sessions(
            self.browser.context,
            checkable,
            cache=self._session_cache,
        )
        missing = missing_sessions(platforms, self._session_cache)
        if missing:
            names = format_platforms(missing)
            return False, f"Нет сессии для {names}. Войдите: python main.py login-sessions"
        return True, ""

    def _finish_task_context(self, ctx: TaskTzContext | None) -> None:
        self.runner.registry.set_tz_context(None)
        if ctx is not None:
            ctx.clear()

    def run_startup_analytics(self) -> tuple[list[dict], list[dict]]:
        """Анализ отклонённых и заданий на доработку + инструкции для fix."""
        console.print("[dim]Аналитика: проверка earn-task-fail и earn-task-fix...[/dim]")
        fail_reviews, fix_reviews = self.reviewer.run_startup_review()
        self._fix_overrides = self.reviewer.build_fix_task_overrides(fix_reviews)
        if self._fix_overrides:
            console.print(
                f"[cyan]Заданий на доработку: {len(self._fix_overrides)} "
                f"(будут обработаны с учётом правок)[/cyan]"
            )
        return fail_reviews, fix_reviews

    def process_fix_queue(
        self,
        *,
        submit: bool = True,
        dry_run: bool = False,
    ) -> list[PipelineResult]:
        """Повторно выполняет задания из earn-task-fix с учётом рекомендаций ИИ."""
        if not self._fix_overrides:
            return []

        results: list[PipelineResult] = []
        console.print(f"\n[bold cyan]Очередь доработки: {len(self._fix_overrides)} заданий[/bold cyan]")

        for task_id, instructions in self._fix_overrides.items():
            page, detail = self.client.read_task_page(task_id)
            try:
                if detail is None:
                    console.print(f"[yellow]T-{task_id}: не удалось прочитать ТЗ[/yellow]")
                    continue
                task = detail
                analysis = self._analyze_task(task)
            finally:
                self.client.close_page(page, reason=f"fix_queue read T-{task_id}")

            if analysis.automation_level == AutomationLevel.SKIP:
                console.print(f"[dim]Пропуск fix T-{task_id}: {analysis.skip_reason}[/dim]")
                continue

            results.append(
                self.process_task(
                    task,
                    analysis,
                    submit=submit,
                    dry_run=dry_run,
                    fix_instructions=instructions,
                )
            )
        return results

    def process_task(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        submit: bool = True,
        dry_run: bool = False,
        fix_instructions: str = "",
    ) -> PipelineResult:
        console.print(f"\n[bold cyan]T-{task.task_id}[/bold cyan] {task.title}")

        if analysis.automation_level.value == "skip":
            return PipelineResult(
                task_id=task.task_id,
                taken=False,
                executed=False,
                submitted=False,
                message=analysis.skip_reason or "Задание пропущено",
            )

        fix_instructions = fix_instructions or self._fix_overrides.get(task.task_id, "")

        if dry_run:
            report = self._generate_report(task, analysis, execution_data={}, fix_instructions=fix_instructions)
            return PipelineResult(
                task_id=task.task_id,
                taken=False,
                executed=False,
                submitted=False,
                message="Dry-run: отчёт сгенерирован, но не отправлен",
                report_text=report,
            )

        obvious = self._obvious_platforms(task, analysis)
        if obvious:
            ok, msg = self._ensure_sessions(obvious)
            if not ok:
                console.print(f"[yellow]⊘ Пропуск — {msg}[/yellow]")
                return PipelineResult(
                    task_id=task.task_id,
                    taken=False,
                    executed=False,
                    submitted=False,
                    message=msg,
                )

        ctx: TaskTzContext | None = None
        recon_page = None
        exec_page = None
        take_page = None
        taken = False
        executed = False
        submitted = False
        report_text = ""
        message = ""
        screenshot_paths: list[str] = []
        exec_collected: dict = {}
        payload: dict = {}
        recon_steps: list[str] = []

        try:
            self.browser._ensure_keeper_page()
            self.client.log.info("=== TASK T-%s START ===", task.task_id)
            console.print("[dim]Разведка: чтение ТЗ без взятия...[/dim]")
            recon_page, detail = self.client.read_task_page(task.task_id)
            if detail is not None:
                task = merge_tasks(task, detail)
            task, analysis = self._load_full_task_spec(task, analysis, recon_page)

            ctx = TaskTzContext(self.runner.client, task, analysis)
            if fix_instructions:
                ctx.log_action("Доработка", fix_instructions[:500])
                console.print(f"[cyan]Правки модератора:[/cyan] {fix_instructions[:200]}...")

            recon_result = self.recon.run(ctx, obvious_platforms=obvious)
            analysis = ReconRunner.apply_to_analysis(analysis, recon_result)
            ctx.bind(task, analysis, steps=recon_result.steps)
            recon_steps = list(recon_result.steps)

            console.print(f"[dim]Разведка: {recon_result.summary or '—'}[/dim]")
            if recon_result.steps:
                console.print(f"[dim]План: {len(recon_result.steps)} шагов[/dim]")

            if not recon_result.proceed:
                message = recon_result.reason or "Разведка: задание отклонено"
                console.print(f"[yellow]⊘ {message}[/yellow]")
                return PipelineResult(
                    task_id=task.task_id,
                    taken=False,
                    executed=False,
                    submitted=False,
                    message=message,
                )

            recon_platforms = self._collect_required_platforms(task, analysis)
            sessions_ok, session_message = self._ensure_sessions(recon_platforms)
            if not sessions_ok:
                console.print(f"[yellow]⊘ Пропуск — {session_message}[/yellow]")
                return PipelineResult(
                    task_id=task.task_id,
                    taken=False,
                    executed=False,
                    submitted=False,
                    message=session_message,
                )

            already_in_work = self.client._report_form_visible(recon_page)
            if already_in_work:
                console.print("[dim]Задание уже в работе — выполнение и сдача[/dim]")
                taken = True
            else:
                console.print("[dim]Выполнение до взятия задания...[/dim]")

            exec_page = self.browser.new_page()
            self.runner.registry.set_tz_context(ctx)

            console.print(
                f"[dim]Тип: {analysis.category.value}, "
                f"платформы: {', '.join(analysis.required_platforms) or '—'}[/dim]"
            )

            try:
                exec_result = self.runner.registry.run(task, analysis, page=exec_page)
            except Exception as exc:
                message = f"Ошибка выполнения: {exc}"
                console.print(f"[red]✗[/red] {message}")
                return PipelineResult(
                    task_id=task.task_id,
                    taken=taken,
                    executed=False,
                    submitted=False,
                    message=message,
                )

            console.print(f"[dim]Исполнитель: {exec_result.mode}[/dim]")
            executed = exec_result.success
            screenshot_paths = list(exec_result.screenshot_paths)
            exec_collected = dict(exec_result.collected_data or {})
            payload = build_execution_payload(
                self.settings,
                task,
                analysis,
                collected_data=exec_collected,
                screenshot_paths=screenshot_paths,
                action_log=ctx.action_log if ctx else [],
                session_cache=self._session_cache,
            )
            report_text = self._generate_report(
                task,
                analysis,
                exec_collected,
                screenshot_paths=screenshot_paths,
                action_log=ctx.action_log if ctx else [],
                payload=payload,
                fix_instructions=fix_instructions,
                tz_context=ctx,
            )

            if not exec_result.success:
                message = f"Выполнение: {exec_result.message}"
                console.print(f"[yellow]![/yellow] {message}")
                return PipelineResult(
                    task_id=task.task_id,
                    taken=taken,
                    executed=False,
                    submitted=False,
                    message=message,
                    report_text=report_text,
                    screenshot_paths=screenshot_paths,
                )

            console.print(f"[green]✓[/green] Выполнено: {exec_result.message}")

            if not already_in_work:
                self.client.close_page(recon_page)
                recon_page = None
                self.client.close_page(exec_page)
                exec_page = None

                take_result = self.client.take_task(task.task_id)
                if not take_result.success:
                    message = take_result.message
                    console.print(f"[red]✗[/red] Не взято: {message}")
                    return PipelineResult(
                        task_id=task.task_id,
                        taken=False,
                        executed=True,
                        submitted=False,
                        message=message,
                        report_text=report_text,
                        screenshot_paths=screenshot_paths,
                    )

                taken = True
                take_page = take_result.task_page or take_result.page
                console.print(f"[green]✓[/green] Взято: {take_result.message}")
            else:
                take_page = recon_page
                recon_page = None
                self.client.close_page(exec_page)
                exec_page = None

            if submit and report_text and taken and take_page is not None:
                submit_page = self.client.prepare_report_page(
                    take_page.context,
                    task.task_id,
                    fallback=take_page,
                )
                submit_result = self.client.submit_report(
                    submit_page,
                    report_text,
                    screenshot_paths,
                )
                submitted = submit_result.success
                message = submit_result.message
                if submitted:
                    console.print(f"[green]✓[/green] Сдано: {submit_result.message}")
                else:
                    console.print(f"[red]✗[/red] Не сдано: {submit_result.message}")

            if report_text:
                self._show_report_to_user(
                    task,
                    report_text,
                    screenshot_paths,
                    submitted=submitted,
                )

            self.journal.save_execution(
                task,
                analysis,
                actions=ctx.action_log if ctx else [],
                recon_steps=recon_steps,
                collected_data=exec_collected,
                report_text=report_text,
                screenshot_paths=screenshot_paths,
                submitted=submitted,
                payload=payload,
                message=message,
            )

            self.client.log.info(
                "=== TASK T-%s END | taken=%s executed=%s submitted=%s ===",
                task.task_id,
                taken,
                executed,
                submitted,
            )
            return PipelineResult(
                task_id=task.task_id,
                taken=taken,
                executed=executed,
                submitted=submitted,
                message=message,
                report_text=report_text,
                screenshot_paths=screenshot_paths,
            )
        except Exception as exc:
            self.client.log.exception("=== TASK T-%s CRASH: %s ===", task.task_id, exc)
            console.print(f"[red]✗[/red] Ошибка T-{task.task_id}: {exc}")
            return PipelineResult(
                task_id=task.task_id,
                taken=taken,
                executed=executed,
                submitted=submitted,
                message=str(exc),
                report_text=report_text,
                screenshot_paths=screenshot_paths,
            )
        finally:
            self._finish_task_context(ctx)
            for p in (exec_page, recon_page, take_page):
                if p is not None:
                    self.client.close_page(p, reason=f"finally T-{task.task_id}")
            self.browser._ensure_keeper_page()

    def process_batch(
        self,
        tasks: list[SeoTask],
        analyses: list[TaskAnalysis],
        *,
        auto_only: bool = True,
        task_ids: list[str] | None = None,
        submit: bool = True,
        dry_run: bool = False,
    ) -> list[PipelineResult]:
        log_dir = self.settings.data_dir / "logs"
        log_file = log_dir / f"browser_{datetime.now():%Y%m%d}.log"
        console.print(f"[dim]Лог браузера: {log_file.resolve()}[/dim]")

        analysis_map = {a.task_id: a for a in analyses}
        results: list[PipelineResult] = []

        if not dry_run:
            self.run_startup_analytics()

        if not dry_run:
            obvious_platforms: set[str] = set()
            for task in tasks:
                if task_ids and task.task_id not in task_ids:
                    continue
                analysis = analysis_map.get(task.task_id)
                if not analysis:
                    continue
                if auto_only and analysis.automation_level == AutomationLevel.SKIP:
                    continue
                obvious_platforms.update(self._obvious_platforms(task, analysis))

            if obvious_platforms:
                console.print("[dim]Проверка очевидных сессий (по списку заданий)...[/dim]")
                self.browser._ensure_keeper_page()
                self.warm_session_cache(list(obvious_platforms))

        for task in tasks:
            if task_ids and task.task_id not in task_ids:
                continue

            analysis = analysis_map[task.task_id]
            if auto_only and analysis.automation_level == AutomationLevel.SKIP:
                console.print(f"[dim]Пропуск T-{task.task_id} — {analysis.skip_reason or 'депозит/казино'}[/dim]")
                continue

            results.append(
                self.process_task(task, analysis, submit=submit, dry_run=dry_run)
            )

        if not dry_run and self._fix_overrides:
            fix_results = self.process_fix_queue(submit=submit, dry_run=dry_run)
            processed_ids = {r.task_id for r in results}
            for fix_result in fix_results:
                if fix_result.task_id not in processed_ids:
                    results.append(fix_result)

        return results

    def _show_report_to_user(
        self,
        task: SeoTask,
        report_text: str,
        screenshot_paths: list[str],
        *,
        submitted: bool,
    ) -> Path:
        reports_dir = self.settings.data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        lines = [report_text.strip()]
        if screenshot_paths:
            lines.append("")
            lines.append("Скриншоты:")
            for shot in screenshot_paths:
                lines.append(str(Path(shot).resolve()))

        body = "\n".join(lines)
        out_file = reports_dir / f"{task.task_id}.txt"
        out_file.write_text(body, encoding="utf-8")

        status = "сдан" if submitted else "не сдан"
        console.print(Panel(body, title=f"Отчёт T-{task.task_id} ({status})"))
        console.print(f"[dim]Файл отчёта: {out_file.resolve()}[/dim]")
        for shot in screenshot_paths:
            console.print(f"[dim]Скрин: {Path(shot).resolve()}[/dim]")

        return out_file

    def _generate_report(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        execution_data: dict,
        *,
        screenshot_paths: list[str] | None = None,
        action_log: list[str] | None = None,
        payload: dict | None = None,
        fix_instructions: str = "",
        tz_context: TaskTzContext | None = None,
    ) -> str:
        full_payload = payload or build_execution_payload(
            self.settings,
            task,
            analysis,
            collected_data=execution_data,
            screenshot_paths=screenshot_paths,
            action_log=action_log,
            session_cache=self._session_cache,
        )
        system = tz_context.system_prompt() if tz_context else ""
        return generate_report_text(
            self.runner.client,
            self.settings,
            task,
            analysis,
            full_payload,
            system_prompt=system,
            fix_instructions=fix_instructions,
        )
