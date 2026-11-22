"""Multi-source name/category evidence providers for Steam 统译 v2.

All network access is best-effort and isolated.  A dead/blocked source must
never prevent the rest from returning.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import category_model, paths, translate
from .metadata import GameInfo
from .state import State

CACHE_PATH = paths.data_dir() / "provider_cache.json"
CACHE_TTL = 30 * 24 * 3600
ERROR_TTL = 24 * 3600
CJK_RE = re.compile(r"[\u3400-\u9fff]")

PRIORITY = {
    "manual": 100,
    "steam_official": 95,
    "dictionary": 90,
    "xiaoheihe": 85,
    "3dm": 80,
    "steam_store": 86,
    "steam_china": 76,
    "steamdb": 60,
    "mymemory": 20,
}

BASE_CONFIDENCE = {
    "manual": 1.0,
    "steam_official": 0.98,
    "dictionary": 0.88,
    "xiaoheihe": 0.9,
    "3dm": 0.85,
    "steam_store": 0.92,
    "steam_china": 0.9,
    "steamdb": 0.78,
    "mymemory": 0.45,
}

LABELS = {
    "steam_official": "Steam 内置中文",
    "steam_store": "Steam 商店",
    "steam_china": "蒸汽平台",
    "steamdb": "SteamDB",
    "xiaoheihe": "小黑盒",
    "3dm": "3DM",
    "dictionary": "内置词典",
    "mymemory": "MyMemory 机翻",
    "manual": "手动选择",
}

DEFAULT_SOURCE_ORDER = [
    "xiaoheihe",
    "steam_official",
    "dictionary",
    "steam_store",
    "3dm",
    "steam_china",
    "steamdb",
    "mymemory",
]


@dataclass
class NameCandidate:
    source: str
    name: str
    confidence: float
    url: str = ""
    status: str = "ok"
    detail: str = ""
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": LABELS.get(self.source, self.source),
            "name": self.name,
            "confidence": round(self.confidence, 3),
            "url": self.url,
            "status": self.status,
            "detail": self.detail,
            "score": round(self.score, 3),
        }


@dataclass
class CategoryEvidence:
    source: str
    labels: list[str]
    confidence: float
    url: str = ""
    status: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": LABELS.get(self.source, self.source),
            "labels": self.labels,
            "confidence": round(self.confidence, 3),
            "url": self.url,
            "status": self.status,
        }


@dataclass
class NameResolution:
    candidates: list[NameCandidate] = field(default_factory=list)
    selected: NameCandidate | None = None
    errors: list[str] = field(default_factory=list)


_CACHE_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, Any] | None = None


def _load_cache() -> dict[str, Any]:
    global _MEMORY_CACHE
    if _MEMORY_CACHE is not None:
        return _MEMORY_CACHE
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    _MEMORY_CACHE = data if isinstance(data, dict) else {}
    return _MEMORY_CACHE


def _save_cache(data: dict[str, Any]) -> None:
    global _MEMORY_CACHE
    _MEMORY_CACHE = data
    path = CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    import os

    os.replace(tmp, path)


def _cache_key(appid: int, kind: str, source: str) -> str:
    return f"{appid}:{kind}:{source}"


def _cache_get(appid: int, kind: str, source: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        data = _load_cache()
        item = data.get(_cache_key(appid, kind, source))
    if not isinstance(item, dict):
        return None
    age = time.time() - float(item.get("at", 0))
    ttl = ERROR_TTL if item.get("status") != "ok" else CACHE_TTL
    if age > ttl:
        return None
    # The old Xiaoheihe search endpoint is retired.  Do not let its cached
    # failures suppress the new appid-detail implementation for 24 hours.
    if source == "xiaoheihe" and item.get("status") != "ok":
        return None
    return item


def _cache_put(appid: int, kind: str, source: str, value: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        data = _load_cache()
        value = dict(value)
        value["at"] = time.time()
        data[_cache_key(appid, kind, source)] = value
        _save_cache(data)


def _text(
    url: str,
    proxy: str | None = None,
    timeout: float = 5.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SteamUnify/2.0",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Accept": "text/html,application/json,*/*",
    }
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, headers=base_headers)
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(2_000_000)
        enc = resp.headers.get_content_charset() or "utf-8"
        try:
            return resp.status, raw.decode(enc, "replace")
        except LookupError:
            return resp.status, raw.decode("utf-8", "replace")


def _clean_name(text: str) -> str:
    text = translate.clean_text(text)
    return text if CJK_RE.search(text) else ""


def _similarity(a: str, b: str) -> float:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


def _source_quality(source: str, game: GameInfo, name: str) -> float:
    q = 0.7
    if name == (game.official_zh or ""):
        q = 1.0
    elif _similarity(name, game.name) > 0.6:
        q = 0.75
    if source in ("steam_official", "dictionary", "manual"):
        q = max(q, 0.95)
    return q


def _make_candidate(source: str, game: GameInfo, name: str, url: str = "", detail: str = "") -> NameCandidate | None:
    name = _clean_name(name)
    if not name:
        return None
    q = _source_quality(source, game, name)
    confidence = BASE_CONFIDENCE.get(source, 0.4) * q
    score = confidence * 100 + PRIORITY.get(source, 0)
    return NameCandidate(source, name, confidence, url, "ok", detail, score)


def _local_candidates(game: GameInfo, dictionary: dict[str, str]) -> list[NameCandidate]:
    out: list[NameCandidate] = []
    official = game.official_zh
    if official:
        c = _make_candidate("steam_official", game, official,
                            f"https://store.steampowered.com/app/{game.appid}/",
                            "appinfo name_localized")
        if c:
            out.append(c)
    dict_name = dictionary.get(str(game.appid))
    if dict_name:
        c = _make_candidate("dictionary", game, dict_name, "", "内置词典")
        if c:
            out.append(c)
    if game.has_cjk:
        c = _make_candidate("steam_official", game, game.name,
                            f"https://store.steampowered.com/app/{game.appid}/",
                            "原名已是中文")
        if c:
            out.append(c)
    return out


def _fetch_steamdb(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    url = f"https://steamdb.info/app/{game.appid}/"
    try:
        status, text = _text(url, proxy, timeout)
    except urllib.error.HTTPError as exc:
        return {"status": f"http {exc.code}", "candidate": None, "url": url}
    except Exception as exc:
        return {"status": type(exc).__name__, "candidate": None, "url": url}
    if status >= 400:
        return {"status": f"http {status}", "candidate": None, "url": url}
    # SteamDB is mainly a reference; only accept an explicit CJK name in the
    # page title/og:title, never an English title as a Chinese translation.
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', text, re.I)
    if not m:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    name = _clean_name(m.group(1) if m else "")
    if not name:
        return {"status": "no-cjk", "candidate": None, "url": url}
    return {"status": "ok", "candidate": {"name": name}, "url": url}


def _parse_3dm(game: GameInfo, text: str, url: str) -> NameCandidate | None:
    candidates: list[tuple[float, str, str]] = []
    blocked = ("3dmgame", "登录", "注册", "商城", "论坛", "首页", "专题", "下载", "攻略", "新闻")
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        href = m.group(1)
        label = re.sub(r"<[^>]+>", "", m.group(2))
        label = translate.clean_text(label)
        if not CJK_RE.search(label):
            continue
        if any(word in label for word in blocked):
            continue
        # Only real game pages; without this, site navigation/ads like
        # "关于3DMGAME" become false translations.
        if "/games/" not in href:
            continue
        score = _similarity(label, game.name) + 0.4
        candidates.append((score, label, href))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, name, href = candidates[0]
    if href.startswith("/"):
        href = "https://www.3dmgame.com" + href
    return _make_candidate("3dm", game, name, href, "3DM 游戏页")


def _fetch_3dm(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    url = "https://so.3dmgame.com/?keyword=" + urllib.parse.quote(game.name)
    try:
        status, text = _text(url, proxy, timeout)
    except Exception as exc:
        return {"status": type(exc).__name__, "candidate": None, "url": url}
    if status >= 400:
        return {"status": f"http {status}", "candidate": None, "url": url}
    c = _parse_3dm(game, text, url)
    return {"status": "ok" if c else "no-match",
            "candidate": {"name": c.name, "url": c.url} if c else None,
            "url": url}


def _fetch_xiaoheihe(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    """Read the public Xiaoheihe game name by Steam appid.

    The old /bbs/app/api/general/search/v1 endpoint now requires signed
    hkey/_time/nonce parameters and returns {"status": "failed"}.  The public
    game detail page accepts a Steam appid plus a timestamp and needs no login.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Referer": "https://www.xiaoheihe.cn/",
        "Origin": "https://www.xiaoheihe.cn",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    stamp = int(time.time())
    url = (
        "https://api.xiaoheihe.cn/game/web/get_game_detail"
        f"?appid={game.appid}&_time={stamp}"
    )
    last_status = "no-result"
    try:
        status, text = _text(url, proxy, timeout, headers=headers)
    except Exception as exc:
        status, text = 0, ""
        last_status = type(exc).__name__
    if status and status < 400:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            last_status = "bad-json"
        else:
            result = data.get("result") if isinstance(data, dict) else None
            if isinstance(result, dict):
                names = [result.get("name")]
                topic = result.get("topic_detail")
                if isinstance(topic, dict):
                    names.append(topic.get("name"))
                    topic_game = topic.get("game")
                    if isinstance(topic_game, dict):
                        names.append(topic_game.get("name"))
                for raw in names:
                    name = _clean_name(str(raw or ""))
                    if name:
                        return {
                            "status": "ok",
                            "candidate": {"name": name, "url": url},
                            "url": url,
                        }
                last_status = "no-cjk"
            else:
                last_status = "no-result"
    elif status >= 400:
        last_status = f"http {status}"

    # Public share page fallback: its <title> contains the localized name.
    share_url = (
        "https://api.xiaoheihe.cn/game/get_game_detail_share/"
        f"?steam_appid={game.appid}"
    )
    try:
        status, text = _text(share_url, proxy, timeout, headers=headers)
    except Exception as exc:
        return {"status": type(exc).__name__, "candidate": None, "url": share_url}
    if status >= 400:
        return {"status": f"http {status}", "candidate": None, "url": share_url}
    raw = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                  text, re.I | re.S)
    if m:
        raw = m.group(1)
    if not raw:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if m:
            raw = m.group(1)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = re.sub(r"\s*[-|—]\s*小黑盒.*$", "", raw).strip()
    name = _clean_name(raw)
    if name:
        return {
            "status": "ok",
            "candidate": {"name": name, "url": share_url},
            "url": share_url,
        }
    return {"status": last_status if last_status != "no-result" else "no-cjk",
            "candidate": None, "url": share_url}


def _fetch_steam_china(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    url = f"https://store.steampowered.com/app/{game.appid}/"
    name = translate.steam_china(game.appid, proxy)
    if name:
        return {"status": "ok", "candidate": {"name": name, "url": url}, "url": url}
    return {"status": "no-result", "candidate": None, "url": url}


def _fetch_steam_store(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    url = f"https://store.steampowered.com/app/{game.appid}/"
    name = translate.steam_store(game.appid, proxy)
    if name:
        return {"status": "ok", "candidate": {"name": name, "url": url}, "url": url}
    name = translate.steam_china(game.appid, proxy)
    if name:
        return {"status": "ok", "candidate": {"name": name, "url": url}, "url": url}
    return {"status": "no-result", "candidate": None, "url": url}


def _fetch_mymemory(game: GameInfo, proxy: str | None, timeout: float) -> dict[str, Any]:
    query = urllib.parse.quote(game.name)
    url = (
        "https://api.mymemory.translated.net/get"
        f"?q={query}&langpair=en|zh-CN"
    )
    try:
        data = translate.http_json(url, proxy, timeout=timeout)
        name = str(data.get("responseData", {}).get("translatedText", ""))
    except Exception:
        return {"status": "error", "candidate": None, "url": ""}
    name = _clean_name(name)
    if name and "MYMEMORY WARNING" not in name.upper():
        return {"status": "ok", "candidate": {"name": name}, "url": ""}
    return {"status": "no-result", "candidate": None, "url": ""}


PROVIDER_FUNCS: dict[str, Callable] = {
    "steamdb": _fetch_steamdb,
    "3dm": _fetch_3dm,
    "xiaoheihe": _fetch_xiaoheihe,
    "steam_store": _fetch_steam_store,
    "steam_china": _fetch_steam_china,
    "mymemory": _fetch_mymemory,
}


def normalize_priority_order(value: Any) -> list[str]:
    """Return a clean user source order, filling missing defaults at the end."""
    known = [s for s in DEFAULT_SOURCE_ORDER]
    supplied: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            source = str(item).strip()
            if source in known and source not in supplied:
                supplied.append(source)
    for source in known:
        if source not in supplied:
            supplied.append(source)
    return supplied


def _priority_rank(order: list[str]) -> dict[str, int]:
    rank = {source: 1000 - index for index, source in enumerate(order)}
    rank["manual"] = 2000
    return rank


def order_name_candidates(
    candidates: list[NameCandidate],
    priority_order: list[str] | None = None,
) -> list[NameCandidate]:
    """Order candidates by the user's source priority, then confidence."""
    if priority_order is None:
        priority_order = DEFAULT_SOURCE_ORDER
    order = normalize_priority_order(priority_order)
    rank = _priority_rank(order)

    def key(c: NameCandidate):
        return (-rank.get(c.source, 0), -c.confidence, -c.score)

    by_name: dict[str, NameCandidate] = {}
    for c in candidates:
        name_key = c.name.casefold()
        old = by_name.get(name_key)
        if old is None or key(c) < key(old):
            by_name[name_key] = c
    return sorted(by_name.values(), key=key)


def reselect_cached_candidates(
    state: State,
    priority_order: list[str] | None = None,
) -> int:
    """Re-select existing automatic names from cached candidates only."""
    order = normalize_priority_order(priority_order)
    rank = _priority_rank(order)
    changed = 0
    for rec in state.apps.values():
        if rec.get("skipped") or rec.get("locked"):
            continue
        if str(rec.get("name_selected_by") or "") == "manual":
            continue
        raw = rec.get("name_candidates")
        if not isinstance(raw, list) or not raw:
            continue
        candidates: list[NameCandidate] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "")
            name = _clean_name(str(item.get("name") or ""))
            if not source or not name:
                continue
            candidates.append(NameCandidate(
                source=source,
                name=name,
                confidence=float(item.get("confidence") or 0),
                url=str(item.get("url") or ""),
                status=str(item.get("status") or "ok"),
                detail=str(item.get("detail") or ""),
                score=float(item.get("score") or 0),
            ))
        if not candidates:
            continue

        def key(c: NameCandidate):
            return (-rank.get(c.source, 0), -c.confidence, -c.score)

        best = sorted(candidates, key=key)[0]
        if (str(rec.get("zh_name") or "") != best.name
                or str(rec.get("source") or "") != best.source):
            rec["zh_name"] = best.name
            rec["source"] = best.source
            rec["detail"] = best.detail or LABELS.get(best.source, best.source)
            rec["source_url"] = best.url
            rec["name_confidence"] = best.confidence
            rec["name_selected_by"] = "auto"
            changed += 1
    if changed:
        state.save()
    return changed


def fetch_name_candidates(
    game: GameInfo,
    *,
    network: bool = True,
    proxy: str | None = None,
    timeout: float = 5.0,
    sources: list[str] | None = None,
    priority_order: list[str] | None = None,
) -> NameResolution:
    dictionary = translate.load_dictionary()
    candidates = _local_candidates(game, dictionary)
    errors: list[str] = []
    wanted = sources or (["steamdb", "3dm", "xiaoheihe", "steam_store", "mymemory"] if network else [])
    if network:
        def run(source: str) -> tuple[str, dict[str, Any]]:
            cached = _cache_get(game.appid, "name", source)
            if cached:
                return source, cached
            try:
                result = PROVIDER_FUNCS[source](game, proxy, timeout)
            except Exception as exc:
                result = {"status": type(exc).__name__, "candidate": None, "url": ""}
            _cache_put(game.appid, "name", source, result)
            return source, result

        with ThreadPoolExecutor(max_workers=min(4, len(wanted))) as pool:
            futures = [pool.submit(run, s) for s in wanted if s in PROVIDER_FUNCS]
            deadline = time.monotonic() + max(timeout + 1.0, 8.0)
            try:
                for future in as_completed(
                    futures, timeout=max(0.1, deadline - time.monotonic())
                ):
                    try:
                        source, result = future.result()
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                        continue
                    status = str(result.get("status") or "")
                    payload = result.get("candidate")
                    url = str(result.get("url") or "")
                    if isinstance(payload, dict) and payload.get("name"):
                        c = _make_candidate(
                            source, game, str(payload["name"]), url or str(payload.get("url") or ""),
                            status,
                        )
                        if c:
                            candidates.append(c)
                    if status not in ("ok",):
                        errors.append(f"{source}: {status}")
            except TimeoutError:
                errors.append("部分来源超时，已使用已返回结果")
            finally:
                for future in futures:
                    future.cancel()

    unique = order_name_candidates(candidates, priority_order)
    selected = unique[0] if unique else None
    return NameResolution(unique, selected, errors)


def candidate_dicts(resolution: NameResolution) -> list[dict[str, Any]]:
    return [c.as_dict() for c in resolution.candidates]


def fetch_source_candidate(
    game: GameInfo,
    source: str,
    *,
    network: bool = True,
    proxy: str | None = None,
    timeout: float = 5.0,
    dictionary: dict[str, str] | None = None,
) -> tuple[str, NameCandidate | None]:
    """Fetch one source for one game.  Used by phase-ordered translation."""
    if source == "steam_official":
        raw = game.official_zh or (game.name if game.has_cjk else "")
        candidate = _make_candidate(
            "steam_official", game, raw,
            f"https://store.steampowered.com/app/{game.appid}/",
            "appinfo name_localized" if game.official_zh else "原名已是中文",
        )
        return ("ok" if candidate else "no-result", candidate)
    if source == "dictionary":
        dictionary = dictionary if dictionary is not None else translate.load_dictionary()
        candidate = _make_candidate(
            "dictionary", game, dictionary.get(str(game.appid), ""), "", "内置词典"
        )
        return ("ok" if candidate else "no-result", candidate)
    if not network:
        return "network-disabled", None
    if source not in PROVIDER_FUNCS:
        return "unsupported", None

    cached = _cache_get(game.appid, "name", source)
    if cached:
        payload = cached.get("candidate")
        status = str(cached.get("status") or "")
        if isinstance(payload, dict) and payload.get("name"):
            candidate = _make_candidate(
                source, game, str(payload["name"]),
                str(cached.get("url") or payload.get("url") or ""),
                LABELS.get(source, source),
            )
            return ("ok" if candidate else "no-cjk"), candidate
        return status or "no-result", None

    try:
        result = PROVIDER_FUNCS[source](game, proxy, timeout)
    except Exception as exc:
        result = {"status": type(exc).__name__, "candidate": None, "url": ""}
    _cache_put(game.appid, "name", source, result)
    status = str(result.get("status") or "")
    payload = result.get("candidate")
    if isinstance(payload, dict) and payload.get("name"):
        candidate = _make_candidate(
            source, game, str(payload["name"]),
            str(result.get("url") or payload.get("url") or ""),
            LABELS.get(source, source),
        )
        return ("ok" if candidate else "no-cjk"), candidate
    return status or "no-result", None


def translate_games_v2(
    games: list[GameInfo],
    state: State,
    *,
    use_network: bool = True,
    proxy: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    timeout: float = 5.0,
    force: bool = False,
    priority_order: list[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """Resolve names source-by-source in the user's priority order.

    Source-major batching is deliberate: every source gets a full pass over the
    games that are still unresolved, so one slow/parallel source cannot starve
    the others.  Once a game gets a candidate, lower-priority sources are not
    queried for that game.
    """
    status: dict[int, str] = {}
    eligible: list[GameInfo] = []
    for game in games:
        rec = state.get_app(game.appid)
        if rec.get("skipped"):
            status[game.appid] = "skipped"
            continue
        if rec.get("locked") and rec.get("zh_name") and not force:
            status[game.appid] = "locked"
            continue
        if rec.get("zh_name") and not force and str(rec.get("source") or "") not in ("", "original"):
            status[game.appid] = str(rec.get("source") or "existing")
            continue
        eligible.append(game)
    if not eligible:
        return status

    dictionary = translate.load_dictionary()
    order = normalize_priority_order(priority_order)
    resolved: set[int] = set()
    errors_by_appid: dict[int, list[str]] = {}
    source_hits: dict[str, int] = {}
    interrupted = False
    dirty = 0
    if log:
        log("自动翻译开始：按优先级逐来源检索："
            + " → ".join(LABELS.get(s, s) for s in order))

    for index, source in enumerate(order, 1):
        pending = [g for g in eligible if g.appid not in resolved]
        if not pending:
            break
        if should_stop and should_stop():
            interrupted = True
            break
        label = LABELS.get(source, source)
        if source not in ("steam_official", "dictionary") and not use_network:
            if log:
                log(f"[{index}/{len(order)}] {label}：已跳过（未启用联网）")
            continue
        if source not in ("steam_official", "dictionary") and source not in PROVIDER_FUNCS:
            if log:
                log(f"[{index}/{len(order)}] {label}：无可用接口，已跳过")
            continue
        if log:
            log(f"[{index}/{len(order)}] {label}：待查 {len(pending)} 个游戏")

        results: dict[int, NameCandidate] = {}
        failures = 0
        completed = 0

        def fetch_one(game: GameInfo) -> tuple[int, str, NameCandidate | None]:
            if should_stop and should_stop():
                return game.appid, "cancelled", None
            code, candidate = fetch_source_candidate(
                game, source, network=use_network, proxy=proxy,
                timeout=timeout, dictionary=dictionary,
            )
            return game.appid, code, candidate

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_one, g): g for g in pending}
            try:
                for future in as_completed(futures):
                    game = futures[future]
                    try:
                        appid, code, candidate = future.result()
                    except Exception as exc:
                        appid, code, candidate = game.appid, type(exc).__name__, None
                    completed += 1
                    if candidate is not None:
                        results[appid] = candidate
                    else:
                        code_text = code or "no-result"
                        errors_by_appid.setdefault(appid, []).append(f"{source}: {code_text}")
                        if code_text not in ("no-result", "no-cjk", "network-disabled"):
                            failures += 1
                    if progress:
                        progress(completed, len(pending), f"{label} {completed}/{len(pending)}")
                    if log and (completed % 25 == 0 or completed == len(pending)):
                        log(f"[{index}/{len(order)}] {label}：已处理 "
                            f"{completed}/{len(pending)}，命中 {len(results)}")
                    if should_stop and should_stop():
                        interrupted = True
                        for f in futures:
                            f.cancel()
                        break
            finally:
                for f in futures:
                    f.cancel()

        for game in pending:
            candidate = results.get(game.appid)
            if candidate is None:
                continue
            rec = state.get_app(game.appid)
            rec["zh_name"] = candidate.name
            rec["source"] = candidate.source
            rec["detail"] = candidate.detail or LABELS.get(candidate.source, candidate.source)
            rec["source_url"] = candidate.url
            rec["name_selected_source"] = candidate.source
            rec["name_confidence"] = candidate.confidence
            rec["name_selected_by"] = "auto"
            rec["name_candidates"] = [candidate.as_dict()]
            rec["name_provider_errors"] = list(errors_by_appid.get(game.appid, []))
            rec["error"] = ""
            status[game.appid] = candidate.source
            resolved.add(game.appid)
            dirty += 1
        source_hits[source] = len(results)
        if log:
            log(f"[{index}/{len(order)}] {label}：完成，命中 {len(results)}，"
                f"剩余 {len(pending) - len(results)}，失败 {failures}")
        if dirty >= 20:
            state.save()
            dirty = 0
        if interrupted:
            break

    if not interrupted:
        for game in eligible:
            if game.appid in resolved:
                continue
            rec = state.get_app(game.appid)
            rec["error"] = "所有来源均未找到中文名，请手动填写"
            rec["name_provider_errors"] = list(errors_by_appid.get(game.appid, []))
            status[game.appid] = "missing"
    state.save()
    if log:
        remaining = sum(1 for g in eligible if g.appid not in resolved)
        hits = "，".join(
            f"{LABELS.get(s, s)} {n}" for s, n in source_hits.items() if n
        ) or "无"
        if interrupted:
            log(f"自动翻译已取消：已命中 {len(resolved)}，未完成 {remaining}")
        else:
            log(f"自动翻译完成：{hits}；未找到 {remaining}")
    return status


def load_category_evidence(
    game: GameInfo,
    *,
    network: bool = False,
    proxy: str | None = None,
    timeout: float = 5.0,
) -> list[CategoryEvidence]:
    """Return cached/available category evidence from community sources."""
    if not network:
        cached = []
        for source in ("3dm", "xiaoheihe", "steamdb"):
            item = _cache_get(game.appid, "category", source)
            if isinstance(item, dict) and item.get("labels"):
                cached.append(CategoryEvidence(
                    source, [str(x) for x in item["labels"]],
                    float(item.get("confidence", 0.5)),
                    str(item.get("url", "")),
                    str(item.get("status", "ok")),
                ))
        return cached
    # Best-effort: find the 3DM game page, then match known category names.
    search_url = "https://so.3dmgame.com/?keyword=" + urllib.parse.quote(game.name)
    try:
        status, text = _text(search_url, proxy, timeout)
    except Exception:
        return []
    if status >= 400:
        return []
    page_url = ""
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        href = m.group(1)
        label = translate.clean_text(re.sub(r"<[^>]+>", "", m.group(2)))
        if "/games/" in href and CJK_RE.search(label):
            page_url = href if not href.startswith("/") else "https://www.3dmgame.com" + href
            break
    if not page_url:
        return []
    try:
        status, text = _text(page_url, proxy, timeout)
    except Exception:
        return []
    if status >= 400:
        return []
    labels = [c for c in category_model.category_names() if c in text]
    if not labels:
        return []
    evidence = CategoryEvidence("3dm", labels[:3], 0.5, page_url, "ok")
    _cache_put(game.appid, "category", "3dm", {
        "labels": evidence.labels,
        "confidence": evidence.confidence,
        "url": page_url,
        "status": "ok",
    })
    return [evidence]
