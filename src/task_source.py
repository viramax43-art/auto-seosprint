from __future__ import annotations

from pathlib import Path

from src.models import SeoTask
from src.parser import parse_task_list_html
from src.seosprint.client import SeosprintClient
from src.seosprint.sources import TaskSource, TaskSourceKind, resolve_task_source


def load_tasks_from_source(
    source: TaskSource,
    *,
    client: SeosprintClient | None = None,
) -> list[SeoTask]:
    if source.kind == TaskSourceKind.FILE:
        html = Path(source.file_path).read_text(encoding="utf-8")
        return parse_task_list_html(html)

    if client is None:
        raise ValueError("Для загрузки с SEOsprint нужен авторизованный client")

    return client.fetch_tasks(source_url=source.site_url)  # pagination внутри fetch_tasks
