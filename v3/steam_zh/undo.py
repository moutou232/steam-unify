"""One-click “undo everything” for names and categories.

The original English names/localizations live in state.json (``orig_name`` and
``orig_localized``).  Categories can always be reset to an empty list.  Before
touching Steam this module creates a full safety backup through apply.backup().
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import appinfo, apply, cache, category_state, collections, steamenv, vdftext
from .appinfo_shards import ShardSet
from .state import State


@dataclass
class UndoResult:
    backup: Path | None = None
    names_restored: int = 0
    names_skipped: int = 0
    categories_cleared: int = 0
    collections_deleted: int = 0
    warning: list[str] = field(default_factory=list)
    steam_restarted: bool = False


def undo_all(
    env: steamenv.SteamEnv,
    state: State,
    games: list,
    *,
    mode: str = "both",
    restart: bool = True,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> UndoResult:
    if mode not in ("names", "categories", "both"):
        raise ValueError(f"unknown undo mode: {mode}")
    result = UndoResult()
    need_appinfo = mode != "categories"
    cache_info = cache.AppInfoCache.detect(env)
    if need_appinfo:
        cache_info.require(env.root)
    snapshot = cache.CacheSnapshot.capture(cache_info) if need_appinfo else None
    apply.preflight_writable(env, include_appinfo=need_appinfo)
    was_running = steamenv.steam_running()
    result.backup = apply.backup(env, state, steamenv.installed_apps(env.root))
    if log:
        log(f"复原前已备份到 {result.backup}")
    if was_running:
        if log:
            log("Steam 正在运行，正在自动关闭 ...")
        try:
            steamenv.close_steam(env, log=log)
            time.sleep(2.0)
            if snapshot is not None:
                snapshot.restore_if_missing(env, result.backup)
        except Exception:
            if not steamenv.steam_running():
                try:
                    steamenv.start_steam(env)
                except Exception:
                    pass
            raise

    apply.unlock_all_shards(env.shard_dir)
    try:
        appids = sorted(int(a) for a in state.apps.keys())
        total = max(1, len(appids))
        ss = snapshot.shards if snapshot else None
        ai = snapshot.app_info if snapshot else None
        changed_appinfo = False

        for index, appid in enumerate(appids, 1):
            rec = state.get_app(appid)
            if rec.get("skipped"):
                continue
            orig = str(rec.get("orig_name") or "").strip()
            zh = str(rec.get("zh_name") or "").strip()
            verified = bool(rec.get("orig_verified"))
            cats = category_state.get_categories(rec)
            if cats and mode != "names":
                result.categories_cleared += 1

            if mode != "categories" and orig and verified:
                exists = (appid in ss.by_appid) if ss is not None else bool(ai.entry(appid))
                if exists:
                    current = (ss.get_name(appid) if ss else ai.get_name(appid)) or ""
                    modified = bool(rec.get("applied") or zh or (current and current != orig))
                    if modified:
                        if current != orig:
                            if ss:
                                ss.set_name(appid, orig)
                            else:
                                ai.set_name(appid, orig)
                            changed_appinfo = True
                            result.names_restored += 1
                        mapping = rec.get("orig_localized")
                        if isinstance(mapping, dict) and mapping:
                            if ss:
                                ss.restore_localized(appid, mapping)
                            else:
                                ai.restore_localized(appid, mapping)
                            changed_appinfo = True
                        # State now represents the original English name.  Keeping
                        # zh_name non-empty and locked prevents auto translation from
                        # immediately filling it again.
                        rec["zh_name"] = orig
                        rec["source"] = "original"
                        rec["detail"] = "已一键复原"
                        rec["orig_unknown"] = False
                        rec["orig_verified"] = True
                        rec["locked"] = True
                        rec["applied"] = False
                        rec.pop("translated_at", None)
                        rec.pop("applied_at", None)
            else:
                if mode != "categories":
                    result.names_skipped += 1
                    rec["orig_unknown"] = True

            if mode != "names":
                category_state.set_categories(rec, [], source="undo", locked=True)
            if progress:
                progress(f"恢复 {index}/{total}")
            if log and index % 40 == 0:
                log(f"恢复进度 {index}/{total}")

        if changed_appinfo:
            def write_appinfo() -> None:
                if ss:
                    ss.save()
                else:
                    assert ai is not None
                    data = ai.build()
                    target = cache_info.appinfo_path
                    assert target is not None
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_name(target.name + ".tmp")
                    with open(tmp, "wb") as f:
                        f.write(data)
                        f.flush()
                        import os
                        os.fsync(f.fileno())
                    os.replace(tmp, target)

            apply._retry_write(write_appinfo)

        # Restore ACF names as well (cosmetic, but keeps library consistent).
        installed = steamenv.installed_apps(env.root)
        for appid in appids:
            if mode == "categories":
                continue
            rec = state.get_app(appid)
            orig = str(rec.get("orig_name") or "").strip()
            info = installed.get(appid)
            acf = info.get("acf") if info else None
            if not orig or not isinstance(acf, Path) or not acf.is_file():
                continue
            try:
                raw = acf.read_text(encoding="utf-8")
                new = vdftext.replace_appmanifest_name(raw, orig)
                if new != raw:
                    acf.write_text(new, encoding="utf-8")
            except OSError as exc:
                result.warning.append(f"{acf.name}: {exc}")

        # Undo removes all static non-system user collections.  Steam uses
        # collection membership for categories, so leaving same-name/adopted
        # collections behind would leave games categorized.
        if mode != "names":
            ns = collections.load_json_array(env.namespace_file)
            deleted, version = collections.tombstone_category_collections(ns)
            if deleted:
                def write_collections() -> None:
                    collections.write_json_array(env.namespace_file, ns)
                    collections.update_namespaces_version(
                        env.namespaces_file, 1, version
                    )

                apply._retry_write(write_collections)
                result.collections_deleted = len(deleted)
            state.collections.clear()
            state.collection_ids.clear()
            state.collection_owned.clear()

        state.save()
        if log:
            log(
                f"复原完成：名称 {result.names_restored}，"
                f"分类 {result.categories_cleared}，收藏集 {result.collections_deleted}"
            )
    except Exception:
        if result.backup:
            try:
                apply._restore_files(env, state, result.backup, restore_state=False)
                if log:
                    log("复原失败，已自动回滚到复原前备份")
            except Exception as exc:
                if log:
                    log(f"复原失败且回滚失败：{exc}")
        if (was_running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(env)
            except Exception:
                pass
        raise

    if restart:
        steamenv.start_steam(env)
        result.steam_restarted = True
        if log:
            log("已重新启动 Steam")
    return result
