from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Dialog, Page, Request, Response

_LOGGERS: dict[str, logging.Logger] = {}


def get_browser_logger(*, log_dir: Path | None = None) -> logging.Logger:
    key = str(log_dir or "default")
    if key in _LOGGERS:
        return _LOGGERS[key]

    logger = logging.getLogger(f"seosprint.browser.{key}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        logger.addHandler(console)

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"browser_{datetime.now():%Y%m%d}.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
            logger.debug("Лог-файл: %s", log_file.resolve())

    _LOGGERS[key] = logger
    return logger


def _page_label(page: Page) -> str:
    try:
        title = page.title()[:60] if not page.is_closed() else "closed"
    except Exception:
        title = "?"
    try:
        url = page.url[:120] if not page.is_closed() else "closed"
    except Exception:
        url = "?"
    return f"page[{title} @ {url}]"


def attach_page_logging(page: Page, logger: logging.Logger, *, label: str = "") -> None:
    """Подписывает page на события: навигация, диалоги, консоль, сеть."""
    tag = label or _page_label(page)
    if getattr(page, "_seosprint_logging", False):
        return
    page._seosprint_logging = True  # type: ignore[attr-defined]

    def on_console(msg) -> None:
        if msg.type in {"error", "warning"}:
            logger.debug("%s | console.%s: %s", tag, msg.type, msg.text[:500])

    def on_dialog(dialog: Dialog) -> None:
        logger.warning(
            "%s | DIALOG type=%s message=%r default=%r",
            tag,
            dialog.type,
            dialog.message,
            dialog.default_value,
        )
        try:
            dialog.dismiss()
            logger.info("%s | DIALOG dismissed", tag)
        except Exception as exc:
            logger.error("%s | DIALOG dismiss failed: %s", tag, exc)

    def on_framenavigated(frame) -> None:
        if frame != page.main_frame:
            return
        logger.info("%s | NAVIGATED → %s", tag, frame.url[:200])

    def on_request(request: Request) -> None:
        if request.is_navigation_request() and request.frame == page.main_frame:
            logger.debug("%s | REQUEST nav %s %s", tag, request.method, request.url[:200])

    def on_response(response: Response) -> None:
        req = response.request
        if req.is_navigation_request() and req.frame == page.main_frame:
            logger.info(
                "%s | RESPONSE nav %s %s → %s",
                tag,
                response.status,
                req.url[:160],
                response.url[:160],
            )
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                if location:
                    logger.info("%s | REDIRECT → %s", tag, location[:200])

    def on_pageerror(error) -> None:
        logger.error("%s | PAGE ERROR: %s", tag, error)

    def on_close() -> None:
        logger.info("%s | PAGE CLOSED", tag)

    page.on("console", on_console)
    page.on("dialog", on_dialog)
    page.on("framenavigated", on_framenavigated)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("pageerror", on_pageerror)
    page.on("close", on_close)
    logger.debug("%s | logging attached", tag)


def log_context_state(logger: logging.Logger, context, *, action: str) -> None:
    try:
        pages = list(context.pages)
        open_pages = [p for p in pages if not p.is_closed()]
        urls = []
        for p in open_pages:
            try:
                urls.append(p.url[:100])
            except Exception:
                urls.append("?")
        logger.info(
            "CONTEXT %s | open=%d/%d | urls=%s",
            action,
            len(open_pages),
            len(pages),
            urls or "—",
        )
    except Exception as exc:
        logger.error("CONTEXT %s | state unreadable: %s", action, exc)


def log_goto(
    page: Page,
    url: str,
    logger: logging.Logger,
    *,
    label: str = "",
    **kwargs: Any,
):
    tag = label or _page_label(page)
    before = page.url if not page.is_closed() else "closed"
    logger.info("%s | GOTO start: %s (from %s)", tag, url, before[:120])
    response = page.goto(url, **kwargs)
    status = response.status if response is not None else None
    final = page.url if not page.is_closed() else "closed"
    logger.info(
        "%s | GOTO done: status=%s final=%s",
        tag,
        status,
        final[:200],
    )
    if before != final and url.split("?")[0] not in final:
        logger.info("%s | GOTO redirect chain: %s → %s", tag, before[:100], final[:200])
    return response


def log_click(
    page: Page,
    logger: logging.Logger,
    *,
    description: str,
    selector: str = "",
    text: str = "",
    href: str = "",
    onclick: str = "",
) -> None:
    tag = _page_label(page)
    before = page.url if not page.is_closed() else "closed"
    logger.info(
        "%s | CLICK %s | selector=%r text=%r href=%r onclick=%r | before=%s",
        tag,
        description,
        selector[:120],
        text[:80],
        href[:120],
        onclick[:120],
        before[:120],
    )


def log_click_result(page: Page, logger: logging.Logger, *, description: str) -> None:
    tag = _page_label(page)
    after = page.url if not page.is_closed() else "closed"
    logger.info("%s | CLICK %s done | after=%s", tag, description, after[:200])


def log_form_action(page: Page, logger: logging.Logger, *, action: str, detail: str = "") -> None:
    logger.info("%s | FORM %s %s", _page_label(page), action, detail)


def log_page_open(logger: logging.Logger, page: Page, *, reason: str) -> None:
    logger.info("PAGE OPEN [%s] | %s", reason, _page_label(page))


def log_page_close(logger: logging.Logger, page: Page | None, *, reason: str) -> None:
    if page is None:
        logger.info("PAGE CLOSE [%s] | page=None", reason)
        return
    try:
        url = page.url[:120] if not page.is_closed() else "already closed"
    except Exception:
        url = "?"
    logger.info("PAGE CLOSE [%s] | %s", reason, url)
