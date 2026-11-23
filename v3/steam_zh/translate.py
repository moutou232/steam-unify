"""Translate game names to Simplified Chinese using several fallback sources."""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .metadata import GameInfo, DATA_DIR
from .state import State, now_ts

USER_AGENT = (
    "SteamZhName/1.0 (+https://github.com/local; game library name translator)"
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
NAME_STRIP_RE = re.compile(
    r"^[\s\"'“”‘’《》〈〉「」『』\[\]()（）]+|[\s\"'“”‘’《》〈〉「」『』\[\]（）]+$"
)


@dataclass
class Translation:
    name: str
    source: str
    detail: str = ""


def load_dictionary() -> dict[str, str]:
    path = DATA_DIR / "official_names.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str) and v.strip():
            out[str(k)] = v.strip()
    return out


def clean_text(text: str) -> str:
    text = html.unescape(str(text or "")).strip()
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"^\s*\(\s*\d+\s*\)\s*", "", text)
    text = NAME_STRIP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def _opener(proxy: str | None):
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def http_json(url: str, proxy: str | None = None, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    opener = _opener(proxy)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return json.loads(text)


def mymemory(name: str, proxy: str | None = None) -> str | None:
    query = urllib.parse.quote(name)
    url = (
        "https://api.mymemory.translated.net/get"
        f"?q={query}&langpair=en|zh-CN"
    )
    try:
        data = http_json(url, proxy, timeout=12)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    text = ""
    try:
        text = data.get("responseData", {}).get("translatedText", "")
    except AttributeError:
        return None
    cleaned = clean_text(text)
    if not cleaned or "MYMEMORY WARNING" in cleaned.upper():
        return None
    if not _has_cjk(cleaned):
        return None
    if cleaned.lower() == name.lower():
        return None
    return cleaned


def steam_store(appid: int, proxy: str | None = None) -> str | None:
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc=cn&l=schinese"
    )
    try:
        data = http_json(url, proxy, timeout=8)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    item = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("success"):
        return None
    name = item.get("data", {}).get("name")
    if isinstance(name, str) and name.strip():
        return clean_text(name)
    return None


def steam_china(appid: int, proxy: str | None = None) -> str | None:
    url = (
        "https://store.steamchina.com/api/appdetails"
        f"?appids={appid}&cc=cn&l=schinese"
    )
    try:
        data = http_json(url, proxy, timeout=8)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    item = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("success"):
        return None
    name = item.get("data", {}).get("name")
    if isinstance(name, str) and name.strip():
        return clean_text(name)
    return None


def probe(proxy: str | None = None) -> dict[str, bool]:
    """Quickly probe which online sources are reachable."""
    result = {"steam": False, "mymemory": False}
    try:
        if steam_store(413150, proxy):
            result["steam"] = True
    except Exception:
        pass
    try:
        if mymemory("Stardew Valley", proxy):
            result["mymemory"] = True
    except Exception:
        pass
    return result


def translate_one(
    game: GameInfo,
    state: State,
    dictionary: dict[str, str],
    use_network: bool,
    proxy: str | None = None,
    delay: float = 0.6,
) -> Translation | None:
    rec = state.get_app(game.appid)
    if rec.get("locked") and rec.get("zh_name"):
        return None
    if rec.get("zh_name") and rec.get("source") in (
        "official",
        "dictionary",
        "steam",
        "manual",
        "already",
        "",
    ):
        return None
    if rec.get("zh_name") and rec.get("source") == "manual":
        return None

    # A name that is already Chinese needs no translation.
    if game.has_cjk:
        return Translation(game.name, "already", "原名已是中文")

    # 1) official localized name from appinfo
    official = game.official_zh
    if official:
        return Translation(official, "official", "Steam 内置简体中文名")

    # 2) bundled dictionary
    dict_name = dictionary.get(str(game.appid))
    if dict_name:
        return Translation(dict_name, "dictionary", "内置词典")

    if not use_network:
        return None

    # 3) official store / Steam China
    steam = steam_store(game.appid, proxy)
    if not steam:
        steam = steam_china(game.appid, proxy)
    if steam:
        return Translation(steam, "steam", "Steam 商店官方中文名")

    # 4) machine translation
    if delay:
        time.sleep(delay)
    machine = mymemory(game.name, proxy)
    if machine:
        return Translation(machine, "machine", "MyMemory 机翻")
    return None


def translate_games(
    games: list[GameInfo],
    state: State,
    use_network: bool = True,
    proxy: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[int, str]:
    """Fill state translations. Returns {appid: status message}."""
    dictionary = load_dictionary()
    status: dict[int, str] = {}
    total = len(games)
    for i, game in enumerate(games, 1):
        if should_stop and should_stop():
            break
        rec = state.get_app(game.appid)
        if rec.get("zh_name") and (rec.get("locked") or rec.get("source") == "manual"):
            status[game.appid] = "已锁定/手动，跳过"
            if progress:
                progress(i, total, game.name)
            continue
        tr = translate_one(game, state, dictionary, use_network, proxy)
        if tr:
            rec["zh_name"] = tr.name
            rec["source"] = tr.source
            rec["detail"] = tr.detail
            rec["error"] = ""
            rec["translated_at"] = now_ts()
            status[game.appid] = tr.source
        else:
            if not rec.get("zh_name"):
                rec["error"] = "未找到中文名，请手动填写"
                rec["source"] = ""
            status[game.appid] = rec.get("source") or "missing"
        if progress:
            progress(i, total, game.name)
        state.save()
    return status
