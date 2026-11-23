"""Locating Steam, its active account, libraries and processes (Windows)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import vdftext


class SteamError(Exception):
    pass


CREATE_NO_WINDOW = 0x08000000


def _reg_value(key: str, name: str) -> Any:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _norm_path(value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value.replace("/", "\\"))
    return p


def _reg_value_hklm(key: str, name: str) -> Any:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def detect_root(override: str | Path | None = None) -> Path:
    if override:
        root = Path(override).expanduser()
        if not (root / "userdata").is_dir():
            raise SteamError(f"{root} 下找不到 userdata，确认这是 Steam 根目录")
        return root
    candidates: list[Path] = []
    reg = _norm_path(_reg_value(r"Software\Valve\Steam", "SteamPath"))
    if reg:
        candidates.append(reg)
    steam_exe = _norm_path(_reg_value(r"Software\Valve\Steam", "SteamExe"))
    if steam_exe:
        candidates.append(steam_exe.parent)
    install = _norm_path(_reg_value_hklm(
        r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"
    ))
    if install:
        candidates.append(install)
    candidates += [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path(r"D:\Steam"),
        Path(r"E:\Steam"),
        Path(r"D:\App\Steam"),
    ]
    for c in candidates:
        try:
            if not (c / "userdata").is_dir():
                continue
            # appcache may be temporarily absent while Steam is starting or
            # shutting down; steamapps is enough to identify the root.
            if (c / "appcache").is_dir() or (c / "steamapps").is_dir():
                return c
        except OSError:
            continue
    raise SteamError(
        "未能自动找到 Steam 安装目录，请在设置中手动指定（包含 steam.exe 的文件夹）"
    )


def _account_mtime(root: Path, account: str) -> float:
    try:
        return (root / "userdata" / account / "config" / "localconfig.vdf").stat().st_mtime
    except OSError:
        return 0.0


def account_persona(root: Path, account: str) -> str:
    loginusers = root / "config" / "loginusers.vdf"
    if not loginusers.is_file():
        return ""
    try:
        data = vdftext.load(loginusers)
        users = vdftext.get_ci(data, "users", {}) or {}
        for steamid64, info in users.items():
            if not isinstance(info, dict):
                continue
            try:
                sid3 = str(int(steamid64) - 76561197960265728)
            except ValueError:
                continue
            if sid3 == str(account):
                return str(vdftext.get_ci(info, "PersonaName", "") or "")
    except Exception:
        pass
    return ""


def list_accounts(root: Path) -> list[str]:
    userdata = root / "userdata"
    if not userdata.is_dir():
        return []
    accounts = [
        p.name
        for p in userdata.iterdir()
        if p.is_dir() and p.name.isdigit() and p.name != "0"
    ]
    accounts.sort(key=lambda a: _account_mtime(root, a), reverse=True)
    return accounts


def detect_account(root: Path, override: str | None = None) -> str:
    if override:
        return str(override)
    active = _reg_value(r"Software\Valve\Steam\ActiveProcess", "ActiveUser")
    try:
        active_s = str(int(active)) if active else ""
    except (TypeError, ValueError):
        active_s = ""
    accounts = list_accounts(root)
    if active_s and active_s in accounts:
        return active_s
    if accounts:
        return accounts[0]
    raise SteamError(f"{root / 'userdata'} 下没有找到账号目录")


@dataclass
class SteamEnv:
    root: Path
    account: str

    @property
    def exe(self) -> Path:
        return self.root / "steam.exe"

    @property
    def appinfo(self) -> Path:
        p = self.root / "appcache" / "appinfo.vdf"
        if p.is_file():
            return p
        alt = self.root / "steamapps" / "appinfo.vdf"
        if alt.is_file():
            return alt
        return p

    @property
    def shard_dir(self) -> Path:
        return self.root / "appcache" / "appinfo"

    @property
    def has_shards(self) -> bool:
        d = self.shard_dir
        return d.is_dir() and any(d.glob("appinfo_*.vdf"))

    @property
    def userdata(self) -> Path:
        return self.root / "userdata" / self.account

    @property
    def config(self) -> Path:
        return self.userdata / "config"

    @property
    def localconfig(self) -> Path:
        return self.config / "localconfig.vdf"

    @property
    def cloudstorage(self) -> Path:
        return self.config / "cloudstorage"

    @property
    def namespace_file(self) -> Path:
        return self.cloudstorage / "cloud-storage-namespace-1.json"

    @property
    def namespaces_file(self) -> Path:
        return self.cloudstorage / "cloud-storage-namespaces.json"

    @property
    def persona(self) -> str:
        return account_persona(self.root, self.account)

    def check(self) -> None:
        if not self.exe.is_file():
            raise SteamError(f"找不到 {self.exe}")
        if not self.appinfo.is_file() and not self.has_shards:
            raise SteamError(f"找不到 {self.appinfo} 或分片缓存 {self.shard_dir}")
        if not self.localconfig.is_file():
            raise SteamError(f"找不到 {self.localconfig}")
        if not self.namespace_file.is_file():
            raise SteamError(f"找不到 {self.namespace_file}")


def steam_pids() -> list[int]:
    """Return PIDs of steam.exe / steamwebhelper.exe without third-party deps."""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        ).stdout
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].strip('"').lower()
        if name in ("steam.exe", "steamwebhelper.exe"):
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def steam_running() -> bool:
    if steam_pids():
        return True
    pid = _reg_value(r"Software\Valve\Steam\ActiveProcess", "pid")
    try:
        return bool(pid and int(pid) > 0)
    except (TypeError, ValueError):
        return False


def close_steam(env: SteamEnv, timeout: float = 90.0, log=None) -> None:
    if not steam_running():
        return
    if not env.exe.is_file():
        raise SteamError(f"找不到 {env.exe}，无法自动关闭 Steam")
    launched = False
    try:
        subprocess.run(
            [str(env.exe), "-shutdown"],
            cwd=str(env.root),
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        launched = True
    except Exception as exc:
        if log:
            log(f"steam -shutdown 失败（{exc}），改用 steam://exit")
    if not launched or steam_running():
        # steam://exit is handled by the running client and does not require
        # launching steam.exe elevated (some systems have RunAsAdmin set).
        try:
            os.startfile("steam://exit")
            if log:
                log("已发送 steam://exit")
        except Exception as exc:
            if log:
                log(f"steam://exit 失败: {exc}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not steam_running():
            time.sleep(1.5)
            if not steam_running():
                return
        time.sleep(1.0)
    raise SteamError(
        "等待 Steam 退出超时。请确认没有游戏正在运行，并手动退出 Steam 后重试。"
    )


def start_steam(env: SteamEnv) -> None:
    if not env.exe.is_file():
        raise SteamError(f"找不到 {env.exe}")
    # Some machines have steam.exe marked "Run as administrator" (or Steam was
    # started elevated by a launcher).  Launching the executable directly from
    # a non-elevated process then fails with WinError 740; the steam://
    # protocol handler starts/focuses Steam without that problem.
    try:
        os.startfile("steam://open/")
        return
    except Exception:
        pass
    try:
        subprocess.Popen(
            [str(env.exe)],
            cwd=str(env.root),
            close_fds=True,
        )
    except OSError as exc:
        raise SteamError(
            f"无法启动 Steam（{exc}）。请手动打开 Steam 后点击“一键修复/校验”。"
        ) from exc


def library_folders(root: Path) -> list[Path]:
    lf = root / "steamapps" / "libraryfolders.vdf"
    paths: list[Path] = []
    if lf.is_file():
        try:
            data = vdftext.load(lf)
            folders = vdftext.get_ci(data, "libraryfolders", {}) or {}
            for key, value in folders.items():
                if isinstance(value, dict):
                    p = vdftext.get_ci(value, "path")
                    if p:
                        paths.append(Path(str(p)))
                elif key.isdigit() and value:
                    paths.append(Path(str(value)))
        except Exception:
            pass
    if root not in paths:
        paths.insert(0, root)
    return paths


def installed_apps(root: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for folder in library_folders(root):
        steamapps = folder / "steamapps"
        if not steamapps.is_dir():
            continue
        for acf in steamapps.glob("appmanifest_*.acf"):
            m = re.search(r"appmanifest_(\d+)\.acf$", acf.name, re.IGNORECASE)
            if not m:
                continue
            appid = int(m.group(1))
            info: dict[str, Any] = {
                "appid": appid,
                "acf": acf,
                "library": folder,
                "name": None,
            }
            try:
                data = vdftext.load(acf)
                state = vdftext.get_ci(data, "AppState", {}) or {}
                info["name"] = vdftext.get_ci(state, "name")
                info["installdir"] = vdftext.get_ci(state, "installdir")
            except Exception:
                pass
            result[appid] = info
    return result


def owned_appids(env: SteamEnv) -> set[int]:
    """App IDs with local Steam app state (roughly: owned / played games)."""
    result: set[int] = set()
    try:
        data = vdftext.load(env.localconfig)
    except Exception:
        return result
    store = vdftext.get_ci(data, "UserLocalConfigStore", {}) or {}
    software = vdftext.get_ci(store, "Software", {}) or {}
    valve = vdftext.get_ci(software, "Valve", {}) or {}
    steam = vdftext.get_ci(valve, "Steam", {}) or {}
    apps = vdftext.get_ci(steam, "apps", {}) or {}
    for key in apps:
        if str(key).isdigit():
            result.add(int(key))
    return result


def tag_names(env: SteamEnv) -> dict[int, str]:
    """Read LocalizedTagNames_schinese (tag id -> Simplified Chinese name)."""
    result: dict[int, str] = {}
    try:
        data = vdftext.load(env.localconfig)
    except Exception:
        return result
    store = vdftext.get_ci(data, "UserLocalConfigStore", {}) or {}
    web = vdftext.get_ci(store, "WebStorage", {}) or {}
    for key in ("LocalizedTagNames_schinese", "LocalizedTagNames2_schinese"):
        raw = vdftext.get_ci(web, key)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            for item in obj.get("tags", []):
                try:
                    result[int(item["tagid"])] = str(item["name"])
                except (KeyError, TypeError, ValueError):
                    continue
        except (json.JSONDecodeError, AttributeError):
            continue
    return result


def active_language() -> str:
    val = _reg_value(r"Software\Valve\Steam", "Language")
    return str(val or "")


def backup_dir(base: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = base / stamp
    suffix = 1
    while out.exists():
        out = base / f"{stamp}-{suffix}"
        suffix += 1
    out.mkdir(parents=True, exist_ok=False)
    return out


def can_write(path: Path) -> bool:
    return os.access(path, os.W_OK)
