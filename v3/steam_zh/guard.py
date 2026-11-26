"""Detect and repair Chinese names that Steam replaced with official data.

Steam refreshes app metadata from its CDN on every session ("Apps changed" in
``logs/appinfo_log.txt``).  Each refresh rewrites a whole ``appinfo_XX.vdf``
shard, so a custom ``common.name`` that Valve also touched is replaced by the
official title again.  This module compares the recorded names in ``state.json``
with the live cache, and can write the missing names back.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import apply, cache, steamenv
from .state import State, now_ts

LOG_TAIL_BYTES = 96 * 1024
MAX_LOG_EVENTS = 400

_CHANGED_RE = re.compile(r"^\[([^\]]+)\]\s+Apps changed:\s*(.+)$")
_PAIR_RE = re.compile(r"(\d+)=(\d+)")


@dataclass
class DriftReport:
    cache_kind: str = "missing"
    applied_total: int = 0
    checked: int = 0
    drifted: dict[int, str] = field(default_factory=dict)   # appid -> current name
    missing: list[int] = field(default_factory=list)
    recent_refreshes: dict[int, int] = field(default_factory=dict)
    refreshed_drifted: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.drifted)

    @property
    def present(self) -> bool:
        return self.cache_kind != "missing"

    def summary(self) -> str:
        if not self.present:
            return "名称守护：未找到 appinfo 缓存（Steam 可能正在启动或重建）"
        parts = [
            f"名称守护：已应用 {self.applied_total} 个",
            f"正常 {self.checked - self.count} 个",
            f"被 Steam 覆盖 {self.count} 个",
        ]
        if self.missing:
            parts.append(f"缓存中不存在 {len(self.missing)} 个")
        if self.recent_refreshes:
            parts.append(f"Steam 最近刷新 {len(self.recent_refreshes)} 个")
        return "，".join(parts)

    def as_dict(self) -> dict:
        return {
            "cache_kind": self.cache_kind,
            "applied_total": self.applied_total,
            "checked": self.checked,
            "drifted": {str(k): v for k, v in self.drifted.items()},
            "missing": [str(x) for x in self.missing],
            "recent_refreshes": {str(k): v for k, v in self.recent_refreshes.items()},
            "refreshed_drifted": [str(x) for x in self.refreshed_drifted],
        }


@dataclass
class RepairResult:
    backup: Path | None = None
    repaired: int = 0
    failed: list[int] = field(default_factory=list)
    locked: int = 0
    steam_closed: bool = False
    steam_restarted: bool = False
    need_close: bool = False
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.need_close:
            return "Steam 正在占用 appinfo 缓存，需要关闭 Steam 后才能回写"
        lines = [f"已回写 {self.repaired} 个被 Steam 覆盖的名称"]
        if self.locked:
            lines.append(f"已锁定 {self.locked} 个 appinfo 分片（Steam 无法再覆盖这些名称）")
        if self.steam_closed and not self.steam_restarted:
            lines.append("Steam 已关闭，未自动重新启动")
        if self.steam_restarted:
            lines.append("已重新启动 Steam")
        if self.failed:
            lines.append(f"{len(self.failed)} 个名称回写失败")
        return "；".join(lines)


def appinfo_log(env: steamenv.SteamEnv) -> Path:
    return env.root / "logs" / "appinfo_log.txt"


def recent_refresh_appids(
    env: steamenv.SteamEnv,
    *,
    tail_bytes: int = LOG_TAIL_BYTES,
    max_events: int = MAX_LOG_EVENTS,
) -> dict[int, int]:
    """Appids Steam refreshed most recently, from ``logs/appinfo_log.txt``."""
    path = appinfo_log(env)
    if not path.is_file():
        return {}
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()  # drop the partial first line
            raw = fh.read()
    except OSError:
        return {}
    text = raw.decode("utf-8", "replace")
    found: dict[int, int] = {}
    events = 0
    for line in reversed(text.splitlines()):
        match = _CHANGED_RE.match(line.strip())
        if not match:
            continue
        events += 1
        if events > max_events:
            break
        for appid_s, change_s in _PAIR_RE.findall(match.group(2)):
            try:
                found.setdefault(int(appid_s), int(change_s))
            except ValueError:
                continue
    return found


def scan_drift(
    env: steamenv.SteamEnv,
    state: State,
    *,
    expect: dict[int, str] | None = None,
) -> DriftReport:
    """Read-only comparison of recorded names against the live appinfo cache."""
    report = DriftReport()
    expected = expect if expect is not None else apply.names_from_state(state)
    report.applied_total = len(expected)
    info = cache.AppInfoCache.detect(env, wait=0.0)
    report.cache_kind = info.kind.value
    if not info.exists:
        return report
    try:
        view = info.load_names()
    except Exception as exc:
        report.cache_kind = "unreadable"
        report.warnings.append(str(exc))
        return report
    report.warnings.extend(view.warnings[:3])
    report.checked = len(expected)
    if not expected:
        return report
    diff = cache.compare_names(view, expected)
    report.drifted = diff.drifted
    report.missing = diff.missing
    recent = recent_refresh_appids(env)
    report.recent_refreshes = recent
    report.refreshed_drifted = sorted(a for a in diff.drifted if a in recent)
    return report


def _backup_shards(shard_dir: Path, files: list[Path], env: steamenv.SteamEnv) -> Path | None:
    if not files:
        return None
    root = apply.BACKUP_ROOT
    root.mkdir(parents=True, exist_ok=True)
    dest = steamenv.backup_dir(root)
    target = dest / "appinfo"
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in files:
        try:
            shutil.copy2(path, target / path.name)
            copied.append(path.name)
        except OSError:
            continue
    (dest / "manifest.json").write_text(
        json.dumps(
            {
                "created": now_ts(),
                "kind": "guard",
                "steam_root": str(env.root),
                "account": env.account,
                "files": [f"appinfo/{name}" for name in copied],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def repair_drift(
    env: steamenv.SteamEnv,
    state: State,
    report: DriftReport | None = None,
    *,
    restart: bool = False,
    lock: bool = False,
    allow_close: bool = True,
    log: Callable[[str], None] | None = None,
) -> RepairResult:
    """Write back names that Steam replaced, optionally locking the shards.

    By default the repair only writes the files; Steam picks the names up on its
    next start.  With ``allow_close`` the cache is written while Steam is closed
    (more reliable), and Steam is restarted afterwards when ``restart`` is set.
    """
    result = RepairResult()
    report = report or scan_drift(env, state)
    if not report.present:
        result.warnings.append("未找到 appinfo 缓存，跳过了名称回写")
        if log:
            log(result.warnings[-1])
        return result
    expected = apply.names_from_state(state)
    targets = {appid: expected[appid] for appid in report.drifted if appid in expected}
    if not targets and not lock:
        return result

    was_running = steamenv.steam_running()
    if was_running and allow_close:
        try:
            if log:
                log("Steam 正在运行，关闭后回写名称 ...")
            steamenv.close_steam(env, log=log)
            result.steam_closed = True
        except Exception as exc:
            result.warnings.append(f"关闭 Steam 失败：{exc}")
            if log:
                log(result.warnings[-1])
            allow_close = False

    info = cache.AppInfoCache.detect(env, wait=10.0)
    if not info.exists:
        result.warnings.append("Steam 退出后 appinfo 缓存消失，稍后再试")
        if log:
            log(result.warnings[-1])
        if result.steam_closed and restart:
            try:
                steamenv.start_steam(env)
                result.steam_restarted = True
            except Exception:
                pass
        return result

    view = info.load_names()
    files = view.shard_files_for(set(targets)) if targets else []
    keep_locked = [path for path in files if apply.is_readonly(path)]
    if targets:
        # A shard may still be read-only from an earlier lock; unlock it before
        # writing and restore the lock afterwards.
        for path in files:
            apply._set_readonly(path, False)
        result.backup = _backup_shards(info.shard_dir, files, env) if info.shard_dir else None
        changed = apply.write_names(view, targets, state=state, warnings=result.warnings, labels=None)

        def _save() -> None:
            apply.save_names(view)

        try:
            apply._retry_write(_save)
        except Exception as exc:
            for path in keep_locked:
                apply._set_readonly(path, True)
            result.need_close = steamenv.steam_running()
            result.warnings.append(f"回写失败：{exc}")
            if log:
                log(f"回写失败：{exc}")
            if result.steam_closed and restart:
                try:
                    steamenv.start_steam(env)
                    result.steam_restarted = True
                except Exception:
                    pass
            return result

        check = cache.compare_names(info.load_names(), targets)
        if check.drifted:
            result.failed = list(check.drifted)
            result.warnings.append(
                "回写后仍有 %d 个名称未生效（Steam 可能正在写缓存）" % len(check.drifted)
            )
        result.repaired = len(changed)
        for appid in targets:
            state.set_app(appid, applied=True, applied_at=now_ts())

    if lock:
        result.locked = apply.lock_written_shards(
            view, list(targets) or list(expected), state
        )
    else:
        for path in keep_locked:
            if path.is_file():
                apply._set_readonly(path, True)

    state.save()
    if result.steam_closed and restart:
        try:
            steamenv.start_steam(env)
            result.steam_restarted = True
        except Exception as exc:
            result.warnings.append(f"重新启动 Steam 失败：{exc}")
    if log:
        log(result.summary())
    return result


def lock_shards(env: steamenv.SteamEnv, state: State, appids: set[int] | None = None) -> int:
    """Write-protect shard files holding ``appids`` (or all recorded names)."""
    info = cache.AppInfoCache.detect(env, wait=0.0)
    if not info.exists:
        return 0
    if info.kind == cache.CacheKind.MONOLITHIC:
        if info.appinfo_path:
            apply._set_readonly(info.appinfo_path, True)
            return 1
        return 0
    expected = apply.names_from_state(state)
    wanted = set(appids) if appids is not None else set(expected)
    view = info.load_names()
    files = view.shard_files_for(wanted)
    for path in files:
        apply._set_readonly(path, True)
    locked = state.data.get("locked_shards")
    if not isinstance(locked, list):
        locked = state.data["locked_shards"] = []
    for path in files:
        if path.name not in locked:
            locked.append(path.name)
    state.save()
    return len(files)


def unlock_shards(env: steamenv.SteamEnv, state: State | None = None) -> int:
    """Clear the read-only flag from every appinfo file and forget the record."""
    count = apply.unlock_all_shards(env.shard_dir)
    if env.appinfo.is_file() and apply.is_readonly(env.appinfo):
        apply._set_readonly(env.appinfo, False)
        count += 1
    if state is not None:
        state.data["locked_shards"] = []
        state.save()
    return count


def lock_state(env: steamenv.SteamEnv) -> tuple[int, int]:
    """(locked files, total shard files)."""
    total = len(list(env.shard_dir.glob("appinfo_*.vdf"))) if env.shard_dir.is_dir() else 0
    return len(cache.readonly_shards(env.shard_dir)), total
