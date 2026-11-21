"""Scan the Steam library and enrich app metadata from appinfo.vdf."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import appinfo, cache, descriptions, paths, steamenv
from .appinfo_shards import ShardSet


DATA_DIR = paths.data_dir()


DEFAULT_GENRES: dict[int, str] = {
    1: "动作",
    2: "策略",
    3: "角色扮演",
    4: "休闲",
    9: "竞速",
    18: "体育",
    23: "独立",
    25: "冒险",
    28: "模拟",
    29: "大型多人在线",
    37: "免费开玩",
    51: "动画与建模",
    52: "音频制作",
    53: "设计与插画",
    54: "教育",
    55: "游戏开发",
    56: "照片编辑",
    57: "实用工具",
    58: "视频制作",
    59: "网络出版",
    60: "软件培训",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def genre_names() -> dict[int, str]:
    data = load_json(DATA_DIR / "genres_zh.json", {})
    out = dict(DEFAULT_GENRES)
    for k, v in data.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


def fallback_tag_names() -> dict[int, str]:
    data = load_json(DATA_DIR / "tag_names_zh.json", {})
    out: dict[int, str] = {}
    for k, v in data.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class GameInfo:
    appid: int
    name: str
    type: str
    genres: list[int] = field(default_factory=list)
    tags: list[int] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)
    localized: dict[str, str] = field(default_factory=dict)
    description: str = ""
    description_source: str = ""
    original_name: str = ""
    category_evidence: list = field(default_factory=list)
    installed: bool = False
    owned: bool = False
    acf: Path | None = None
    library: Path | None = None

    @property
    def is_game(self) -> bool:
        return self.type.lower() == "game"

    @property
    def official_zh(self) -> str | None:
        return self.localized.get("schinese") or self.localized.get("sc_schinese")

    @property
    def has_cjk(self) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in self.name)


def _int_values(node: dict | list | None) -> list[int]:
    if isinstance(node, dict):
        vals = node.values()
    elif isinstance(node, list):
        vals = node
    else:
        return []
    out: list[int] = []
    for v in vals:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def scan(
    env: steamenv.SteamEnv,
    include_software: bool = False,
    all_known: bool = False,
    warnings: list[str] | None = None,
) -> list[GameInfo]:
    """Return games owned/installed by the active account.

    all_known=True also includes every game-type app in the local appinfo cache
    (including Family-shared games that have no localconfig entry).
    """
    installed = steamenv.installed_apps(env.root)
    owned = steamenv.owned_appids(env)
    tag_map = steamenv.tag_names(env)
    desc_cache = descriptions.load_cache()
    fallback = fallback_tag_names()
    for tid, name in fallback.items():
        tag_map.setdefault(tid, name)

    wanted_types = {"game"}
    if include_software:
        wanted_types |= {"application", "tool"}

    shards = None
    ai = None
    app_cache = cache.AppInfoCache.detect(env)
    if app_cache.kind == cache.CacheKind.SHARDS:
        shards = app_cache.load_shards()
        if warnings is not None:
            warnings.extend(shards.warnings)
        all_ids = set(shards.appids)
    elif app_cache.kind == cache.CacheKind.MONOLITHIC:
        ai = app_cache.load_appinfo()
        all_ids = set(ai._by_appid)
    else:
        return []

    candidate_ids = all_ids if all_known else (owned | set(installed))

    out: list[GameInfo] = []
    for appid in candidate_ids:
        if shards is not None:
            fields = shards.common_fields(appid)
            if not fields:
                continue
            name = fields["name"]
            typ = fields["type"]
            genre_ids = fields["genres"]
            tag_ids = fields["store_tags"]
            localized = fields["localized"]
        else:
            assert ai is not None
            entry = ai.entry(appid)
            if entry is None:
                continue
            common = ai.common(entry)
            if common is None:
                continue
            name_node = None
            type_node = None
            genres_node = None
            tags_node = None
            for child in common.children():
                key = ai.key_text(child)
                if key == "name":
                    name_node = child
                elif key == "type":
                    type_node = child
                elif key == "genres":
                    genres_node = child
                elif key == "store_tags":
                    tags_node = child
            if name_node is None or name_node.type != appinfo.T_STRING:
                continue
            name = name_node.value.decode("utf-8", "replace")
            typ = (
                type_node.value.decode("utf-8", "replace")
                if type_node is not None and type_node.type == appinfo.T_STRING
                else ""
            )
            genre_ids = (
                _int_values([c.value for c in genres_node.children()])
                if genres_node is not None and genres_node.type == appinfo.T_OBJECT
                else []
            )
            tag_ids = (
                _int_values([c.value for c in tags_node.children()])
                if tags_node is not None and tags_node.type == appinfo.T_OBJECT
                else []
            )
            localized = ai.get_localized(appid) or {}

        if typ.lower() not in wanted_types:
            continue
        info = installed.get(appid)
        out.append(
            GameInfo(
                appid=appid,
                name=name,
                type=typ,
                genres=genre_ids,
                tags=tag_ids,
                tag_names=[tag_map[t] for t in tag_ids if t in tag_map],
                localized=localized,
                description=desc_cache.get(str(appid), ""),
                description_source="steam" if str(appid) in desc_cache else "",
                original_name=name,
                installed=info is not None,
                owned=appid in owned,
                acf=info["acf"] if info else None,
                library=info["library"] if info else None,
            )
        )
    out.sort(key=lambda g: (g.name.lower(), g.appid))
    return out


def export_tag_names(env: steamenv.SteamEnv, path: Path) -> None:
    """Write the active account's localized tag table for offline fallback."""
    tags = steamenv.tag_names(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({str(k): v for k, v in sorted(tags.items())}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
