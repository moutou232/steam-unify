from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import webbrowser
import ctypes
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import (
    apply,
    backup_bundle,
    category_model,
    category_state,
    descriptions,
    paths,
    providers,
    scoring,
    service,
    steamenv,
    ui_widgets,
)
from .gui_dialogs import (
    APP_TITLE,
    BG,
    CATEGORY_COLORS,
    FG,
    GREEN,
    MUTED,
    ORANGE,
    PANEL,
    PANEL2,
    RED,
    SOURCE_LABEL,
    STATUS_FILTERS,
    ACCENT,
    BatchCategoryDialog,
    CategoryPickerDialog,
    NameEditDialog,
    SettingsDialog,
)
from .state import State


class SteamZhApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        try:
            scaling = float(root.tk.call("tk", "scaling")) or 1.0
        except Exception:
            scaling = 1.0
        self.settings = State.load_settings()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = min(sw, 1200)
        h = min(sh, 800)
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
        self.root.minsize(1100, 720)
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2a475e")
        self.root.option_add("*TCombobox*Listbox.selectForeground", FG)
        self.ctx: service.Context | None = None
        self.busy = False
        self.cancel_flag = False
        self.games_by_id: dict[int, object] = {}
        self.bus: queue.Queue = queue.Queue()
        self.sort_key = ""
        self.sort_desc = True
        self.log_visible = False
        self.last_batch_snapshot: dict[int, list[str]] | None = None
        self._sidebar_guard = False
        self.workspace = "both"
        self.inspector_visible = True
        self._source_loading: set[int] = set()
        self._refresh_after = None
        self._originals_running = False
        self._originals_attempted = False
        self._undo_after_originals = False
        self._undo_after_prepare = False
        self._pending_undo_mode = "both"
        self.buttons: dict[str, ttk.Button] = {}
        self._build_style()
        self._build_ui()
        self.root.after_idle(self.apply_window_mode)
        self.root.after(150, self.apply_window_mode)
        self.root.after(500, self.apply_window_mode)
        self.root.bind("<F11>", lambda _e: self._toggle_fullscreen())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_undo_button()
        self.root.after(100, self._poll_bus)
        self.reload()

    def apply_window_mode(self) -> None:
        mode = str(self.settings.get("window_mode") or "maximized")
        if mode not in ("maximized", "fullscreen", "windowed"):
            mode = "maximized"
        try:
            self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        if mode == "fullscreen":
            try:
                self.root.attributes("-fullscreen", True)
                return
            except Exception:
                mode = "maximized"
        if mode == "maximized":
            # Fill the Windows work area (excludes taskbar) and keep the native
            # title bar.  This is more reliable than tk's zoomed state here.
            try:
                class _Rect(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                rect = _Rect()
                ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0
                )
                w = max(1100, rect.right - rect.left - 16)
                h = max(720, rect.bottom - rect.top - 48)
                self.root.geometry(f"{w}x{h}+{rect.left}+{rect.top}")
            except Exception:
                try:
                    self.root.state("zoomed")
                except Exception:
                    pass
            return
        geometry = str(self.settings.get("window_geometry") or "").strip()
        if geometry:
            try:
                self.root.geometry(geometry)
                return
            except Exception:
                pass
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = min(sw, 1200), min(sh, 800)
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")

    def _toggle_fullscreen(self) -> None:
        try:
            current = bool(self.root.attributes("-fullscreen"))
        except Exception:
            current = False
        try:
            self.root.attributes("-fullscreen", not current)
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            mode = str(self.settings.get("window_mode") or "maximized")
            if mode == "windowed" and not self.root.attributes("-fullscreen"):
                self.settings["window_geometry"] = self.root.geometry()
                if self.ctx:
                    self.ctx.state.save_settings(self.settings)
                else:
                    State.load().save_settings(self.settings)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------------ style
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".", background=BG, foreground=FG, fieldbackground=PANEL2,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure("TButton", background=PANEL2, foreground=FG, padding=(8, 4))
        style.map("TButton", background=[("active", "#2a475e")])
        style.configure(
            "Accent.TButton", background="#2a6f97", foreground="white", padding=(9, 4)
        )
        style.configure(
            "Primary.TButton", background="#2a6f97", foreground="white", padding=(12, 5)
        )
        style.map("Primary.TButton", background=[("active", "#3182ad")])
        style.configure(
            "Secondary.TButton", background=PANEL2, foreground=FG, padding=(9, 4)
        )
        style.configure(
            "Tool.TMenubutton", background=PANEL2, foreground=FG, padding=(8, 4)
        )
        style.map("Tool.TMenubutton", background=[("active", "#2a475e")])
        style.map("Accent.TButton", background=[("active", "#3a8fbd")])
        style.configure(
            "Danger.TButton", background="#7d2b2b", foreground="#ffffff", padding=(9, 4)
        )
        style.map("Danger.TButton", background=[("active", "#a33a3a")])
        style.configure("Small.TButton", padding=(3, 1),
                        font=("Microsoft YaHei UI", 8))
        style.configure("Group.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Sidebar.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=27, borderwidth=0)
        style.map("Sidebar.Treeview", background=[("selected", "#2a475e")])
        style.configure("TEntry", fieldbackground=PANEL2, foreground=FG)
        style.configure(
            "TCombobox", fieldbackground=PANEL2, background=PANEL2,
            foreground=FG, arrowcolor=FG, selectbackground=PANEL2,
            selectforeground=FG,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL2)],
            foreground=[("readonly", FG)],
            selectbackground=[("readonly", PANEL2)],
            selectforeground=[("readonly", FG)],
        )
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL2, foreground=FG, padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL2)
        style.configure("Horizontal.TScrollbar", background=PANEL2)

    # ------------------------------------------------------------------- UI
    def _on_table_selection(self, _ids: set[int]) -> None:
        self._update_selection_label()
        self._update_action_state()
        self.refresh_inspector()

    def _selected_appid(self) -> int | None:
        if not hasattr(self, "table"):
            return None
        ids = self.table.selected_ids()
        return next(iter(ids)) if len(ids) == 1 else None

    def refresh_inspector(self) -> None:
        if not hasattr(self, "name_source_text") or not self.ctx:
            return
        appid = self._selected_appid()
        if appid is None:
            self.name_source_text.delete("1.0", "end")
            self.name_source_text.insert("end", "在表格中选择一个游戏查看译名参考源。")
            self.cat_source_text.delete("1.0", "end")
            self.cat_source_text.insert("end", "在表格中选择一个游戏查看分类参考源。")
            self.name_choice["values"] = []
            self._inspector_candidates = []
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        rec = self.ctx.state.get_app(appid)
        candidates = rec.get("name_candidates") or []
        if not isinstance(candidates, list):
            candidates = []
        self._inspector_candidates = [c for c in candidates if isinstance(c, dict)]
        self.name_source_text.delete("1.0", "end")
        self.name_source_text.insert(
            "end",
            "参考源：\n"
            f"Steam 商店  https://store.steampowered.com/app/{appid}/\n"
            f"SteamDB    https://steamdb.info/app/{appid}/\n"
            f"3DM        https://so.3dmgame.com/?keyword={game.name}\n"
            "小黑盒      https://www.xiaoheihe.cn/\n\n",
        )
        if not self._inspector_candidates:
            self.name_source_text.insert(
                "end",
                f"当前：{rec.get('zh_name') or '（无）'}\n"
                f"来源：{rec.get('source') or '未翻译'}\n\n"
                "还没有多来源候选；点下方“获取参考译名”联网获取。",
            )
            self.name_choice["values"] = []
            self.name_choice_var.set("")
        else:
            values = []
            for i, c in enumerate(self._inspector_candidates, 1):
                label = c.get("label") or c.get("source") or ""
                name = c.get("name") or ""
                conf = float(c.get("confidence") or 0)
                url = c.get("url") or ""
                self.name_source_text.insert(
                    "end", f"{i}. {label}  {conf*100:.0f}%\n   {name}\n   {url}\n"
                )
                values.append(f"{i}. {label} | {name} | {conf*100:.0f}%")
            self.name_choice["values"] = values
            if values and self.name_choice_var.get() not in values:
                self.name_choice_var.set(values[0])
        errors = rec.get("name_provider_errors") or []
        if errors:
            self.name_source_text.insert("end", "\n未返回译名的来源：\n")
            for err in errors[:10]:
                self.name_source_text.insert("end", f"- {err}\n")
        self._refresh_category_inspector(game, rec)

    def _refresh_category_inspector(self, game, rec) -> None:
        self.cat_source_text.delete("1.0", "end")
        cats = category_state.get_categories(rec)
        self.cat_source_text.insert("end", f"当前分类：{' / '.join(cats) or '未分类'}\n")
        tags = getattr(game, "tag_names", []) or []
        if tags:
            self.cat_source_text.insert("end", "Steam 标签：" + " / ".join(tags[:12]) + "\n")
        evidence = getattr(game, "category_evidence", []) or []
        self.cat_source_text.insert("end", "\n参考来源：\n")
        if not evidence:
            self.cat_source_text.insert("end", "（暂无；点“获取分类证据”）\n")
        for ev in evidence:
            try:
                labels = " / ".join(getattr(ev, "labels", []) or [])
                source = getattr(ev, "source", "")
                conf = float(getattr(ev, "confidence", 0))
                url = getattr(ev, "url", "")
                self.cat_source_text.insert("end", f"- {source}  {conf*100:.0f}%  {labels}\n  {url}\n")
            except Exception:
                continue

    def _fetch_selected_sources(self) -> None:
        appid = self._selected_appid()
        if appid is None or not self.ctx or appid in self._source_loading:
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        self._source_loading.add(appid)
        proxy = str(self.settings.get("proxy") or "").strip() or None

        def work():
            try:
                res = providers.fetch_name_candidates(
                    game, network=True, proxy=proxy,
                    priority_order=self.settings.get("source_priority"),
                )
                self.ctx.state.get_app(appid)["name_candidates"] = providers.candidate_dicts(res)
                self.ctx.state.get_app(appid)["name_provider_errors"] = list(res.errors)
                self.ctx.state.save()
                self.bus.put(("name_candidates", (appid, res)))
            except Exception as exc:
                self.bus.put(("log", f"获取译名失败：{exc}"))
            finally:
                self._source_loading.discard(appid)

        threading.Thread(target=work, daemon=True).start()

    def _use_selected_candidate(self) -> None:
        appid = self._selected_appid()
        if appid is None or not self.ctx:
            return
        idx = self.name_choice.current()
        if idx < 0 or idx >= len(self._inspector_candidates):
            return
        c = self._inspector_candidates[idx]
        rec = self.ctx.state.get_app(appid)
        rec["zh_name"] = str(c.get("name") or "")
        rec["source"] = str(c.get("source") or "manual")
        rec["source_url"] = str(c.get("url") or "")
        rec["name_selected_source"] = rec["source"]
        rec["name_confidence"] = float(c.get("confidence") or 0)
        rec["name_selected_by"] = "manual"
        rec["locked"] = True
        rec["detail"] = f"手动选择：{c.get('label') or rec['source']}"
        self.ctx.state.save()
        self.refresh_table()
        self.refresh_inspector()
        self._append_log(f"已选择译名：{rec['zh_name']}（{rec['source']}）")

    def _open_selected_source(self) -> None:
        idx = self.name_choice.current()
        if idx < 0 or idx >= len(self._inspector_candidates):
            return
        url = str(self._inspector_candidates[idx].get("url") or "")
        if url:
            webbrowser.open(url)

    def _fetch_selected_categories(self) -> None:
        appid = self._selected_appid()
        if appid is None or not self.ctx:
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        proxy = str(self.settings.get("proxy") or "").strip() or None

        def work():
            try:
                evidence = providers.load_category_evidence(game, network=True, proxy=proxy)
                game.category_evidence = evidence
                self.bus.put(("category_evidence", (appid, evidence)))
            except Exception as exc:
                self.bus.put(("log", f"获取分类证据失败：{exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _categorize_selected_with_evidence(self) -> None:
        appid = self._selected_appid()
        if appid is None or not self.ctx:
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        rec = self.ctx.state.get_app(appid)
        res = scoring.classify(
            game,
            description=getattr(game, "description", "") or "",
            extra_names=[str(rec.get("orig_name") or "")],
        )
        category_state.set_categories(
            rec, res.categories, source="auto", scores=res.scores,
            reasons=res.reasons, locked=False,
        )
        rec["category_confidence"] = {k: round(v, 3) for k, v in res.scores.items()}
        self.ctx.state.save()
        self.refresh_table()
        self.refresh_sidebar()
        self.refresh_inspector()
        self._append_log(f"宸叉寜鍙傝€冩簮閲嶆柊分类 {game.name}")

    def _build_filters(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="搜索", bg=BG, fg=MUTED).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._schedule_table_refresh())
        tk.Entry(
            parent, textvariable=self.search_var, width=24, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(side="left", padx=(6, 12), ipady=3)
        tk.Label(parent, text="状态", bg=BG, fg=MUTED).pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        status_combo = ttk.Combobox(
            parent, textvariable=self.filter_var, state="readonly", width=9,
            values=STATUS_FILTERS,
        )
        status_combo.pack(side="left", padx=(4, 8))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())
        tk.Label(parent, text="分类", bg=BG, fg=MUTED).pack(side="left")
        self.category_var = tk.StringVar(value="全部分类")
        cat_values = ["全部分类"] + category_model.category_names()
        cat_combo = ttk.Combobox(
            parent, textvariable=self.category_var, state="readonly", width=10,
            values=cat_values,
        )
        cat_combo.pack(side="left", padx=(4, 8))
        cat_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())
        self.installed_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent, text="仅已安装", variable=self.installed_var,
            bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG,
            activeforeground=FG, command=self.refresh_table,
        ).pack(side="left", padx=(0, 6))

    # ------------------------------------------------------------- logging
    def log(self, message: str) -> None:
        self.bus.put(("log", str(message)))

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "log_last_var"):
            return
        stamp = time.strftime("%H:%M:%S")
        self.log_last_var.set(f"[{stamp}] {message}")
        if hasattr(self, "log_text"):
            self.log_text.insert("end", f"[{stamp}] {message}\n")
            self.log_text.see("end")
        if not self.log_visible and any(k in message for k in ("失败", "错误", "异常")):
            self._toggle_log(force=True)

    def _toggle_log(self, force: bool = False) -> None:
        if self.log_visible and not force:
            self.log_frame.place_forget()
            self.log_visible = False
            self.log_toggle_btn.configure(text="日志")
            return
        self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=178)
        self.log_frame.lift()
        self.log_visible = True
        self.log_toggle_btn.configure(text="收起日志")

    # --------------------------------------------------------------- tasks
    def _poll_bus(self) -> None:
        try:
            while True:
                kind, payload = self.bus.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    cur, total, msg = payload
                    if total:
                        self.progress.configure(mode="determinate", maximum=total, value=cur)
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                    if msg:
                        self.header_status.set(msg)
                        self.status_var.set(msg)
                elif kind == "task_done":
                    name, result = payload
                    self._task_finished(name, result, None)
                elif kind == "task_error":
                    name, exc, tb = payload
                    self._task_finished(name, None, (exc, tb))
                elif kind == "status":
                    self.header_status.set(payload)
                elif kind == "name_candidates":
                    _appid, _res = payload
                    self.refresh_inspector()
                elif kind == "category_evidence":
                    _appid, _evidence = payload
                    self.refresh_inspector()
                elif kind == "originals_done":
                    self._on_originals_done()
                elif kind == "originals_error":
                    self._on_originals_error(str(payload))
        except queue.Empty:
            pass
        self.root.after(120, self._poll_bus)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in self.buttons.values():
            b.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if hasattr(self, "cancel_btn2"):
            self.cancel_btn2.configure(state="normal" if busy else "disabled")
        self._update_undo_button()
        self._set_workspace(self.workspace, initial=True)

    def _update_undo_button(self) -> None:
        if "undo_batch" not in self.buttons:
            return
        enabled = bool(self.last_batch_snapshot) and not self.busy
        self.buttons["undo_batch"].configure(state="normal" if enabled else "disabled")

    def run_task(self, name: str, func) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

        def worker():
            try:
                result = func()
            except Exception as exc:
                self.bus.put(("task_error", (name, exc, traceback.format_exc())))
            else:
                self.bus.put(("task_done", (name, result)))

        threading.Thread(target=worker, daemon=True).start()

    def _task_finished(self, name: str, result, error) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        if error:
            exc, tb = error
            if name == "prepare_undo":
                self._undo_after_prepare = False
            self._append_log(f"操作失败：{exc}")
            self._append_log(tb.strip().splitlines()[-1] if tb else "")
            messagebox.showerror("操作失败", str(exc), parent=self.root)
            self.update_status()
            return
        if name == "prepare_undo":
            should_continue = self._undo_after_prepare
            self._undo_after_prepare = False
            self.refresh_table()
            self.refresh_inspector()
            self._append_log(
                "中文商店原名准备完成："
                f"列表内可恢复 {result.tracked_restorable}，"
                f"缓存额外 {result.untracked_restorable}，"
                f"相同/跳过 {result.natural_skipped}，"
                f"未知 {result.unknown_skipped}"
            )
            if should_continue:
                self.on_undo_all(getattr(self, "_pending_undo_mode", "both"))
            else:
                self.update_status()
            return
        if name == "load":
            self.ctx = result
            self.games_by_id = {g.appid: g for g in self.ctx.games}
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log(
                f"扫描完成：{len(self.ctx.games)} 个条目，"
                f"已安装 {sum(1 for g in self.ctx.games if g.installed)}"
            )
            for warning in getattr(self.ctx, "warnings", [])[:5]:
                self._append_log(f"缓存警告：{warning}")
            self.update_status()
            self._append_log("已跳过启动时自动获取原名；需要时请点“工具 → 补全原名”。")
            self._append_log("扫描完成，不会自动翻译；需要时请点击“自动翻译”。")
            self._maybe_auto_translate_new()
        elif name == "auto_new_translate":
            service.auto_categorize(self.ctx)
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log("启动自动翻译完成")
            self.update_status()
        elif name == "translate":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            c = service.counts(self.ctx)
            self._append_log(
                f"翻译完成：官方 {c['official']}，词典 {c['dictionary']}，"
                f"社区 {c.get('community', 0)}，机翻 {c['machine']}，待翻译 {c['missing']}"
            )
            messagebox.showinfo(
                "翻译完成",
                f"官方 {c['official']} · 词典 {c['dictionary']} · "
                f"社区 {c.get('community', 0)} · 机翻 {c['machine']} · 待翻译 {c['missing']}",
                parent=self.root,
            )
        elif name == "categorize":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log("自动分类完成（最多 3 个标签）")
        elif name == "apply":
            res, ver = result
            mode = getattr(self, "_last_apply_mode", "both")
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            if mode == "names":
                msg = f"已写入中文名 {res.names_applied} 个。"
            elif mode == "categories":
                msg = f"已更新收藏集 {res.collections_changed} 个。"
            else:
                msg = f"已写入中文名 {res.names_applied} 个，更新收藏集 {res.collections_changed} 个。"
            if res.warnings:
                msg += "\n\n提示：\n" + "\n".join(res.warnings[:8])
            if ver:
                msg += "\n\n重启后校验完成。"
            messagebox.showinfo("应用完成", msg, parent=self.root)
            self._append_log(msg.replace("\n", " "))
        elif name == "undo_all":
            res = result
            self.last_batch_snapshot = None
            self._update_undo_button()
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            msg = (
                f"恢复名称 {res.names_restored} 个，"
                f"清空分类 {res.categories_cleared} 个，"
                f"删除分类收藏集 {res.collections_deleted} 个。"
            )
            if getattr(res, "names_skipped", 0):
                msg += f"\n另有 {res.names_skipped} 个原名未知，名称未恢复。"
            messagebox.showinfo("复原完成", msg, parent=self.root)
            self._append_log(msg.replace("\n", " "))
        elif name == "autofix":
            res, ver = result
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log(f"修复完成：写入 {res.names_applied} 个名称")
            messagebox.showinfo("修复完成", f"写入 {res.names_applied} 个名称。", parent=self.root)
        elif name == "restore":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log("还原完成")
            messagebox.showinfo("还原完成", "已从备份恢复原始数据。", parent=self.root)
        elif name == "export_backup":
            self._append_log(f"备份已导出：{result.path}")
            messagebox.showinfo("备份完成", f"已导出到：\n{result.path}", parent=self.root)
        elif name == "import_backup":
            res = result
            self.refresh_table()
            self.refresh_sidebar()
            self.refresh_inspector()
            self.update_status()
            messagebox.showinfo("导入完成", f"已导入 {res.files} 个文件。", parent=self.root)
        elif name == "export":
            self._append_log(f"已导出：{result}")
            messagebox.showinfo("导出完成", f"已导出到：\n{result}", parent=self.root)
        elif name == "fetch_desc":
            self.refresh_table()
            self._append_log(f"简介补全完成：{result}")
            self.update_status()
        elif name == "originals":
            self._originals_running = False
            self.refresh_table()
            self.refresh_sidebar()
            self.refresh_inspector()
            self.update_status()
            if result is not None:
                self._append_log(
                    f"原名准备完成：列表内可恢复 {getattr(result, 'tracked_restorable', 0)}，"
                    f"缓存额外 {getattr(result, 'untracked_restorable', 0)}，"
                    f"未知 {getattr(result, 'unknown_skipped', 0)}"
                )
        self.update_status()

    def refresh_sidebar(self) -> None:
        if not hasattr(self, "sidebar"):
            return
        selected = self.sidebar.selection()
        self.sidebar.delete(*self.sidebar.get_children())
        if not self.ctx:
            return
        games = self.ctx.games
        recs = self.ctx.state
        total = len(games)
        translated = sum(
            1 for g in games
            if str(recs.get_app(g.appid).get("zh_name") or "").strip()
            and str(recs.get_app(g.appid).get("source") or "") != "original"
            and not recs.get_app(g.appid).get("skipped")
        )
        restored = sum(
            1 for g in games
            if str(recs.get_app(g.appid).get("source") or "") == "original"
            and (not str(recs.get_app(g.appid).get("zh_name") or "").strip()
                 or str(recs.get_app(g.appid).get("zh_name") or "") == str(recs.get_app(g.appid).get("orig_name") or ""))
        )
        pending_restore = sum(
            1 for g in games
            if str(recs.get_app(g.appid).get("source") or "") == "original"
            and str(recs.get_app(g.appid).get("zh_name") or "").strip()
            and str(recs.get_app(g.appid).get("zh_name") or "") != str(recs.get_app(g.appid).get("orig_name") or "")
        )
        skipped = sum(1 for g in games if recs.get_app(g.appid).get("skipped"))
        missing = total - translated - restored - pending_restore - skipped
        applied = sum(1 for g in games if recs.get_app(g.appid).get("applied"))
        installed = sum(1 for g in games if g.installed)
        cats: dict[str, int] = {}
        uncategorized = 0
        for g in games:
            got = category_state.get_categories(recs.get_app(g.appid))
            if not got:
                uncategorized += 1
            for cat in got:
                cats[cat] = cats.get(cat, 0) + 1
        self._sidebar_rows = []
        def add(iid: str, text: str, tags: tuple[str, ...] = ()) -> None:
            self.sidebar.insert("", "end", iid=iid, text=text, tags=tags)
            self._sidebar_rows.append(iid)
        add("all", f"全部游戏   {total}")
        add("translated", f"已翻译   {translated}")
        add("missing", f"待翻译   {missing}")
        add("pending_restore", f"待复原   {pending_restore}")
        add("original", f"已复原   {restored}")
        add("skipped", f"已跳过   {skipped}")
        add("applied", f"已应用   {applied}")
        add("installed", f"已安装   {installed}")
        add("uncategorized", f"未分类   {uncategorized}")
        add("section:cat", "分类", ("section",))
        for cat in category_model.category_names():
            add(f"cat:{cat}", f"● {cat}   {cats.get(cat, 0)}", (f"cat:{cat}",))
        for cat in category_model.category_names():
            color = CATEGORY_COLORS.get(cat, "#7d8a96")
            try:
                self.sidebar.tag_configure(f"cat:{cat}", foreground=color)
            except tk.TclError:
                pass
        self.sidebar.tag_configure("section", foreground="#5f7180")
        for iid in selected:
            if self.sidebar.exists(iid):
                try:
                    self.sidebar.selection_set(iid)
                except Exception:
                    pass

    def _on_sidebar(self, _event=None) -> None:
        if self._sidebar_guard or not hasattr(self, "sidebar"):
            return
        sel = self.sidebar.selection()
        if not sel:
            return
        iid = str(sel[0])
        if iid.startswith("section:"):
            return
        self._sidebar_guard = True
        try:
            if iid == "all":
                self.filter_var.set("全部"); self.category_var.set("全部分类"); self.installed_var.set(False)
            elif iid == "translated":
                self.filter_var.set("已翻译"); self.category_var.set("全部分类")
            elif iid == "missing":
                self.filter_var.set("待翻译"); self.category_var.set("全部分类")
            elif iid == "pending_restore":
                self.filter_var.set("待复原"); self.category_var.set("全部分类")
            elif iid == "original":
                self.filter_var.set("已复原"); self.category_var.set("全部分类")
            elif iid == "skipped":
                self.filter_var.set("已跳过"); self.category_var.set("全部分类")
            elif iid == "applied":
                self.filter_var.set("已应用"); self.category_var.set("全部分类")
            elif iid == "installed":
                self.filter_var.set("已安装"); self.category_var.set("全部分类"); self.installed_var.set(True)
            elif iid == "uncategorized":
                self.filter_var.set("未分类"); self.category_var.set("全部分类")
            elif iid.startswith("cat:"):
                self.category_var.set(iid[4:]); self.filter_var.set("全部")
        finally:
            self._sidebar_guard = False
        self.refresh_table()

    # --------------------------------------------------------------- table
    def _update_selection_label(self) -> None:
        if not hasattr(self, "table"):
            return
        active = self.table.active_ids()
        self.sel_var.set(f"宸查€?{len(active)}" if active else "")

    def _schedule_table_refresh(self) -> None:
        if self._refresh_after is not None:
            try:
                self.root.after_cancel(self._refresh_after)
            except Exception:
                pass
        self._refresh_after = self.root.after(180, self._do_table_refresh)

    def _do_table_refresh(self) -> None:
        self._refresh_after = None
        self.refresh_table()

    def refresh_table(self) -> None:
        if not self.ctx or not hasattr(self, "table"):
            return
        self.table.set_profile(self.workspace)
        q = self.search_var.get().strip().lower()
        flt = self.filter_var.get()
        cat_filter = self.category_var.get()
        only_installed = bool(self.installed_var.get())
        rows: list[ui_widgets.TableRow] = []
        for g in self.ctx.games:
            rec = self.ctx.state.get_app(g.appid)
            zh = str(rec.get("zh_name") or "")
            src = str(rec.get("source") or "")
            cats = category_state.get_categories(rec)
            orig_name = str(rec.get("orig_name") or "").strip()
            skipped = bool(rec.get("skipped"))
            if skipped:
                status = "已跳过"
            elif src == "original":
                status = "已复原" if not zh or zh == orig_name else "待复原"
            elif not zh:
                status = "待翻译"
            elif rec.get("applied"):
                status = "已应用"
            else:
                status = "可应用"
            hay = " ".join([g.name, zh, str(g.appid), " ".join(cats)]).lower()
            if q and q not in hay:
                continue
            if flt == "已翻译" and (not zh or src == "original" or skipped):
                continue
            if flt == "待翻译" and (zh or src == "original" or skipped):
                continue
            if flt == "已复原" and not (src == "original" and (not zh or zh == orig_name)):
                continue
            if flt == "待复原" and not (src == "original" and zh and zh != orig_name):
                continue
            if flt == "已跳过" and not skipped:
                continue
            if flt not in ("全部", "已跳过") and skipped:
                continue
            if flt == "未分类" and cats:
                continue
            if flt == "机翻" and src != "machine":
                continue
            if flt == "官方/词典" and src not in ("official", "dictionary", "steam", "already", "steam_official"):
                continue
            if flt == "手动" and src != "manual":
                continue
            if flt == "已应用" and not rec.get("applied"):
                continue
            if flt == "已安装" and not g.installed:
                continue
            if flt == "已拥有" and not g.owned:
                continue
            if cat_filter != "全部分类" and cat_filter not in cats:
                continue
            if only_installed and not g.installed:
                continue
            rows.append(ui_widgets.TableRow(
                appid=g.appid,
                zh=zh,
                orig=(
                    str(rec.get("orig_name") or "")
                    or ("（正在获取原名…）" if g.appid in getattr(self.ctx, "originals_pending", set())
                        else "（原名未知）")
                ),
                categories=cats,
                source=SOURCE_LABEL.get(src, src or "待翻译"),
                status=status,
                installed=g.installed,
                raw=g,
            ))
        if self.sort_key:
            def key(row: ui_widgets.TableRow):
                if self.sort_key == "categories":
                    return " / ".join(row.categories).lower()
                if self.sort_key == "game":
                    return (row.zh + " " + row.orig).lower()
                return str(getattr(row, self.sort_key, "")).lower()
            rows.sort(key=key, reverse=self.sort_desc)
        self.table.sort_key = self.sort_key
        self.table.sort_desc = self.sort_desc
        self.table.set_rows(rows)
        self.count_var.set(
            f"显示 {len(rows)} / {len(self.ctx.games)} · 已应用 "
            f"{sum(1 for r in self.ctx.state.apps.values() if r.get('applied'))}"
        )
        self._update_selection_label()

    def on_sort(self, column: str) -> None:
        if column == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = column
            self.sort_desc = True
        self.refresh_table()

    def selected_appids(self) -> set[int]:
        if not hasattr(self, "table"):
            return set()
        return self.table.active_ids()

    def on_table_double(self, appid: int, column: str) -> None:
        if not self.ctx:
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        rec = self.ctx.state.get_app(appid)
        if column == "categories":
            CategoryPickerDialog(self, game, rec)
        else:
            NameEditDialog(self, game, rec)

    def on_table_context(self, event: tk.Event, appid: int) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=FG,
                       activebackground="#2a475e", activeforeground=FG)
        menu.add_command(label="编辑名称", command=lambda: self._open_name(appid))
        menu.add_command(label="编辑分类", command=lambda: self._open_category(appid))
        rec = self.ctx.state.get_app(appid) if self.ctx else {}
        menu.add_command(
            label="取消跳过" if rec.get("skipped") else "跳过此游戏（不再补全原名）",
            command=lambda: self._toggle_skipped(appid),
        )
        menu.add_separator()
        menu.add_command(label="翻译选中", command=self.on_translate)
        menu.add_command(label="应用选中名称", command=lambda: self.on_apply(True, "names"))
        menu.add_command(label="应用选中分类", command=lambda: self.on_apply(True, "categories"))
        menu.add_command(label="应用选中全部", command=lambda: self.on_apply(True, "both"))
        menu.add_separator()
        menu.add_command(label="批量追加分类…", command=lambda: self.on_batch("append"))
        menu.add_command(label="批量替换分类…", command=lambda: self.on_batch("replace"))
        menu.add_command(label="批量移除分类…", command=lambda: self.on_batch("remove"))
        menu.add_separator()
        menu.add_command(label="自动分类选中", command=self.on_auto_selected)
        menu.add_command(label="清除选中分类", command=self.on_clear_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_name(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g:
            NameEditDialog(self, g, self.ctx.state.get_app(appid))

    def _open_category(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g:
            CategoryPickerDialog(self, g, self.ctx.state.get_app(appid))

    def _toggle_skipped(self, appid: int) -> None:
        if not self.ctx:
            return
        rec = self.ctx.state.get_app(appid)
        rec["skipped"] = not bool(rec.get("skipped"))
        self.ctx.state.save()
        self.refresh_table()
        self.refresh_inspector()
        self._append_log(("已跳过：" if rec["skipped"] else "已取消跳过：") + str(appid))

    def _skip_unknown_originals(self) -> int:
        if not self.ctx:
            return 0
        changed = 0
        for rec in self.ctx.state.apps.values():
            orig = str(rec.get("orig_name") or "").strip()
            if rec.get("skipped"):
                continue
            if orig and not rec.get("orig_unknown"):
                continue
            rec["skipped"] = True
            changed += 1
        if changed:
            self.ctx.state.save()
            self.refresh_table()
            self.refresh_inspector()
            self._append_log(f"已跳过 {changed} 个原名未知的游戏，后续不再查询/撤销它们")
        return changed

    def on_translate(self) -> None:
        if not self.ctx or self.busy:
            return
        if not messagebox.askyesno(
            "自动翻译",
            f"将为 {len(self.ctx.games)} 个条目补齐中文名。\n"
            "官方名/词典优先；联网源可能较慢。是否继续？",
            parent=self.root,
        ):
            return
        use_network = bool(self.settings.get("use_network", True))
        proxy = str(self.settings.get("proxy") or "").strip() or None
        self.cancel_flag = False
        self._append_log(f"开始翻译（联网：{'是' if use_network else '否'}）...")

        def work():
            service.reset_undo_names(self.ctx, log=lambda m: self.bus.put(("log", m)))
            return service.auto_translate(
                self.ctx, use_network=use_network, proxy=proxy,
                priority_order=self.settings.get("source_priority"),
                progress=lambda i, n, name: self.bus.put(
                    ("progress", (i, n, f"翻译 {i}/{n}: {name}"))
                ),
                should_stop=lambda: self.cancel_flag,
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("translate", work)

    def on_categorize(self) -> None:
        if not self.ctx:
            return
        service.reset_undo_categories(self.ctx, log=self._append_log)
        service.auto_categorize(self.ctx)
        self.refresh_table()
        self.refresh_sidebar()
        self.update_status()
        self._append_log("已按类型/标签/简介关键词重新分类（最多 3 个，手动锁定不覆盖）")

    def on_fetch_descriptions(self) -> None:
        if not self.ctx:
            return
        if not bool(self.settings.get("use_network", True)):
            messagebox.showinfo("提示", "设置中未启用联网。", parent=self.root)
            return
        missing = [g.appid for g in self.ctx.games if not getattr(g, "description", "")]
        if not missing:
            messagebox.showinfo("提示", "当前显示的游戏都已有简介缓存。", parent=self.root)
            return
        if not messagebox.askyesno(
            "补全简介",
            f"将为 {len(missing)} 个游戏尝试获取 Steam 商店简介。\n"
            "不可达时会跳过，可能需几分钟。是否继续？",
            parent=self.root,
        ):
            return
        proxy = str(self.settings.get("proxy") or "").strip() or None

        def work():
            cache = descriptions.fetch_many(missing, proxy=proxy)
            for g in self.ctx.games:
                text = cache.get(str(g.appid))
                if text:
                    g.description = text
                    self.ctx.state.get_app(g.appid)["description"] = text
            self.ctx.state.save()
            return len(cache)

        self.run_task("fetch_desc", work)

    def on_apply(self, selected: bool, mode: str = "both") -> None:
        if not self.ctx:
            return
        if mode not in ("names", "categories", "both"):
            return
        appids = self.selected_appids() if selected else None
        if selected and not appids:
            messagebox.showinfo("提示", "先在表格中勾选/选中要应用的游戏。", parent=self.root)
            return
        if mode == "categories":
            targets = [g for g in self.ctx.games
                       if (appids is None or g.appid in appids)
                       and category_state.get_categories(self.ctx.state.get_app(g.appid))]
            empty = "没有可应用的分类。"
        else:
            targets = [g for g in self.ctx.games
                       if (appids is None or g.appid in appids)
                       and str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()]
            empty = "没有可应用的中文名。"
        if not targets:
            messagebox.showinfo("提示", empty, parent=self.root)
            return
        if mode in ("names", "both"):
            missing_orig = [
                g for g in targets
                if not bool(self.ctx.state.get_app(g.appid).get("orig_verified"))
            ]
            if missing_orig:
                choice = messagebox.askyesnocancel(
                    "原名未补全",
                    f"有 {len(missing_orig)} 个游戏尚未补全原名。\n"
                    "如果现在应用名称，后悔药将无法恢复这些名称。\n\n"
                    "是否先补全原名？\n"
                    "选择“是”会先补全，然后请你重新点击应用；"
                    "选择“否”仍然应用；选择“取消”返回。",
                    parent=self.root,
                )
                if choice is None:
                    return
                if choice:
                    self._start_originals_fetch(force=True)
                    return
        what = {"names": "中文名", "categories": "分类收藏集", "both": "名称和分类"}[mode]
        if not messagebox.askyesno(
            "应用更改",
            f"将写入 {len(targets)} 个游戏的{what}。\n"
            "Steam 正在运行时会先关闭，写入后自动重启。\n是否继续？",
            parent=self.root,
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        lock = bool(self.settings.get("lock_appinfo", False)) and mode in ("names", "both")
        self._last_apply_mode = mode

        def work():
            kwargs = dict(selected=appids, restart=restart, lock_appinfo=lock,
                          progress=lambda m: self.bus.put(("progress", (0, 0, m))),
                          log=lambda m: self.bus.put(("log", m)))
            if mode == "names":
                res = service.do_apply_names(self.ctx, **kwargs)
            elif mode == "categories":
                kwargs.pop("lock_appinfo")
                res = service.do_apply_categories(self.ctx, **kwargs)
            else:
                res = service.do_apply(self.ctx, **kwargs)
            ver = None
            if restart:
                ver = service.do_verify(self.ctx, wait=12,
                                        log=lambda m: self.bus.put(("log", m)), mode=mode)
            return res, ver

        self.run_task("apply", work)

    def on_undo_all(self, mode: str = "both") -> None:
        if not self.ctx:
            return
        if not self.ctx.undo_prepared:
            if self.busy:
                return
            self._undo_after_prepare = True
            self._pending_undo_mode = mode
            proxy = str(self.settings.get("proxy") or "").strip() or None
            self._append_log("正在从 Steam 中文商店准备全部原名 ...")

            def prepare_work():
                return service.prepare_undo(
                    self.ctx, proxy=proxy,
                    progress=lambda done, total, appid: (
                        self.bus.put(("log", f"中文商店原名 {done}/{total}"))
                        if done % 10 == 0 or done == total else None
                    ),
                    should_stop=lambda: self.cancel_flag,
                )

            self.cancel_flag = False
            self.run_task("prepare_undo", prepare_work)
            return
        names = unknown = categories = extra = 0
        for rec in self.ctx.state.apps.values():
            if rec.get("skipped"):
                continue
            zh = str(rec.get("zh_name") or "").strip()
            orig = str(rec.get("orig_name") or "").strip()
            if orig and rec.get("orig_verified") and zh and (zh != orig or rec.get("applied")):
                names += 1
                if rec.get("cache_entry"):
                    extra += 1
            elif not orig or rec.get("orig_unknown"):
                unknown += 1
            if category_state.get_categories(rec):
                categories += 1
        collections_count = service.collection_cleanup_count(self.ctx)
        if names == 0 and categories == 0 and collections_count == 0:
            messagebox.showinfo("无需复原", "当前没有可复原的名称或分类。", parent=self.root)
            return
        scope = {"names": "只复原游戏名称", "categories": "只复原游戏分类", "both": "全部复原"}[mode]
        lines = ["⚠ 危险操作", "", f"操作范围：{scope}", ""]
        if mode in ("names", "both"):
            lines.append(f"将恢复名称：{names}")
            if extra:
                lines.append(f"  其中 {extra} 个来自 Steam 缓存额外条目")
        if mode in ("categories", "both"):
            lines.append(f"将清空分类：{categories}")
            lines.append(f"将删除分类收藏集：{collections_count}")
            lines.append("  收藏夹、已隐藏和智能过滤收藏集保留")
        if unknown and mode != "categories":
            lines.append(f"原名未知、将跳过：{unknown}")
        lines += ["", "操作前会自动完整备份。", "此操作会修改 Steam 本地数据。", "", "确定继续？"]
        if not messagebox.askyesno("危险操作", "\n".join(lines), parent=self.root, icon="warning", default="no"):
            return
        confirm = simpledialog.askstring("危险操作确认", "请输入“复原”以确认：", parent=self.root)
        if confirm != "复原":
            messagebox.showinfo("已取消", "输入不匹配，未执行任何操作。", parent=self.root)
            return
        restart = bool(self.settings.get("restart_steam", True))

        def work():
            return service.do_undo_all(
                self.ctx, restart=restart, mode=mode,
                progress=lambda m: self.bus.put(("progress", (0, 0, m))),
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("undo_all", work)

    def on_autofix(self) -> None:
        if not self.ctx:
            return
        if not messagebox.askyesno(
            "一键修复",
            "将关闭 Steam（如正在运行），重新写入中文名，然后启动 Steam。\n是否继续？",
            parent=self.root,
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        lock = bool(self.settings.get("lock_appinfo", False))

        def work():
            res = service.do_autofix(self.ctx, restart=restart, lock_appinfo=lock,
                                     log=lambda m: self.bus.put(("log", m)))
            ver = service.do_verify(self.ctx, wait=12, log=lambda m: self.bus.put(("log", m))) if restart else None
            return res, ver

        self.run_task("autofix", work)

    def on_restore(self) -> None:
        if not self.ctx:
            return
        backups = service.latest_backups(10)
        if not backups:
            messagebox.showinfo("提示", "还没有备份。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("选择备份")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="选择要还原的 Steam 本地缓存备份：", bg=BG, fg=FG).pack(padx=12, pady=8)
        lb = tk.Listbox(win, width=60, height=10, bg=PANEL2, fg=FG, relief="flat")
        lb.pack(padx=12, pady=6)
        for b in backups:
            lb.insert("end", b.name)
        lb.selection_set(0)
        restart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            win, text="还原后重新启动 Steam", variable=restart_var, bg=BG, fg=FG,
            selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", padx=12)

        def do_restore():
            sel = lb.curselection()
            if not sel:
                return
            b = backups[sel[0]]
            restart = bool(restart_var.get())
            win.destroy()
            if not messagebox.askyesno(
                "确认还原",
                f"将从备份 {b.name} 还原 appinfo 和收藏集。\n是否继续？",
                parent=self.root,
            ):
                return

            def work():
                service.do_restore(self.ctx, b, restart=restart,
                                   log=lambda m: self.bus.put(("log", m)))
                return str(b)

            self.run_task("restore", work)

        tk.Button(win, text="还原", command=do_restore, bg=ACCENT, fg="white").pack(pady=8)

    def on_export(self) -> None:
        if not self.ctx:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出清单", defaultextension=".csv",
            initialfile="steam_zh_report.csv", filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        service.export_report(self.ctx, Path(path))
        self._append_log(f"已导出到 {path}")
        messagebox.showinfo("导出完成", path, parent=self.root)

    def on_settings(self) -> None:
        SettingsDialog(self)

    def on_cancel(self) -> None:
        self.cancel_flag = True
        self._append_log("已请求取消（当前网络请求完成后停止）")

    def update_status(self) -> None:
        if not self.ctx:
            self.status_var.set("未加载")
            self.header_status.set("未加载")
            return
        c = service.counts(self.ctx)
        running = steamenv.steam_running()
        cache = "分片缓存" if self.ctx.env.has_shards else "单文件缓存"
        text = (
            f"{'Steam 运行中' if running else 'Steam 已关闭'} · "
            f"{self.ctx.env.persona or self.ctx.env.account} · {cache} · "
            f"共 {c['total']} · 已应用 {c['applied']} · 待翻译 {c['missing']}"
        )
        self.status_var.set(text)
        self.header_status.set(
            f"{self.ctx.env.persona or self.ctx.env.account} · "
            f"{'Steam 运行中' if running else 'Steam 已关闭'}"
        )


    def reload(self) -> None:
        if self.busy:
            return
        self._append_log("正在扫描 Steam 本地缓存 ...")
        self.cancel_flag = False
        def work():
            return service.load_context(self.settings)
        self.run_task("load", work)

    def _maybe_auto_translate_new(self) -> None:
        if not self.ctx or not bool(self.settings.get("auto_translate_new", False)):
            return
        missing = [g for g in self.ctx.games
                   if not str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
                   and not self.ctx.state.get_app(g.appid).get("skipped")]
        if not missing:
            return
        self._append_log(f"发现 {len(missing)} 个新增/待翻译游戏，开始自动联网翻译 ...")
        proxy = str(self.settings.get("proxy") or "").strip() or None
        def work():
            service.auto_translate(
                self.ctx, use_network=True, proxy=proxy,
                priority_order=self.settings.get("source_priority"),
                progress=lambda i, n, name: self.bus.put(("progress", (i, n, f"翻译 {i}/{n}: {name}"))),
                should_stop=lambda: self.cancel_flag,
                log=lambda m: self.bus.put(("log", m)),
            )
            service.auto_categorize(self.ctx)
            return True
        self.run_task("auto_new_translate", work)

    def _start_originals_fetch(self, force: bool = False) -> None:
        if not self.ctx or self._originals_running:
            return
        if self._originals_attempted and not force:
            return
        self._originals_running = True
        self._originals_attempted = True
        proxy = str(self.settings.get("proxy") or "").strip() or None
        self._append_log("正在从 Steam 中文商店准备全部原名 ...")
        def work():
            return service.fetch_missing_originals(
                self.ctx, proxy=proxy, force=force,
                progress=lambda i, n, appid: self.bus.put(("progress", (i, n, f"原名 {i}/{n}"))),
                should_stop=lambda: self.cancel_flag,
            )
        self.run_task("originals", work)

    def on_batch(self, mode: str = "append") -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "先在表格中勾选/选中游戏。", parent=self.root)
            return
        BatchCategoryDialog(self, ids, mode)

    def on_auto_selected(self) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            return
        for appid in ids:
            rec = self.ctx.state.get_app(appid)
            rec["locked"] = False
        service.auto_categorize(self.ctx)
        for appid in ids:
            self.ctx.state.get_app(appid)["locked"] = True
        self.refresh_table(); self.refresh_inspector()
        self.log(f"已重新自动分类 {len(ids)} 个游戏")

    def on_clear_selected(self) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            return
        if not messagebox.askyesno("清除分类", f"清除 {len(ids)} 个游戏的分类？", parent=self.root):
            return
        for appid in ids:
            category_state.set_categories(self.ctx.state.get_app(appid), [], source="manual", locked=True)
        self.ctx.state.save()
        self.refresh_table(); self.refresh_inspector()
        self.log(f"已清除 {len(ids)} 个游戏的分类")

    def on_undo_batch(self) -> None:
        if not self.ctx or not self.last_batch_snapshot:
            return
        service.batch_undo(self.ctx, self.last_batch_snapshot)
        self.last_batch_snapshot = None
        self.ctx.state.save()
        self.refresh_table(); self.refresh_sidebar()
        self._update_undo_button()
        self.log("已撤销上次批量分类")

    def on_export_backup(self) -> None:
        if not self.ctx:
            return
        path = filedialog.asksaveasfilename(
            title="导出完整备份", defaultextension=".zip",
            filetypes=[("ZIP 备份", "*.zip")], parent=self.root,
            initialfile="steam-unify-backup.zip",
        )
        if not path:
            return
        def work():
            return backup_bundle.export_backup(self.ctx.env, self.ctx.state, Path(path),
                                               log=lambda m: self.bus.put(("log", m)))
        self.run_task("export_backup", work)

    def on_import_backup(self) -> None:
        if not self.ctx:
            return
        path = filedialog.askopenfilename(
            title="导入备份", filetypes=[("ZIP 备份", "*.zip")], parent=self.root
        )
        if not path:
            return
        if not messagebox.askyesno(
            "导入备份",
            "导入会覆盖当前名称/分类状态和 Steam 本地缓存，程序会先自动备份。\n继续？",
            parent=self.root,
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        def work():
            return backup_bundle.import_backup(self.ctx.env, self.ctx.state, Path(path),
                                               restart=restart,
                                               log=lambda m: self.bus.put(("log", m)))
        self.run_task("import_backup", work)

    def _build_ui(self) -> None:
        self.status_var = tk.StringVar(value="正在加载 ...")
        self.count_var = tk.StringVar(value="")
        self.sel_var = tk.StringVar(value="")
        self._build_menubar()

        header = tk.Frame(self.root, bg=PANEL, height=50)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text="Steam 统译 v2.0.4", bg=PANEL, fg="#ffffff",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left", padx=16)
        tk.Label(header, text="翻译名称 / 分类整理 / 缓存级复原", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=4)
        self.header_status = tk.StringVar(value="正在加载 ...")
        tk.Label(header, textvariable=self.header_status, bg=PANEL, fg=ACCENT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right", padx=16)

        status_bar = tk.Frame(self.root, bg=PANEL, height=26)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        tk.Label(status_bar, textvariable=self.status_var, bg=PANEL, fg=MUTED,
                 anchor="w", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=8)
        tk.Label(status_bar, textvariable=self.sel_var, bg=PANEL, fg=ACCENT,
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=5)
        tk.Label(status_bar, textvariable=self.count_var, bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=5)
        self.cancel_btn = ttk.Button(status_bar, text="取消", style="Small.TButton",
                                     command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="right", padx=4)
        self.log_toggle_btn = ttk.Button(status_bar, text="日志", style="Small.TButton",
                                         command=self._toggle_log)
        self.log_toggle_btn.pack(side="right", padx=4)
        self.progress = ttk.Progressbar(status_bar, mode="determinate", maximum=100, length=130)
        self.progress.pack(side="right", padx=4)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        sidebar = tk.Frame(body, bg=PANEL, width=195)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="导航", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(8, 4))
        sb_wrap = tk.Frame(sidebar, bg=PANEL)
        sb_wrap.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 6))
        self.sidebar = ttk.Treeview(sb_wrap, show="tree", selectmode="browse", style="Sidebar.Treeview")
        self.sidebar.pack(side="left", fill="both", expand=True)
        sb_vsb = ttk.Scrollbar(sb_wrap, orient="vertical", command=self.sidebar.yview)
        self.sidebar.configure(yscrollcommand=sb_vsb.set)
        sb_vsb.pack(side="right", fill="y")
        self.sidebar.bind("<<TreeviewSelect>>", self._on_sidebar)

        self.inspector = tk.Frame(body, bg=PANEL, width=320)
        self.inspector.pack(side="right", fill="y")
        self.inspector.pack_propagate(False)
        self._build_inspector(self.inspector)

        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)
        tabs = tk.Frame(main, bg=BG)
        tabs.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 0))
        self._build_tabs(tabs)
        actions = tk.Frame(main, bg=BG)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 4))
        self._build_action_bar(actions)
        filters = tk.Frame(main, bg=BG)
        filters.grid(row=2, column=0, sticky="ew", padx=12, pady=2)
        self._build_filters(filters)
        table_frame = tk.Frame(main, bg=BG)
        table_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        self.table = ui_widgets.TagTable(
            table_frame,
            on_selection=self._on_table_selection,
            on_check=lambda _ids: self._update_selection_label(),
            on_double=self.on_table_double,
            on_context=self.on_table_context,
            on_sort=self.on_sort,
        )
        self.table.pack(fill="both", expand=True)

        self.log_frame = tk.Frame(main, bg="#0e141b", highlightthickness=1,
                                  highlightbackground="#2a3b4d")
        log_head = tk.Frame(self.log_frame, bg=PANEL)
        log_head.pack(fill="x")
        self.log_last_var = tk.StringVar(value="日志")
        tk.Label(log_head, textvariable=self.log_last_var, bg=PANEL, fg=MUTED,
                 anchor="w", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8, pady=3, fill="x", expand=True)
        ttk.Button(log_head, text="收起", style="Small.TButton",
                   command=self._toggle_log).pack(side="right", padx=4, pady=2)
        self.log_body = tk.Frame(self.log_frame, bg=BG)
        self.log_body.pack(fill="both", expand=True)
        self.log_text = tk.Text(self.log_body, height=6, bg="#0e141b", fg=MUTED,
                                insertbackground=FG, relief="flat", wrap="word",
                                font=("Microsoft YaHei UI", 9))
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb = ttk.Scrollbar(self.log_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")
        self._set_workspace("both", initial=True)

    def _build_menubar(self) -> None:
        bar = tk.Frame(self.root, bg="#0e141b", height=26)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        def add(label):
            mb = tk.Menubutton(bar, text=label, bg="#0e141b", fg=FG,
                               activebackground="#2a475e", activeforeground="#ffffff",
                               bd=0, padx=10, pady=2, font=("Microsoft YaHei UI", 9))
            menu = tk.Menu(mb, tearoff=0, bg=PANEL2, fg=FG,
                           activebackground="#2a475e", activeforeground="#ffffff")
            mb.configure(menu=menu); mb.pack(side="left"); return menu
        m = add("文件")
        m.add_command(label="扫描库", command=self.reload)
        m.add_command(label="补全简介", command=self.on_fetch_descriptions)
        m.add_command(label="导出清单", command=self.on_export)
        m.add_separator(); m.add_command(label="退出", command=self._on_close)
        m = add("编辑")
        m.add_command(label="全选", command=lambda: self.table.select_all_checked())
        m.add_command(label="取消全选", command=lambda: self.table.clear_checked())
        m.add_separator()
        m.add_command(label="编辑名称", command=self._edit_selected_name)
        m.add_command(label="编辑分类", command=self._edit_selected_category)
        m = add("视图")
        for key, label in (("names", "翻译名称"), ("categories", "分类整理"), ("both", "全部")):
            m.add_command(label=label, command=lambda k=key: self._set_workspace(k))
        m.add_separator()
        m.add_command(label="显示/隐藏参考源", command=self._toggle_inspector)
        m.add_command(label="显示/隐藏日志", command=self._toggle_log)
        m = add("工具")
        m.add_command(label="导出备份", command=self.on_export_backup)
        m.add_command(label="导入备份", command=self.on_import_backup)
        m.add_command(label="补全原名", command=lambda: self._start_originals_fetch(force=True))
        m.add_command(label="跳过未知原名", command=self._skip_unknown_originals)
        m.add_command(label="设置", command=self.on_settings)
        m = add("帮助")
        m.add_command(label="使用说明", command=self._show_help)
        m.add_command(label="关于", command=self._show_about)

    def _build_tabs(self, parent):
        self.workspace_buttons = {}
        for key, label in (("names", "翻译名称"), ("categories", "分类整理"), ("both", "全部")):
            b = ttk.Button(parent, text=label, style="TButton",
                           command=lambda k=key: self._set_workspace(k))
            b.pack(side="left", padx=(0, 6)); self.workspace_buttons[key] = b
        ttk.Button(parent, text="参考源", style="Small.TButton",
                   command=self._toggle_inspector).pack(side="right")

    def _build_action_bar(self, parent):
        self.action_frames = {}; self.buttons = {}
        def make(space, specs):
            frame = tk.Frame(parent, bg=BG); self.action_frames[space] = frame
            for key, text, command, style_name in specs:
                b = ttk.Button(frame, text=text, command=command,
                               style=style_name or "Secondary.TButton")
                b.pack(side="left", padx=(0, 6)); self.buttons[key] = b
        make("names", [
            ("auto_translate", "自动翻译", self.on_translate, "Primary.TButton"),
            ("apply_names", "应用名称", lambda: self.on_apply(False, "names"), ""),
            ("apply_sel_names", "应用选中", lambda: self.on_apply(True, "names"), ""),
        ])
        make("categories", [
            ("auto_categories", "自动分类", self.on_categorize, "Primary.TButton"),
            ("batch", "批量分类", self.on_batch, ""),
            ("apply_categories", "应用分类", lambda: self.on_apply(False, "categories"), ""),
            ("apply_sel_categories", "应用选中", lambda: self.on_apply(True, "categories"), ""),
            ("undo_batch", "撤销批量", self.on_undo_batch, ""),
        ])
        make("both", [
            ("auto_translate", "自动翻译", self.on_translate, "Primary.TButton"),
            ("auto_categories", "自动分类", self.on_categorize, ""),
            ("apply_all", "应用全部", lambda: self.on_apply(False, "both"), "Primary.TButton"),
            ("apply_sel_all", "应用选中", lambda: self.on_apply(True, "both"), ""),
        ])
        right = tk.Frame(parent, bg=BG); right.pack(side="right")
        tools = ttk.Menubutton(right, text="⚙ 工具", style="Tool.TMenubutton")
        tools.pack(side="left", padx=(0, 4))
        tm = tk.Menu(tools, tearoff=0, bg=PANEL2, fg=FG,
                     activebackground="#2a475e", activeforeground="#ffffff")
        tm.add_command(label="导出清单", command=self.on_export)
        tm.add_command(label="导出备份", command=self.on_export_backup)
        tm.add_command(label="导入备份", command=self.on_import_backup)
        tm.add_separator()
        tm.add_command(label="补全原名", command=lambda: self._start_originals_fetch(force=True))
        tm.add_command(label="跳过未知原名", command=self._skip_unknown_originals)
        tm.add_command(label="补全简介", command=self.on_fetch_descriptions)
        tm.add_separator(); tm.add_command(label="设置", command=self.on_settings)
        tools.configure(menu=tm); self.tool_menu = tm
        danger = ttk.Menubutton(right, text="⚠ 危险", style="Tool.TMenubutton")
        danger.pack(side="left")
        dm = tk.Menu(danger, tearoff=0, bg=PANEL2, fg=FG,
                     activebackground="#7d2b2b", activeforeground="#ffffff")
        dm.add_command(label="只复原游戏名称…", command=lambda: self.on_undo_all("names"))
        dm.add_command(label="只复原游戏分类…", command=lambda: self.on_undo_all("categories"))
        dm.add_command(label="全部复原…", command=lambda: self.on_undo_all("both"))
        danger.configure(menu=dm); self.danger_menu = dm

    def _build_inspector(self, parent):
        head = tk.Frame(parent, bg=PANEL); head.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(head, text="参考源", bg=PANEL, fg="#ffffff",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(head, text="收起", style="Small.TButton",
                   command=self._toggle_inspector).pack(side="right")
        self.inspector_notebook = ttk.Notebook(parent)
        self.inspector_notebook.pack(fill="both", expand=True, padx=6, pady=4)
        name_tab = tk.Frame(self.inspector_notebook, bg=BG)
        cat_tab = tk.Frame(self.inspector_notebook, bg=BG)
        self.inspector_notebook.add(name_tab, text="译名参考")
        self.inspector_notebook.add(cat_tab, text="分类参考")
        self.name_source_text = tk.Text(name_tab, height=12, bg=PANEL2, fg=FG,
                                        relief="flat", wrap="word", font=("Microsoft YaHei UI", 9))
        self.name_source_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.name_choice_var = tk.StringVar()
        self.name_choice = ttk.Combobox(name_tab, textvariable=self.name_choice_var, state="readonly")
        self.name_choice.pack(fill="x", padx=4, pady=2)
        row = tk.Frame(name_tab, bg=BG); row.pack(fill="x", padx=4, pady=(2, 6))
        ttk.Button(row, text="获取参考译名", style="Small.TButton",
                   command=self._fetch_selected_sources).pack(side="left", padx=2)
        ttk.Button(row, text="使用选中译名", style="Accent.TButton",
                   command=self._use_selected_candidate).pack(side="left", padx=2)
        ttk.Button(row, text="打开来源", style="Small.TButton",
                   command=self._open_selected_source).pack(side="left", padx=2)
        self.cat_source_text = tk.Text(cat_tab, height=12, bg=PANEL2, fg=FG,
                                       relief="flat", wrap="word", font=("Microsoft YaHei UI", 9))
        self.cat_source_text.pack(fill="both", expand=True, padx=4, pady=4)
        row2 = tk.Frame(cat_tab, bg=BG); row2.pack(fill="x", padx=4, pady=(2, 6))
        ttk.Button(row2, text="获取分类证据", style="Small.TButton",
                   command=self._fetch_selected_categories).pack(side="left", padx=2)
        ttk.Button(row2, text="按证据重新分类", style="Accent.TButton",
                   command=self._categorize_selected_with_evidence).pack(side="left", padx=2)
        self._inspector_candidates = []

    def _set_workspace(self, workspace: str, initial: bool = False) -> None:
        if workspace not in ("names", "categories", "both"):
            return
        self.workspace = workspace
        for key, frame in getattr(self, "action_frames", {}).items():
            if key == workspace:
                if not frame.winfo_ismapped():
                    frame.pack(side="left")
            else:
                frame.pack_forget()
        for key, button in getattr(self, "workspace_buttons", {}).items():
            button.configure(style="Accent.TButton" if key == workspace else "TButton")
        if hasattr(self, "table"):
            self.table.set_profile(workspace)
        self._update_action_state()
        if not initial and self.ctx:
            self.refresh_table()

    def _toggle_inspector(self) -> None:
        if self.inspector_visible:
            self.inspector.pack_forget(); self.inspector_visible = False
        else:
            self.inspector.pack(side="right", fill="y"); self.inspector_visible = True

    def _update_action_state(self) -> None:
        if not hasattr(self, "buttons"):
            return
        selected = bool(self.table.active_ids()) if hasattr(self, "table") else False
        any_names = any(str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
                        for g in self.ctx.games) if self.ctx else False
        any_categories = any(category_state.get_categories(self.ctx.state.get_app(g.appid))
                             for g in self.ctx.games) if self.ctx else False
        for key, button in self.buttons.items():
            enabled = True
            if key.startswith("apply_sel") or key == "batch":
                enabled = selected
            elif key == "undo_batch":
                enabled = self.last_batch_snapshot is not None
            elif key == "apply_names":
                enabled = any_names
            elif key == "apply_categories":
                enabled = any_categories
            elif key == "apply_all":
                enabled = any_names or any_categories
            if self.busy:
                enabled = False
            button.configure(state="normal" if enabled else "disabled")
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.configure(state="normal" if self.busy else "disabled")

    def _edit_selected_name(self):
        appid = self._selected_appid()
        if appid is not None: self._open_name(appid)

    def _edit_selected_category(self):
        appid = self._selected_appid()
        if appid is not None: self._open_category(appid)

    def _show_help(self):
        path = paths.app_dir() / "README.md"
        if path.is_file():
            try: os.startfile(str(path)); return
            except Exception: pass
        messagebox.showinfo("使用说明", "README.md 位于程序目录。", parent=self.root)

    def _show_about(self):
        messagebox.showinfo("关于", "Steam 统译 v2.0.4\n游戏名汉化 / 多标签分类 / 缓存级复原",
                            parent=self.root)


def main() -> None:
    from . import paths

    paths.ensure_data()
    root = tk.Tk()
    SteamZhApp(root)
    root.mainloop()

