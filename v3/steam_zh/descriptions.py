"""Optional Steam store short-description cache for keyword classification.

Local appinfo does not contain store descriptions, so this module is strictly
optional: scanning never blocks on it.  A caller can use `fetch_many()` to
populate the cache when Steam Store/API is reachable (including via a proxy).
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import paths


def cache_path() -> Path:
    return paths.data_dir() / "descriptions.json"


def load_cache() -> dict[str, str]:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for appid, value in data.items():
            if isinstance(value, str):
                out[str(appid)] = value
            elif isinstance(value, dict) and isinstance(value.get("text"), str):
                out[str(appid)] = value["text"]
    return out


def get_description(appid: int) -> str | None:
    return load_cache().get(str(appid))


def save_cache(cache: dict[str, str]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    import os

    os.replace(tmp, path)


def _clean(text: str) -> str:
    text = html.unescape(str(text or "")).strip()
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_one(appid: int, proxy: str | None = None, timeout: float = 10.0) -> str | None:
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc=cn&l=schinese"
    )
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SteamZhName/2.0 (local category metadata)",
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    item = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("success"):
        return None
    info = item.get("data", {})
    text = info.get("short_description") or info.get("detailed_description") or ""
    cleaned = _clean(text)
    return cleaned or None


def fetch_many(
    appids: list[int],
    proxy: str | None = None,
    delay: float = 0.7,
) -> dict[str, str]:
    cache = load_cache()
    changed = False
    for appid in appids:
        key = str(appid)
        if key in cache:
            continue
        text = fetch_one(appid, proxy=proxy)
        if text:
            cache[key] = text
            changed = True
        time.sleep(delay)
    if changed:
        save_cache(cache)
    return cache
