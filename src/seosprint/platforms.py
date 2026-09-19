from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

CHECKABLE_PLATFORMS = frozenset(
    {"youtube", "telegram", "vk", "facebook", "instagram", "linkedin", "viber"}
)

PLATFORM_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "telegram": "Telegram",
    "vk": "VK",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "viber": "Viber",
    "google": "Google (Gmail)",
}

PLATFORM_COOKIE_DOMAINS: dict[str, list[str]] = {
    "youtube": [".youtube.com", "youtube.com"],
    "telegram": [".telegram.org", "web.telegram.org"],
    "vk": [".vk.com", ".vk.ru"],
    "facebook": [".facebook.com", "facebook.com"],
    "instagram": [".instagram.com", "instagram.com"],
    "linkedin": [".linkedin.com", "linkedin.com"],
    "viber": [".viber.com", "viber.com"],
}

PLATFORM_PATTERNS: dict[str, list[str]] = {
    "youtube": ["youtube.com", "youtu.be"],
    "telegram": ["t.me", "telegram.me", "telegram.org"],
    "vk": ["vk.com", "vk.ru"],
    "facebook": ["facebook.com", "fb.com"],
    "instagram": ["instagram.com"],
    "linkedin": ["linkedin.com"],
    "viber": ["viber.com", "invite.viber.com"],
    "twitter": ["twitter.com", "x.com"],
}

LOGIN_HINTS: dict[str, list[str]] = {
    "youtube": ["войти", "sign in", "login"],
    "telegram": ["log in", "start messaging"],
    "vk": ["войти", "log in"],
    "facebook": ["log in", "войти", "create new account"],
    "instagram": ["log in", "войти", "sign up"],
    "linkedin": ["sign in", "join now"],
}


def platform_for_url(url: str) -> str | None:
    lowered = (url or "").lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if platform not in CHECKABLE_PLATFORMS:
            continue
        if any(p in lowered for p in patterns):
            return platform
    return None


def detect_platforms(text: str) -> list[str]:
    lowered = (text or "").lower()
    found: list[str] = []
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(p in lowered for p in patterns):
            found.append(platform)
    tag_map = {
        "youtube": "youtube",
        "вконтакте": "vk",
        "facebook": "facebook",
        "telegram": "telegram",
        "прочие соцсети": "social",
    }
    for tag, platform in tag_map.items():
        if tag in lowered and platform not in found:
            found.append(platform)
    return found


def platform_check_url(platform: str) -> str:
    urls = {
        "youtube": "https://www.youtube.com/",
        "telegram": "https://web.telegram.org/",
        "vk": "https://vk.com/",
        "facebook": "https://www.facebook.com/",
        "instagram": "https://www.instagram.com/",
        "linkedin": "https://www.linkedin.com/feed/",
        "viber": "https://www.viber.com/",
    }
    return urls.get(platform, "")


def is_platform_logged_in(page: Page, platform: str) -> bool:
    content = page.content().lower()
    hints = LOGIN_HINTS.get(platform, ["sign in", "log in", "войти"])
    logged_markers = {
        "youtube": ["avatar-btn", "ytd-topbar-menu-button-renderer", "guide-button"],
        "telegram": ["chatlist", "sidebar", "im_page_wrap"],
        "vk": ["top_profile_link", "left_menu", "page_header"],
        "facebook": ["aria-label=\"аккаунт\"", "aria-label=\"account\"", "fbxwelcome"],
        "instagram": ["svg[aria-label=\"главная\"]", "role=\"main\""],
        "linkedin": ["global-nav__me", "feed-identity-module"],
    }
    markers = logged_markers.get(platform, [])
    if any(m.lower() in content for m in markers):
        return True
    # Если явных форм входа нет — считаем, что сессия есть
    if not any(h in content for h in hints):
        return True
    return False


def missing_sessions(required: list[str], available: dict[str, bool]) -> list[str]:
    return [p for p in required if p in CHECKABLE_PLATFORMS and not available.get(p, False)]


def filter_checkable_platforms(platforms: list[str]) -> list[str]:
    return [p for p in platforms if p in CHECKABLE_PLATFORMS]


def format_platforms(platforms: list[str]) -> str:
    return ", ".join(PLATFORM_LABELS.get(p, p) for p in platforms)


def required_platforms_for_task(title: str, description: str, *, site: str = "") -> list[str]:
    text = f"{title} {description} {site}".lower()
    found = detect_platforms(text)
    if "gmail" in text or "google" in text and "поиск" not in text:
        if "google" not in found:
            found.append("google")
    return found


def has_session_cookies(context: BrowserContext, platform: str) -> bool:
    domains = PLATFORM_COOKIE_DOMAINS.get(platform, [])
    if not domains:
        return False
    try:
        cookies = context.cookies()
    except Exception:
        return False
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if not any(d in domain or domain.endswith(d.lstrip(".")) for d in domains):
            continue
        name = cookie.get("name", "").lower()
        if platform == "youtube" and name in {"sid", "ssid", "hsid", "login_info", "__secure-1psid"}:
            return True
        if platform == "telegram" and name in {"stel_ssid", "stel_token", "stel_dt"}:
            return True
        if platform == "vk" and name in {"remixsid", "remixstid"}:
            return True
        if platform == "facebook" and name in {"c_user", "xs", "datr"}:
            return True
        if platform == "instagram" and name in {"sessionid", "ds_user_id"}:
            return True
        if platform == "linkedin" and name in {"li_at", "JSESSIONID"}:
            return True
        if cookie.get("value"):
            return True
    return False


def check_session(context: BrowserContext, platform: str, *, navigate: bool = True) -> bool:
    if platform not in CHECKABLE_PLATFORMS:
        return True
    if has_session_cookies(context, platform):
        return True
    if not navigate:
        return False
    url = platform_check_url(platform)
    if not url:
        return False
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        return is_platform_logged_in(page, platform)
    except Exception:
        return False
    finally:
        try:
            page.close()
        except Exception:
            pass


def check_sessions(
    context: BrowserContext,
    platforms: list[str],
    *,
    navigate: bool = True,
    cache: dict[str, bool] | None = None,
) -> dict[str, bool]:
    result: dict[str, bool] = dict(cache or {})
    for platform in filter_checkable_platforms(platforms):
        if platform in result:
            continue
        result[platform] = check_session(context, platform, navigate=navigate)
    return result
