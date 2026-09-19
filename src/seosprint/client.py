from __future__ import annotations

import re
import time
from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from pathlib import Path

from src.parser import (
    parse_paginated_earn_base,
    parse_review_tasks_html,
    parse_task_detail_html,
    parse_task_list_html,
)
from src.seosprint.browser import SeosprintBrowser
from src.seosprint.browser_log import (
    log_click,
    log_click_result,
    log_context_state,
    log_form_action,
    log_goto,
)
from src.seosprint.selectors import (
    FILE_INPUT_SELECTORS,
    REPORT_SELECTORS,
    START_PATTERNS,
    START_SELECTORS,
    SUBMIT_PATTERNS,
    SUBMIT_SELECTORS,
)
from src.models import SeoTask


@dataclass
class TakeTaskResult:
    success: bool
    message: str
    page: Page | None = None
    task_page: Page | None = None


@dataclass
class SubmitReportResult:
    success: bool
    message: str


class SeosprintClient:
    def __init__(self, browser: SeosprintBrowser) -> None:
        self.browser = browser
        self.settings = browser.settings
        self.base_url = self.settings.seosprint_base_url.rstrip("/")
        self.log = browser.logger

    def login_interactive(self) -> bool:
        """Открывает SEOsprint для ручного входа. Cookies сохраняются автоматически."""
        page = self.browser.new_page()
        page.goto(f"{self.base_url}/login", wait_until="domcontentloaded", timeout=60000)
        print("Войдите в SEOsprint в открывшемся браузере.")
        print("После входа нажмите Enter в консоли...")
        input()
        logged = self._page_looks_logged_in(page)
        if logged:
            self.browser.save_cookies()
        self.close_page(page, reason="login_interactive")
        return logged

    def _goto(self, page: Page, url: str, **kwargs) -> None:
        log_goto(page, url, self.log, **kwargs)

    def fetch_tasks_html(
        self,
        *,
        source_url: str | None = None,
        work_mode: str = "fast",
    ) -> str:
        page = self.browser.new_page()
        try:
            if source_url:
                urls = [source_url]
            else:
                urls = [
                    f"{self.base_url}/earn?work_mode={work_mode}",
                    f"{self.base_url}/earn",
                    f"{self.base_url}/tasks?work_mode={work_mode}",
                    f"{self.base_url}/tasks",
                ]

            last_html = ""
            for url in urls:
                self._goto(page, url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                last_html = page.content()
                if "adv-line" in last_html or "task-list" in last_html or "taskline_" in last_html:
                    return last_html

            return last_html
        finally:
            self.close_page(page)

    def fetch_tasks(
        self,
        *,
        source_url: str | None = None,
        work_mode: str = "fast",
    ) -> list[SeoTask]:
        paginated = parse_paginated_earn_base(source_url) if source_url else None
        if paginated:
            return self.fetch_tasks_paginated(source_url=source_url)

        html = self.fetch_tasks_html(source_url=source_url, work_mode=work_mode)
        tasks = parse_task_list_html(html)
        if not tasks and source_url:
            raise RuntimeError(
                f"На странице {source_url} задания не найдены. "
                "Проверьте вход (python main.py login) и что страница открывается в браузере."
            )
        return tasks

    def fetch_tasks_paginated(
        self,
        *,
        source_url: str,
        max_pages: int = 50,
    ) -> list[SeoTask]:
        """Обходит /earn-task/N, /earn-task/N+1, … пока на странице есть новые задания."""
        parsed = parse_paginated_earn_base(source_url)
        if not parsed:
            return self.fetch_tasks(source_url=source_url)

        base, start_page = parsed
        all_tasks: list[SeoTask] = []
        seen_ids: set[str] = set()
        empty_streak = 0

        for page_num in range(start_page, start_page + max_pages):
            url = f"{base}/{page_num}"
            self.log.info("FETCH TASKS page=%d url=%s", page_num, url)
            html = self.fetch_tasks_html(source_url=url)
            tasks = parse_task_list_html(html)
            new_tasks = [t for t in tasks if t.task_id not in seen_ids]

            if not new_tasks:
                empty_streak += 1
                if empty_streak >= 1:
                    self.log.info("FETCH TASKS stop at page=%d (empty)", page_num)
                    break
                continue

            empty_streak = 0
            for task in new_tasks:
                seen_ids.add(task.task_id)
                all_tasks.append(task)
            self.log.info("FETCH TASKS page=%d | +%d | total=%d", page_num, len(new_tasks), len(all_tasks))

        if not all_tasks:
            raise RuntimeError(
                f"На страницах {base}/{start_page}+ задания не найдены. "
                "Проверьте вход (python main.py login)."
            )
        return all_tasks

    def fetch_review_tasks(self, kind: str) -> list[dict]:
        """kind: 'fail' | 'fix' → earn-task-fail / earn-task-fix."""
        slug = "earn-task-fail" if kind == "fail" else "earn-task-fix"
        url = f"{self.base_url}/{slug}"
        html = self.fetch_tasks_html(source_url=url)
        return parse_review_tasks_html(html, kind=kind)

    def fetch_inwork_tasks(self) -> list[SeoTask]:
        url = f"{self.base_url}/earn-task-inwork"
        html = self.fetch_tasks_html(source_url=url)
        return parse_task_list_html(html)

    def read_task_page(self, task_id: str) -> tuple[Page, SeoTask | None]:
        self.log.info("READ TASK T-%s | opening /read-task/%s", task_id, task_id)
        log_context_state(self.log, self.browser.context, action=f"before read_task T-{task_id}")
        page = self.browser.new_page()
        self._goto(
            page,
            f"{self.base_url}/read-task/{task_id}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(1500)
        detail = self.parse_task_from_page(page, task_id)
        self.log.info(
            "READ TASK T-%s | url=%s | desc_len=%d",
            task_id,
            page.url[:200],
            len(detail.description) if detail else 0,
        )
        return page, detail

    def parse_task_from_page(self, page: Page, task_id: str) -> SeoTask | None:
        task = parse_task_detail_html(page.content(), task_id)
        if task is not None:
            return task
        tasks = parse_task_list_html(page.content())
        task = next((t for t in tasks if t.task_id == task_id), None)
        if task is not None:
            return task
        return self._parse_single_task_page(page, task_id)

    def take_task(self, task_id: str) -> TakeTaskResult:
        self.log.info("TAKE TASK T-%s | start", task_id)
        task_page, _ = self.read_task_page(task_id)

        if self._report_form_visible(task_page):
            self.log.info("TAKE TASK T-%s | already in work (report form visible)", task_id)
            return TakeTaskResult(
                True,
                "Задание уже в работе — форма отчёта открыта",
                page=task_page,
                task_page=task_page,
            )

        if self._has_start_form(task_page):
            clicked = self._click_start(task_page, task_id)
            if not clicked:
                self.log.error("TAKE TASK T-%s | start button not clicked", task_id)
                self.close_page(task_page)
                return TakeTaskResult(False, "Не удалось нажать «Начать выполнение»", task_page=None)

            if self._wait_for_report_form(task_page, timeout_ms=25000):
                self.log.info("TAKE TASK T-%s | success, report form visible", task_id)
                return TakeTaskResult(True, "Задание взято в работу", page=task_page, task_page=task_page)

            self.log.warning("TAKE TASK T-%s | report form not visible, reloading", task_id)
            self._goto(
                task_page,
                f"{self.base_url}/read-task/{task_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            task_page.wait_for_timeout(2500)
            if self._report_form_visible(task_page):
                self.log.info("TAKE TASK T-%s | success after reload", task_id)
                return TakeTaskResult(True, "Задание в работе (после перезагрузки)", page=task_page, task_page=task_page)

            self._save_debug_html(task_page, task_id, "after_start")
            self.close_page(task_page)
            return TakeTaskResult(
                False,
                "Форма отчёта не появилась после «Начать выполнение»",
                task_page=None,
            )

        if self._report_form_visible(task_page):
            return TakeTaskResult(True, "Задание уже в работе", page=task_page, task_page=task_page)

        self.log.error("TAKE TASK T-%s | no start form found", task_id)
        self._save_debug_html(task_page, task_id, "no_start_form")
        self.close_page(task_page)
        return TakeTaskResult(False, "Кнопка «Начать выполнение» не найдена", task_page=None)

    def submit_report(
        self,
        page: Page,
        report_text: str,
        screenshot_paths: list[str] | None = None,
    ) -> SubmitReportResult:
        screenshot_paths = screenshot_paths or []
        task_id = self._task_id_from_url(page.url)

        try:
            self.log.info(
                "SUBMIT T-%s | start | report_len=%d screenshots=%d url=%s",
                task_id,
                len(report_text),
                len(screenshot_paths),
                page.url[:200],
            )
            page.bring_to_front()
            page.wait_for_timeout(500)

            if not self._report_form_visible(page):
                if not self._wait_for_report_form(page, timeout_ms=8000):
                    self.log.error("SUBMIT T-%s | report form not found", task_id)
                    return SubmitReportResult(False, "Форма отчёта не найдена")

            if not self._fill_report_text(page, report_text):
                self.log.error("SUBMIT T-%s | fill report failed", task_id)
                return SubmitReportResult(False, "Не удалось заполнить поле отчёта")

            uploaded = self._upload_screenshots(page, screenshot_paths)
            self.log.info("SUBMIT T-%s | uploaded %d screenshots", task_id, uploaded)

            if not self._click_submit(page):
                self.log.error("SUBMIT T-%s | submit button not found", task_id)
                return SubmitReportResult(False, "Кнопка отправки отчёта не найдена")

            result = self._confirm_submit_result(page)
            self.log.info("SUBMIT T-%s | result success=%s msg=%s", task_id, result.success, result.message)
            return result
        except PlaywrightTimeout as exc:
            return SubmitReportResult(False, f"Таймаут: {exc}")
        except Exception as exc:
            return SubmitReportResult(False, str(exc))

    def find_task_page(self, context_pages, task_id: str) -> Page | None:
        for page in context_pages:
            if page.is_closed():
                continue
            if f"/read-task/{task_id}" in page.url:
                return page
        return None

    def close_extra_pages(self, context, *, keep_task_id: str) -> None:
        """Закрывает рабочие вкладки (about:blank и пр.), оставляет страницу задания и keeper."""
        keep = self.find_task_page(context.pages, keep_task_id)
        self.log.info(
            "CLOSE EXTRA T-%s | keep=%s pages_before=%d",
            keep_task_id,
            keep.url[:120] if keep else None,
            len(context.pages),
        )
        for page in list(context.pages):
            if page.is_closed() or page is keep:
                continue
            if self.browser.is_keeper_page(page):
                continue
            url = page.url or ""
            if keep is not None or url.startswith("about:"):
                self.close_page(page, reason=f"close_extra T-{keep_task_id}")
        log_context_state(self.log, context, action=f"after close_extra T-{keep_task_id}")
        time.sleep(0.2)

    def prepare_report_page(self, context, task_id: str, fallback: Page | None = None) -> Page:
        """Возвращает вкладку с формой отчёта, закрывая лишние about:blank."""
        self.close_extra_pages(context, keep_task_id=task_id)

        page = self.find_task_page(context.pages, task_id)
        if page is None and fallback is not None and not fallback.is_closed():
            if f"/read-task/{task_id}" in fallback.url and "about:blank" not in fallback.url:
                page = fallback

        if page is None or page.is_closed() or "about:blank" in page.url:
            page = self.browser.new_page()
            self._goto(
                page,
                f"{self.base_url}/read-task/{task_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1500)

        page.bring_to_front()
        if not self._report_form_visible(page):
            self._goto(
                page,
                f"{self.base_url}/read-task/{task_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1500)
            self._wait_for_report_form(page, timeout_ms=10000)

        self.log.info("PREPARE REPORT T-%s | url=%s", task_id, page.url[:200])
        return page

    def close_page(self, page: Page | None, *, reason: str = "") -> None:
        self.browser.safe_close_page(page, reason=reason or "client.close_page")

    def _task_id_from_url(self, url: str) -> str:
        match = re.search(r"/read-task/(\d+)", url or "")
        return match.group(1) if match else "?"

    def _parse_single_task_page(self, page: Page, task_id: str) -> SeoTask | None:
        title = ""
        title_el = page.locator(".form-title b, .earn-title, h1").first
        if title_el.count():
            title = title_el.inner_text(timeout=3000).strip()

        body = page.locator("body").inner_text(timeout=5000)
        desc, report = body, ""
        if "Что нужно указать в отчёте" in body:
            desc, report = body.split("Что нужно указать в отчёте", 1)
        elif "Что указать в отчёте" in body:
            desc, report = body.split("Что указать в отчёте", 1)

        return SeoTask(
            task_id=task_id,
            title=title or f"Task {task_id}",
            description=desc[:3000],
            report_requirements=report[:2000],
            url=f"{self.base_url}/read-task/{task_id}",
        )

    def _has_start_form(self, page: Page) -> bool:
        return page.locator("#task-use").count() > 0

    def _click_start(self, page: Page, task_id: str) -> bool:
        """Нажимает «Начать выполнение» — formSubmit('#task-use') или клик по .btn-ok."""
        try:
            page.wait_for_selector("#task-use, .btn-dock a.btn-ok", timeout=10000)
            page.wait_for_function(
                "() => typeof formSubmit === 'function' || document.querySelector('.btn-dock a.btn-ok')",
                timeout=10000,
            )
        except PlaywrightTimeout:
            self.log.warning("TAKE TASK T-%s | start controls wait timeout", task_id)

        btn = page.locator(".btn-dock a.btn-ok").filter(has_text=re.compile(r"начать", re.I))
        if btn.count():
            text = btn.first.inner_text()
            href = btn.first.get_attribute("href") or ""
            onclick = btn.first.get_attribute("onclick") or ""
            log_click(
                page,
                self.log,
                description=f"Начать выполнение T-{task_id}",
                selector=".btn-dock a.btn-ok",
                text=text,
                href=href,
                onclick=onclick,
            )
            btn.first.click(no_wait_after=True)
            page.wait_for_timeout(2500)
            log_click_result(page, self.log, description=f"Начать выполнение T-{task_id}")
            return True

        if page.locator("#task-use").count():
            log_form_action(page, self.log, action="formSubmit", detail="#task-use")
            submitted = page.evaluate(
                """() => {
                    if (typeof formSubmit === 'function') {
                        formSubmit('#task-use');
                        return true;
                    }
                    return false;
                }"""
            )
            if submitted:
                page.wait_for_timeout(2500)
                log_click_result(page, self.log, description=f"formSubmit T-{task_id}")
                return True

        for selector in START_SELECTORS:
            loc = page.locator(selector)
            for i in range(loc.count()):
                text = loc.nth(i).inner_text()
                onclick = loc.nth(i).get_attribute("onclick") or ""
                if re.search(r"начать|start", text, re.I) or "formSubmit" in onclick:
                    log_click(
                        page,
                        self.log,
                        description=f"Start fallback T-{task_id}",
                        selector=selector,
                        text=text,
                        onclick=onclick,
                    )
                    loc.nth(i).click(no_wait_after=True)
                    page.wait_for_timeout(2500)
                    log_click_result(page, self.log, description=f"Start fallback T-{task_id}")
                    return True

        self.log.error("TAKE TASK T-%s | no start control matched", task_id)
        return False

    def _save_debug_html(self, page: Page, task_id: str, tag: str) -> None:
        debug_dir = self.settings.data_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{task_id}_{tag}.html"
        path.write_text(page.content(), encoding="utf-8")

    def _confirm_submit_result(self, page: Page) -> SubmitReportResult:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            page.wait_for_timeout(3000)

        for _ in range(8):
            try:
                content = page.content().lower()
            except Exception:
                page.wait_for_timeout(1000)
                continue

            if any(x in content for x in ("отчёт отправлен", "отчет отправлен", "на проверке", "ожидает проверки")):
                return SubmitReportResult(True, "Отчёт отправлен")

            if "ошиб" in content or "error" in content:
                return SubmitReportResult(False, "SEOsprint вернул ошибку при отправке")

            page.wait_for_timeout(1000)

        return SubmitReportResult(True, "Отчёт отправлен (страница обновилась)")

    def _fill_report_text(self, page: Page, report_text: str) -> bool:
        page.wait_for_timeout(800)
        return bool(
            page.evaluate(
                """(text) => {
                    const html = text.replace(/\\n/g, '<br>');
                    if (window.tinymce) {
                        const editor = tinymce.get('task_report') || tinymce.activeEditor;
                        if (editor) {
                            editor.setContent(html);
                            editor.save();
                            return true;
                        }
                    }
                    const textarea = document.querySelector(
                        '#task_report, textarea[name="task_report"], textarea[name="report"], textarea'
                    );
                    if (textarea) {
                        textarea.value = text;
                        textarea.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                    }
                    return false;
                }""",
                report_text,
            )
        )

    def _click_submit(self, page: Page) -> bool:
        log_form_action(page, self.log, action="submit attempt", detail="formSubmitExt/formSubmit")
        submitted = page.evaluate(
            """() => {
                if (typeof formSubmitExt === 'function' && document.querySelector('#task-use')) {
                    formSubmitExt('#task-use', '&job=2');
                    return 'formSubmitExt';
                }
                const forms = ['#task-report', '#report-use', '#task-use', 'form[id*="report"]'];
                if (typeof formSubmit === 'function') {
                    for (const sel of forms) {
                        if (document.querySelector(sel)) {
                            formSubmit(sel);
                            return sel;
                        }
                    }
                }
                return '';
            }"""
        )
        if submitted:
            self.log.info("SUBMIT | via JS %s | url=%s", submitted, page.url[:200])
            return True

        for pattern in SUBMIT_PATTERNS:
            for selector in SUBMIT_SELECTORS:
                loc = page.locator(selector)
                for i in range(loc.count()):
                    text = (loc.nth(i).inner_text() or "") + (loc.nth(i).get_attribute("value") or "")
                    if pattern.search(text):
                        log_click(
                            page,
                            self.log,
                            description="Submit report",
                            selector=selector,
                            text=text,
                        )
                        loc.nth(i).click()
                        log_click_result(page, self.log, description="Submit report")
                        return True

        submit = page.locator("input[type='submit'], button[type='submit']").first
        if submit.count():
            text = submit.inner_text() or submit.get_attribute("value") or ""
            log_click(page, self.log, description="Submit input[type=submit]", text=text)
            submit.click()
            log_click_result(page, self.log, description="Submit input[type=submit]")
            return True
        return False

    def _find_report_textarea(self, page: Page):
        for selector in REPORT_SELECTORS:
            loc = page.locator(selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue
        return None

    def _report_form_visible(self, page: Page) -> bool:
        if self._find_report_textarea(page) is not None:
            return True
        return page.locator("#task_report, .tox-tinymce, #task-use textarea").count() > 0

    def _wait_for_report_form(self, page: Page, timeout_ms: int = 20000) -> bool:
        selectors = [
            "#task_report",
            "#task-report textarea",
            "form#task-use",
            "form#task-report",
            ".tox-tinymce",
            "textarea[name='task_report']",
            "textarea[name='report']",
            "textarea",
        ]
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            for selector in selectors:
                loc = page.locator(selector)
                if loc.count():
                    try:
                        if loc.first.is_visible():
                            return True
                    except Exception:
                        continue
            page.wait_for_timeout(500)
        return False

    def _upload_screenshots(self, page: Page, paths: list[str]) -> int:
        valid_paths: list[str] = []
        for raw in paths:
            path = Path(raw)
            if not path.is_absolute():
                path = path.resolve()
            if not path.exists():
                self.log.warning("SUBMIT | screenshot missing: %s", path)
                continue
            if path.stat().st_size == 0:
                self.log.warning("SUBMIT | screenshot empty: %s", path)
                continue
            valid_paths.append(str(path))

        if not valid_paths:
            return 0

        uploaded = 0
        file_inputs = page.locator(",".join(FILE_INPUT_SELECTORS))
        input_count = file_inputs.count()

        if input_count == 0:
            self.log.warning("SUBMIT | no file inputs on page")
            return 0

        for index, path in enumerate(valid_paths):
            input_idx = min(index, input_count - 1)
            target = file_inputs.nth(input_idx)
            try:
                target.set_input_files(path)
                page.evaluate(
                    """(idx) => {
                        const inputs = document.querySelectorAll("input[type='file']");
                        const el = inputs[idx];
                        if (el) {
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                    }""",
                    input_idx,
                )
                uploaded += 1
                self.log.info("SUBMIT | uploaded screenshot %s → input[%d]", path, input_idx)
                time.sleep(0.8)
            except Exception as exc:
                self.log.warning("SUBMIT | upload failed %s: %s", path, exc)

        if uploaded < len(valid_paths) and input_count == 1 and len(valid_paths) > 1:
            try:
                file_inputs.first.set_input_files(valid_paths)
                page.evaluate(
                    """() => {
                        const el = document.querySelector("input[type='file']");
                        if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
                    }"""
                )
                uploaded = len(valid_paths)
                self.log.info("SUBMIT | uploaded %d screenshots via multi-file input", uploaded)
            except Exception as exc:
                self.log.warning("SUBMIT | multi-file upload failed: %s", exc)

        return uploaded

    def _page_looks_logged_in(self, page: Page) -> bool:
        content = page.content().lower()
        return any(x in content for x in ("выйти", "logout", "tasks", "задания", "work_mode"))
