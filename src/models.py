from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskCategory(str, Enum):
    VISIT_URLS = "visit_urls"
    REGISTRATION = "registration"
    REGISTRATION_ACTIVITY = "registration_activity"
    SOCIAL_SUBSCRIBE = "social_subscribe"
    TELEGRAM = "telegram"
    YOUTUBE = "youtube"
    SURFING = "surfing"
    SITE_ACTIVITY = "site_activity"
    FINANCIAL = "financial"
    MOBILE_APP = "mobile_app"
    FORUM = "forum"
    OTHER = "other"


class AutomationLevel(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    MANUAL = "manual"
    SKIP = "skip"


class SeoTask(BaseModel):
    task_id: str
    title: str
    site: str = ""
    price_usd: float = 0.0
    category_tag: str = ""
    description: str = ""
    report_requirements: str = ""
    paid_count: int = 0
    rejected_count: int = 0
    url: str = ""

    @property
    def reject_rate(self) -> float:
        total = self.paid_count + self.rejected_count
        if total == 0:
            return 0.0
        return self.rejected_count / total


class TaskAnalysis(BaseModel):
    task_id: str
    category: TaskCategory
    automation_level: AutomationLevel
    difficulty: int = Field(ge=1, le=5)
    estimated_minutes: int = Field(ge=1)
    risk_notes: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    report_fields: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    can_automate: bool = True
    skip_reason: str = ""
    summary: str = ""
    required_platforms: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    task_id: str
    success: bool
    mode: str
    message: str = ""
    collected_data: dict[str, Any] = Field(default_factory=dict)
    report_text: str = ""
    screenshot_paths: list[str] = Field(default_factory=list)
    taken: bool = False
    submitted: bool = False
    submit_message: str = ""


class PipelineResult(BaseModel):
    task_id: str
    taken: bool
    executed: bool
    submitted: bool
    message: str = ""
    report_text: str = ""
    screenshot_paths: list[str] = Field(default_factory=list)
