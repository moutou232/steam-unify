"""Recover original names from Steam's Chinese store pages.

The local appinfo only stores the current name.  Steam's Chinese store page
still exposes the name Steam itself displays in the Chinese store:

    https://store.steampowered.com/app/<appid>/?l=schinese&cc=cn

For games with an official Chinese name this is Chinese; for games without one
Steam falls back to English.  This is the desired "original name" source for
v2: never a self-built translation and never the v1 state.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import cache, paths, translate
from .metadata import GameInfo
from .state import State

CACHE_PATH = paths.data_dir() / "original_names_zh_cache.json"
CACHE_TTL = 90 * 24 * 3600
ERROR_TTL = 24 * 3600
CJK_RE = re.compile(r"[\u3400-\u9fff]")
SOURCE = "steam_zh_store"

_CACHE_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, dict] | None = None


@dataclass
class OriginalNameResult:
    appid: int
    name: str = ""
    status: str = "ok"
    source: str = SOURCE


@dataclass
class RepairResult:
    pending: set[int] = field(default_factory=set)
    unknown: set[int] = field(default_factory=set)
    repaired: int = 0
    fetched: int = 0
    changed: bool = False


@dataclass
class UndoPreparation:
    tracked_restorable: int = 0
    untracked_restorable: int = 0
    natural_skipped: int = 0
    unknown_skipped: int = 0
    fetched: int = 0
    pending: set[int] = field(default_factory=set)

    @property
    def restorable(self) -> int:
        return self.tracked_restorable + self.untracked_restorable


def _has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def _load_cache() -> dict[str, dict]:
    global _MEMORY_CACHE
    if _MEMORY_CACHE is not None:
        return _MEMORY_CACHE
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    _MEMORY_CACHE = data if isinstance(data, dict) else {}
    return _MEMORY_CACHE


def _save_cache(data: dict[str, dict]) -> None:
    global _MEMORY_CACHE
    _MEMORY_CACHE = data
    path = CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _cache_get(appid: int) -> dict | None:
    with _CACHE_LOCK:
        item = _load_cache().get(str(appid))
    if not isinstance(item, dict):
        return None
    if item.get("source") != SOURCE:
        return None
    age = time.time() - float(item.get("at", 0))
    ttl = CACHE_TTL if item.get("status") == "ok" else ERROR_TTL
    return item if age <= ttl else None


def _cache_put(appid: int, name: str, status: str) -> None:
    with _CACHE_LOCK:
        data = _load_cache()
        data[str(appid)] = {
            "name": name if status == "ok" else "",
            "status": status,
            "source": SOURCE,
            "at": time.time(),
        }
        _save_cache(data)


def _parse_page(text: str) -> str:
    m = re.search(
        r'<div[^>]+id="appHubAppName"[^>]*>(.*?)</div>',
        text, re.I | re.S,
    )
    if not m:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if not m:
        return ""
    name = re.sub(r"<[^>]+>", "", m.group(1))
    name = re.sub(r"\s+on Steam\s*$", "", name, flags=re.I)
    return translate.clean_text(name)


def _fetch_one(appid: int, proxy: str | None, timeout: float) -> OriginalNameResult:
    page_url = (
        f"https://store.steampowered.com/app/{appid}/?l=schinese&cc=cn"
    )
    api_url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&l=schinese&cc=cn"
    )
    last = OriginalNameResult(appid, "", "error:unknown", SOURCE)
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                page_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SteamUnify/2.0",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
                },
            )
            with translate._opener(proxy).open(req, timeout=timeout) as resp:
                raw = resp.read(1_500_000)
            name = _parse_page(raw.decode("utf-8", "replace"))
            if name:
                return OriginalNameResult(appid, name, "ok", SOURCE)
        except Exception as exc:
            code = getattr(exc, "code", "")
            last = OriginalNameResult(
                appid, "", f"error:{type(exc).__name__}:{code}", SOURCE
            )
        try:
            data = translate.http_json(api_url, proxy=proxy, timeout=timeout)
            item = data.get(str(appid), {})
            if item.get("success"):
                name = str((item.get("data") or {}).get("name") or "").strip()
                if name:
                    return OriginalNameResult(appid, name, "ok", SOURCE)
        except Exception as exc:
            code = getattr(exc, "code", "")
            last = OriginalNameResult(
                appid, "", f"error:{type(exc).__name__}:{code}", SOURCE
            )
        if attempt == 0:
            time.sleep(0.6)
    return last


def fetch_store_originals(
    appids: Iterable[int],
    *,
    proxy: str | None = None,
    workers: int = 4,
    timeout: float = 5.0,
    force: bool = False,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[int, OriginalNameResult]:
    ids = sorted({int(a) for a in appids})
    results: dict[int, OriginalNameResult] = {}
    pending: list[int] = []
    for appid in ids:
        cached = _cache_get(appid)
        if cached and (cached.get("status") == "ok" or not force):
            results[appid] = OriginalNameResult(
                appid,
                str(cached.get("name") or ""),
                str(cached.get("status") or "ok"),
                SOURCE,
            )
        else:
            pending.append(appid)
    if not pending:
        return results
    total = len(pending)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = {
            pool.submit(_fetch_one, appid, proxy, timeout): appid
            for appid in pending
        }
        for future in as_completed(futures):
            if should_stop and should_stop():
                for f in futures:
                    f.cancel()
                break
            appid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = OriginalNameResult(
                    appid, "", f"error:{type(exc).__name__}", SOURCE
                )
            results[appid] = result
            _cache_put(appid, result.name, result.status)
            done += 1
            if progress:
                progress(done, total, appid)
    return results


def _verified(rec: dict) -> bool:
    return bool(
        rec.get("orig_verified")
        and rec.get("orig_source") == SOURCE
        and str(rec.get("orig_name") or "").strip()
    )


def _set_verified(rec: dict, name: str) -> None:
    rec["orig_name"] = name.strip()
    rec["orig_source"] = SOURCE
    rec["orig_verified"] = True
    rec["orig_unknown"] = False


def _apply_result(
    rec: dict, game: GameInfo, result: OriginalNameResult
) -> bool:
    if result.status != "ok" or not result.name.strip():
        rec["orig_unknown"] = True
        rec["orig_verified"] = False
        return False
    _set_verified(rec, result.name)
    return True


def _backup_state(name: str) -> None:
    path = paths.data_dir() / "state.json"
    backup = paths.data_dir() / name
    if backup.exists() or not path.is_file():
        return
    try:
        import shutil

        shutil.copy2(path, backup)
    except OSError:
        pass


def repair_state(
    state: State,
    games: list[GameInfo],
    *,
    fetch: bool = False,
    force: bool = False,
    proxy: str | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RepairResult:
    result = RepairResult()
    changed = False
    for game in games:
        rec = state.get_app(game.appid)
        if rec.get("skipped"):
            continue
        if _verified(rec):
            continue
        orig = str(rec.get("orig_name") or "").strip()
        if not orig:
            rec["orig_name"] = ""
            rec["orig_unknown"] = True
            rec["orig_verified"] = False
            changed = True
        else:
            # Keep the old value visible until the Chinese store confirms it,
            # but do not let undo treat it as verified.
            rec["orig_verified"] = False
            rec["orig_source"] = str(rec.get("orig_source") or "")
            changed = True
        result.pending.add(game.appid)
    if not result.pending:
        return result
    if changed:
        _backup_state("state.pre-orig-repair.bak")
    if fetch and result.pending:
        fetched = fetch_store_originals(
            result.pending,
            proxy=proxy,
            force=force,
            progress=progress,
            should_stop=should_stop,
        )
        by_id = {g.appid: g for g in games}
        for appid, item in fetched.items():
            game = by_id.get(appid)
            if not game:
                continue
            rec = state.get_app(appid)
            if _apply_result(rec, game, item):
                result.repaired += 1
            result.fetched += 1
        result.pending = {
            appid for appid in result.pending
            if not _verified(state.get_app(appid))
        }
    result.unknown = set(result.pending)
    if changed or result.fetched:
        state.save()
    result.changed = changed or bool(result.fetched)
    return result


def fetch_missing_originals(
    state: State,
    games: list[GameInfo],
    *,
    force: bool = False,
    proxy: str | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RepairResult:
    return repair_state(
        state,
        games,
        fetch=True,
        force=force,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
    )


def prepare_undo(
    env,
    state: State,
    *,
    proxy: str | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> UndoPreparation:
    """Resolve Chinese-store names for the state and every Chinese appinfo entry."""
    cache_info = cache.AppInfoCache.detect(env)
    root = getattr(env, "root", None)
    cache_info = cache_info.require(root) if root is not None else cache_info.require()
    live: dict[int, str] = {}
    kind = getattr(cache_info, "kind", cache.CacheKind.SHARDS)
    if kind == cache.CacheKind.SHARDS:
        shards = cache_info.load_shards()
        for appid in shards.appids:
            name = str(shards.get_name(appid) or "").strip()
            if name:
                live[appid] = name
    else:
        app_info = cache_info.load_appinfo()
        for entry in app_info.entries:
            name = str(app_info.get_name(entry.appid) or "").strip()
            if name:
                live[entry.appid] = name
    result = UndoPreparation()
    candidates: set[int] = set()

    # Tracked records: include English current names too, because the target is
    # the Steam Chinese store name, which may itself be Chinese.
    for key, rec in state.apps.items():
        try:
            appid = int(key)
        except (TypeError, ValueError):
            continue
        if appid not in live:
            continue
        if rec.get("skipped"):
            continue
        if _verified(rec):
            orig = str(rec.get("orig_name") or "").strip()
            zh = str(rec.get("zh_name") or "").strip()
            if orig and zh and zh != orig:
                if rec.get("cache_entry"):
                    result.untracked_restorable += 1
                else:
                    result.tracked_restorable += 1
            else:
                result.natural_skipped += 1
        else:
            candidates.add(appid)

    # Untracked Chinese names in appinfo: these caused the "partial restore".
    for appid, current in live.items():
        if appid in state.apps:
            continue
        if _has_cjk(current):
            candidates.add(appid)

    if not candidates:
        return result
    fetched = fetch_store_originals(
        candidates,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
    )
    changed = False
    for appid, item in fetched.items():
        current = live.get(appid) or ""
        existed = str(appid) in state.apps
        rec = state.get_app(appid)
        was_cache_entry = bool(rec.get("cache_entry")) or not existed
        result.fetched += 1
        if item.status != "ok" or not item.name.strip():
            rec["orig_unknown"] = True
            rec["orig_verified"] = False
            result.unknown_skipped += 1
            changed = True
            continue
        _set_verified(rec, item.name)
        if item.name.strip() == current:
            rec["zh_name"] = current
            result.natural_skipped += 1
            changed = True
            continue
        rec["zh_name"] = current
        rec["source"] = "original"
        rec["locked"] = True
        rec["applied"] = False
        rec["cache_entry"] = was_cache_entry
        if rec["cache_entry"]:
            result.untracked_restorable += 1
        else:
            result.tracked_restorable += 1
        changed = True
    if changed:
        _backup_state("state.pre-undo-prepare.bak")
        state.save()
    result.pending = {
        appid for appid in candidates
        if not _verified(state.get_app(appid))
    }
    return result
    if changed:
        _backup_state("state.pre-undo-prepare.bak")
        state.save()
    result.pending = {
        appid for appid in candidates
        if not _verified(state.get_app(appid))
    }
    return result
