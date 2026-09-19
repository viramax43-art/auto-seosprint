from __future__ import annotations

import json

from src.models import AutomationLevel, SeoTask, TaskAnalysis, TaskCategory
from src.openrouter import OpenRouterClient
from src.parser import extract_urls_from_text
from src.seosprint.platforms import detect_platforms

SYSTEM_PROMPT = """Ты эксперт по автоматизации заданий SEOsprint.

ВАЖНО: почти все задания автоматизируемы через браузер с сохранённой сессией.
can_automate=true для ВСЕХ заданий, кроме финансовых (депозит/казино за реальные деньги).

Правила:
- visit_urls, surfing, registration, social, youtube, telegram, vk, facebook, instagram: FULL или PARTIAL, can_automate=true
- site_activity (Facebook→сайт→поиск): PARTIAL, can_automate=true (браузерная сессия)
- financial (депозит, казино): SKIP, can_automate=false
- mobile_app: PARTIAL, can_automate=true (если через браузер)

required_platforms: список платформ где нужна сессия: youtube, telegram, vk, facebook, instagram, linkedin, viber

JSON:
{
  "category": "...",
  "automation_level": "full|partial|skip",
  "difficulty": 1-5,
  "estimated_minutes": число,
  "risk_notes": [],
  "required_actions": ["конкретные шаги из ТЗ"],
  "report_fields": ["поля для отчёта из ТЗ"],
  "urls": [],
  "can_automate": true,
  "required_platforms": [],
  "skip_reason": "",
  "summary": "..."
}
"""


class TaskAnalyzer:
    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client

    def analyze(self, task: SeoTask) -> TaskAnalysis:
        heuristic = self._heuristic_analysis(task)
        try:
            ai = self._ai_analysis(task)
            merged = self._merge(heuristic, ai, task.task_id)
        except Exception:
            merged = heuristic
        return self._apply_policy(merged)

    def analyze_batch(self, tasks: list[SeoTask]) -> list[TaskAnalysis]:
        return [self.analyze(task) for task in tasks]

    def _ai_analysis(self, task: SeoTask) -> dict:
        user_content = json.dumps(
            {
                "task_id": task.task_id,
                "title": task.title,
                "site": task.site,
                "price_usd": task.price_usd,
                "category_tag": task.category_tag,
                "description": task.description,
                "report_requirements": task.report_requirements,
                "paid": task.paid_count,
                "rejected": task.rejected_count,
            },
            ensure_ascii=False,
        )
        return self.client.chat_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )

    def _heuristic_analysis(self, task: SeoTask) -> TaskAnalysis:
        text = f"{task.title} {task.description} {task.category_tag} {task.site}".lower()
        urls = extract_urls_from_text(f"{task.description} {task.report_requirements}")
        platforms = detect_platforms(text)

        category = TaskCategory.OTHER
        automation = AutomationLevel.FULL
        can_automate = True
        skip_reason = ""
        difficulty = 2
        minutes = 8

        if self._is_financial(text):
            category = TaskCategory.FINANCIAL
            automation = AutomationLevel.SKIP
            can_automate = False
            skip_reason = "Требует финансовых вложений (депозит/казино)"
            difficulty = 5
        elif self._is_visit_task(text, urls):
            category = TaskCategory.VISIT_URLS
            automation = AutomationLevel.FULL
            difficulty = 1
            minutes = 3
        elif "youtube" in text or task.category_tag.lower() == "youtube":
            category = TaskCategory.YOUTUBE
            automation = AutomationLevel.FULL
            minutes = 6
        elif task.category_tag.lower() in {"telegram - боты", "telegram - подписки"} or "t.me" in text:
            category = TaskCategory.TELEGRAM
            automation = AutomationLevel.FULL
            minutes = 5
        elif task.category_tag.lower() in {"вконтакте", "facebook", "прочие соцсети"} or "instagram" in text:
            category = TaskCategory.SOCIAL_SUBSCRIBE
            automation = AutomationLevel.FULL
            minutes = 5
        elif "facebook" in text and ("переход" in text or "посети" in text or "поиск" in text):
            category = TaskCategory.SITE_ACTIVITY
            automation = AutomationLevel.PARTIAL
            difficulty = 3
            minutes = 12
        elif "регистр" in text and "серфинг" in text:
            category = TaskCategory.SURFING
            automation = AutomationLevel.PARTIAL
            minutes = 15
        elif "регистр" in text:
            category = (
                TaskCategory.REGISTRATION_ACTIVITY
                if "актив" in text
                else TaskCategory.REGISTRATION
            )
            automation = AutomationLevel.PARTIAL
            minutes = 10
        elif "google play" in text or task.category_tag.lower() == "мобильные приложения":
            category = TaskCategory.MOBILE_APP
            automation = AutomationLevel.PARTIAL
            minutes = 20
        elif "форум" in text:
            category = TaskCategory.FORUM
            automation = AutomationLevel.PARTIAL
            minutes = 12
        elif urls:
            category = TaskCategory.VISIT_URLS
            automation = AutomationLevel.FULL
            difficulty = 1
            minutes = 4

        report_fields = []
        if task.report_requirements:
            for line in task.report_requirements.replace("\n", ".").split("."):
                line = line.strip()
                if line:
                    report_fields.append(line)

        return TaskAnalysis(
            task_id=task.task_id,
            category=category,
            automation_level=automation,
            difficulty=difficulty,
            estimated_minutes=minutes,
            risk_notes=self._risk_notes(task),
            required_actions=[],
            report_fields=report_fields or ["См. описание задания"],
            urls=urls,
            can_automate=can_automate,
            skip_reason=skip_reason,
            summary=task.title,
            required_platforms=platforms,
        )

    @staticmethod
    def _is_financial(text: str) -> bool:
        financial_words = ("депозит", "казино", "casino", "100грн", "100 грн", "вложен")
        if any(w in text for w in financial_words):
            if "без депозита" in text or "без вложений" in text:
                return False
            return True
        return False

    @staticmethod
    def _is_visit_task(text: str, urls: list[str]) -> bool:
        visit_words = (
            "перейти на",
            "перейти",
            "посети",
            "открыть",
            "зайти на",
            "visit",
            "go to",
        )
        if any(w in text for w in visit_words) and urls:
            return True
        if urls and len(urls) <= 5 and "регистр" not in text:
            return True
        return False

    @staticmethod
    def _risk_notes(task: SeoTask) -> list[str]:
        notes: list[str] = []
        if task.reject_rate > 0.05 and task.paid_count > 50:
            notes.append(f"Высокий % отклонений: {task.reject_rate:.1%}")
        if task.price_usd >= 1.0:
            notes.append("Высокая оплата — проверьте условия")
        if "vpn" in task.description.lower():
            notes.append("Может потребоваться VPN")
        if "телефон" in task.description.lower() or "sms" in task.description.lower():
            notes.append("Нужна верификация телефона")
        return notes

    @staticmethod
    def _merge(heuristic: TaskAnalysis, ai: dict, task_id: str) -> TaskAnalysis:
        try:
            category = TaskCategory(ai.get("category", heuristic.category.value))
        except ValueError:
            category = heuristic.category

        try:
            automation = AutomationLevel(ai.get("automation_level", heuristic.automation_level.value))
        except ValueError:
            automation = heuristic.automation_level

        urls = ai.get("urls") or heuristic.urls
        if not urls:
            urls = heuristic.urls

        platforms = ai.get("required_platforms") or heuristic.required_platforms
        if not platforms:
            platforms = heuristic.required_platforms

        if heuristic.category == TaskCategory.VISIT_URLS and urls:
            category = TaskCategory.VISIT_URLS
            platforms = []

        return TaskAnalysis(
            task_id=task_id,
            category=category,
            automation_level=automation,
            difficulty=int(ai.get("difficulty", heuristic.difficulty)),
            estimated_minutes=int(ai.get("estimated_minutes", heuristic.estimated_minutes)),
            risk_notes=ai.get("risk_notes") or heuristic.risk_notes,
            required_actions=ai.get("required_actions") or heuristic.required_actions,
            report_fields=ai.get("report_fields") or heuristic.report_fields,
            urls=urls,
            can_automate=bool(ai.get("can_automate", heuristic.can_automate)),
            skip_reason=str(ai.get("skip_reason", heuristic.skip_reason)),
            summary=str(ai.get("summary", heuristic.summary)),
            required_platforms=platforms,
        )

    @staticmethod
    def _apply_policy(analysis: TaskAnalysis) -> TaskAnalysis:
        """Почти всё автоматизируется; skip только для финансов."""
        if analysis.category == TaskCategory.FINANCIAL or analysis.automation_level == AutomationLevel.SKIP:
            if analysis.category == TaskCategory.FINANCIAL:
                return analysis.model_copy(
                    update={
                        "automation_level": AutomationLevel.SKIP,
                        "can_automate": False,
                        "skip_reason": analysis.skip_reason or "Депозит/казино",
                    }
                )
            return analysis

        return analysis.model_copy(update={"can_automate": True})
