#!/usr/bin/env python3
"""CLI для Auto SEOsprint."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config import Settings
from src.pipeline import SeosprintPipeline
from src.runner import TaskRunner
from src.seosprint.browser import SeosprintBrowser
from src.seosprint.client import SeosprintClient
from src.seosprint.sources import TaskSourceKind, resolve_task_source
from src.task_source import load_tasks_from_source

app = typer.Typer(help="AI-ассистент для заданий SEOsprint (Gemini 2.5 Flash / OpenRouter)")
console = Console()


@app.command("login")
def login():
    """Войти в SEOsprint через браузер (cookies сохраняются)."""
    settings = Settings.load(require_openrouter=False)
    with SeosprintBrowser(settings) as browser:
        client = SeosprintClient(browser)
        if client.login_interactive():
            path = browser.save_cookies()
            console.print(f"[green]Вход выполнен. Cookies: {path}[/green]")
        else:
            console.print("[red]Не удалось подтвердить вход[/red]")
            raise typer.Exit(1)


@app.command("login-sessions")
def login_sessions():
    """Войти в YouTube, VK, Telegram и др. в том же браузерном профиле."""
    settings = Settings.load(require_openrouter=False)
    platforms = [
        ("YouTube", "https://www.youtube.com/"),
        ("VK", "https://vk.com/"),
        ("Telegram", "https://web.telegram.org/"),
        ("Facebook", "https://www.facebook.com/"),
        ("Instagram", "https://www.instagram.com/"),
        ("LinkedIn", "https://www.linkedin.com/login"),
    ]

    with SeosprintBrowser(settings) as browser:
        for name, url in platforms:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            console.print(f"  Открыто: {name}")

        console.print(
            "\n[bold]Войдите во все нужные сервисы в открытых вкладках.[/bold]\n"
            "Сессии сохранятся в browser profile.\n"
            "После входа нажмите Enter..."
        )
        input()
        path = browser.save_cookies()
        console.print(f"[green]Сессии сохранены: {path}[/green]")


@app.command("fetch")
def fetch_tasks(
    source: Optional[str] = typer.Argument(None, help="earn, URL SEOsprint или HTML-файл"),
    output: Path = typer.Option(Path("tasks.html"), "--output", "-o"),
):
    """Скачать список заданий с SEOsprint (нужен login)."""
    settings = Settings.load(require_openrouter=False)
    try:
        resolved = resolve_task_source(source, base_url=settings.seosprint_base_url)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with SeosprintBrowser(settings) as browser:
        client = SeosprintClient(browser)
        if not browser.is_logged_in():
            console.print("[red]Сначала выполните: python main.py login[/red]")
            raise typer.Exit(1)

        if resolved.kind == TaskSourceKind.SITE:
            console.print(f"Загрузка: {resolved.site_url}")
            html = client.fetch_tasks_html(source_url=resolved.site_url)
            output.write_text(html, encoding="utf-8")

        tasks = load_tasks_from_source(resolved, client=client)
        console.print(f"Найдено {len(tasks)} заданий → {output}")


@app.command("parse")
def parse_tasks(
    input_file: Path = typer.Argument(..., help="HTML-файл со списком заданий"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="JSON с распарсенными заданиями"),
):
    """Распарсить HTML SEOsprint без вызова AI."""
    from src.parser import parse_task_list_html

    html = input_file.read_text(encoding="utf-8")
    tasks = parse_task_list_html(html)
    console.print(f"Найдено заданий: [green]{len(tasks)}[/green]")

    for task in tasks[:10]:
        console.print(f"  T-{task.task_id} | ${task.price_usd:.4f} | {task.title[:50]}")

    if output:
        import json

        output.write_text(
            json.dumps([t.model_dump() for t in tasks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"Сохранено: {output}")


@app.command("analyze")
def analyze_tasks(
    input_file: Path = typer.Argument(..., help="HTML-файл со списком заданий"),
    save: bool = typer.Option(True, help="Сохранить analysis.json"),
):
    """Проанализировать задания через Gemini (OpenRouter)."""
    settings = Settings.load()
    runner = TaskRunner(settings)

    try:
        tasks = runner.load_tasks_from_file(input_file)
        console.print(f"Загружено заданий: {len(tasks)}")
        console.print(f"Модель: {settings.openrouter_model}")

        analyses = runner.analyze_tasks(tasks)
        runner.print_summary(tasks, analyses)

        if save:
            path = runner.save_analysis(analyses)
            console.print(f"\nАнализ сохранён: {path}")

        auto_count = len(runner.filter_automatable(analyses))
        console.print(
            Panel(
                f"К выполнению (без депозитов/казино): {auto_count} из {len(tasks)}",
                title="Итог",
            )
        )
    finally:
        runner.close()


@app.command("pipeline")
def pipeline(
    source: Optional[str] = typer.Argument(
        None,
        help="earn, https://seosprint.net/earn, tasks.html или пусто (= /earn)",
    ),
    task_id: Optional[list[str]] = typer.Option(None, "--id", help="ID задания"),
    auto_only: bool = typer.Option(
        True,
        "--auto-only/--all",
        help="Пропускать только депозиты/казино (--all = включая их)",
    ),
    no_submit: bool = typer.Option(False, "--no-submit", help="Не отправлять отчёт"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Только сгенерировать отчёт"),
    from_site: bool = typer.Option(False, "--from-site", help="Загрузить с /earn (устарело, можно просто earn)"),
):
    """Полный цикл: разведка → выполнить → взять → сдать отчёт."""
    settings = Settings.load()

    try:
        resolved = resolve_task_source(
            source,
            base_url=settings.seosprint_base_url,
            from_site_flag=from_site,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with SeosprintPipeline(settings) as pipe:
        if not pipe.ensure_logged_in():
            raise typer.Exit(1)

        if resolved.kind == TaskSourceKind.SITE:
            console.print(f"Загрузка заданий: {resolved.site_url}")
            tasks = load_tasks_from_source(resolved, client=pipe.client)
        else:
            console.print(f"Загрузка из файла: {resolved.file_path}")
            tasks = load_tasks_from_source(resolved)

        console.print(f"Заданий: {len(tasks)}")
        analyses = pipe.runner.analyze_tasks(tasks)

        results = pipe.process_batch(
            tasks,
            analyses,
            auto_only=auto_only,
            task_ids=task_id,
            submit=not no_submit,
            dry_run=dry_run,
        )

        table = Table(title="Результаты pipeline")
        table.add_column("ID")
        table.add_column("Взято")
        table.add_column("Выполнено")
        table.add_column("Сдано")
        table.add_column("Сообщение")

        for r in results:
            table.add_row(
                r.task_id,
                "✓" if r.taken else "—",
                "✓" if r.executed else "—",
                "✓" if r.submitted else "—",
                r.message[:60],
            )
        console.print(table)

        ok = sum(1 for r in results if r.submitted)
        console.print(f"\n[bold]Сдано отчётов: {ok}/{len(results)}[/bold]")


@app.command("review")
def review_tasks():
    """Аналитика: отклонённые (fail) и задания на доработку (fix) + рекомендации ИИ."""
    settings = Settings.load()
    with SeosprintPipeline(settings) as pipe:
        if not pipe.ensure_logged_in():
            raise typer.Exit(1)
        fail_reviews, fix_reviews = pipe.run_startup_analytics()
        console.print(
            Panel(
                f"Отклонено: {len(fail_reviews)}\n"
                f"На доработку: {len(fix_reviews)}\n"
                f"Логи: {settings.data_dir / 'analytics' / 'reviews'}",
                title="Аналитика",
            )
        )


@app.command("inwork")
def inwork_tasks():
    """Показать взятые в работу задания и нужные сессии."""
    from src.seosprint.platforms import format_platforms, required_platforms_for_task

    settings = Settings.load(require_openrouter=False)
    with SeosprintBrowser(settings) as browser:
        client = SeosprintClient(browser)
        if not browser.is_logged_in():
            console.print("[red]Сначала: python main.py login[/red]")
            raise typer.Exit(1)

        tasks = client.fetch_inwork_tasks()
        if not tasks:
            console.print("[yellow]Нет заданий «Взяты в работу»[/yellow]")
            return

        table = Table(title="Взяты в работу")
        table.add_column("ID")
        table.add_column("Название")
        table.add_column("Нужные сессии")

        all_platforms: set[str] = set()
        for task in tasks:
            platforms = required_platforms_for_task(task.title, task.description, site=task.site)
            for p in platforms:
                all_platforms.add(p)
            label = format_platforms(platforms) if platforms else "— (браузер / вручную)"
            table.add_row(task.task_id, task.title[:50], label)

        console.print(table)
        checkable = [p for p in all_platforms if p != "google"]
        if checkable:
            console.print(
                f"\n[bold]Для завершения войдите:[/bold] python main.py login-sessions\n"
                f"Нужны: {format_platforms(checkable)}"
            )
        manual = [p for p in all_platforms if p == "google"]
        if manual:
            console.print(
                "[dim]Gmail/Google — выполняется в приложении или через accounts.google.com "
                "в том же браузерном профиле.[/dim]"
            )


@app.command("take")
def take_task(task_id: str = typer.Argument(..., help="ID задания")):
    """Взять задание в работу на SEOsprint."""
    settings = Settings.load(require_openrouter=False)
    with SeosprintBrowser(settings) as browser:
        client = SeosprintClient(browser)
        if not browser.is_logged_in():
            console.print("[red]Сначала: python main.py login[/red]")
            raise typer.Exit(1)

        result = client.take_task(task_id)
        if result.success:
            console.print(f"[green]{result.message}[/green]")
            if result.page:
                console.print("Браузер оставлен открытым — выполните задание и сдайте отчёт вручную.")
                input("Нажмите Enter для закрытия...")
                client.close_page(result.page)
        else:
            console.print(f"[red]{result.message}[/red]")
            raise typer.Exit(1)


@app.command("submit")
def submit_task(
    task_id: str = typer.Argument(..., help="ID задания"),
    report_file: Path = typer.Option(..., "--report", "-r", help="Файл с текстом отчёта"),
    screenshot: Optional[list[Path]] = typer.Option(None, "--screenshot", "-s", help="Скриншоты"),
):
    """Отправить отчёт по уже взятому заданию."""
    settings = Settings.load(require_openrouter=False)
    report_text = report_file.read_text(encoding="utf-8")

    with SeosprintBrowser(settings) as browser:
        client = SeosprintClient(browser)
        if not browser.is_logged_in():
            console.print("[red]Сначала: python main.py login[/red]")
            raise typer.Exit(1)

        page, _ = client.read_task_page(task_id)
        try:
            result = client.submit_report(
                page,
                report_text,
                [str(p) for p in (screenshot or [])],
            )
            if result.success:
                console.print(f"[green]{result.message}[/green]")
            else:
                console.print(f"[red]{result.message}[/red]")
                raise typer.Exit(1)
        finally:
            client.close_page(page)


@app.command("run")
def run_tasks(
    input_file: Path = typer.Argument(..., help="HTML-файл со списком заданий"),
    task_id: Optional[list[str]] = typer.Option(None, "--id", help="ID задания (можно несколько)"),
    auto_only: bool = typer.Option(False, "--auto-only", help="Только полностью автоматизируемые"),
    guide_all: bool = typer.Option(
        False,
        "--guide-all",
        help="Сгенерировать инструкции для всех заданий (включая ручные)",
    ),
):
    """Выполнить задания локально (без SEOsprint) или сгенерировать инструкции."""
    settings = Settings.load()
    runner = TaskRunner(settings)

    try:
        tasks = runner.load_tasks_from_file(input_file)
        analyses = runner.analyze_tasks(tasks)

        only_automatable = auto_only if not guide_all else False

        results = runner.run_batch(
            tasks,
            analyses,
            only_automatable=only_automatable,
            task_ids=task_id,
        )

        ok = sum(1 for r in results if r.success)
        console.print(f"\n[bold]Готово:[/bold] {ok}/{len(results)} успешно")
    finally:
        runner.close()


@app.command("report")
def generate_report(
    task_id: str = typer.Argument(..., help="ID задания"),
    input_file: Path = typer.Option(..., "--from", help="HTML-файл с заданием"),
    notes: str = typer.Option("", help="Ваши данные для отчёта (логин, ссылки и т.д.)"),
):
    """Сгенерировать текст отчёта для SEOsprint."""
    settings = Settings.load()
    runner = TaskRunner(settings)

    try:
        tasks = runner.load_tasks_from_file(input_file)
        task = next((t for t in tasks if t.task_id == task_id), None)
        if not task:
            raise typer.BadParameter(f"Задание {task_id} не найдено")

        prompt = f"""Сформируй готовый текст отчёта для SEOsprint.

Задание T-{task.task_id}:
Название: {task.title}
Описание: {task.description}
Требования к отчёту: {task.report_requirements}

Данные исполнителя:
{notes or 'не указаны'}

Telegram профиля: {settings.executor_telegram}
Email профиля: {settings.executor_email}

Верни только текст отчёта, готовый для копирования в форму SEOsprint.
"""
        report = runner.client.chat(
            [
                {"role": "system", "content": "Ты помощник SEOsprint. Пиши отчёты кратко и по требованиям."},
                {"role": "user", "content": prompt},
            ]
        )
        console.print(Panel(report, title=f"Отчёт T-{task_id}"))

        out = settings.data_dir / "reports" / f"{task_id}_draft.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        console.print(f"Сохранено: {out}")
    finally:
        runner.close()


@app.command("battle-test")
def battle_test(
    offline: bool = typer.Option(False, "--offline", help="Только локальные проверки без SEOsprint"),
):
    """Полная проверка: API, парсер, сессии, SEOsprint."""
    from src.battle_test import print_battle_report, run_battle_test

    settings = Settings.load(require_openrouter=False)
    report = run_battle_test(settings, live=not offline)
    print_battle_report(report)
    if report.failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
