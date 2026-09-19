from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup

from src.models import SeoTask

PRICE_RE = re.compile(r"[\d,\.]+")
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _clean_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_price(raw: str) -> float:
    match = PRICE_RE.search(raw.replace(",", "."))
    if not match:
        return 0.0
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return 0.0


REPORT_MARKERS = (
    "Что нужно указать в отчёте о выполненном задании",
    "Что нужно указать в отчёте",
    "Что указать в отчёте о выполненном задании",
    "Что указать в отчёте",
    "What to include in the report",
)


def _split_description(raw_html: str) -> tuple[str, str]:
    text = BeautifulSoup(raw_html, "lxml").get_text("\n", strip=True)
    text = unescape(text)

    for report_marker in REPORT_MARKERS:
        if report_marker in text:
            desc, report = text.split(report_marker, 1)
            return _clean_text(desc), _clean_text(report)

    return _clean_text(text), ""


def _strip_report_title(report: str) -> str:
    report = _clean_text(report)
    for marker in REPORT_MARKERS:
        if report.lower().startswith(marker.lower()):
            report = report[len(marker) :].strip(" :—-")
    return _clean_text(report)


def merge_tasks(base: SeoTask, detail: SeoTask) -> SeoTask:
    updates: dict[str, object] = {}
    for field in ("title", "description", "report_requirements", "site", "category_tag", "url"):
        value = getattr(detail, field, "")
        if value:
            updates[field] = value
    if detail.price_usd:
        updates["price_usd"] = detail.price_usd
    if detail.paid_count or detail.rejected_count:
        updates["paid_count"] = detail.paid_count
        updates["rejected_count"] = detail.rejected_count
    return base.model_copy(update=updates)


def parse_task_detail_html(html: str, task_id: str) -> SeoTask | None:
    """Полное ТЗ со страницы /read-task/{id}."""
    soup = BeautifulSoup(html, "lxml")

    title = ""
    form_title = soup.select_one(".form-title")
    if form_title:
        title_raw = form_title.get_text(" ", strip=True)
        title = _clean_text(re.sub(r"^№\s*\d+\s*[-–—]\s*", "", title_raw))

    desc_el = soup.select_one(".block-quest .mess.longtext, .block-quest .mess")
    description = _clean_text(desc_el.get_text("\n", strip=True)) if desc_el else ""

    report = ""
    report_el = soup.select_one(".block-quest .answer-fix.longtext, .block-quest .answer-fix")
    if report_el:
        report = _strip_report_title(report_el.get_text("\n", strip=True))

    if not description:
        block = soup.select_one(".block-quest")
        if block:
            description, report_from_block = _split_description(str(block))
            if not report:
                report = report_from_block

    if not title and not description:
        return None

    site = ""
    for span in soup.select(".table-rtask span, .citext"):
        icon = span.select_one("i.fa-link, i.fal.fa-link")
        if icon:
            site = _clean_text(span.get_text())
            break

    price = 0.0
    price_el = soup.select_one(".price-click")
    if price_el:
        price = _parse_price(price_el.get_text())

    category_tag = ""
    for tag in soup.select(".tag-mini-tr, .citext"):
        txt = _clean_text(tag.get_text())
        if txt and not re.match(r"^\d+\s*/\s*\d+$", txt):
            category_tag = txt
            break

    return SeoTask(
        task_id=task_id,
        title=title or f"Task {task_id}",
        site=site,
        price_usd=price,
        category_tag=category_tag,
        description=description,
        report_requirements=report,
        url=f"https://seosprint.net/read-task/{task_id}",
    )


def parse_task_list_html(html: str) -> list[SeoTask]:
    soup = BeautifulSoup(html, "lxml")
    tasks: list[SeoTask] = []
    seen: set[str] = set()

    for line in soup.select("div.adv-line, div[id^='taskline_']"):
        task_id = ""
        if line.get("id", "").startswith("taskline_"):
            task_id = line["id"].replace("taskline_", "")

        title_el = line.select_one(".earn-title, b.earn-title")
        title = _clean_text(title_el.get_text()) if title_el else ""

        if not task_id:
            link = line.select_one("a[href*='/read-task/']")
            if link and link.get("href"):
                task_id = link["href"].split("/read-task/")[-1].split("?")[0]

        if not task_id or task_id in seen:
            continue
        seen.add(task_id)

        site = ""
        for span in line.select("span"):
            icon = span.select_one("i.fa-link, i.fal.fa-link")
            if icon:
                site = _clean_text(span.get_text())
                site = site.replace("Инструкции внутри", "").strip()
                break

        price = 0.0
        price_el = line.select_one(".watch-price")
        if price_el:
            price = _parse_price(price_el.get_text())

        category_tag = ""
        for tag in line.select(".tag-mini-tr"):
            txt = _clean_text(tag.get_text())
            if txt and not re.match(r"^\d+\s*/\s*\d+$", txt) and "fa-flag" not in str(tag):
                if not re.search(r"^\d+$", txt):
                    category_tag = txt
                    break

        paid, rejected = 0, 0
        stats = line.select_one(".tag-mini-tr")
        if stats:
            nums = re.findall(r"\d+", stats.get_text())
            if len(nums) >= 2:
                paid, rejected = int(nums[0]), int(nums[1])

        desc_html = ""
        desc_el = line.select_one(f"#task{task_id}")
        if desc_el:
            desc_html = str(desc_el)

        description, report = _split_description(desc_html)

        link_el = line.select_one(f"a[href='/read-task/{task_id}']")
        url = f"https://seosprint.net/read-task/{task_id}" if task_id else ""

        tasks.append(
            SeoTask(
                task_id=task_id,
                title=title,
                site=site,
                price_usd=price,
                category_tag=category_tag,
                description=description,
                report_requirements=report,
                paid_count=paid,
                rejected_count=rejected,
                url=url,
            )
        )

    return tasks


def parse_paginated_earn_base(source_url: str) -> tuple[str, int] | None:
    """Если URL вида .../earn-task/3 — вернуть (base без номера, стартовая страница)."""
    match = re.search(r"(/earn-task)/(\d+)(?:[/?#]|$)", source_url, re.I)
    if match:
        prefix = source_url[: match.start()] + match.group(1)
        return prefix, int(match.group(2))
    if re.search(r"/earn-task(?:[/?#]|$)", source_url, re.I):
        base = re.sub(r"[?#].*$", "", source_url).rstrip("/")
        return base, 1
    return None


def parse_review_tasks_html(html: str, *, kind: str) -> list[dict]:
    """Парсит earn-task-fail / earn-task-fix: задания + чат с отказом/правками."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen: set[str] = set()

    task_blocks: list = list(soup.select("div.adv-line, div[id^='taskline_']"))
    if not task_blocks:
        task_blocks = [soup]

    for block in task_blocks:
        task_id = ""
        if block.get("id", "").startswith("taskline_"):
            task_id = block["id"].replace("taskline_", "")
        link = block.select_one("a[href*='/read-task/']")
        if not task_id and link and link.get("href"):
            task_id = link["href"].split("/read-task/")[-1].split("?")[0]
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)

        title_el = block.select_one(".earn-title, b.earn-title")
        title = _clean_text(title_el.get_text()) if title_el else ""

        submitted_report = ""
        moderator_note = ""
        rejection_reason = ""
        fix_required = ""

        for chat in block.select(".chat-block"):
            for mess in chat.select(".mess.longtext, .mess"):
                if mess.find_parent(class_=["mess-fail", "mess-fix"]):
                    continue
                text = _clean_text(mess.get_text("\n", strip=True))
                if text and not submitted_report:
                    submitted_report = text

            fail = chat.select_one(".mess-fail")
            if fail:
                title_el = fail.select_one(".title")
                body = _clean_text(fail.get_text("\n", strip=True))
                if title_el:
                    body = body.replace(_clean_text(title_el.get_text()), "", 1).strip()
                rejection_reason = body or rejection_reason

            fix = chat.select_one(".mess-fix")
            if fix:
                title_el = fix.select_one(".title")
                body = _clean_text(fix.get_text("\n", strip=True))
                if title_el:
                    body = body.replace(_clean_text(title_el.get_text()), "", 1).strip()
                fix_required = body or fix_required

        if not submitted_report and not rejection_reason and not fix_required:
            for sel in (".mess-fail", ".mess-fix"):
                el = block.select_one(sel)
                if el:
                    body = _clean_text(el.get_text("\n", strip=True))
                    if "mess-fail" in sel:
                        rejection_reason = body
                    else:
                        fix_required = body

        results.append(
            {
                "task_id": task_id,
                "title": title,
                "kind": kind,
                "submitted_report": submitted_report,
                "rejection_reason": rejection_reason,
                "fix_required": fix_required,
            }
        )

    if not results:
        for chat in soup.select(".chat-block"):
            link = chat.find_previous("a", href=re.compile(r"/read-task/\d+"))
            task_id = ""
            if link and link.get("href"):
                task_id = link["href"].split("/read-task/")[-1].split("?")[0]
            if not task_id:
                continue
            if task_id in seen:
                continue
            seen.add(task_id)
            submitted_report = ""
            rejection_reason = ""
            fix_required = ""
            for mess in chat.select(".mess.longtext, .mess"):
                if mess.find_parent(class_=["mess-fail", "mess-fix"]):
                    continue
                text = _clean_text(mess.get_text("\n", strip=True))
                if text:
                    submitted_report = text
                    break
            fail = chat.select_one(".mess-fail")
            if fail:
                rejection_reason = _clean_text(fail.get_text("\n", strip=True))
            fix = chat.select_one(".mess-fix")
            if fix:
                fix_required = _clean_text(fix.get_text("\n", strip=True))
            results.append(
                {
                    "task_id": task_id,
                    "title": "",
                    "kind": kind,
                    "submitted_report": submitted_report,
                    "rejection_reason": rejection_reason,
                    "fix_required": fix_required,
                }
            )

    return results


def extract_urls_from_text(text: str) -> list[str]:
    urls = URL_RE.findall(text or "")
    cleaned: list[str] = []
    for url in urls:
        url = url.rstrip(".,);]")
        if url not in cleaned:
            cleaned.append(url)
    return cleaned
