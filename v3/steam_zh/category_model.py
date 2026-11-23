"""Data-driven category model: metadata, weights, thresholds, keywords."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

_MODEL: dict[str, Any] | None = None


def model_path() -> Path:
    return paths.data_dir() / "category_model.json"


def legacy_rules_path() -> Path:
    return paths.data_dir() / "category_rules.json"


def load_model(reload: bool = False) -> dict[str, Any]:
    global _MODEL
    if _MODEL is not None and not reload:
        return _MODEL
    try:
        data = json.loads(model_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    cats = data.get("categories")
    if not isinstance(cats, list) or not cats:
        # Fallback to the old rules file if the new model is missing/broken.
        try:
            old = json.loads(legacy_rules_path().read_text(encoding="utf-8"))
            cats = old.get("categories") or []
        except (OSError, json.JSONDecodeError):
            cats = []
    data["categories"] = [str(c) for c in cats]
    data.setdefault("max_labels", 3)
    data.setdefault("thresholds", {"primary": 2.8, "label": 2.0, "secondary": 1.5})
    data.setdefault("priority", {})
    data.setdefault("secondary_only", [])
    data.setdefault("genre_weights", {})
    data.setdefault("tag_facets", {})
    data.setdefault("facet_caps", {})
    data.setdefault("tag_weights", {})
    data.setdefault("description_keywords", {})
    data.setdefault("description_weight", 1.5)
    data.setdefault("description_cap", 3.0)
    data.setdefault("name_keywords", {})
    data.setdefault("name_weight", 0.75)
    data.setdefault("name_cap", 1.5)
    _MODEL = data
    return data


def category_names() -> list[str]:
    return list(load_model()["categories"])


def priority(name: str) -> int:
    try:
        return int(load_model()["priority"].get(name, 999))
    except (TypeError, ValueError):
        return 999


def overrides_path() -> Path:
    return paths.data_dir() / "category_overrides.json"


def load_overrides() -> dict[str, list[str]]:
    try:
        data = json.loads(overrides_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list):
                out[str(key)] = [str(x) for x in value]
    return out


def save_overrides(data: dict[str, list[str]]) -> None:
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
