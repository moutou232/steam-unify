"""Read/write Steam's modern library collections (cloudstorage JSON)."""

from __future__ import annotations

import json
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any

COLLECTION_PREFIX = "user-collections."
SYSTEM_IDS = {"favorite", "hidden"}


class CollectionError(Exception):
    pass


def load_json_array(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(data, list):
        raise CollectionError(f"{path} 不是数组格式")
    return data


def write_json_array(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    last: Exception | None = None
    for attempt in range(4):
        try:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return
        except (PermissionError, OSError) as exc:
            last = exc
            if attempt < 3:
                time.sleep(0.8 * (attempt + 1))
    assert last is not None
    raise last


def _records(namespace: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in namespace:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict):
            out[item[0]] = item[1]
    return out


def _record_name(record: dict) -> str:
    if record.get("is_deleted"):
        return ""
    raw = record.get("value")
    if not isinstance(raw, str):
        return ""
    try:
        obj = json.loads(raw)
        return str(obj.get("name", "") or "")
    except (json.JSONDecodeError, TypeError):
        return ""


def max_version(namespace: list) -> int:
    best = 0
    for item in namespace:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict)):
            continue
        try:
            best = max(best, int(item[1].get("version", 0)))
        except (TypeError, ValueError):
            continue
    return best


def new_collection_id(used_keys: set[str]) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        cid = "uc-" + "".join(secrets.choice(alphabet) for _ in range(12))
        if COLLECTION_PREFIX + cid not in used_keys:
            return cid


def _is_static(record: dict) -> bool:
    try:
        obj = json.loads(record.get("value", "{}"))
        return "filterSpec" not in obj
    except (json.JSONDecodeError, TypeError):
        return False


def scan_user_collections(namespace: list) -> dict[str, list[dict]]:
    """Return {casefolded name: [record info]} for all live user collections.

    Every duplicate name is kept.  Dynamic collections (filterSpec) are
    included with static=False so callers can avoid rewriting them.
    """
    by_name: dict[str, list[dict]] = {}
    for item in namespace:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], dict)
        ):
            continue
        key, rec = item
        if not key.startswith(COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        name = _record_name(rec).strip()
        if not name:
            continue
        by_name.setdefault(name.casefold(), []).append({
            "key": key,
            "cid": key[len(COLLECTION_PREFIX):],
            "name": name,
            "static": _is_static(rec),
            "record": rec,
        })
    return by_name


def ensure_maps(managed: dict[str, str],
                managed_ids: dict[str, list[str]] | None,
                owned_ids: dict[str, list[str]] | None) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Migrate the legacy one-ID map to the multi-ID maps.

    Only when both multi-ID maps are absent do legacy IDs count as owned.
    A managed ID that was adopted by name must not become owned later.
    """
    legacy = managed_ids is None and owned_ids is None
    if managed_ids is None:
        managed_ids = {}
    if owned_ids is None:
        owned_ids = {}
    for category, cid in list(managed.items()):
        if not cid:
            continue
        bucket = managed_ids.setdefault(category, [])
        if cid not in bucket:
            bucket.append(cid)
        if legacy:
            owned = owned_ids.setdefault(category, [])
            if cid not in owned:
                owned.append(cid)
    for category, ids in list(managed_ids.items()):
        cleaned = [str(cid) for cid in ids if cid]
        managed_ids[category] = cleaned
        if cleaned and not managed.get(category):
            managed[category] = cleaned[0]
    return managed_ids, owned_ids


def sync_collections(
    namespace: list,
    managed: dict[str, str],
    computed: dict[str, list[int]],
    managed_ids: dict[str, list[str]] | None = None,
    owned_ids: dict[str, list[str]] | None = None,
) -> tuple[int, int, list[str]]:
    """Create/update ALL same-name static collections.

    Returns (changed_record_count, max_version, skipped_dynamic_names).
    """
    managed_ids, owned_ids = ensure_maps(managed, managed_ids, owned_ids)
    by_key = {
        item[0]: item[1]
        for item in namespace
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)
    }
    by_name = scan_user_collections(namespace)
    dynamic_names = {
        name for name, items in by_name.items()
        if items and not any(i["static"] for i in items)
    }
    current_max = max_version(namespace)
    version = current_max
    now = int(time.time())
    changed = 0
    skipped: list[str] = []
    used_keys = set(by_key)

    for category, appids in computed.items():
        category = str(category).strip()
        if not category:
            continue
        target = sorted(set(appids))
        candidates: dict[str, dict] = {}

        # 1) IDs recorded in state, if they still exist and are static.
        for cid in managed_ids.get(category, []):
            key = COLLECTION_PREFIX + cid
            rec = by_key.get(key)
            if rec is not None and not rec.get("is_deleted") and _is_static(rec):
                candidates[key] = rec

        # 2) ALL live static collections with the same name.
        for item in by_name.get(category.casefold(), []):
            if item["static"]:
                candidates[item["key"]] = item["record"]

        if not candidates:
            # A filterSpec collection with the same name cannot be safely
            # rewritten; do not create a duplicate static one.
            if category.casefold() in dynamic_names:
                skipped.append(category)
                continue
            # Do not create a collection for an empty category.
            if not target:
                continue
            cid = new_collection_id(used_keys)
            key = COLLECTION_PREFIX + cid
            version += 1
            value = {"id": cid, "name": category, "added": target, "removed": []}
            rec = {
                "key": key,
                "timestamp": now,
                "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                "version": str(version),
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            }
            namespace.append([key, rec])
            by_key[key] = rec
            used_keys.add(key)
            managed_ids[category] = [cid]
            owned_ids.setdefault(category, []).append(cid)
            managed[category] = cid
            changed += 1
            continue

        # Update every static same-name / managed record.
        category_keys: list[str] = []
        category_owned: list[str] = list(owned_ids.get(category, []))
        for key, record in candidates.items():
            cid = key[len(COLLECTION_PREFIX):]
            category_keys.append(cid)
            try:
                value = json.loads(record.get("value", "{}"))
            except (json.JSONDecodeError, TypeError):
                value = {"id": cid, "name": category}
            current = sorted(int(x) for x in value.get("added", []) if str(x).lstrip("-").isdigit())
            if (
                current == target
                and not value.get("removed")
                and str(value.get("name") or "") == category
            ):
                continue
            value["added"] = target
            value["removed"] = []
            value["name"] = category
            value["id"] = cid
            record["value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            record["timestamp"] = now
            version += 1
            record["version"] = str(version)
            changed += 1

        managed_ids[category] = category_keys
        managed[category] = category_keys[0] if category_keys else ""
        # Keep only IDs that were already program-created; adopted manual
        # collections must not be deleted by the undo feature.
        owned_ids[category] = [cid for cid in category_keys if cid in category_owned]

    return changed, version, skipped


def category_collection_keys(namespace: list) -> list[tuple[str, str]]:
    """Return every live static non-system collection as (key, name)."""
    out: list[tuple[str, str]] = []
    for item in namespace:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], dict)
        ):
            continue
        key, rec = item
        if not key.startswith(COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        cid = key[len(COLLECTION_PREFIX):]
        if cid in SYSTEM_IDS:
            continue
        if not _is_static(rec):
            continue
        name = _record_name(rec).strip()
        if not name or name in ("收藏夹", "已隐藏"):
            continue
        out.append((key, name))
    return out


def tombstone_category_collections(namespace: list) -> tuple[list[str], int]:
    """Soft-delete all live static non-system user collections.

    This is the undo path: it removes old/same-name/manual category collections
    too, because Steam stores category membership as collection membership.
    System favorites/hidden and dynamic filter collections are preserved.
    """
    version = max_version(namespace)
    now = int(time.time())
    deleted: list[str] = []
    by_key = {
        item[0]: item[1]
        for item in namespace
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)
    }
    for key, name in category_collection_keys(namespace):
        rec = by_key.get(key)
        if rec is None or rec.get("is_deleted"):
            continue
        version += 1
        rec.clear()
        rec.update({
            "key": key,
            "timestamp": now,
            "is_deleted": True,
            "version": str(version),
        })
        deleted.append(name)
    return deleted, version


def tombstone_collections(
    namespace: list,
    managed: dict[str, str],
    owned_ids: dict[str, list[str]] | None = None,
) -> tuple[list[str], int]:
    """Soft-delete only collections created by this program.

    Same-name collections adopted from the user are deliberately preserved.
    """
    if owned_ids is None:
        owned_ids = {cat: [cid] for cat, cid in managed.items() if cid}
    by_key = {
        item[0]: item[1]
        for item in namespace
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)
    }
    version = max_version(namespace)
    now = int(time.time())
    deleted: list[str] = []
    for category, ids in list(owned_ids.items()):
        for cid in list(ids):
            key = COLLECTION_PREFIX + cid
            rec = by_key.get(key)
            if rec is None or rec.get("is_deleted"):
                continue
            version += 1
            rec.clear()
            rec.update({
                "key": key,
                "timestamp": now,
                "is_deleted": True,
                "version": str(version),
            })
            deleted.append(category)
    managed.clear()
    owned_ids.clear()
    return deleted, version


def update_namespaces_version(path: Path, namespace_id: int, version: int) -> None:
    data = load_json_array(path)
    found = False
    for item in data:
        if isinstance(item, list) and len(item) == 2 and item[0] == namespace_id:
            item[1] = str(version)
            found = True
    if not found:
        data.append([namespace_id, str(version)])
    write_json_array(path, data)


def existing_user_names(namespace: list) -> dict[str, str]:
    """Return {lower_name: key} for live user collections."""
    out: dict[str, str] = {}
    for item in namespace:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)):
            continue
        key, rec = item
        if not key.startswith(COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        name = _record_name(rec).strip()
        if name:
            out.setdefault(name.lower(), key)
    return out
