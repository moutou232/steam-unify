"""Portable backup bundle: export/import a Steam library snapshot as a zip."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import apply, cache, steamenv
from .state import State

FORMAT = "steam-unify-backup"
FORMAT_VERSION = 1
MAX_ZIP_BYTES = 512 * 1024 * 1024
MAX_FILES = 5000


class BackupError(Exception):
    pass


@dataclass
class ExportResult:
    path: Path
    files: int
    size: int
    cache_kind: str
    account: str


@dataclass
class ImportResult:
    path: Path
    files: int
    state_restored: bool
    cache_kind: str
    account: str


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_add_tree(zf: zipfile.ZipFile, root: Path, arc_root: str) -> int:
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            arc = f"{arc_root}/{path.relative_to(root).as_posix()}"
            zf.write(path, arc)
            count += 1
    return count


def export_backup(
    env: steamenv.SteamEnv,
    state: State,
    path: Path,
    log: Callable[[str], None] | None = None,
) -> ExportResult:
    cache_info = cache.AppInfoCache.detect(env).require()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="steam-unify-backup-") as td:
        temp = Path(td)
        auto_dir = apply.backup(
            env, state, steamenv.installed_apps(env.root), base=temp
        )
        state_copy = auto_dir / "state.json"
        shutil.copy2(apply.paths.data_dir() / "state.json", state_copy)
        manifest_path = auto_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        files = []
        for file in sorted(auto_dir.rglob("*")):
            if file.is_file() and file.name != "manifest.json":
                files.append({
                    "path": file.relative_to(auto_dir).as_posix(),
                    "size": file.stat().st_size,
                    "sha256": _sha256(file),
                })
        manifest.update({
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": "3.1.2",
            "created": int(time.time()),
            "account": env.account,
            "cache_kind": cache_info.kind.value,
            "state_version": int(state.data.get("version", 0)),
            "files": files,
        })
        (auto_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (auto_dir / "README.txt").write_text(
            "Steam 统译备份文件。包含 Steam 本地缓存和个人游戏状态，"
            "请勿上传到公共网络。\n",
            encoding="utf-8",
        )
        tmp_zip = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            count = _zip_add_tree(zf, auto_dir, "backup")
        tmp_zip.replace(path)
        size = path.stat().st_size
    if log:
        log(f"备份已导出：{path}（{size / 1024 / 1024:.1f} MB）")
    return ExportResult(path, count, size, cache_info.kind.value, env.account)


def _validate_member(name: str) -> None:
    pure = Path(name)
    if pure.is_absolute() or ".." in pure.parts or any(part == "" for part in pure.parts):
        raise BackupError(f"备份包包含不安全路径：{name}")
    if len(name) > 300:
        raise BackupError(f"备份包路径过长：{name[:80]}")


def peek_backup(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise BackupError("备份文件不存在")
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise BackupError("备份文件过大")
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"不是有效的 zip 备份：{exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise BackupError("备份文件数量过多")
        if sum(i.file_size for i in infos) > MAX_ZIP_BYTES * 3:
            raise BackupError("备份解压后过大")
        for info in infos:
            _validate_member(info.filename)
        names = {i.filename for i in infos}
        if "backup/manifest.json" not in names:
            raise BackupError("备份包缺少 manifest.json")
        try:
            manifest = json.loads(zf.read("backup/manifest.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("manifest.json 损坏") from exc
        if manifest.get("format") != FORMAT:
            raise BackupError("不是 Steam 统译备份文件")
        if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
            raise BackupError("备份格式版本过新")
        return manifest


def import_backup(
    env: steamenv.SteamEnv,
    state: State,
    path: Path,
    restart: bool = True,
    log: Callable[[str], None] | None = None,
) -> ImportResult:
    path = Path(path)
    manifest = peek_backup(path)
    current = cache.AppInfoCache.detect(env).require()
    backup_kind = str(manifest.get("cache_kind") or "")
    if backup_kind and backup_kind != current.kind.value:
        raise BackupError(
            f"备份使用 {backup_kind} 缓存，当前 Steam 使用 {current.kind.value}；"
            "为避免损坏，跨缓存格式的备份不能直接导入。"
        )
    backup_account = str(manifest.get("account") or "")
    if backup_account and backup_account != env.account:
        raise BackupError(
            f"备份账号为 {backup_account}，当前 Steam 账号为 {env.account}；"
            "为避免收藏集错乱，请切换到对应账号后再导入。"
        )
    apply.preflight_writable(env)
    with tempfile.TemporaryDirectory(prefix="steam-unify-import-") as td:
        temp = Path(td)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(temp)
        backup_dir = temp / "backup"
        if not backup_dir.is_dir():
            raise BackupError("备份目录结构损坏")
        # Verify every declared file.
        for item in manifest.get("files", []):
            if not isinstance(item, dict):
                raise BackupError("manifest 文件列表损坏")
            rel = str(item.get("path") or "")
            _validate_member(rel)
            file = backup_dir / rel
            if not file.is_file():
                raise BackupError(f"备份缺少文件：{rel}")
            if item.get("size") is not None and file.stat().st_size != int(item["size"]):
                raise BackupError(f"备份文件大小不匹配：{rel}")
            if item.get("sha256") and _sha256(file) != item["sha256"]:
                raise BackupError(f"备份文件校验失败：{rel}")
        was_running = steamenv.steam_running()
        if was_running:
            steamenv.close_steam(env, log=log)
            time.sleep(1.5)
        safety = apply.backup(env, state, steamenv.installed_apps(env.root))
        try:
            count = apply._restore_files(env, state, backup_dir, restore_state=True)
            state_file = backup_dir / "state.json"
            state_restored = False
            if state_file.is_file():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise BackupError(f"state.json 损坏：{exc}") from exc
                if not isinstance(data, dict):
                    raise BackupError("state.json 不是对象")
                state.data = data
                state.data.setdefault("version", 3)
                state.save()
                state_restored = True
            if log:
                log(f"已导入 {count} 个文件（安全备份：{safety}）")
        except Exception:
            try:
                apply._restore_files(env, state, safety, restore_state=False)
            except Exception:
                pass
            if was_running or restart:
                try:
                    steamenv.start_steam(env)
                except Exception:
                    pass
            raise
        if restart:
            steamenv.start_steam(env)
    return ImportResult(
        path, count, state_restored,
        str(manifest.get("cache_kind") or ""),
        str(manifest.get("account") or ""),
    )
