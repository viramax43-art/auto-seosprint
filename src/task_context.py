from __future__ import annotations

from dataclasses import dataclass, field

from src.models import SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient


@dataclass
class TaskTzContext:
    """Контекст ТЗ на время одного задания. Очищается после завершения или отказа."""

    client: OpenRouterClient
    task: SeoTask
    analysis: TaskAnalysis
    steps: list[str] = field(default_factory=list)
    action_log: list[str] = field(default_factory=list)
    _active: bool = True

    def bind(self, task: SeoTask, analysis: TaskAnalysis, *, steps: list[str] | None = None) -> None:
        self.task = task
        self.analysis = analysis
        if steps is not None:
            self.steps = list(steps)

    def tz_block(self) -> str:
        steps = self.steps or self.analysis.required_actions
        steps_text = "\n".join(f"- {s}" for s in steps) if steps else "—"
        return f"""Задание T-{self.task.task_id}: {self.task.title}

Описание (ТЗ):
{self.task.description}

Что указать в отчёте:
{self.task.report_requirements}

Шаги по ТЗ:
{steps_text}

URL: {", ".join(self.analysis.urls) if self.analysis.urls else "—"}
Платформы: {", ".join(self.analysis.required_platforms) if self.analysis.required_platforms else "—"}"""

    def system_prompt(self) -> str:
        return (
            "Ты выполняешь задание SEOsprint. На КАЖДОМ шаге сверяйся с ТЗ. "
            "Не выходи за рамки описания и требований к отчёту.\n\n"
            f"{self.tz_block()}"
        )

    def log_action(self, action: str, outcome: str = "") -> None:
        if not self._active:
            return
        entry = action if not outcome else f"{action} → {outcome}"
        self.action_log.append(entry)

    def verify_action(self, action: str, outcome: str) -> tuple[bool, str]:
        """Сверка шага с ТЗ через ИИ."""
        if not self._active:
            return True, ""

        self.log_action(action, outcome)
        prompt = f"""Проверь выполнение шага по ТЗ SEOsprint.

{self.tz_block()}

Шаг: {action}
Результат: {outcome}

JSON:
{{
  "ok": true/false,
  "reason": "кратко"
}}"""
        try:
            data = self.client.chat_json(
                [
                    {"role": "system", "content": self.system_prompt()},
                    {"role": "user", "content": prompt},
                ]
            )
            ok = bool(data.get("ok", True))
            reason = str(data.get("reason", ""))
            return ok, reason
        except Exception:
            return True, ""

    def clear(self) -> None:
        self._active = False
        self.steps.clear()
        self.action_log.clear()
