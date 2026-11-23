"""Compatibility wrappers around the weighted multi-label classifier."""

from __future__ import annotations

from . import category_model, scoring


def load_rules() -> tuple[list[str], list[dict]]:
    """Kept for older callers; the new model is data-driven scoring."""
    return category_model.category_names(), []


def normalize_tag(tag: str) -> str:
    return str(tag).strip().lower()


def category_for(
    game,
    rules: list[dict] | None = None,
    categories: list[str] | None = None,
) -> str:
    """Return only the primary label (backward-compatible helper)."""
    result = scoring.classify(game)
    return result.primary or "其他"


def assign_all(games: list) -> dict[int, str]:
    """Return {appid: primary_label} (backward-compatible helper)."""
    results = scoring.classify_all(games)
    return {appid: (res.primary or "其他") for appid, res in results.items()}


if __name__ == "__main__":
    from . import metadata, steamenv

    root = steamenv.detect_root()
    env = steamenv.SteamEnv(root, steamenv.detect_account(root))
    games = metadata.scan(env)
    results = scoring.classify_all(games)
    counts: dict[str, int] = {}
    for game in games:
        for cat in results[game.appid].categories:
            counts[cat] = counts.get(cat, 0) + 1
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{cat}\t{n}")
