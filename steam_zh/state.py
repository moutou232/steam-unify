"""Persistent local state: translations, categories, managed collections."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import paths

DATA_DIR = paths.data_dir()
STATE_PATH = DATA_DIR / "state.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

APP_VERSION = "2.0.5"


DEFAULT_SETTINGS: dict[str, Any] = {
    "steam_root": "",
    "account": "",
    "use_network": True,
    "auto_translate_new": False,
    "proxy": "",
    "include_software": False,
    "all_known": True,
    "restart_steam": True,
    "lock_appinfo": False,
    "backup_keep": 10,
    "window_mode": "maximized",
    "window_geometry": "",
    "source_priority": [
        "xiaoheihe",
        "steam_official",
        "dictionary",
        "steam_store",
        "3dm",
        "steam_china",
        "steamdb",
        "mymemory",
    ],
}

OLD_SOURCE_PRIORITY = [
    "steam_official",
    "dictionary",
    "steam_store",
    "xiaoheihe",
    "3dm",
    "steam_china",
    "steamdb",
    "mymemory",
]


def _load(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


class State:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data if isinstance(data, dict) else {}
        self.data.setdefault("version", 3)
        self.data.setdefault("apps", {})
        self.data.setdefault("collections", {})
        self.data.setdefault("collection_ids", {})
        self.data.setdefault("collection_owned", {})
        self.data.setdefault("settings", {})
        self._normalize_collections()
        for key, value in DEFAULT_SETTINGS.items():
            self.data["settings"].setdefault(key, value)

    def _normalize_collections(self) -> None:
        cols = self.collections
        ids = self.data["collection_ids"]
        owned = self.data["collection_owned"]
        if not isinstance(cols, dict):
            cols = self.data["collections"] = {}
        if not isinstance(ids, dict):
            ids = self.data["collection_ids"] = {}
        if not isinstance(owned, dict):
            owned = self.data["collection_owned"] = {}
        legacy = not ids and not owned
        for category, cid in list(cols.items()):
            if not cid:
                continue
            category = str(category)
            cid = str(cid)
            bucket = ids.setdefault(category, [])
            if cid not in bucket:
                bucket.append(cid)
            if legacy:
                owned_bucket = owned.setdefault(category, [])
                if cid not in owned_bucket:
                    owned_bucket.append(cid)
        for category, values in list(ids.items()):
            cleaned = [str(cid) for cid in values if cid]
            ids[str(category)] = cleaned
            if cleaned and not cols.get(category):
                cols[str(category)] = cleaned[0]
        for category, values in list(owned.items()):
            owned[str(category)] = [str(cid) for cid in values if cid]
        self.data["collections"] = cols
        self.data["collection_ids"] = ids
        self.data["collection_owned"] = owned

    @classmethod
    def load(cls) -> "State":
        return cls(_load(STATE_PATH, {}))

    def save(self) -> None:
        _atomic_write_json(STATE_PATH, self.data)

    @property
    def apps(self) -> dict[str, Any]:
        return self.data["apps"]

    @property
    def collections(self) -> dict[str, str]:
        return self.data["collections"]

    @property
    def collection_ids(self) -> dict[str, list[str]]:
        return self.data["collection_ids"]

    @property
    def collection_owned(self) -> dict[str, list[str]]:
        return self.data["collection_owned"]

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    def get_app(self, appid: int) -> dict[str, Any]:
        return self.apps.setdefault(str(appid), {})

    def set_app(self, appid: int, **values: Any) -> dict[str, Any]:
        rec = self.get_app(appid)
        rec.update(values)
        return rec

    def is_skipped(self, appid: int) -> bool:
        return bool(self.apps.get(str(appid), {}).get("skipped"))

    def merge_games(self, games) -> None:
        for g in games:
            rec = self.get_app(g.appid)
            rec.setdefault("zh_name", "")
            rec.setdefault("source", "")
            rec.setdefault("categories", [])
            rec.setdefault("primary_category", "")
            rec.setdefault("category", "")
            rec.setdefault("category_source", "")
            rec.setdefault("category_sources", {})
            rec.setdefault("category_confidence", {})
            rec.setdefault("name_candidates", [])
            rec.setdefault("name_provider_errors", [])
            rec.setdefault("name_selected_source", "")
            rec.setdefault("name_confidence", 0.0)
            rec.setdefault("name_selected_by", "")
            rec.setdefault("category_locked", False)
            rec.setdefault("locked", False)
            rec.setdefault("applied", False)
            rec.setdefault("orig_unknown", False)
            rec.setdefault("orig_source", "")
            rec.setdefault("orig_verified", False)
            rec.setdefault("cache_entry", False)
            rec.setdefault("skipped", False)
            rec.setdefault("error", "")
            current = str(g.name or "")
            current_is_cjk = bool(re.search(r"[\u3400-\u9fff]", current))
            zh = str(rec.get("zh_name") or "")
            orig = str(rec.get("orig_name") or "").strip()
            # Never invent an original from a Chinese current name.  If this is
            # a translated cache and no canonical original is known yet, leave
            # it empty and let originals.py ask Steam's English API for it.
            if not orig:
                if current_is_cjk:
                    rec["orig_name"] = ""
                    rec["orig_unknown"] = True
                else:
                    rec["orig_name"] = current
                    rec["orig_unknown"] = False
                    rec["orig_source"] = "appinfo"
                    rec["orig_verified"] = False
                    rec["orig_localized"] = g.localized or rec.get("orig_localized")
            else:
                if (
                    not current_is_cjk
                    and current != zh
                    and current != orig
                    and str(rec.get("source") or "") != "original"
                ):
                    # Steam refreshed the real English name; keep the
                    # translation but update the original for undo.
                    rec["orig_name"] = current
                    rec["orig_unknown"] = False
                    rec["orig_source"] = "appinfo"
                    rec["orig_verified"] = False
                    rec["orig_localized"] = g.localized or rec.get("orig_localized")
                elif not re.search(r"[\u3400-\u9fff]", orig):
                    rec["orig_unknown"] = False
            if not rec.get("orig_localized") and not current_is_cjk and g.localized:
                rec["orig_localized"] = g.localized

    def to_settings(self) -> dict[str, Any]:
        return self.settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.data["settings"].update(settings)
        _atomic_write_json(SETTINGS_PATH, self.settings)
        self.save()

    @staticmethod
    def load_settings() -> dict[str, Any]:
        raw = _load(SETTINGS_PATH, {})
        if not isinstance(raw, dict):
            raw = {}
        data = dict(DEFAULT_SETTINGS)
        data.update(raw)
        # Migrate only the exact old default order.  A user-customized order
        # must never be overwritten.
        if raw.get("source_priority") == OLD_SOURCE_PRIORITY:
            data["source_priority"] = list(DEFAULT_SETTINGS["source_priority"])
        return data


def now_ts() -> int:
    return int(time.time())
