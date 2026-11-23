"""Batch category editing with a hard 3-label cap and undo snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import category_state

MODES = ("append", "replace", "remove")


@dataclass
class BatchPlan:
    appid: int
    old: list[str]
    new: list[str]
    dropped: list[str] = field(default_factory=list)
    was_locked: bool = False
    cap_truncated: bool = False

    @property
    def changed(self) -> bool:
        return self.old != self.new


@dataclass
class BatchPreview:
    plans: list[BatchPlan]
    targets: list[str]
    mode: str

    @property
    def changed(self) -> list[BatchPlan]:
        return [p for p in self.plans if p.changed]

    @property
    def truncated(self) -> list[BatchPlan]:
        return [p for p in self.plans if p.cap_truncated]

    @property
    def locked(self) -> list[BatchPlan]:
        return [p for p in self.plans if p.was_locked]


def plan_batch(
    state: Any,
    appids: Iterable[int],
    targets: list[str],
    mode: str,
    *,
    max_labels: int = category_state.MAX_LABELS,
) -> BatchPreview:
    if mode not in MODES:
        raise ValueError(f"未知批量模式: {mode}")
    target_cats = category_state.normalize_categories(targets)
    if mode in ("append", "replace") and not target_cats:
        raise ValueError("请至少选择一个分类")
    if len(target_cats) > max_labels:
        raise ValueError(f"最多只能选择 {max_labels} 个分类")

    plans: list[BatchPlan] = []
    for appid in appids:
        appid = int(appid)
        rec = state.get_app(appid)
        old = category_state.get_categories(rec)
        was_locked = bool(rec.get("category_locked"))
        dropped: list[str] = []
        cap_truncated = False
        if mode == "replace":
            new = list(target_cats[:max_labels])
            dropped = [c for c in old if c not in new]
        elif mode == "remove":
            new = [c for c in old if c not in target_cats]
        else:  # append
            new = list(old)
            for cat in target_cats:
                if cat not in new:
                    new.append(cat)
            if len(new) > max_labels:
                dropped = new[max_labels:]
                new = new[:max_labels]
                cap_truncated = True
        plans.append(
            BatchPlan(
                appid=appid,
                old=old,
                new=new,
                dropped=dropped,
                was_locked=was_locked,
                cap_truncated=cap_truncated,
            )
        )
    return BatchPreview(plans, target_cats, mode)


def apply_batch(state: Any, preview: BatchPreview) -> dict[int, list[str]]:
    """Persist plans and return a one-step undo snapshot."""
    snapshot: dict[int, list[str]] = {}
    for plan in preview.plans:
        snapshot[plan.appid] = list(plan.old)
        rec = state.get_app(plan.appid)
        category_state.set_categories(
            rec,
            plan.new,
            source="manual",
            locked=True,
        )
    state.save()
    return snapshot


def undo_batch(state: Any, snapshot: dict[int, list[str]]) -> None:
    for appid, cats in snapshot.items():
        rec = state.get_app(int(appid))
        category_state.set_categories(rec, cats, source="manual", locked=True)
    state.save()
