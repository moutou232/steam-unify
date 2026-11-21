"""Steam appinfo cache detection for both old and new clients.

Steam has two layouts:

* old:   ``appcache/appinfo.vdf`` (v29, monolithic)
* new:   ``appcache/appinfo/appinfo_XX.vdf`` (v30, sharded)

Never open the monolithic path just because it is the historical default.
All code that touches names must go through :class:`AppInfoCache`.
"""

from __future__ import annotations

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
            shard_dir = env.shard_dir
            if shard_dir.is_dir() and any(shard_dir.glob("appinfo_*.vdf")):
                return cls(CacheKind.SHARDS, shard_dir=shard_dir)
            candidates = [
                env.root / "appcache" / "appinfo.vdf",
                env.root / "steamapps" / "appinfo.vdf",
            ]
            for path in candidates:
                if path.is_file():
                    return cls(CacheKind.MONOLITHIC, appinfo_path=path)
            if time.monotonic() >= deadline:
                return cls(CacheKind.MISSING)
            time.sleep(0.25)

    def require(self) -> "AppInfoCache":
        if self.kind == CacheKind.MISSING:
            raise CacheError(
                "未找到 Steam appinfo 缓存（appcache/appinfo.vdf 或 "
                "appcache/appinfo/appinfo_*.vdf）。\n"
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
