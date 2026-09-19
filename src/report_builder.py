from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import Settings
from src.models import SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient
from src.seosprint.platforms import PLATFORM_LABELS, format_platforms


def build_executor_profile(settings: Settings) -> dict[str, str]:
    return {
        "telegram": settings.executor_telegram or "",
        "email": settings.executor_email or "",
        "account_id": settings.executor_account_id or "",
        "registration_date": settings.executor_registration_date or "",
    }


def build_session_info(
    required_platforms: list[str],
    session_cache: dict[str, bool] | None,
) -> dict[str, str]:
    cache = session_cache or {}
    info: dict[str, str] = {}
    for platform in required_platforms:
        label = PLATFORM_LABELS.get(platform, platform)
        if platform in cache:
            info[label] = "авторизован" if cache[platform] else "нет сессии"
        else:
            info[label] = "не проверялся"
    return info


def build_execution_payload(
    settings: Settings,
    task: SeoTask,
    analysis: TaskAnalysis,
    *,
    collected_data: dict[str, Any] | None = None,
    screenshot_paths: list[str] | None = None,
    action_log: list[str] | None = None,
    session_cache: dict[str, bool] | None = None,
    extra_notes: str = "",
) -> dict[str, Any]:
    shots = screenshot_paths or []
    resolved_shots: list[str] = []
    for shot in shots:
        path = Path(shot)
        resolved_shots.append(str(path.resolve()))
    collected = dict(collected_data or {})
    if "visited_urls" not in collected and analysis.urls:
        collected.setdefault("visited_urls", list(analysis.urls))

    return {
        "task_id": task.task_id,
        "title": task.title,
        "executor": build_executor_profile(settings),
        "sessions": build_session_info(analysis.required_platforms, session_cache),
        "required_platforms": analysis.required_platforms,
        "report_fields": analysis.report_fields,
        "visited_urls": collected.get("visited_urls", []),
        "collected_data": collected,
        "screenshots": resolved_shots,
        "screenshots_attached_to_form": bool(resolved_shots),
        "screenshots_files_exist": all(Path(p).exists() for p in resolved_shots) if resolved_shots else False,
        "action_log": list(action_log or []),
        "extra_notes": extra_notes,
    }


def generate_report_text(
    client: OpenRouterClient,
    settings: Settings,
    task: SeoTask,
    analysis: TaskAnalysis,
    payload: dict[str, Any],
    *,
    system_prompt: str = "",
    fix_instructions: str = "",
) -> str:
    tz_block = f"""Задание T-{task.task_id}: {task.title}

Описание (ТЗ):
{task.description}

Что указать в отчёте:
{task.report_requirements}

Требуемые поля отчёта: {", ".join(analysis.report_fields) if analysis.report_fields else "—"}"""

    fix_block = ""
    if fix_instructions.strip():
        fix_block = f"\n\nИсправления от модератора (обязательно учесть):\n{fix_instructions.strip()}"

    prompt = f"""Сформируй готовый текст отчёта для формы SEOsprint.

{tz_block}{fix_block}

Данные исполнителя и выполнения (используй ВСЕ подходящие поля):
{payload}

Правила:
1. Включи ВСЕ данные из executor, которые требует ТЗ (Telegram, email, ID аккаунта, дата регистрации и т.д.).
2. Если в ТЗ просят указать сессии/аккаунты платформ — перечисли их из sessions.
3. Если есть visited_urls — укажи посещённые ссылки.
4. Если screenshots_attached_to_form=true — напиши что скриншот приложен (файлы загружаются отдельно, пути не дублируй в текст).
5. Не используй markdown. Только текст для поля отчёта.
6. Заполни плейсхolders ([ваш ID], [дата]) реальными значениями из executor, если они есть."""

    system = system_prompt or (
        "Ты помощник SEOsprint. Пиши отчёты полностью по ТЗ, используя все переданные данные исполнителя."
    )

    try:
        return client.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
        ).strip()
    except Exception:
        return _fallback_report(task, analysis, payload)


def _fallback_report(task: SeoTask, analysis: TaskAnalysis, payload: dict[str, Any]) -> str:
    lines: list[str] = []
    executor = payload.get("executor", {})
    if executor.get("account_id"):
        lines.append(f"ID аккаунта: {executor['account_id']}")
    if executor.get("registration_date"):
        lines.append(f"Дата регистрации: {executor['registration_date']}")
    if executor.get("telegram"):
        lines.append(f"Telegram: {executor['telegram']}")
    if executor.get("email"):
        lines.append(f"Email: {executor['email']}")

    sessions = payload.get("sessions", {})
    if sessions:
        lines.append("Сессии: " + ", ".join(f"{k} ({v})" for k, v in sessions.items()))

    for url in payload.get("visited_urls", []):
        lines.append(str(url))

    if payload.get("screenshots_attached_to_form"):
        lines.append("Скриншот приложен.")

    if not lines:
        if "скрин" in task.report_requirements.lower():
            return "Задание выполнено. Скриншот приложен."
        return task.title
    return "\n".join(lines)
