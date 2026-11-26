"""Apply names/categories to Steam, verify, roll back, and auto-fix."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import appinfo, cache, category_state, collections, paths, steamenv, vdftext
from .appinfo_shards import ShardSet
from .metadata import GameInfo
from .state import State, now_ts

BASE_DIR = paths.app_dir()
BACKUP_ROOT = BASE_DIR / "backups"

SKIP_CATEGORIES = {"", "不分类", "未分类", "none", "null"}
APPLY_MODES = ("names", "categories", "both")


class ApplyError(Exception):
    pass


@dataclass
class ApplyResult:
    backup: Path | None = None
    names_applied: int = 0
    names_expected: int = 0
    acfs_updated: int = 0
    collections_changed: int = 0
    collection_skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified_names: int = 0
    verified_acfs: int = 0
    verify_failed: list[int] = field(default_factory=list)
    drifted: list[int] = field(default_factory=list)
    collections_verified: bool = False
    steam_restarted: bool = False
    cache_missing_after_restart: bool = False
    cache_migration_timeout: bool = False
    cache_ready: bool = False
    cache_rebuilt: bool = False
    repaired: int = 0
    locked_shards: int = 0


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _probe_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".steam_zh_write_probe"
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def preflight_writable(env: steamenv.SteamEnv, *, include_appinfo: bool = True) -> None:
    """Fail before closing Steam if any target directory is not writable."""
    targets: list[Path] = []
    if include_appinfo:
        if env.appinfo.is_file():
            targets.append(env.appinfo.parent)
        if env.has_shards:
            targets.append(env.shard_dir)
        if not env.appinfo.is_file() and not env.has_shards:
            targets.append(env.appinfo.parent)
    targets += [env.cloudstorage]
    for info in steamenv.installed_apps(env.root).values():
        acf = info.get("acf")
        if isinstance(acf, Path):
            targets.append(acf.parent)
    for d in targets:
        try:
            _probe_writable(d)
        except OSError as exc:
            raise ApplyError(
                f"没有写入权限：{d}\n{exc}\n\n"
                "请把 Steam/本程序放在可写目录，或以管理员身份运行；"
                "也可以在设置中切换到正确的 Steam 账号。"
            ) from exc


def _retry_write(func, *args, attempts: int = 4, delay: float = 0.8):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return func(*args)
        except (PermissionError, OSError) as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(delay * (i + 1))
    assert last is not None
    raise last


def _verify_cache_written(
    snapshot: cache.CacheSnapshot,
    changed_ids: list[int],
    names: dict[int, str],
) -> None:
    if not changed_ids:
        return
    if snapshot.info.kind == cache.CacheKind.SHARDS:
        fresh = ShardSet.load(snapshot.info.shard_dir)  # type: ignore[arg-type]
        bad = [appid for appid in changed_ids if (fresh.get_name(appid) or "") != names[appid]]
    else:
        fresh = appinfo.AppInfo.load(snapshot.info.appinfo_path)  # type: ignore[arg-type]
        bad = [appid for appid in changed_ids if (fresh.get_name(appid) or "") != names[appid]]
    if bad:
        raise ApplyError(
            "appinfo 写入后校验失败，Steam 可能正在覆盖缓存；"
            f"未生效条目：{', '.join(str(x) for x in bad[:8])}"
        )


def _set_readonly(path: Path, readonly: bool) -> None:
    if not path.exists():
        return
    mode = os.stat(path).st_mode
    if readonly:
        os.chmod(path, mode & ~stat.S_IWRITE)
    else:
        os.chmod(path, mode | stat.S_IWRITE)


def is_readonly(path: Path) -> bool:
    try:
        return not bool(os.stat(path).st_mode & stat.S_IWRITE)
    except OSError:
        return False


def unlock_all_shards(shard_dir: Path | None) -> int:
    """Make every shard writable again (the lock feature sets them read-only)."""
    if not shard_dir or not shard_dir.is_dir():
        return 0
    count = 0
    for path in shard_dir.glob("appinfo_*.vdf"):
        if is_readonly(path):
            _set_readonly(path, False)
            count += 1
    return count


def names_from_state(state: State, appids: Iterable[int] | None = None) -> dict[int, str]:
    """Names the user asked for and that were already applied at least once."""
    wanted = set(appids) if appids is not None else None
    out: dict[int, str] = {}
    for key, rec in state.apps.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("skipped"):
            continue
        if str(rec.get("source") or "") == "original":
            continue
        if not rec.get("applied"):
            continue
        zh = str(rec.get("zh_name") or "").strip()
        if not zh:
            continue
        try:
            appid = int(key)
        except (TypeError, ValueError):
            continue
        if wanted is not None and appid not in wanted:
            continue
        out[appid] = zh
    return out


def _names_accessor(cache_obj):
    """Return (get_name, get_localized, remove_localized, set_name) for a view."""
    shards = getattr(cache_obj, "shards", None)
    if shards is not None:
        return shards.get_name, shards.get_localized, shards.remove_localized, shards.set_name
    app_info = getattr(cache_obj, "app_info", None)
    if app_info is not None:
        return (
            app_info.get_name,
            app_info.get_localized,
            app_info.remove_localized,
            app_info.set_name,
        )
    raise ApplyError("缓存视图既不是分片也不是单文件")


def write_names(
    cache_obj,
    names: dict[int, str],
    *,
    state: State | None = None,
    warnings: list[str] | None = None,
    labels: dict[int, str] | None = None,
) -> list[int]:
    """Write ``names`` into a loaded cache view and return the changed appids.

    The caller owns saving/verification.  ``common.name_localized`` is removed
    so the custom name wins in every language, and the removed mapping is kept
    in state for undo.
    """
    get_name, get_localized, remove_localized, set_name = _names_accessor(cache_obj)
    changed: list[int] = []
    for appid, zh in names.items():
        try:
            if (get_name(appid) or "") == zh and not get_localized(appid):
                continue
            orig_localized = remove_localized(appid)
            if state is not None and orig_localized:
                rec = state.get_app(appid)
                if not rec.get("orig_localized"):
                    rec["orig_localized"] = orig_localized
            set_name(appid, zh)
            changed.append(appid)
        except Exception as exc:
            if warnings is not None:
                label = (labels or {}).get(appid) or str(appid)
                warnings.append(f"{label}: {exc}")
    return changed


def save_names(cache_obj) -> None:
    """Persist a view mutated through :func:`write_names`."""
    shards = getattr(cache_obj, "shards", None)
    if shards is not None:
        # ShardSet.save() is a no-op when nothing is dirty.
        _retry_write(shards.save)
        return
    app_info = getattr(cache_obj, "app_info", None)
    if app_info is None:
        return
    info = getattr(cache_obj, "info", None)
    target = getattr(cache_obj, "appinfo_path", None)
    if target is None and info is not None:
        target = info.appinfo_path
    if target is None:
        raise ApplyError("单文件 appinfo 缓存没有路径，无法保存")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = app_info.build()
    tmp = target.with_name(target.name + ".tmp")

    def write_file() -> None:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    _retry_write(write_file)


def lock_written_shards(cache_obj, appids: list[int], state: State | None) -> int:
    """Write-protect the cache files that hold ``appids`` (prevents overwrites).

    Files locked by earlier runs stay locked: the previous list is kept in
    ``state.data['locked_shards']`` and re-applied here, so applying a few new
    names never silently unlocks older ones.
    """
    wanted = set(appids)
    shards = getattr(cache_obj, "shards", None)
    info = getattr(cache_obj, "info", None)
    kind = info.kind if info is not None else None
    if shards is not None:
        files = {
            sh.path for sh in shards.shards
            if any(rec.appid in wanted for rec in sh.records)
        }
        if state is not None:
            locked = state.data.get("locked_shards")
            if not isinstance(locked, list):
                locked = state.data["locked_shards"] = []
            directory = getattr(shards, "directory", None)
            if directory is not None:
                for name in list(locked):
                    path = Path(directory) / str(name)
                    if path.is_file():
                        files.add(path)
            for path in files:
                _set_readonly(path, True)
            for path in files:
                if path.name not in locked:
                    locked.append(path.name)
        else:
            for path in files:
                _set_readonly(path, True)
        return len(files)
    target = getattr(cache_obj, "appinfo_path", None)
    if target is None and info is not None:
        target = info.appinfo_path
    if target is not None and kind == cache.CacheKind.MONOLITHIC:
        _set_readonly(Path(target), True)
        return 1
    return 0


def backup(
    env: steamenv.SteamEnv,
    state: State,
    installed: dict[int, dict[str, Any]],
    base: Path | None = None,
) -> Path:
    root = base or BACKUP_ROOT
    root.mkdir(parents=True, exist_ok=True)
    dest = steamenv.backup_dir(root)
    files: list[str] = []
    if env.has_shards:
        shutil.copytree(env.shard_dir, dest / "appinfo")
        files.append("appinfo/ (%d shards)" % len(list(env.shard_dir.glob("appinfo_*.vdf"))))
    targets = [
        env.appinfo,
        env.namespace_file,
        env.namespaces_file,
        env.localconfig,
    ]
    for p in targets:
        if p.is_file():
            rel = p.relative_to(env.root) if str(p).lower().startswith(str(env.root).lower()) else Path(p.name)
            out = dest / rel
            _copy(p, out)
            files.append(str(rel))
    acf_dir = dest / "acf"
    for appid, info in installed.items():
        acf = info.get("acf")
        if isinstance(acf, Path) and acf.is_file():
            _copy(acf, acf_dir / acf.name)
            files.append(f"acf/{acf.name}")
    manifest = {
        "created": now_ts(),
        "account": env.account,
        "steam_root": str(env.root),
        "files": files,
        "managed_collections": dict(state.collections),
        "collection_ids": {k: list(v) for k, v in state.collection_ids.items()},
        "collection_owned": {k: list(v) for k, v in state.collection_owned.items()},
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def prune_backups(keep: int = 10, root: Path | None = None) -> None:
    base = root or BACKUP_ROOT
    if not base.is_dir():
        return
    dirs = sorted(
        [p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in dirs[max(1, keep):]:
        _force_rmtree(old)


def _plan_names(
    state: State, games: list[GameInfo], selected: set[int] | None
) -> tuple[list[GameInfo], dict[int, str]]:
    planned: list[GameInfo] = []
    names: dict[int, str] = {}
    for g in games:
        if selected is not None and g.appid not in selected:
            continue
        rec = state.get_app(g.appid)
        if rec.get("skipped"):
            continue
        zh = str(rec.get("zh_name") or "").strip()
        if not zh:
            continue
        planned.append(g)
        names[g.appid] = zh
    return planned, names


def _sync_categories(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    result: ApplyResult,
    progress: Callable[[str], None] | None,
    log: Callable[[str], None] | None,
) -> None:
    computed: dict[str, list[int]] = {}
    for g in games:
        rec = state.get_app(g.appid)
        if rec.get("skipped"):
            continue
        for cat in category_state.get_categories(rec):
            cat = str(cat).strip()
            if not cat or cat.lower() in SKIP_CATEGORIES:
                continue
            bucket = computed.setdefault(cat, [])
            if g.appid not in bucket:
                bucket.append(g.appid)
    if not computed:
        return
    if progress:
        progress("正在写入 Steam 收藏集 ...")
    ns = collections.load_json_array(env.namespace_file)
    changed, version, skipped = collections.sync_collections(
        ns,
        state.collections,
        computed,
        state.collection_ids,
        state.collection_owned,
    )
    result.collections_changed = changed
    result.collection_skipped = skipped
    if changed:
        def write_collections() -> None:
            collections.write_json_array(env.namespace_file, ns)
            collections.update_namespaces_version(env.namespaces_file, 1, version)

        _retry_write(write_collections)
        if log:
            log(f"已更新 {changed} 个收藏集（同名静态收藏集一起更新）")
    if skipped:
        result.warnings.append(
            "以下同名智能收藏集使用过滤规则，未直接改写: " + "、".join(skipped)
        )


def apply_changes(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    selected: set[int] | None = None,
    restart: bool = True,
    lock_appinfo: bool = False,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    mode: str = "both",
) -> ApplyResult:
    if mode not in APPLY_MODES:
        raise ValueError(f"unknown apply mode: {mode}")
    result = ApplyResult()
    need_appinfo = mode != "categories"
    cache_info = cache.AppInfoCache.detect(env)
    if need_appinfo:
        cache_info.require(env.root)
    if log and need_appinfo:
        log(cache.diagnostic(env))
    snapshot = cache.CacheSnapshot.capture(cache_info) if need_appinfo else None
    preflight_writable(env, include_appinfo=need_appinfo)
    was_running = steamenv.steam_running()
    # Back up before closing Steam.  New Steam clients may remove the shard
    # directory on exit, so a post-shutdown backup can miss the cache.
    result.backup = backup(env, state, steamenv.installed_apps(env.root))
    if was_running:
        if log:
            log("Steam 正在运行，正在自动关闭 ...")
        try:
            steamenv.close_steam(env, log=log)
            # Give Steam a moment to release its handles on cloudstorage files.
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
    if log:
        log(f"已备份到 {result.backup}")
    if progress:
        progress(f"已备份，正在写入 {cache_info.describe() if cache_info.exists else '分类收藏集'} ...")

    # Make sure the cache is writable (we may have locked it previously).
    if cache_info.kind == cache.CacheKind.MONOLITHIC and cache_info.appinfo_path and is_readonly(cache_info.appinfo_path):
        _set_readonly(cache_info.appinfo_path, False)
    if cache_info.kind == cache.CacheKind.SHARDS:
        unlock_all_shards(cache_info.shard_dir)
        if not lock_appinfo:
            # Locking is off: the cache must stay writable for Steam.
            state.data["locked_shards"] = []

    try:
        if mode == "categories":
            planned, names = [], {}
        else:
            planned, names = _plan_names(state, games, selected)
        result.names_expected = len(names)
        changed_ids: list[int] = []
        if mode != "categories" and snapshot is not None:
            labels = {g.appid: f"{g.appid} {g.name}" for g in planned}
            changed_ids = write_names(
                snapshot, names, state=state, warnings=result.warnings, labels=labels
            )
            if changed_ids:
                save_names(snapshot)
                _verify_cache_written(snapshot, changed_ids, names)
            if lock_appinfo:
                result.locked_shards = lock_written_shards(snapshot, changed_ids, state)

        result.names_applied = len(changed_ids)
        if log:
            log(
                f"已写入 {len(changed_ids)} 个中文名"
                if changed_ids
                else "appinfo 中没有需要改写的名称"
            )

        # Update installed games' ACF name (cosmetic, but keeps things consistent).
        for appid in changed_ids:
            info = steamenv.installed_apps(env.root).get(appid)
            if not info:
                continue
            acf = info.get("acf")
            if not isinstance(acf, Path) or not acf.is_file():
                continue
            try:
                raw = acf.read_text(encoding="utf-8")
                new = vdftext.replace_appmanifest_name(raw, names[appid])
                if new != raw:
                    acf.write_text(new, encoding="utf-8")
                    result.acfs_updated += 1
            except Exception as exc:
                result.warnings.append(f"更新 {acf.name} 失败: {exc}")

        if mode != "names":
            _sync_categories(env, state, games, result, progress, log)

        for appid in changed_ids:
            state.set_app(appid, applied=True, applied_at=now_ts())
        state.save()
        if log:
            log("数据文件写入完成")
    except Exception:
        # Roll back the files we may have already changed.
        if result.backup:
            try:
                _restore_files(env, state, result.backup, restore_state=False)
                if log:
                    log("写入失败，已自动回滚到本次备份")
            except Exception as exc:
                if log:
                    log(f"自动回滚失败: {exc}")
        # Never leave Steam closed after a failed apply.
        if (was_running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(env)
                if log:
                    log("写入失败后已重新启动 Steam")
            except Exception as exc:
                if log:
                    log(f"重新启动 Steam 失败: {exc}")
        raise

    if restart:
        if progress:
            progress("正在启动 Steam ...")
        steamenv.start_steam(env)
        result.steam_restarted = True
        if log:
            log("已启动 Steam，正在核对名称是否落盘 ...")
        if need_appinfo and names:
            _reconcile_after_restart(
                env, state, names, result, log,
                restart=restart, written_kind=cache_info.kind,
                backup_dir=result.backup,
            )

    return result


def _wait_for_cache(
    env: steamenv.SteamEnv,
    log: Callable[[str], None] | None,
    budget: float,
) -> "cache.CacheNames | None":
    """Wait until Steam has a readable appinfo cache again, logging progress."""
    started = time.time()
    last_note = 0.0
    while time.time() - started < budget:
        info = cache.AppInfoCache.detect(env, wait=0.0)
        if info.exists:
            try:
                return info.load_names()
            except Exception as exc:
                if log:
                    log(f"读取 appinfo 缓存失败，继续等待：{exc}")
        if log and time.time() - last_note >= 15:
            last_note = time.time()
            log(f"Steam 正在迁移/重建 appinfo 缓存，已等待 {int(time.time() - started)} 秒 ...")
        time.sleep(2.0)
    return None


def _force_rmtree(path: Path) -> None:
    """Remove a directory that may contain read-only files (locked shards)."""
    if not path.exists():
        return
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                _set_readonly(Path(root) / name, False)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _restore_cache_from_backup(env: steamenv.SteamEnv, backup_dir: Path | None) -> bool:
    """Put the pre-apply cache back when Steam deleted it while exiting."""
    if backup_dir is None or not Path(backup_dir).is_dir():
        return False
    backup_dir = Path(backup_dir)
    shard_backup = backup_dir / "appinfo"
    if shard_backup.is_dir():
        _force_rmtree(env.shard_dir)
        shutil.copytree(shard_backup, env.shard_dir)
        unlock_all_shards(env.shard_dir)
        return True
    for candidate in (backup_dir / "appcache" / "appinfo.vdf", backup_dir / "appinfo.vdf"):
        if candidate.is_file():
            _copy(candidate, env.appinfo)
            return True
    return False


def _reconcile_after_restart(
    env: steamenv.SteamEnv,
    state: State,
    names: dict[int, str],
    result: ApplyResult,
    log: Callable[[str], None] | None,
    *,
    restart: bool,
    written_kind: cache.CacheKind | None = None,
    backup_dir: Path | None = None,
    budget: float = 240.0,
    confirm_wait: float = 5.0,
) -> None:
    """After Steam starts, check the final cache and repair what Steam dropped.

    Steam may migrate a monolithic ``appinfo.vdf`` to shards, may rebuild a
    deleted cache, and may replace app records with fresh copies downloaded
    from Steam's CDN.  Instead of guessing at timers we wait for the cache to
    settle, compare every planned name, and only then report a result.
    """
    view = _wait_for_cache(env, log, budget)
    if view is None:
        result.cache_missing_after_restart = True
        result.warnings.append(
            "Steam 尚未重建 appinfo 缓存，暂时无法核对名称；稍后可用“检查名称覆盖”补写。"
        )
        if log:
            log("Steam 在预期时间内没有重建 appinfo 缓存；稍后可用“检查名称覆盖”补写名称")
        return

    # Steam writes shards progressively, then downloads deltas a bit later.
    time.sleep(3.0)
    diff = cache.compare_names(view, names)
    if diff.ok:
        time.sleep(confirm_wait)
        try:
            again = cache.AppInfoCache.detect(env, wait=0.0).load_names()
        except Exception:
            again = None
        if again is not None:
            second = cache.compare_names(again, names)
            if second.ok:
                result.cache_ready = True
                if log:
                    log(f"名称核对完成：{len(names)} 个中文名全部生效")
                return
            view, diff = again, second
        else:
            result.cache_ready = True
            if log:
                log(f"名称核对完成：{len(names)} 个中文名全部生效")
            return

    if written_kind is not None and written_kind != view.kind:
        result.cache_rebuilt = True
    if log:
        log(
            f"Steam 已替换 {len(diff.drifted)} 个游戏的元数据，"
            f"正在把中文名补写回 {view.kind.value} 缓存 ..."
        )
    try:
        if steamenv.steam_running():
            steamenv.close_steam(env, log=log)
            time.sleep(1.5)
        fresh_info = cache.AppInfoCache.detect(env, wait=30.0)
        if not fresh_info.exists:
            if _restore_cache_from_backup(env, backup_dir):
                if log:
                    log("Steam 退出时删除了缓存，已从本次备份恢复")
            fresh_info = cache.AppInfoCache.detect(env, wait=30.0)
        fresh_info.require(env.root)
        fresh = fresh_info.load_names()
        repair_names = {
            appid: want
            for appid, want in names.items()
            if appid not in diff.missing and (fresh.get(appid) or "") != want
        }
        changed = write_names(fresh, repair_names, state=state)
        if changed:
            save_names(fresh)
            check = cache.compare_names(fresh_info.load_names(), repair_names)
            if not check.ok:
                raise ApplyError(
                    "补写后仍有 %d 个名称未生效" % check.count
                )
        result.cache_ready = True
        if fresh.kind != view.kind:
            result.cache_rebuilt = True
        result.repaired = len(changed)
        if log:
            log(f"已把 {len(changed)} 个中文名补写到 {fresh.kind.value} 缓存")
        if restart:
            steamenv.start_steam(env)
            result.steam_restarted = True
    except Exception as exc:
        result.drifted = list(diff.drifted)
        result.warnings.append(f"补写名称失败：{exc}")
        if log:
            log(f"补写名称失败：{exc}")


def verify(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    wait: float = 30.0,
    log: Callable[[str], None] | None = None,
    mode: str = "both",
) -> ApplyResult:
    if mode not in APPLY_MODES:
        raise ValueError(f"unknown verify mode: {mode}")
    result = ApplyResult()
    if wait:
        time.sleep(wait)
    if not steamenv.steam_running():
        result.warnings.append("校验时 Steam 未运行")
    if mode == "categories":
        cache_info = cache.AppInfoCache.detect(env, wait=0.0)
    else:
        cache_info = cache.AppInfoCache.detect(env, wait=0.0)
        deadline = time.time() + 45.0
        noted = False
        while cache_info.kind == cache.CacheKind.MISSING and time.time() < deadline:
            if log and not noted:
                noted = True
                log("Steam 正在重建 appinfo 缓存，等待缓存出现 ...")
            time.sleep(2.0)
            cache_info = cache.AppInfoCache.detect(env, wait=0.0)
    if cache_info.kind == cache.CacheKind.MISSING and mode != "categories":
        result.cache_missing_after_restart = True
        result.warnings.append(
            "Steam 重启后仍未重建 appinfo 缓存；名称可能未持久化。"
            "请退出 Steam 后点击“重新应用”，或手动重启 Steam 后再校验。"
        )
        if log:
            log(cache.diagnostic(env))
        return result
    if mode != "categories":
        expected = names_from_state(state, {g.appid for g in games})
        view = cache_info.load_names()
        diff = cache.compare_names(view, expected)
        result.drifted = list(diff.drifted)
        result.verify_failed = list(diff.drifted)
        result.verified_names = len(expected) - len(diff.drifted) - len(diff.missing)
        installed = steamenv.installed_apps(env.root)
        for appid, zh in expected.items():
            info = installed.get(appid)
            acf = info.get("acf") if info else None
            if isinstance(acf, Path) and acf.is_file():
                try:
                    raw = acf.read_text(encoding="utf-8")
                    if vdftext.appmanifest_name(raw) == zh:
                        result.verified_acfs += 1
                except Exception:
                    pass
    result.collections_verified = True
    return result


def describe_result(res: ApplyResult, mode: str = "both") -> str:
    """Human-readable, truthful summary shared by the GUI, CLI and logs."""
    lines: list[str] = []
    if mode != "categories":
        if res.names_applied:
            lines.append(f"已写入中文名 {res.names_applied} 个。")
        elif res.names_expected:
            lines.append("appinfo 中已是最新，无需改写名称。")
        else:
            lines.append("没有可写入的中文名。")
    if mode != "names":
        lines.append(f"已更新收藏集 {res.collections_changed} 个。")
    if mode != "categories" and res.names_expected:
        if res.cache_ready:
            lines.append(f"重启后核对：{res.names_expected} 个名称全部生效。")
        elif res.repaired:
            lines.append(f"Steam 重建缓存后已补写 {res.repaired} 个名称。")
        elif res.cache_missing_after_restart:
            lines.append("Steam 尚未重建 appinfo 缓存，稍后可用“检查名称覆盖”补写。")
        elif res.drifted:
            lines.append(
                f"其中 {len(res.drifted)} 个名称已被 Steam 重新下载覆盖"
                "（Steam 会定期更新元数据，属正常现象）。"
            )
        else:
            lines.append("名称已写入，Steam 下次启动时生效。")
    if res.locked_shards:
        lines.append(f"已把 {res.locked_shards} 个 appinfo 文件设为只读，防止 Steam 覆盖。")
    if res.warnings:
        lines.append("提示：")
        lines.extend(res.warnings[:8])
    return "\n".join(lines)

def _find_backup_file(backup_dir: Path, env: steamenv.SteamEnv, name: str, subdirs: list[str]) -> Path | None:
    candidates = [backup_dir / name]
    for sub in subdirs:
        candidates.append(backup_dir / sub / name)
    candidates.append(backup_dir / "userdata" / env.account / "config" / "cloudstorage" / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _restore_files(
    env: steamenv.SteamEnv,
    state: State,
    backup_dir: Path,
    restore_state: bool = True,
) -> int:
    if not env.has_shards and is_readonly(env.appinfo):
        _set_readonly(env.appinfo, False)
    unlock_all_shards(env.shard_dir)
    state.data["locked_shards"] = []
    restored = 0
    shard_backup = backup_dir / "appinfo"
    if shard_backup.is_dir():
        if env.appinfo.is_file():
            env.appinfo.unlink()
        if env.shard_dir.exists():
            shutil.rmtree(env.shard_dir)
        shutil.copytree(shard_backup, env.shard_dir)
        unlock_all_shards(env.shard_dir)
        restored += len(list(env.shard_dir.glob("appinfo_*.vdf")))
    else:
        appinfo_candidates = [
            backup_dir / "appinfo.vdf",
            backup_dir / "appcache" / "appinfo.vdf",
        ]
        for c in appinfo_candidates:
            if c.is_file():
                _copy(c, env.appinfo)
                restored += 1
                # A monolithic file and the shard directory must not both be
                # present; Steam may prefer either one depending on version.
                if env.shard_dir.exists():
                    shutil.rmtree(env.shard_dir)
                break
    for name, dst in (
        ("cloud-storage-namespace-1.json", env.namespace_file),
        ("cloud-storage-namespaces.json", env.namespaces_file),
    ):
        src = _find_backup_file(
            backup_dir,
            env,
            name,
            ["config/cloudstorage", f"userdata/{env.account}/config/cloudstorage"],
        )
        if src:
            _copy(src, dst)
            restored += 1
    acf_dir = backup_dir / "acf"
    if acf_dir.is_dir():
        for acf in acf_dir.glob("appmanifest_*.acf"):
            for lib in steamenv.library_folders(env.root):
                dst = lib / "steamapps" / acf.name
                if dst.is_file():
                    _copy(acf, dst)
                    restored += 1
                    break
    if restore_state:
        managed: dict[str, str] = {}
        collection_ids: dict[str, list[str]] = {}
        collection_owned: dict[str, list[str]] = {}
        manifest_path = backup_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                raw = manifest.get("managed_collections")
                if isinstance(raw, dict):
                    managed = {str(k): str(v) for k, v in raw.items()}
                raw_ids = manifest.get("collection_ids")
                if isinstance(raw_ids, dict):
                    collection_ids = {
                        str(k): [str(x) for x in v]
                        for k, v in raw_ids.items()
                        if isinstance(v, list)
                    }
                raw_owned = manifest.get("collection_owned")
                if isinstance(raw_owned, dict):
                    collection_owned = {
                        str(k): [str(x) for x in v]
                        for k, v in raw_owned.items()
                        if isinstance(v, list)
                    }
            except (OSError, json.JSONDecodeError):
                managed = {}
        if not collection_ids:
            collection_ids = {k: [v] for k, v in managed.items() if v}
        if not collection_owned:
            collection_owned = {k: list(v) for k, v in collection_ids.items()}
        state.data["collections"] = managed
        state.data["collection_ids"] = collection_ids
        state.data["collection_owned"] = collection_owned
        for rec in state.apps.values():
            rec["applied"] = False
        state.save()
    return restored


def restore_backup(
    env: steamenv.SteamEnv,
    state: State,
    backup_dir: Path,
    restart: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    if steamenv.steam_running():
        steamenv.close_steam(env, log=log)
    count = _restore_files(env, state, backup_dir, restore_state=True)
    if log:
        log(f"已从 {backup_dir} 还原 {count} 个文件")
        log(
            "提示：还原的是旧缓存，Steam 会重新下载全量元数据，"
            "名称可能再次被覆盖；建议随后用“检查名称覆盖”回写。"
        )
    if restart:
        steamenv.start_steam(env)


def autofix(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    restart: bool = True,
    lock_appinfo: bool = False,
    log: Callable[[str], None] | None = None,
) -> ApplyResult:
    """Re-apply names whose current appinfo value no longer matches state."""
    if steamenv.steam_running():
        if log:
            log("检测到 Steam 正在运行，自动关闭以修复 ...")
        steamenv.close_steam(env, log=log)
    return apply_changes(
        env,
        state,
        games,
        selected=None,
        restart=restart,
        lock_appinfo=lock_appinfo,
        log=log,
    )
