"""High level operations shared by the GUI and CLI."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import apply, categories, category_batch, category_model, category_state, collections, metadata, originals, paths, providers, scoring, steamenv, translate, undo
from .metadata import GameInfo
from .state import State

BASE_DIR = paths.app_dir()
DATA_DIR = paths.data_dir()


@dataclass
class Context:
    env: steamenv.SteamEnv
    state: State
    games: list[GameInfo]
    include_software: bool = False
    all_known: bool = False
    new_appids: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    originals_pending: set[int] = field(default_factory=set)
    undo_prepared: bool = False


def make_env(settings: dict) -> steamenv.SteamEnv:
    root = steamenv.detect_root(settings.get("steam_root") or None)
    account = steamenv.detect_account(root, settings.get("account") or None)
    env = steamenv.SteamEnv(root, account)
    env.check()
    return env


def load_context(settings: dict) -> Context:
    paths.ensure_data()
    env = make_env(settings)
    st = State.load()
    category_state.migrate(st)
    existing_ids = set(st.apps)
    # Collection IDs are per Steam account.  If the active account changed,
    # forget the previous account's IDs; apply() will adopt same-name
    # collections on the new account when possible.
    previous_account = str(st.data.get("account") or "")
    if previous_account and previous_account != env.account:
        st.data["collections"] = {}
        st.save()
    st.data["account"] = env.account
    scan_warnings: list[str] = []
    games = metadata.scan(
        env,
        include_software=bool(settings.get("include_software")),
        all_known=bool(settings.get("all_known")),
        warnings=scan_warnings,
    )
    ctx = Context(
        env=env,
        state=st,
        games=games,
        include_software=bool(settings.get("include_software")),
        all_known=bool(settings.get("all_known")),
        new_appids={g.appid for g in games if str(g.appid) not in existing_ids},
        warnings=scan_warnings,
    )
    merge_state(ctx)
    st.save()
    return ctx


def _attach_originals(ctx: Context) -> None:
    for game in ctx.games:
        rec = ctx.state.get_app(game.appid)
        game.original_name = str(rec.get("orig_name") or "")
        if not game.description:
            game.description = str(rec.get("description") or "")
        try:
            game.category_evidence = providers.load_category_evidence(game, network=False)
        except Exception:
            game.category_evidence = []


def merge_state(ctx: Context) -> None:
    ctx.state.merge_games(ctx.games)
    repair = originals.repair_state(ctx.state, ctx.games, fetch=False)
    ctx.originals_pending = set(repair.pending)
    _attach_originals(ctx)
    results = scoring.classify_all(ctx.games)
    for g in ctx.games:
        rec = ctx.state.get_app(g.appid)
        if rec.get("skipped"):
            continue
        if rec.get("category_locked"):
            cats = category_state.get_categories(rec)
            if cats:
                category_state.set_categories(
                    rec,
                    cats,
                    source=str(rec.get("category_source") or "manual"),
                    scores=category_state.get_scores(rec) or None,
                    reasons=rec.get("category_reasons") if isinstance(rec.get("category_reasons"), dict) else None,
                    locked=True,
                )
            continue
        result = results.get(g.appid)
        if result:
            category_state.set_categories(
                rec,
                result.categories,
                source="auto",
                scores=result.scores,
                reasons=result.reasons,
                locked=False,
            )
    ctx.state.save()


def auto_categorize(ctx: Context) -> dict[int, str]:
    _attach_originals(ctx)
    results = scoring.classify_all(ctx.games)
    changed = 0
    for g in ctx.games:
        rec = ctx.state.get_app(g.appid)
        if rec.get("skipped"):
            continue
        if rec.get("category_locked"):
            continue
        res = results.get(g.appid)
        if not res:
            continue
        old = tuple(category_state.get_categories(rec))
        cats = category_state.set_categories(
            rec,
            res.categories,
            source="auto",
            scores=res.scores,
            reasons=res.reasons,
            locked=False,
        )
        rec["category_confidence"] = {
            c: round(float(res.scores.get(c, 0.0)), 3) for c in cats
        }
        rec["category_sources"] = {
            c: [
                ev.as_dict() for ev in (getattr(g, "category_evidence", []) or [])
                if c in (getattr(ev, "labels", []) or [])
            ]
            for c in cats
        }
        if tuple(cats) != old:
            changed += 1
    ctx.state.save()
    return {g.appid: (results[g.appid].primary if g.appid in results else "其他") for g in ctx.games}


def batch_preview(ctx: Context, appids: list[int], targets: list[str], mode: str):
    return category_batch.plan_batch(ctx.state, appids, targets, mode)


def batch_apply(ctx: Context, preview) -> dict[int, list[str]]:
    snapshot = category_batch.apply_batch(ctx.state, preview)
    return snapshot


def batch_undo(ctx: Context, snapshot: dict[int, list[str]]) -> None:
    category_batch.undo_batch(ctx.state, snapshot)


def fetch_missing_originals(
    ctx: Context,
    proxy: str | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    force: bool = False,
) -> originals.RepairResult:
    result = originals.repair_state(
        ctx.state,
        ctx.games,
        fetch=True,
        force=force,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
    )
    ctx.originals_pending = set(result.pending)
    _attach_originals(ctx)
    ctx.state.save()
    return result


def prepare_originals(ctx: Context) -> originals.RepairResult:
    result = originals.repair_state(ctx.state, ctx.games, fetch=False)
    ctx.originals_pending = set(result.pending)
    _attach_originals(ctx)
    return result


def prepare_undo(
    ctx: Context,
    proxy: str | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> originals.UndoPreparation:
    originals.repair_state(
        ctx.state,
        ctx.games,
        fetch=True,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
    )
    result = originals.prepare_undo(
        ctx.env,
        ctx.state,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
    )
    ctx.originals_pending = set(result.pending)
    ctx.undo_prepared = True
    _attach_originals(ctx)
    ctx.state.save()
    return result


def auto_translate(
    ctx: Context,
    use_network: bool = True,
    proxy: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    games: list[GameInfo] | None = None,
    priority_order: list[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[int, str]:
    return providers.translate_games_v2(
        games if games is not None else ctx.games,
        ctx.state,
        use_network=use_network,
        proxy=proxy,
        progress=progress,
        should_stop=should_stop,
        priority_order=priority_order,
        log=log,
    )


def reset_undo_names(ctx: Context, log: Callable[[str], None] | None = None) -> int:
    """Re-enable explicit auto-translation for records restored by undo.

    Only records marked source="original" are touched.  Manual edits are
    source="manual" and are intentionally preserved.
    """
    changed = 0
    for rec in ctx.state.apps.values():
        if str(rec.get("source") or "") != "original":
            continue
        rec["zh_name"] = ""
        rec["source"] = ""
        rec["detail"] = ""
        rec["locked"] = False
        rec["applied"] = False
        rec.pop("translated_at", None)
        rec.pop("applied_at", None)
        changed += 1
    if changed:
        ctx.state.save()
        if log:
            log(f"检测到 {changed} 个“已复原”记录，已重新加入自动翻译")
    return changed


def reset_undo_categories(ctx: Context, log: Callable[[str], None] | None = None) -> int:
    """Unlock categories created by the undo feature for explicit auto-categorize."""
    changed = 0
    for rec in ctx.state.apps.values():
        if str(rec.get("category_source") or "") != "undo":
            continue
        rec["category_locked"] = False
        rec["category_source"] = ""
        rec["categories"] = []
        rec["primary_category"] = ""
        rec["category"] = ""
        rec.pop("category_scores", None)
        rec.pop("category_reasons", None)
        changed += 1
    if changed:
        ctx.state.save()
        if log:
            log(f"检测到 {changed} 个“已复原”分类，已重新加入自动分类")
    return changed


def collection_cleanup_count(ctx: Context) -> int:
    try:
        ns = collections.load_json_array(ctx.env.namespace_file)
        return len(collections.category_collection_keys(ns))
    except Exception:
        return 0


def counts(ctx: Context) -> dict[str, int]:
    out = {
        "total": len(ctx.games),
        "installed": 0,
        "official": 0,
        "dictionary": 0,
        "machine": 0,
        "steam": 0,
        "manual": 0,
        "community": 0,
        "missing": 0,
        "applied": 0,
    }
    for g in ctx.games:
        rec = ctx.state.get_app(g.appid)
        out["installed"] += 1 if g.installed else 0
        src = str(rec.get("source") or "")
        if rec.get("applied"):
            out["applied"] += 1
        if not rec.get("zh_name"):
            out["missing"] += 1
        elif src in ("steam_official", "steam_china"):
            out["official"] += 1
        elif src in ("dictionary",):
            out["dictionary"] += 1
        elif src in ("mymemory",):
            out["machine"] += 1
        elif src in ("3dm", "xiaoheihe", "steamdb"):
            out["community"] += 1
        elif src in out:
            out[src] += 1
        elif src == "already":
            out["official"] += 1
    return out


def export_report(ctx: Context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["appid", "原名", "中文名", "来源", "分类", "已安装", "已拥有", "已应用", "备注"]
        )
        for g in ctx.games:
            rec = ctx.state.get_app(g.appid)
            w.writerow(
                [
                    g.appid,
                    g.name,
                    rec.get("zh_name", ""),
                    rec.get("source", ""),
                    " / ".join(category_state.get_categories(rec)),
                    1 if g.installed else 0,
                    1 if g.owned else 0,
                    1 if rec.get("applied") else 0,
                    rec.get("error", "") or rec.get("detail", ""),
                ]
            )


def do_apply(
    ctx: Context,
    selected: set[int] | None,
    restart: bool,
    lock_appinfo: bool,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    mode: str = "both",
) -> apply.ApplyResult:
    return apply.apply_changes(
        ctx.env,
        ctx.state,
        ctx.games,
        selected=selected,
        restart=restart,
        lock_appinfo=lock_appinfo,
        progress=progress,
        log=log,
        mode=mode,
    )


def do_apply_names(ctx: Context, selected: set[int] | None, restart: bool,
                   lock_appinfo: bool, progress=None, log=None):
    return do_apply(ctx, selected, restart, lock_appinfo, progress, log, mode="names")


def do_apply_categories(ctx: Context, selected: set[int] | None, restart: bool,
                        progress=None, log=None):
    return do_apply(ctx, selected, restart, False, progress, log, mode="categories")


def do_undo_all(
    ctx: Context,
    restart: bool = True,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    mode: str = "both",
) -> undo.UndoResult:
    return undo.undo_all(
        ctx.env,
        ctx.state,
        ctx.games,
        mode=mode,
        restart=restart,
        progress=progress,
        log=log,
    )


def do_verify(
    ctx: Context,
    wait: float = 12.0,
    log: Callable[[str], None] | None = None,
    mode: str = "both",
) -> apply.ApplyResult:
    return apply.verify(ctx.env, ctx.state, ctx.games, wait=wait, log=log, mode=mode)


def do_restore(
    ctx: Context,
    backup_dir: Path,
    restart: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    apply.restore_backup(ctx.env, ctx.state, backup_dir, restart=restart, log=log)


def do_autofix(
    ctx: Context,
    restart: bool = True,
    lock_appinfo: bool = False,
    log: Callable[[str], None] | None = None,
) -> apply.ApplyResult:
    return apply.autofix(
        ctx.env,
        ctx.state,
        ctx.games,
        restart=restart,
        lock_appinfo=lock_appinfo,
        log=log,
    )


def latest_backups(limit: int = 10) -> list[Path]:
    base = apply.BACKUP_ROOT
    if not base.is_dir():
        return []
    dirs = [
        p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
    ]
    dirs.sort(key=lambda p: p.name, reverse=True)
    return dirs[:limit]
