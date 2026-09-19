from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class TaskSourceKind(str, Enum):
    SITE = "site"
    FILE = "file"


@dataclass(frozen=True)
class TaskSource:
    kind: TaskSourceKind
    value: str = ""

    @property
    def site_url(self) -> str:
        if self.kind != TaskSourceKind.SITE:
            raise ValueError("Not a site source")
        return self.value

    @property
    def file_path(self) -> Path:
        if self.kind != TaskSourceKind.FILE:
            raise ValueError("Not a file source")
        return Path(self.value)


def resolve_task_source(
    arg: str | None,
    *,
    base_url: str = "https://seosprint.net",
    from_site_flag: bool = False,
) -> TaskSource:
    """Разрешает аргумент: HTML-файл, URL SEOsprint или путь (/earn, earn)."""
    base = base_url.rstrip("/")

    if from_site_flag or arg is None:
        return TaskSource(TaskSourceKind.SITE, f"{base}/earn")

    raw = arg.strip().strip('"').strip("'")
    if not raw:
        return TaskSource(TaskSourceKind.SITE, f"{base}/earn")

    lowered = raw.lower()

    if lowered.startswith("http://") or lowered.startswith("https://"):
        parsed = urlparse(raw)
        if "seosprint" in parsed.netloc:
            path = parsed.path or "/earn"
            if parsed.query:
                return TaskSource(TaskSourceKind.SITE, f"{base}{path}?{parsed.query}")
            return TaskSource(TaskSourceKind.SITE, f"{base}{path}")
        raise ValueError(f"Поддерживаются только URL SEOsprint, получено: {raw}")

    if raw.startswith("/"):
        return TaskSource(TaskSourceKind.SITE, f"{base}{raw}")

    site_paths = {
        "earn",
        "tasks",
        "work",
        "member",
        "index.php",
    }
    if lowered in site_paths or lowered.startswith("earn") or "seosprint" in lowered:
        path = raw if raw.startswith("/") else f"/{raw.split('?')[0]}"
        if "?" in raw:
            path = "/" + raw.lstrip("/")
            return TaskSource(TaskSourceKind.SITE, f"{base}{path}" if path.startswith("/") else f"{base}/{path}")
        return TaskSource(TaskSourceKind.SITE, f"{base}{path}")

    path = Path(raw)
    if path.exists():
        return TaskSource(TaskSourceKind.FILE, str(path.resolve()))

    if path.suffix.lower() in {".html", ".htm", ".xml"}:
        raise FileNotFoundError(
            f"Файл не найден: {path}\n"
            f"Если вы имели в виду страницу SEOsprint, используйте:\n"
            f"  python main.py pipeline --from-site\n"
            f"  python main.py pipeline https://seosprint.net/earn\n"
            f"  python main.py pipeline earn"
        )

    return TaskSource(TaskSourceKind.SITE, f"{base}/earn")
