"""Safe create/rename/delete/member operations for Steam collections."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import apply, collections, paths, steamenv


class ActionError(Exception):
    pass


@dataclass
class ActionResult:
    backup: Path
    changed: int = 0
    created: int = 0

    def __str__(self) -> str:
        return f"修改 {self.changed} 个收藏集，新建 {self.created} 个"


def _backup(ctx) -> Path:
    root = apply.BACKUP_ROOT / ("collections-" + time.strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True, exist_ok=True)
    for src, name in ((ctx.env.namespace_file, "cloud-storage-namespace-1.json"),
                      (ctx.env.namespaces_file, "cloud-storage-namespaces.json"),
                      (paths.data_dir() / "state.json", "state.json")):
        if src.is_file():
            shutil.copy2(src, root / name)
    (root / "manifest.json").write_text(json.dumps({
        "format": "steam-unify-collection-plan-backup",
        "format_version": 1,
        "created": int(time.time()),
        "account": ctx.env.account,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _records(namespace: list) -> dict[str, dict]:
    return {item[0]: item[1] for item in namespace
            if isinstance(item, list) and len(item) == 2
            and isinstance(item[0], str) and isinstance(item[1], dict)}


def _static(name: str, namespace: list) -> list[tuple[str, dict, dict]]:
    found = []
    for item in collections.scan_user_collections(namespace).get(name.casefold(), []):
        if not item.get("static"):
            continue
        rec = item["record"]
        try:
            value = json.loads(rec.get("value") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        found.append((item["key"], rec, value))
    return found


def _normalize_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise ActionError("收藏集名称不能为空")
    if len(name) > 80:
        raise ActionError("收藏集名称过长")
    if name.casefold() in ("收藏夹", "已隐藏", "favorites", "hidden"):
        raise ActionError("不能使用 Steam 系统收藏集名称")
    return name


def _run(ctx, mutate, *, log: Callable | None = None, restart: bool = True) -> ActionResult:
    backup = _backup(ctx)
    if log:
        log(f"已备份收藏集到 {backup}")
    running = steamenv.steam_running()
    if running:
        if log:
            log("Steam 正在运行，正在关闭 ...")
        steamenv.close_steam(ctx.env, log=log)
        time.sleep(2.0)
    try:
        namespace = collections.load_json_array(ctx.env.namespace_file)
        result = mutate(namespace)
        collections.write_json_array(ctx.env.namespace_file, namespace)
        collections.update_namespaces_version(
            ctx.env.namespaces_file, 1, collections.max_version(namespace)
        )
        if restart and not steamenv.steam_running():
            try:
                steamenv.start_steam(ctx.env)
            except Exception:
                pass
        return result
    except Exception:
        try:
            for name in ("cloud-storage-namespace-1.json", "cloud-storage-namespaces.json"):
                src = backup / name
                if src.is_file():
                    shutil.copy2(src, ctx.env.cloudstorage / name)
        except Exception:
            pass
        if (running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(ctx.env)
            except Exception:
                pass
        raise


def _touch(record: dict, version: int, value: dict, now: int) -> None:
    value["name"] = str(value.get("name") or "")
    value["added"] = sorted({int(x) for x in value.get("added", []) if str(x).lstrip("-").isdigit() and int(x) > 0})
    value["removed"] = []
    record["value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    record["timestamp"] = now
    record["version"] = str(version)


def create_collection(ctx, name: str, appids: list[int], *, log=None, restart=True) -> ActionResult:
    name = _normalize_name(name)
    target = sorted({int(x) for x in appids if int(x) > 0})

    def mutate(namespace):
        if _static(name, namespace):
            raise ActionError(f"收藏集“{name}”已存在")
        now = int(time.time())
        version = collections.max_version(namespace) + 1
        cid = collections.new_collection_id(set(_records(namespace)))
        key = collections.COLLECTION_PREFIX + cid
        value = {"id": cid, "name": name, "added": target, "removed": []}
        namespace.append([key, {
            "key": key, "timestamp": now,
            "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            "version": str(version), "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
        }])
        return ActionResult(Path(), created=1)

    return _run(ctx, mutate, log=log, restart=restart)


def add_games(ctx, name: str, appids: list[int], *, log=None, restart=True) -> ActionResult:
    name = _normalize_name(name)
    target = {int(x) for x in appids if int(x) > 0}

    def mutate(namespace):
        found = _static(name, namespace)
        if not found:
            raise ActionError(f"找不到收藏集“{name}”")
        now = int(time.time())
        version = collections.max_version(namespace)
        changed = 0
        for _key, rec, value in found:
            current = {int(x) for x in value.get("added", []) if str(x).lstrip("-").isdigit()}
            if target <= current:
                continue
            value["added"] = sorted(current | target)
            value["removed"] = []
            version += 1
            _touch(rec, version, value, now)
            changed += 1
        return ActionResult(Path(), changed=changed)

    return _run(ctx, mutate, log=log, restart=restart)


def remove_games(ctx, name: str, appids: list[int], *, log=None, restart=True) -> ActionResult:
    name = _normalize_name(name)
    target = {int(x) for x in appids if int(x) > 0}

    def mutate(namespace):
        found = _static(name, namespace)
        if not found:
            raise ActionError(f"找不到收藏集“{name}”")
        now = int(time.time())
        version = collections.max_version(namespace)
        changed = 0
        for _key, rec, value in found:
            current = {int(x) for x in value.get("added", []) if str(x).lstrip("-").isdigit()}
            new = current - target
            if new == current:
                continue
            value["added"] = sorted(new)
            value["removed"] = []
            version += 1
            _touch(rec, version, value, now)
            changed += 1
        return ActionResult(Path(), changed=changed)

    return _run(ctx, mutate, log=log, restart=restart)


def rename_collection(ctx, name: str, new_name: str, *, log=None, restart=True) -> ActionResult:
    name = _normalize_name(name)
    new_name = _normalize_name(new_name)
    if name.casefold() == new_name.casefold():
        raise ActionError("新名称与旧名称相同")

    def mutate(namespace):
        found = _static(name, namespace)
        if not found:
            raise ActionError(f"找不到收藏集“{name}”")
        if _static(new_name, namespace):
            raise ActionError(f"收藏集“{new_name}”已存在")
        now = int(time.time())
        version = collections.max_version(namespace)
        for _key, rec, value in found:
            value["name"] = new_name
            version += 1
            _touch(rec, version, value, now)
        return ActionResult(Path(), changed=len(found))

    return _run(ctx, mutate, log=log, restart=restart)


def delete_collection(ctx, name: str, *, log=None, restart=True) -> ActionResult:
    name = _normalize_name(name)

    def mutate(namespace):
        found = _static(name, namespace)
        if not found:
            raise ActionError(f"找不到收藏集“{name}”")
        now = int(time.time())
        version = collections.max_version(namespace)
        for key, rec, _value in found:
            version += 1
            rec.clear()
            rec.update({"key": key, "timestamp": now, "is_deleted": True, "version": str(version)})
        return ActionResult(Path(), changed=len(found))

    return _run(ctx, mutate, log=log, restart=restart)


def restore_backup(ctx, backup_dir: Path, *, log=None, restart=True) -> ActionResult:
    backup_dir = Path(backup_dir)
    source_ns = backup_dir / "cloud-storage-namespace-1.json"
    source_versions = backup_dir / "cloud-storage-namespaces.json"
    if not source_ns.is_file():
        raise ActionError("备份目录缺少 cloud-storage-namespace-1.json")
    try:
        namespace = json.loads(source_ns.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionError(f"备份 namespace 损坏：{exc}") from exc
    if not isinstance(namespace, list):
        raise ActionError("备份 namespace 不是数组")
    running = steamenv.steam_running()
    if running:
        steamenv.close_steam(ctx.env, log=log)
        time.sleep(2.0)
    backup = _backup(ctx)
    try:
        shutil.copy2(source_ns, ctx.env.namespace_file)
        if source_versions.is_file():
            shutil.copy2(source_versions, ctx.env.namespaces_file)
        if restart and not steamenv.steam_running():
            try:
                steamenv.start_steam(ctx.env)
            except Exception:
                pass
        return ActionResult(backup, changed=1)
    except Exception:
        for name in ("cloud-storage-namespace-1.json", "cloud-storage-namespaces.json"):
            src = backup / name
            if src.is_file():
                shutil.copy2(src, ctx.env.cloudstorage / name)
        raise

