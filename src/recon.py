from __future__ import annotations

from pydantic import BaseModel, Field

from src.models import SeoTask, TaskAnalysis
from src.openrouter import OpenRouterClient
from src.seosprint.platforms import detect_platforms, filter_checkable_platforms
from src.task_context import TaskTzContext


class ReconResult(BaseModel):
    proceed: bool = True
    reason: str = ""
    required_platforms: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    summary: str = ""


RECON_PROMPT = """Разведка задания SEOsprint ПЕРЕД взятием в работу.

Изучи ТЗ и определи:
1. Точный порядок действий исполнителя
2. Все платформы, где нужна авторизованная сессия (youtube, vk, telegram, facebook, instagram, linkedin, viber)
3. Есть ли скрытые требования (подписка, лайк, коммент, регистрация, депозит)
4. Можно ли автоматизировать без ручных действий

Если задание требует депозит/казино — proceed=false.
Если нужны платформы, которых нет в obvious_platforms, укажи их в required_platforms.

JSON:
{
  "proceed": true,
  "reason": "",
  "required_platforms": ["youtube"],
  "steps": ["шаг 1", "шаг 2"],
  "summary": "кратко что делать",
  "needs_deposit": false
}
"""


class ReconRunner:
    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client

    def run(
        self,
        ctx: TaskTzContext,
        *,
        obvious_platforms: list[str] | None = None,
    ) -> ReconResult:
        task = ctx.task
        analysis = ctx.analysis
        obvious = obvious_platforms or []

        text_platforms = detect_platforms(
            f"{task.title} {task.description} {task.report_requirements} {task.site}"
        )
        heuristic_steps = list(analysis.required_actions)
        if not heuristic_steps and analysis.urls:
            heuristic_steps = [f"Открыть {url}" for url in analysis.urls[:5]]

        try:
            data = self.client.chat_json(
                [
                    {"role": "system", "content": ctx.system_prompt()},
                    {
                        "role": "user",
                        "content": (
                            f"{RECON_PROMPT}\n\n"
                            f"obvious_platforms (уже проверены на списке): {obvious}\n\n"
                            f"{ctx.tz_block()}"
                        ),
                    },
                ]
            )
        except Exception as exc:
            return ReconResult(
                proceed=bool(task.description.strip()),
                reason=f"Разведка через эвристику: {exc}",
                required_platforms=filter_checkable_platforms(text_platforms),
                steps=heuristic_steps,
                summary=task.title,
            )

        if data.get("needs_deposit"):
            return ReconResult(
                proceed=False,
                reason="Требуется депозит/финансовые вложения",
                required_platforms=[],
                steps=[],
            )

        platforms = list(data.get("required_platforms") or [])
        for p in text_platforms:
            if p not in platforms:
                platforms.append(p)
        for p in analysis.required_platforms:
            if p not in platforms:
                platforms.append(p)
        platforms = filter_checkable_platforms(platforms)

        steps = [str(s) for s in (data.get("steps") or heuristic_steps) if str(s).strip()]
        proceed = bool(data.get("proceed", True))
        reason = str(data.get("reason", ""))

        return ReconResult(
            proceed=proceed,
            reason=reason,
            required_platforms=platforms,
            steps=steps,
            summary=str(data.get("summary", task.title)),
        )

    @staticmethod
    def apply_to_analysis(analysis: TaskAnalysis, recon: ReconResult) -> TaskAnalysis:
        platforms = list(analysis.required_platforms)
        for p in recon.required_platforms:
            if p not in platforms:
                platforms.append(p)
        actions = recon.steps or analysis.required_actions
        return analysis.model_copy(
            update={
                "required_platforms": platforms,
                "required_actions": actions,
                "summary": recon.summary or analysis.summary,
            }
        )
