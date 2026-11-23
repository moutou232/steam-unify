"""Weighted multi-label category scoring.

Signals (all optional, degrade gracefully):
    1. Steam official genres (appinfo common.genres)
    2. Steam store/user tags (appinfo common.store_tags)
    3. Game description keywords (store cache; optional)
    4. Game name keywords (weak)
    5. Local dictionary/overrides (authoritative)

The output is an ordered list of at most three categories.  The first label is
the primary category.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import category_model
from .metadata import GameInfo

CATEGORY_SOURCE_WEIGHT = {
    "steam_tags": 0.85,
    "steam_genres": 0.75,
    "local_model": 0.70,
    "steamdb": 0.65,
    "xiaoheihe": 0.55,
    "3dm": 0.50,
    "description": 0.45,
}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'’+.-]*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")

TAG_CAP = 6.0
GENRE_CAP = 4.0
DESCRIPTION_CAP = 3.0
NAME_CAP = 1.5


@dataclass
class CategoryResult:
    categories: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    source: str = "auto"

    @property
    def primary(self) -> str:
        return self.categories[0] if self.categories else ""


def _norm_tag(tag: str) -> str:
    return str(tag).strip().lower()


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _keyword_hits(text: str, keywords: Iterable[str]) -> int:
    """Count keyword occurrences; English tokens must match whole words."""
    if not text:
        return 0
    lower = text.casefold()
    count = 0
    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue
        if _has_cjk(kw):
            if kw.casefold() in lower:
                count += 1
        else:
            if re.search(rf"(?<![a-z0-9]){re.escape(kw.casefold())}(?![a-z0-9])", lower):
                count += 1
    return count


def _add(scores: dict[str, float], reasons: dict[str, list[str]], cat: str, value: float, reason: str) -> None:
    if value <= 0:
        return
    scores[cat] += value
    if reason:
        reasons[cat].append(reason)


def _apply_overrides(
    game: GameInfo,
    overrides: dict[str, list[str]],
    model: dict[str, Any],
) -> CategoryResult | None:
    keys = [str(game.appid), game.name]
    for key in keys:
        cats = overrides.get(key)
        if not cats:
            continue
        from .category_state import normalize_categories

        normalized = normalize_categories(cats)
        if not normalized:
            continue
        scores = {c: 100.0 for c in normalized}
        reasons = {c: ["本地词典"] for c in normalized}
        return CategoryResult(normalized, scores, reasons, "dictionary")
    return None


def classify(
    game: GameInfo,
    *,
    description: str | None = None,
    model: dict[str, Any] | None = None,
    overrides: dict[str, list[str]] | None = None,
    extra_names: Iterable[str] | None = None,
) -> CategoryResult:
    model = model or category_model.load_model()
    overrides = overrides if overrides is not None else category_model.load_overrides()
    if overrides:
        result = _apply_overrides(game, overrides, model)
        if result:
            return result

    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)

    # 1. genres (collected first; some are only counted after tag support)
    genre_scores: dict[str, float] = defaultdict(float)
    genre_weights = model.get("genre_weights", {})
    for gid in getattr(game, "genres", []):
        weights = genre_weights.get(str(gid), {})
        for cat, weight in weights.items():
            try:
                genre_scores[cat] += float(weight)
            except (TypeError, ValueError):
                continue

    # 2. Steam tags, with facet collapsing.
    tag_weights: dict[str, dict[str, float]] = {
        _norm_tag(k): {str(c): float(w) for c, w in v.items()}
        for k, v in model.get("tag_weights", {}).items()
        if isinstance(v, dict)
    }
    facets: dict[str, list[str]] = {
        str(name): [_norm_tag(t) for t in tags]
        for name, tags in model.get("tag_facets", {}).items()
    }
    facet_caps = {str(k): float(v) for k, v in model.get("facet_caps", {}).items()}
    facet_min = {str(k): int(v) for k, v in model.get("facet_min_tags", {}).items()}
    second_weight = float(model.get("facet_second_weight", 0.5))
    tag_names = [_norm_tag(t) for t in getattr(game, "tag_names", [])]
    tag_set = set(tag_names)
    tag_scores: dict[str, float] = defaultdict(float)
    for facet, tags in facets.items():
        matched = [t for t in tags if t in tag_set]
        if not matched or len(matched) < facet_min.get(facet, 1):
            continue
        per_cat: dict[str, list[float]] = defaultdict(list)
        for tag in matched:
            for cat, weight in tag_weights.get(tag, {}).items():
                per_cat[cat].append(weight)
        cap = facet_caps.get(facet, TAG_CAP)
        for cat, values in per_cat.items():
            values.sort(reverse=True)
            value = values[0]
            if len(values) > 1:
                value += second_weight * values[1]
            tag_scores[cat] += min(value, cap)
    # Tags not covered by any facet.
    in_facet = {t for tags in facets.values() for t in tags}
    for tag in tag_names:
        if tag in in_facet:
            continue
        for cat, weight in tag_weights.get(tag, {}).items():
            tag_scores[cat] += weight

    # 1b. genres; a few broad genres only count with tag support.
    requires_tag = {str(x) for x in model.get("genre_requires_tag", [])}
    for cat, value in genre_scores.items():
        if cat in requires_tag and tag_scores.get(cat, 0.0) <= 0:
            continue
        _add(scores, reasons, cat, min(value, GENRE_CAP), f"类型 +{min(value, GENRE_CAP):g}")

    for cat, value in tag_scores.items():
        _add(scores, reasons, cat, min(value, TAG_CAP), f"标签 +{min(value, TAG_CAP):g}")

    # 3. description keywords
    desc = description or getattr(game, "description", None) or ""
    if desc:
        for cat, keywords in model.get("description_keywords", {}).items():
            hits = _keyword_hits(desc, keywords or [])
            if hits:
                weight = min(DESCRIPTION_CAP, hits * float(model.get("description_weight", 1.5)))
                _add(scores, reasons, cat, weight, f"简介关键词 +{weight:g}")

    # 4. name keywords (weak)
    names = [getattr(game, "name", "") or ""]
    if extra_names:
        names.extend([str(x) for x in extra_names if x])
    combined_names = " ".join(names)
    if combined_names:
        for cat, keywords in model.get("name_keywords", {}).items():
            hits = _keyword_hits(combined_names, keywords or [])
            if hits:
                weight = min(NAME_CAP, hits * float(model.get("name_weight", 0.75)))
                _add(scores, reasons, cat, weight, f"名称关键词 +{weight:g}")

    # 5. external community category evidence (best-effort, cached)
    allowed = set(model.get("categories", []))
    for evidence in getattr(game, "category_evidence", []) or []:
        try:
            labels = getattr(evidence, "labels", []) or []
            confidence = float(getattr(evidence, "confidence", 0.5))
            source = str(getattr(evidence, "source", "community"))
        except Exception:
            continue
        for cat in labels:
            cat = str(cat).strip()
            if cat in allowed:
                base = CATEGORY_SOURCE_WEIGHT.get(source, 0.5)
                weight = 5.0 * base * max(0.0, min(1.0, confidence))
                _add(scores, reasons, cat, weight, f"{source} +{weight:g}")

    # Remove zero/unknown categories; keep only configured labels.
    scores = {c: s for c, s in scores.items() if c in allowed and s > 0}
    if not scores:
        return CategoryResult(["其他"], {}, {}, "auto")

    priority = model.get("priority", {})
    thresholds = model.get("thresholds", {})
    primary_min = float(thresholds.get("primary", 2.8))
    label_min = float(thresholds.get("label", 2.0))
    secondary_min = float(thresholds.get("secondary", 1.5))
    secondary_only = set(model.get("secondary_only", []))

    def sort_key(cat: str):
        return (-scores.get(cat, 0.0), int(priority.get(cat, 999)), cat)

    ranked = sorted(scores, key=sort_key)
    specific = [c for c in ranked if c not in secondary_only]
    secondary = [c for c in ranked if c in secondary_only]

    chosen: list[str] = []
    if specific and scores[specific[0]] >= primary_min:
        chosen.append(specific[0])
    elif specific and scores[specific[0]] >= label_min:
        chosen.append(specific[0])
    elif secondary and scores[secondary[0]] >= secondary_min:
        chosen.append(secondary[0])
    else:
        # A low-confidence specific signal is still more useful than "其他".
        if specific and scores[specific[0]] >= 1.0:
            chosen.append(specific[0])
        else:
            return CategoryResult(["其他"], scores, reasons, "auto")

    for cat in specific:
        if len(chosen) >= 3:
            break
        if cat in chosen:
            continue
        if scores[cat] >= label_min:
            chosen.append(cat)

    for cat in secondary:
        if len(chosen) >= 3:
            break
        if cat in chosen:
            continue
        if scores[cat] >= secondary_min:
            chosen.append(cat)

    # Keep at most 3 and make the highest-score specific label primary.
    chosen = chosen[:3]
    chosen.sort(key=sort_key)
    selected_scores = {c: round(scores.get(c, 0.0), 3) for c in chosen}
    selected_reasons = {c: reasons.get(c, [])[:6] for c in chosen}
    return CategoryResult(chosen, selected_scores, selected_reasons, "auto")


def classify_all(
    games: list[GameInfo],
    descriptions: dict[str, str] | None = None,
    overrides: dict[str, list[str]] | None = None,
) -> dict[int, CategoryResult]:
    model = category_model.load_model()
    overrides = overrides if overrides is not None else category_model.load_overrides()
    out: dict[int, CategoryResult] = {}
    for game in games:
        desc = None
        if descriptions:
            desc = descriptions.get(str(game.appid))
        out[game.appid] = classify(
            game,
            description=desc,
            model=model,
            overrides=overrides,
            extra_names=[getattr(game, "original_name", "") or ""],
        )
    return out
