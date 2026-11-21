"""Multi-label category state helpers, validation and v1 -> v2 migration."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from . import category_model, paths

MAX_LABELS = 3


def _as_list(items: Any) -> list[str]:
    if items is None:
        return []
    if isinstance(items, str):
        # tolerate a legacy comma/slash separated field
        raw = items.replace("，", ",").replace("/", ",").replace("、", ",")
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(items, (list, tuple, set)):
        return [str(x).strip() for x in items if str(x).strip()]
    return []


def normalize_categories(
    items: Any,
    *,
    scores: dict[str, float] | None = None,
    trim_by_scores: bool = False,
    allowed: list[str] | None = None,
) -> list[str]:
    """Deduplicate, filter unknown categories and enforce max 3 labels.

    Manual edits keep their given order; automated results can be trimmed by
    score (highest first) before the final priority tie-break.
    """
    model = category_model.load_model()
    allowed_list = allowed or model["categories"]
    allowed_set = set(allowed_list)
    priority = model.get("priority", {})
    out: list[str] = []
    seen: set[str] = set()
    for cat in _as_list(items):
        if cat not in allowed_set or cat in seen:
            continue
        seen.add(cat)
        out.append(cat)
    if len(out) > MAX_LABELS and trim_by_scores and scores:
        out.sort(
            key=lambda c: (
                -float(scores.get(c, 0)),
                int(priority.get(c, 999)),
                c,
            )
        )
    return out[:MAX_LABELS]


def get_categories(rec: dict[str, Any]) -> list[str]:
    items = rec.get("categories")
    if not isinstance(items, list) or not items:
        # v1 compatibility: a single category string
        legacy = rec.get("category")
        if legacy:
            items = [legacy]
        else:
            items = []
    return normalize_categories(items)


def set_categories(
    rec: dict[str, Any],
    categories: Any,
    *,
    source: str = "auto",
    scores: dict[str, float] | None = None,
    reasons: dict[str, list[str]] | None = None,
    locked: bool = False,
    trim_by_scores: bool = False,
) -> list[str]:
    cats = normalize_categories(categories, scores=scores, trim_by_scores=trim_by_scores)
    rec["categories"] = cats
    rec["primary_category"] = cats[0] if cats else ""
    rec["category"] = cats[0] if cats else ""  # compatibility with older readers
    rec["category_source"] = source
    rec["category_locked"] = bool(locked)
    if scores:
        rec["category_scores"] = {
            c: round(float(scores.get(c, 0.0)), 3) for c in cats
        }
    else:
        rec.pop("category_scores", None)
    if reasons:
        rec["category_reasons"] = {c: list(reasons.get(c, []))[:8] for c in cats}
    else:
        rec.pop("category_reasons", None)
    return cats


def get_primary(rec: dict[str, Any]) -> str:
    cats = get_categories(rec)
    return cats[0] if cats else ""


def get_scores(rec: dict[str, Any]) -> dict[str, float]:
    raw = rec.get("category_scores")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def migrate(st: Any) -> bool:
    """Upgrade a State object to schema version 2. Returns True if changed."""
    data = st.data
    version = int(data.get("version", 1) or 1)
    changed = False
    if version < 2:
        # Keep a one-time backup next to state.json.
        state_path = paths.data_dir() / "state.json"
        if state_path.is_file():
            backup = paths.data_dir() / "state.v1.bak"
            if not backup.exists():
                try:
                    shutil.copy2(state_path, backup)
                except OSError:
                    pass
        for rec in data.get("apps", {}).values():
            if not isinstance(rec, dict):
                continue
            cats = get_categories(rec)
            source = str(rec.get("category_source") or "auto")
            set_categories(
                rec,
                cats,
                source=source,
                scores=get_scores(rec) or None,
                reasons=rec.get("category_reasons") if isinstance(rec.get("category_reasons"), dict) else None,
                locked=bool(rec.get("category_locked")),
            )
        data["version"] = 2
        version = 2
        changed = True
    if version < 3:
        state_path = paths.data_dir() / "state.json"
        if state_path.is_file():
            backup = paths.data_dir() / "state.v2.bak"
            if not backup.exists():
                try:
                    shutil.copy2(state_path, backup)
                except OSError:
                    pass
        for rec in data.get("apps", {}).values():
            if not isinstance(rec, dict):
                continue
            rec.setdefault("name_candidates", [])
            rec.setdefault("name_provider_errors", [])
            rec.setdefault("orig_unknown", False)
            rec.setdefault("orig_source", "")
            rec.setdefault("orig_verified", False)
            rec.setdefault("cache_entry", False)
            rec.setdefault("skipped", False)
            rec.setdefault("name_selected_source", "")
            rec.setdefault("name_confidence", 0.0)
            rec.setdefault("name_selected_by", "")
            rec.setdefault("category_sources", {})
            rec.setdefault("category_confidence", {})
        data["version"] = 3
        changed = True
    else:
        # Repair malformed / oversized records without discarding the version.
        for rec in data.get("apps", {}).values():
            if not isinstance(rec, dict):
                continue
            cats = get_categories(rec)
            if cats != rec.get("categories") or rec.get("primary_category") != (cats[0] if cats else ""):
                set_categories(
                    rec,
                    cats,
                    source=str(rec.get("category_source") or "auto"),
                    scores=get_scores(rec) or None,
                    reasons=rec.get("category_reasons") if isinstance(rec.get("category_reasons"), dict) else None,
                    locked=bool(rec.get("category_locked")),
                )
                changed = True
    if changed:
        st.save()
    return changed
