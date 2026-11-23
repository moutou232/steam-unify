"""Steam collection-based category plans (v3)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import apply, collections, paths, steamenv

PLAN_FORMAT = "steam-unify-collection-plan"
PLAN_VERSION = 1
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_FILES = 100


class PlanError(Exception):
    pass


def normalize_name(value: str) -> str:
    text = str(value or "").casefold().strip()
    text = text.replace("™", "").replace("®", "").replace("©", "")
    text = re.sub(r"\b(deluxe|definitive|ultimate|complete|premium|enhanced)\s+edition\b", "", text, flags=re.I)
    text = re.sub(r"[：:—–\-_/\\]+", " ", text)
    text = re.sub(r"[^\w\u3400-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class PlanGame:
    appid: int
    names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"appid": self.appid, "names": list(self.names)}


@dataclass
class PlanCollection:
    name: str
    appids: list[int] = field(default_factory=list)
    games: list[PlanGame] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "appids": list(self.appids),
            "games": [g.as_dict() for g in self.games],
            "note": self.note,
        }


@dataclass
class Plan:
    name: str
    collections: list[PlanCollection]
    created: int = 0
    author: str = ""


@dataclass
class MatchedCollection:
    plan: PlanCollection
    appids: list[int]
    unmatched: list[PlanGame]
    existing: bool = False
    dynamic: bool = False
    action: str = "merge"


@dataclass
class Preview:
    plan: Plan
    collections: list[MatchedCollection]
    local_only: int = 0

    @property
    def matched_games(self) -> int:
        return len({a for c in self.collections for a in c.appids})

    @property
    def unmatched_games(self) -> int:
        return sum(len(c.unmatched) for c in self.collections)

    @property
    def conflicts(self) -> int:
        return sum(1 for c in self.collections if c.existing)


def read_live_collections(ctx) -> list[PlanCollection]:
    namespace = collections.load_json_array(ctx.env.namespace_file)
    grouped: dict[str, list[dict]] = {}
    for key, rec in _pairs(namespace):
        if not key.startswith(collections.COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        cid = key[len(collections.COLLECTION_PREFIX):]
        if cid in collections.SYSTEM_IDS:
            continue
        try:
            value = json.loads(rec.get("value") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if "filterSpec" in value:
            continue
        name = str(value.get("name") or "").strip()
        if not name:
            continue
        added = _appids(value.get("added"))
        removed = _appids(value.get("removed"))
        grouped.setdefault(name.casefold(), []).append({
            "name": name,
            "appids": added - removed,
        })
    out: list[PlanCollection] = []
    for items in grouped.values():
        name = str(items[0]["name"])
        ids = sorted({a for item in items for a in item["appids"]})
        out.append(PlanCollection(name=name, appids=ids))
    out.sort(key=lambda c: c.name.casefold())
    return out


def _pairs(namespace: list) -> Iterable[tuple[str, dict]]:
    for item in namespace:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict):
            yield item[0], item[1]


def _appids(values: Any) -> set[int]:
    out: set[int] = set()
    if isinstance(values, list):
        for value in values:
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out.add(n)
    return out


def _game_names(ctx, appid: int) -> list[str]:
    names: list[str] = []
    by_id = getattr(ctx, "games_by_id", None)
    if not by_id:
        by_id = {g.appid: g for g in getattr(ctx, "games", [])}
    game = by_id.get(appid)
    rec = ctx.state.get_app(appid)
    for value in (
        getattr(game, "name", "") if game else "",
        getattr(game, "official_zh", "") if game else "",
        rec.get("orig_name"), rec.get("zh_name"),
    ):
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def build_plan(ctx, selected_names: list[str] | None = None, plan_name: str = "我的分类方案") -> Plan:
    wanted = {normalize_name(n) for n in selected_names} if selected_names else None
    collections_out: list[PlanCollection] = []
    for live in read_live_collections(ctx):
        if wanted is not None and normalize_name(live.name) not in wanted:
            continue
        games = [PlanGame(appid=a, names=_game_names(ctx, a)) for a in live.appids]
        collections_out.append(PlanCollection(live.name, list(live.appids), games))
    return Plan(name=plan_name, collections=collections_out, created=int(time.time()))


def plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "schema": 1,
        "name": plan.name,
        "created": plan.created,
        "collections": [c.as_dict() for c in plan.collections],
    }


def _zip_manifest(data: bytes, plan: Plan) -> dict[str, Any]:
    return {
        "format": PLAN_FORMAT,
        "format_version": PLAN_VERSION,
        "created": int(plan.created or time.time()),
        "plan_name": plan.name,
        "author": plan.author,
        "collections_count": len(plan.collections),
        "games_count": sum(len(c.appids) for c in plan.collections),
        "sha256": hashlib.sha256(data).hexdigest(),
        "files": ["plan/collections.json"],
    }


def export_plan(plan: Plan, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(plan_payload(plan), ensure_ascii=False, indent=2).encode("utf-8")
    manifest = _zip_manifest(data, plan)
    with tempfile.TemporaryDirectory(prefix="steam-unify-plan-") as td:
        tmp = Path(td) / "plan.zip"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("plan/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("plan/collections.json", data)
            zf.writestr("plan/README.txt", "Steam 统译 v3 分类方案\n只包含静态收藏集，不包含账号或缓存文件。\n")
        shutil.copy2(tmp, path)
    return path


def read_plan(path: Path) -> Plan:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if len(names) > MAX_ZIP_FILES:
                raise PlanError("方案文件条目过多")
            total = sum(zf.getinfo(n).file_size for n in names)
            if total > MAX_ZIP_BYTES:
                raise PlanError("方案文件过大")
            for n in names:
                if ".." in Path(n).parts or n.startswith("/"):
                    raise PlanError("方案文件路径不安全")
            manifest = json.loads(zf.read("plan/manifest.json").decode("utf-8"))
            if manifest.get("format") != PLAN_FORMAT:
                raise PlanError("不是 Steam 统译分类方案")
            if int(manifest.get("format_version", 0)) > PLAN_VERSION:
                raise PlanError("方案版本过高，请升级程序")
            raw = zf.read("plan/collections.json")
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise PlanError(f"无法读取方案：{exc}") from exc
    if hashlib.sha256(raw).hexdigest() != str(manifest.get("sha256") or ""):
        raise PlanError("方案校验失败，文件可能已损坏")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError("方案内容损坏") from exc
    collections_out: list[PlanCollection] = []
    for item in payload.get("collections", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        appids = sorted(_appids(item.get("appids")))
        games = []
        for g in item.get("games", []):
            if isinstance(g, dict):
                try:
                    appid = int(g.get("appid"))
                except (TypeError, ValueError):
                    continue
                if appid > 0:
                    names = [str(x).strip() for x in g.get("names", []) if str(x).strip()]
                    games.append(PlanGame(appid, names))
        collections_out.append(PlanCollection(name, appids, games, str(item.get("note") or "")))
    return Plan(str(payload.get("name") or path.stem), collections_out,
                int(payload.get("created") or manifest.get("created") or 0),
                str(manifest.get("author") or ""))


def match_plan(ctx, plan: Plan) -> Preview:
    local = {g.appid: g for g in ctx.games}
    namespace = collections.load_json_array(ctx.env.namespace_file)
    dynamic_names: set[str] = set()
    for key, rec in _pairs(namespace):
        if not key.startswith(collections.COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        try:
            value = json.loads(rec.get("value") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if "filterSpec" in value:
            dynamic_names.add(normalize_name(str(value.get("name") or "")))
    name_index: dict[str, set[int]] = {}
    for g in ctx.games:
        rec = ctx.state.get_app(g.appid)
        for value in (getattr(g, "name", ""), getattr(g, "official_zh", ""),
                      rec.get("orig_name"), rec.get("zh_name")):
            key = normalize_name(str(value or ""))
            if key:
                name_index.setdefault(key, set()).add(g.appid)
    live_names = {normalize_name(c.name): c for c in read_live_collections(ctx)}
    matched: list[MatchedCollection] = []
    for pc in plan.collections:
        ids: set[int] = set()
        unmatched: list[PlanGame] = []
        for pg in pc.games or [PlanGame(a) for a in pc.appids]:
            if pg.appid in local:
                ids.add(pg.appid)
                continue
            hit: set[int] = set()
            for name in pg.names:
                hit |= name_index.get(normalize_name(name), set())
            if len(hit) == 1:
                ids.add(next(iter(hit)))
            else:
                # Last-resort unique fuzzy match.  Never guess when two local
                # games are equally close.
                import difflib
                best_score = 0.0
                best_ids: set[int] = set()
                for name in pg.names:
                    probe = normalize_name(name)
                    if not probe:
                        continue
                    for local_name, local_ids in name_index.items():
                        score = difflib.SequenceMatcher(None, probe, local_name).ratio()
                        if score >= 0.9 and score > best_score:
                            best_score = score
                            best_ids = set(local_ids)
                        elif abs(score - best_score) < 1e-9 and score >= 0.9:
                            best_ids |= set(local_ids)
                if len(best_ids) == 1:
                    ids.add(next(iter(best_ids)))
                else:
                    unmatched.append(pg)
        key = normalize_name(pc.name)
        existing = key in live_names
        matched.append(MatchedCollection(pc, sorted(ids), unmatched,
                                          existing=existing,
                                          dynamic=key in dynamic_names))
    return Preview(plan, matched, local_only=len(local))


def _backup(ctx, label: str) -> Path:
    root = apply.BACKUP_ROOT / ("collections-" + time.strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True, exist_ok=True)
    for src, name in ((ctx.env.namespace_file, "cloud-storage-namespace-1.json"),
                      (ctx.env.namespaces_file, "cloud-storage-namespaces.json"),
                      (paths.data_dir() / "state.json", "state.json")):
        if src.is_file():
            shutil.copy2(src, root / name)
    (root / "manifest.json").write_text(json.dumps({
        "format": PLAN_FORMAT + "-backup",
        "format_version": 1,
        "created": int(time.time()),
        "account": ctx.env.account,
        "label": label,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def apply_preview(
    ctx,
    preview: Preview,
    *,
    log: Callable[[str], None] | None = None,
    progress: Callable[[str], None] | None = None,
    restart: bool = True,
) -> dict[str, int]:
    backup = _backup(ctx, preview.plan.name)
    if log:
        log(f"已备份收藏集到 {backup}")
    was_running = steamenv.steam_running()
    if was_running:
        if log:
            log("Steam 正在运行，正在自动关闭 ...")
        steamenv.close_steam(ctx.env, log=log)
        time.sleep(2.0)
    try:
        namespace = collections.load_json_array(ctx.env.namespace_file)
        version = collections.max_version(namespace)
        now = int(time.time())
        by_name = collections.scan_user_collections(namespace)
        by_key = {item[0]: item[1] for item in namespace
                  if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)}
        changed = created = 0
        for item in preview.collections:
            if item.action == "skip" or item.dynamic or not item.appids:
                continue
            target = sorted(set(item.appids))
            existing = [x for x in by_name.get(item.plan.name.casefold(), []) if x["static"]]
            if not existing:
                cid = collections.new_collection_id(set(by_key))
                key = collections.COLLECTION_PREFIX + cid
                version += 1
                value = {"id": cid, "name": item.plan.name, "added": target, "removed": []}
                namespace.append([key, {
                    "key": key, "timestamp": now,
                    "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    "version": str(version), "conflictResolutionMethod": "custom",
                    "strMethodId": "union-collections",
                }])
                created += 1
                continue
            for one in existing:
                rec = one["record"]
                try:
                    value = json.loads(rec.get("value") or "{}")
                except (TypeError, json.JSONDecodeError):
                    value = {}
                current = _appids(value.get("added"))
                new_ids = target if item.action == "replace" else sorted(current | set(target))
                value.update({"name": item.plan.name, "id": one["cid"], "added": new_ids, "removed": []})
                rec["value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                rec["timestamp"] = now
                version += 1
                rec["version"] = str(version)
                changed += 1
        collections.write_json_array(ctx.env.namespace_file, namespace)
        collections.update_namespaces_version(ctx.env.namespaces_file, 1, version)
        if restart and not steamenv.steam_running():
            steamenv.start_steam(ctx.env)
        return {"changed": changed, "created": created, "backup": len(str(backup))}
    except Exception:
        try:
            for f in backup.iterdir():
                if f.name in ("cloud-storage-namespace-1.json", "cloud-storage-namespaces.json"):
                    dst = ctx.env.cloudstorage / f.name
                    shutil.copy2(f, dst)
        except Exception:
            pass
        if (was_running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(ctx.env)
            except Exception:
                pass
        raise
