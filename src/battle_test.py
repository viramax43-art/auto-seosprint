from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.analyzer import TaskAnalyzer
from src.config import Settings
from src.openrouter import OpenRouterClient
from src.parser import extract_urls_from_text, parse_task_detail_html, parse_task_list_html
from src.seosprint.browser import SeosprintBrowser
from src.seosprint.client import SeosprintClient
from src.seosprint.platforms import check_sessions, filter_checkable_platforms

console = Console()


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class BattleReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, ok, detail))

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok)


def run_battle_test(settings: Settings, *, live: bool = True) -> BattleReport:
    report = BattleReport()

    report.add("OPENROUTER_API_KEY", bool(settings.openrouter_api_key), "ключ задан" if settings.openrouter_api_key else "нет ключа")
    report.add("cookies.json", settings.seosprint_cookies_file.exists(), str(settings.seosprint_cookies_file))
    report.add("browser_profile", settings.browser_profile_dir.exists(), str(settings.browser_profile_dir))

    debug_dir = settings.data_dir / "debug"
    html_files = sorted(debug_dir.glob("*_after_start.html"))
    parsed_ok = 0
    for path in html_files:
        task_id = path.name.split("_")[0]
        task = parse_task_detail_html(path.read_text(encoding="utf-8"), task_id)
        if task and len(task.description) > 20:
            parsed_ok += 1
    report.add(
        "parser (debug HTML)",
        parsed_ok >= max(1, len(html_files) // 2),
        f"{parsed_ok}/{len(html_files)} ТЗ распознано",
    )

    try:
        client = OpenRouterClient(settings)
        reply = client.chat(
            [
                {"role": "system", "content": "Ответь одним словом: OK"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
        )
        client.close()
        report.add("OpenRouter API", "ok" in reply.lower(), reply.strip()[:40])
    except Exception as exc:
        report.add("OpenRouter API", False, str(exc)[:120])

    if html_files:
        sample = parse_task_detail_html(html_files[0].read_text(encoding="utf-8"), html_files[0].name.split("_")[0])
        if sample:
            try:
                ai_client = OpenRouterClient(settings)
                analyzer = TaskAnalyzer(ai_client)
                analysis = analyzer.analyze(sample)
                ai_client.close()
                urls = analysis.urls or extract_urls_from_text(f"{sample.description} {sample.report_requirements}")
                report.add(
                    "AI analyzer",
                    bool(analysis.summary) and analysis.can_automate,
                    f"{analysis.category.value}, urls={len(urls)}, actions={len(analysis.required_actions)}",
                )
            except Exception as exc:
                report.add("AI analyzer", False, str(exc)[:120])

    if not live:
        return report

    browser = SeosprintBrowser(settings)
    try:
        browser.start()
        logged = browser.is_logged_in()
        report.add("SEOsprint login", logged, "сессия активна" if logged else "нужен: python main.py login")

        if not logged:
            return report

        seosprint = SeosprintClient(browser)
        inwork = seosprint.fetch_inwork_tasks()
        report.add("fetch inwork", True, f"{len(inwork)} заданий в работе")

        earn_html = seosprint.fetch_tasks_html(source_url=f"{settings.seosprint_base_url}/earn")
        earn_tasks = parse_task_list_html(earn_html)
        empty_desc = sum(1 for t in earn_tasks if len(t.description) < 20)
        report.add(
            "fetch /earn",
            len(earn_tasks) > 0,
            f"{len(earn_tasks)} заданий, пустых описаний: {empty_desc} (норма — читается после take)",
        )

        if inwork:
            task = inwork[0]
            page, detail = seosprint.read_task_page(task.task_id)
            try:
                full = seosprint.parse_task_from_page(page, task.task_id)
                ok = full is not None and len(full.description) > 20
                report.add(
                    "read full TZ (live)",
                    ok,
                    f"T-{task.task_id}: desc={len(full.description) if full else 0} chars",
                )
            finally:
                seosprint.close_page(page)

        platforms = filter_checkable_platforms(["youtube", "telegram", "facebook", "vk"])
        sessions = check_sessions(browser.context, platforms, cache={})
        active = [p for p, v in sessions.items() if v]
        report.add("social sessions", bool(active), ", ".join(active) or "нет — python main.py login-sessions")

    except Exception as exc:
        report.add("browser live", False, str(exc)[:160])
    finally:
        browser.stop()

    return report


def print_battle_report(report: BattleReport) -> None:
    table = Table(title="Battle test — Auto SEOsprint")
    table.add_column("Проверка")
    table.add_column("Статус")
    table.add_column("Детали")

    for check in report.checks:
        table.add_row(check.name, "[green]OK[/green]" if check.ok else "[red]FAIL[/red]", check.detail[:80])

    console.print(table)
    console.print(f"\n[bold]Итого: {report.passed}/{len(report.checks)} OK[/bold]")

    if any(c.name == "SEOsprint login" and not c.ok for c in report.checks):
        console.print("\n[yellow]Сессия SEOsprint истекла. Выполните: python main.py login[/yellow]")
    if any(c.name == "social sessions" and not c.ok for c in report.checks):
        console.print("[yellow]Соцсети: python main.py login-sessions[/yellow]")
