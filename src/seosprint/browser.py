from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from src.config import Settings
from src.seosprint.browser_log import (
    attach_page_logging,
    get_browser_logger,
    log_context_state,
    log_page_close,
    log_page_open,
)


class SeosprintBrowser:
    """Persistent Playwright-сессия для SEOsprint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._keeper_page: Page | None = None
        self.logger = get_browser_logger(log_dir=settings.data_dir / "logs")

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Браузер не запущен. Вызовите start() или используйте with.")
        return self._context

    def start(self) -> BrowserContext:
        if self._context is not None:
            return self._context

        channel = self.settings.browser_channel or None
        self.logger.info(
            "BROWSER start | headless=%s slow_mo=%s channel=%s",
            self.settings.headless,
            self.settings.browser_slow_mo,
            channel or "bundled",
        )
        self._playwright = sync_playwright().start()
        profile_dir = self.settings.browser_profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)

        launch_kwargs: dict = {
            "user_data_dir": str(profile_dir),
            "headless": self.settings.headless,
            "slow_mo": self.settings.browser_slow_mo,
            "viewport": {"width": 1280, "height": 900},
            "locale": "ru-RU",
        }
        if channel:
            launch_kwargs["channel"] = channel

        try:
            self._context = self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:
            if channel:
                raise RuntimeError(
                    f"Не удалось запустить браузер channel={channel!r}: {exc}\n"
                    "Установите Google Chrome или Microsoft Edge, либо выполните: playwright install chromium"
                ) from exc
            raise RuntimeError(
                f"Playwright Chromium не установлен: {exc}\n"
                "Вариант 1: playwright install chromium\n"
                "Вариант 2: установите Chrome и добавьте в .env: BROWSER_CHANNEL=chrome"
            ) from exc

        if self.settings.seosprint_cookies_file and self.settings.seosprint_cookies_file.exists():
            self._load_cookies(self.settings.seosprint_cookies_file)

        self._ensure_keeper_page()
        log_context_state(self.logger, self._context, action="after start")
        return self._context

    def stop(self) -> None:
        self.logger.info("BROWSER stop requested")
        if self._context is not None:
            for page in list(self._context.pages):
                if page is self._keeper_page:
                    continue
                try:
                    if not page.is_closed():
                        log_page_close(self.logger, page, reason="stop")
                        page.close()
                except Exception:
                    pass
            time.sleep(0.3)
            try:
                self.save_cookies()
            except Exception as exc:
                self.logger.warning("BROWSER stop | save cookies failed: %s", exc)
            try:
                if self._keeper_page and not self._keeper_page.is_closed():
                    self._keeper_page.close()
            except Exception:
                pass
            self._keeper_page = None
            try:
                self._context.close()
            except Exception as exc:
                self.logger.warning("BROWSER stop | context close: %s", exc)
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self.logger.info("BROWSER stopped")

    def __enter__(self) -> SeosprintBrowser:
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()

    def _ensure_keeper_page(self) -> Page:
        """Держит хотя бы одну вкладку — иначе persistent context может закрыться."""
        if self._keeper_page is not None and not self._keeper_page.is_closed():
            return self._keeper_page

        for page in self.context.pages:
            if not page.is_closed() and page.url in ("about:blank", ""):
                self._keeper_page = page
                if self.settings.browser_debug:
                    attach_page_logging(page, self.logger, label="keeper")
                self.logger.info("BROWSER keeper reused | %s", page.url)
                return page

        self._keeper_page = self.context.new_page()
        try:
            self._keeper_page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
        except Exception as exc:
            self.logger.warning("BROWSER keeper goto failed: %s", exc)
        if self.settings.browser_debug:
            attach_page_logging(self._keeper_page, self.logger, label="keeper")
        log_page_open(self.logger, self._keeper_page, reason="keeper created")
        return self._keeper_page

    def is_keeper_page(self, page: Page | None) -> bool:
        return page is not None and page is self._keeper_page

    def new_page(self) -> Page:
        self._ensure_keeper_page()
        if self._context is None:
            raise RuntimeError("Browser context closed")
        try:
            page = self.context.new_page()
        except Exception as exc:
            log_context_state(self.logger, self.context, action="new_page FAILED")
            self.logger.error("BROWSER new_page failed: %s", exc)
            raise
        if self.settings.browser_debug:
            attach_page_logging(page, self.logger)
        log_page_open(self.logger, page, reason="new_page")
        log_context_state(self.logger, self.context, action="after new_page")
        return page

    def safe_close_page(self, page: Page | None, *, reason: str = "") -> None:
        if page is None or page.is_closed():
            return
        if self.is_keeper_page(page):
            self.logger.debug("PAGE CLOSE skipped (keeper) | reason=%s", reason)
            return
        try:
            log_page_close(self.logger, page, reason=reason or "close")
            page.close()
            time.sleep(0.15)
        except Exception as exc:
            self.logger.warning("PAGE CLOSE error | reason=%s | %s", reason, exc)
        self._ensure_keeper_page()
        if self._context is not None:
            log_context_state(self.logger, self.context, action=f"after close ({reason})")

    def save_cookies(self) -> Path:
        cookies_path = self.settings.seosprint_cookies_file
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies = self.context.cookies()
        cookies_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.debug("COOKIES saved → %s (%d)", cookies_path.resolve(), len(cookies))
        return cookies_path

    def _load_cookies(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw:
            self.context.add_cookies(raw)
            self.logger.debug("COOKIES loaded from %s (%d)", path.resolve(), len(raw))

    def is_logged_in(self) -> bool:
        page = self.new_page()
        try:
            page.goto(f"{self.settings.seosprint_base_url}/earn", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            url = page.url.lower()
            if "/login" in url:
                return False
            content = page.content().lower()
            if "adv-line" in content or "taskline_" in content:
                return True
            if "выйти" in content or "logout" in content or "/member/" in url:
                return True
            if "войти" in content and "выйти" not in content and "logout" not in content:
                return False
            return "задания" in content or "earn-task" in content
        finally:
            self.safe_close_page(page, reason="is_logged_in")
