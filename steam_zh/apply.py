"""Apply names/categories to Steam, verify, roll back, and auto-fix."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    acfs_updated: int = 0
    collections_changed: int = 0
    collection_skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified_names: int = 0
    verify_failed: list[int] = field(default_factory=list)
    collections_verified: bool = False
    steam_restarted: bool = False


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
        if env.has_shards:
            targets.append(env.shard_dir)
        else:
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
        shutil.rmtree(old, ignore_errors=True)


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

    # Make sure appinfo is writable (we may have locked it previously).
    if cache_info.kind == cache.CacheKind.MONOLITHIC and cache_info.appinfo_path and is_readonly(cache_info.appinfo_path):
        _set_readonly(cache_info.appinfo_path, False)

    try:
        if mode == "categories":
            planned, names = [], {}
        else:
            planned, names = _plan_names(state, games, selected)
        changed_ids: list[int] = []
        if mode == "categories":
            pass
        elif cache_info.kind == cache.CacheKind.SHARDS:
            assert snapshot is not None and snapshot.shards is not None
            ss = snapshot.shards
            for g in planned:
                zh = names[g.appid]
                try:
                    current = ss.get_name(g.appid) or ""
                    localized = ss.get_localized(g.appid)
                    if current == zh and not localized:
                        continue
                    orig_localized = ss.remove_localized(g.appid)
                    rec = state.get_app(g.appid)
                    if orig_localized and not rec.get("orig_localized"):
                        rec["orig_localized"] = orig_localized
                    ss.set_name(g.appid, zh)
                    changed_ids.append(g.appid)
                except Exception as exc:
                    result.warnings.append(f"{g.appid} {g.name}: {exc}")
            if changed_ids:
                _retry_write(ss.save)
                _verify_cache_written(snapshot, changed_ids, names)
            if lock_appinfo:
                result.warnings.append("分片 appinfo 模式不支持只读锁定，已跳过")
        else:
            assert snapshot is not None and snapshot.app_info is not None
            ai = snapshot.app_info
            for g in planned:
                zh = names[g.appid]
                try:
                    current = ai.get_name(g.appid) or ""
                    localized = ai.get_localized(g.appid)
                    if current == zh and not localized:
                        continue
                    orig_localized = ai.remove_localized(g.appid)
                    rec = state.get_app(g.appid)
                    if orig_localized and not rec.get("orig_localized"):
                        rec["orig_localized"] = orig_localized
                    ai.set_name(g.appid, zh)
                    changed_ids.append(g.appid)
                except Exception as exc:
                    result.warnings.append(f"{g.appid} {g.name}: {exc}")
            if changed_ids:
                data = ai.build()
                appinfo_path = snapshot.info.appinfo_path
                assert appinfo_path is not None
                tmp = appinfo_path.with_name(appinfo_path.name + ".tmp")
                appinfo_path.parent.mkdir(parents=True, exist_ok=True)

                def write_appinfo() -> None:
                    with open(tmp, "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, appinfo_path)

                _retry_write(write_appinfo)
                _verify_cache_written(snapshot, changed_ids, names)
                if lock_appinfo:
                    _set_readonly(appinfo_path, True)

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
            log("已启动 Steam，等待库加载后校验 ...")

    return result


def verify(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    wait: float = 12.0,
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
    cache_info = cache.AppInfoCache.detect(env, wait=15.0)
    if cache_info.kind == cache.CacheKind.MISSING:
        result.warnings.append("校验时未找到 appinfo 缓存，稍后重新扫描即可")
        return result
    ss = cache_info.load_shards() if cache_info.kind == cache.CacheKind.SHARDS else None
    ai = cache_info.load_appinfo() if cache_info.kind == cache.CacheKind.MONOLITHIC else None
    if mode in ("both", "names"):
        for g in games:
            rec = state.get_app(g.appid)
            if rec.get("skipped"):
                continue
            zh = str(rec.get("zh_name") or "").strip()
            if not zh:
                continue
            current = (ss.get_name(g.appid) if ss else ai.get_name(g.appid)) or ""
            if current == zh:
                result.verified_names += 1
            else:
                result.verify_failed.append(g.appid)
    if mode in ("both", "categories"):
        try:
            ns = collections.load_json_array(env.namespace_file)
            live_ids = set()
            for item in ns:
                if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str):
                    if item[0].startswith(collections.COLLECTION_PREFIX) and not item[1].get("is_deleted"):
                        live_ids.add(item[0])
            managed_ids = [
                cid
                for values in state.collection_ids.values()
                for cid in values
                if cid
            ]
            if not managed_ids:
                managed_ids = [cid for cid in state.collections.values() if cid]
            result.collections_verified = all(
                collections.COLLECTION_PREFIX + cid in live_ids
                for cid in managed_ids
            )
        except Exception as exc:
            result.warnings.append(f"校验收藏集失败: {exc}")
    if log:
        log(
            f"校验：名称 {result.verified_names} 个正确，"
            f"{len(result.verify_failed)} 个异常；收藏集 "
            f"{'正常' if result.collections_verified else '异常'}"
        )
    return result


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
    restored = 0
    shard_backup = backup_dir / "appinfo"
    if shard_backup.is_dir():
        if env.appinfo.is_file():
            env.appinfo.unlink()
        if env.shard_dir.exists():
            shutil.rmtree(env.shard_dir)
        shutil.copytree(shard_backup, env.shard_dir)
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
