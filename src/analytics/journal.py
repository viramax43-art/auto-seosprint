from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models import SeoTask, TaskAnalysis


class TaskJournal:
    """Аналитический журнал выполнения заданий."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "analytics"
        self.executions_dir = self.root / "executions"
        self.reviews_dir = self.root / "reviews"
        self.executions_dir.mkdir(parents=True, exist_ok=True)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)

    def save_execution(
        self,
        task: SeoTask,
        analysis: TaskAnalysis,
        *,
        actions: list[str],
        recon_steps: list[str],
        collected_data: dict[str, Any],
        report_text: str,
        screenshot_paths: list[str],
        submitted: bool,
        payload: dict[str, Any] | None = None,
        message: str = "",
    ) -> Path:
        record = {
            "task_id": task.task_id,
            "title": task.title,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "description": task.description,
            "report_requirements": task.report_requirements,
            "category": analysis.category.value,
            "platforms": analysis.required_platforms,
            "recon_steps": recon_steps,
            "actions": actions,
            "collected_data": collected_data,
            "execution_payload": payload or {},
            "report_text": report_text,
            "screenshot_paths": [str(Path(p).resolve()) for p in screenshot_paths],
            "submitted": submitted,
            "message": message,
        }
        out = self.executions_dir / f"{task.task_id}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_timeline(record)
        return out

    def load_execution(self, task_id: str) -> dict[str, Any] | None:
        path = self.executions_dir / f"{task_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_review(self, task_id: str, review: dict[str, Any]) -> Path:
        out = self.reviews_dir / f"{task_id}_{review.get('kind', 'review')}.json"
        out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def _append_timeline(self, record: dict[str, Any]) -> None:
        timeline = self.root / "timeline.jsonl"
        with timeline.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
