"""Steam appinfo cache detection for both old and new clients.

Steam has two layouts:

* old:   ``appcache/appinfo.vdf`` (v29, monolithic)
* new:   ``appcache/appinfo/appinfo_XX.vdf`` (v30, sharded)

Never open the monolithic path just because it is the historical default.
All code that touches names must go through :class:`AppInfoCache`.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from . import appinfo, steamenv
from .appinfo_shards import ShardSet


class CacheKind(str, Enum):
    SHARDS = "shards"
    MONOLITHIC = "monolithic"
    MISSING = "missing"


class CacheError(Exception):
    pass


@dataclass
class AppInfoCache:
    kind: CacheKind
    appinfo_path: Path | None = None
    shard_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return self.kind != CacheKind.MISSING

    @classmethod
    def detect(cls, env: steamenv.SteamEnv, wait: float = 0.0) -> "AppInfoCache":
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            # Steam v30 still treats an existing monolithic appinfo.vdf as
            # authoritative: it reads that file, regenerates the shards, and
            # deletes the old file.  Prefer it when both layouts exist.
            candidates = [
                env.root / "appcache" / "appinfo.vdf",
                env.root / "steamapps" / "appinfo.vdf",
            ]
            for path in candidates:
                if path.is_file():
                    return cls(CacheKind.MONOLITHIC, appinfo_path=path)
            shard_dir = env.shard_dir
            if shard_dir.is_dir() and any(shard_dir.glob("appinfo_*.vdf")):
                return cls(CacheKind.SHARDS, shard_dir=shard_dir)
            if time.monotonic() >= deadline:
                return cls(CacheKind.MISSING)
            time.sleep(0.25)

    def require(self, root: Path | None = None) -> "AppInfoCache":
        if self.kind == CacheKind.MISSING:
            where = f"\nSteam 根目录：{root}" if root else ""
            raise CacheError(
                "未找到 Steam appinfo 缓存（appcache/appinfo.vdf 或 "
                "appcache/appinfo/appinfo_*.vdf）。\n"
                f"{where}\n"
                "请先启动一次 Steam 让缓存生成，或在设置中检查 Steam 目录。"
            )
        return self

    def load_shards(self) -> ShardSet:
        if self.kind != CacheKind.SHARDS or not self.shard_dir:
            raise CacheError("当前 Steam 使用单文件 appinfo.vdf，不能读取分片")
        return ShardSet.load(self.shard_dir)

    def load_appinfo(self) -> appinfo.AppInfo:
        if self.kind != CacheKind.MONOLITHIC or not self.appinfo_path:
            raise CacheError("当前 Steam 使用分片 appinfo 缓存，不能读取单文件")
        return appinfo.AppInfo.load(self.appinfo_path)

    def describe(self) -> str:
        if self.kind == CacheKind.SHARDS:
            return "分片 appinfo 缓存"
        if self.kind == CacheKind.MONOLITHIC:
            return "单文件 appinfo.vdf"
        return "未找到 appinfo 缓存"


@dataclass
class CacheSnapshot:
    """Cache data loaded before Steam is closed.

    Steam can remove appcache/appinfo while exiting.  Keep the parsed cache
    and its paths so apply/undo can write after shutdown without re-detecting.
    """

    info: AppInfoCache
    shards: ShardSet | None = None
    app_info: appinfo.AppInfo | None = None
    shard_count: int = 0

    @classmethod
    def capture(cls, info: AppInfoCache) -> "CacheSnapshot":
        snapshot = cls(info)
        if info.kind == CacheKind.SHARDS:
            snapshot.shards = info.load_shards()
            snapshot.shard_count = len(list(info.shard_dir.glob("appinfo_*.vdf"))) if info.shard_dir else 0
        elif info.kind == CacheKind.MONOLITHIC:
            snapshot.app_info = info.load_appinfo()
        return snapshot

    def restore_if_missing(self, env: steamenv.SteamEnv, backup_dir: Path) -> None:
        """Restore only appinfo files from the pre-shutdown backup if needed."""
        backup_dir = Path(backup_dir)
        if self.info.kind == CacheKind.SHARDS:
            current = env.shard_dir
            files = list(current.glob("appinfo_*.vdf")) if current.is_dir() else []
            if len(files) >= self.shard_count and files:
                return
            source = backup_dir / "appinfo"
            if not source.is_dir():
                raise CacheError(
                    "Steam 退出后 appinfo 分片缓存消失，但备份中没有 appinfo 目录；"
                    "请重新启动 Steam 后再试。"
                )
            if current.exists():
                shutil.rmtree(current)
            shutil.copytree(source, current)
            return
        if self.info.kind == CacheKind.MONOLITHIC:
            if env.appinfo.is_file():
                return
            for candidate in (backup_dir / "appcache" / "appinfo.vdf",
                              backup_dir / "steamapps" / "appinfo.vdf",
                              backup_dir / "appinfo.vdf"):
                if candidate.is_file():
                    env.appinfo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, env.appinfo)
                    return
            raise CacheError(
                "Steam 退出后 appinfo.vdf 消失，但备份中没有对应文件；"
                "请重新启动 Steam 后再试。"
            )


def detect_for_context(
    env: steamenv.SteamEnv,
    wait: float = 0.0,
    log: Callable[[str], None] | None = None,
) -> AppInfoCache:
    cache = AppInfoCache.detect(env, wait=wait)
    if cache.kind == CacheKind.MISSING:
        if log:
            log(
                "没有找到 appinfo 缓存；如果 Steam 正在启动，请稍后重试。"
            )
    return cache


def diagnostic(env: steamenv.SteamEnv) -> str:
    mono = env.root / "appcache" / "appinfo.vdf"
    alt = env.root / "steamapps" / "appinfo.vdf"
    shard_dir = env.shard_dir
    shards = list(shard_dir.glob("appinfo_*.vdf")) if shard_dir.is_dir() else []
    chosen = AppInfoCache.detect(env)
    def state(path: Path) -> str:
        if not path.is_file():
            return "不存在"
        try:
            st = path.stat()
            return f"存在，{st.st_size} bytes，{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}"
        except OSError:
            return "存在，无法读取属性"
    return (
        "缓存诊断：\n"
        f"- appcache/appinfo.vdf：{state(mono)}\n"
        f"- steamapps/appinfo.vdf：{state(alt)}\n"
        f"- shards：{len(shards)} 个（{shard_dir}）\n"
        f"- 本次将使用：{chosen.describe()}"
    )
