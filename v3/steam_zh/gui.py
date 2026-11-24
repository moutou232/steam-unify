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
    collection_actions,
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
from .gui_plan_dialogs import PlanImportDialog
from .ui_anim import Animator, set_motion_mode
from .ui_components import (toast, ToggleSwitch, MotionButton, RoundedSurface, RoundedField, TabBar, AnimatedSelect, AnimatedMenuButton)
from .ui_sidebar import SidebarList
from . import collection_plan


class SteamZhApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        try:
            scaling = float(root.tk.call("tk", "scaling")) or 1.0
        except Exception:
            scaling = 1.0
        self.settings = State.load_settings()
        set_motion_mode(str(self.settings.get("ui_motion") or "full"))
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
        self._collection_filter: set[int] | None = None
        self._collection_name = ""
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
        from . import ui_theme as T
        style.configure(
            ".", background=BG, foreground=FG, fieldbackground=PANEL2,
            bordercolor=T.BORDER, lightcolor=T.BORDER, darkcolor=T.BORDER,
            font=(T.FONT, 9),
        )
        style.configure("TButton", background=T.SURFACE_2, foreground=T.TEXT,
                        padding=(10, 5), borderwidth=0, focusthickness=0,
                        font=(T.FONT, 9))
        style.map("TButton",
                  background=[("active", T.SURFACE_3), ("disabled", "#151d27")],
                  foreground=[("disabled", T.DIM)],
                  relief=[("pressed", "flat")])
        style.configure("Primary.TButton", background=T.PRIMARY, foreground="white",
                        padding=(14, 6), borderwidth=0,
                        font=(T.FONT, 9, "bold"))
        style.map("Primary.TButton",
                  background=[("active", T.PRIMARY_HOVER), ("pressed", T.PRIMARY_PRESSED),
                              ("disabled", "#26333f")],
                  foreground=[("disabled", T.DIM)])
        style.configure("Accent.TButton", background=T.PRIMARY, foreground="white",
                        padding=(12, 5), borderwidth=0,
                        font=(T.FONT, 9, "bold"))
        style.map("Accent.TButton",
                  background=[("active", T.PRIMARY_HOVER), ("disabled", "#26333f")],
                  foreground=[("disabled", T.DIM)])
        style.configure("Secondary.TButton", background=T.SURFACE_2, foreground=T.TEXT,
                        padding=(10, 5), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", T.SURFACE_3),
                                                   ("disabled", "#151d27")],
                  foreground=[("disabled", T.DIM)])
        style.configure("Ghost.TButton", background=BG, foreground=T.MUTED,
                        padding=(8, 4), borderwidth=0)
        style.map("Ghost.TButton", background=[("active", T.SURFACE_2)],
                  foreground=[("active", T.TEXT)])
        style.configure("Tool.TMenubutton", background=T.SURFACE_2, foreground=T.TEXT,
                        padding=(10, 5), borderwidth=0)
        style.map("Tool.TMenubutton", background=[("active", T.SURFACE_3)])
        style.configure("Danger.TButton", background="#40232a", foreground=T.DANGER,
                        padding=(10, 5))
        style.map("Danger.TButton", background=[("active", "#5a2b34")])
        style.configure("Small.TButton", padding=(5, 2),
                        font=(T.FONT, 8), background=T.SURFACE_2)
        style.map("Small.TButton", background=[("active", T.SURFACE_3)])
        style.configure("Group.TLabel", background=BG, foreground=T.MUTED,
                        font=(T.FONT, 8))
        style.configure("Sidebar.Treeview", background=T.SURFACE,
                        fieldbackground=T.SURFACE, foreground=T.TEXT,
                        rowheight=30, borderwidth=0, font=(T.FONT, 9))
        style.map("Sidebar.Treeview",
                  background=[("selected", T.SELECTION)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TEntry", fieldbackground=T.SURFACE_2, foreground=T.TEXT,
                        bordercolor=T.BORDER, insertcolor=T.ACCENT, padding=(8, 5))
        style.configure("TCombobox", fieldbackground=T.SURFACE_2, background=T.SURFACE_2,
                        foreground=T.TEXT, arrowcolor=T.MUTED,
                        selectbackground=T.SURFACE_2, selectforeground=T.TEXT,
                        bordercolor=T.BORDER, padding=(6, 4))
        style.map("TCombobox",
                  fieldbackground=[("readonly", T.SURFACE_2)],
                  foreground=[("readonly", T.TEXT)],
                  selectbackground=[("readonly", T.SURFACE_2)],
                  selectforeground=[("readonly", T.TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=T.MUTED)
        style.configure("TNotebook", background=T.SURFACE, borderwidth=0)
        style.configure("TNotebook.Tab", background=T.SURFACE_2, foreground=T.MUTED,
                        padding=(12, 5), font=(T.FONT, 9))
        style.map("TNotebook.Tab", background=[("selected", T.SURFACE_3)],
                  foreground=[("selected", T.TEXT)])
        style.configure("TProgressbar", background=T.ACCENT, troughcolor=T.SURFACE_2,
                        borderwidth=0, thickness=4)
        style.configure("Vertical.TScrollbar", background=T.SURFACE_3,
                        troughcolor=T.BG, borderwidth=0, arrowsize=0, width=8)
        style.configure("Horizontal.TScrollbar", background=T.SURFACE_3,
                        troughcolor=T.BG, borderwidth=0, arrowsize=0)

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
        search_field = RoundedField(parent, width=220, height=32)
        search_field.pack(side="left", padx=(6, 12))
        entry = tk.Entry(
            search_field.inner, textvariable=self.search_var, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat", bd=0,
        )
        self.search_entry = entry
        entry.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(parent, text="状态", bg=BG, fg=MUTED).pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        status_combo = AnimatedSelect(parent, self.filter_var, STATUS_FILTERS,
                                      command=self.refresh_table, width=96)
        status_combo.pack(side="left", padx=(4, 8))
        tk.Label(parent, text="收藏集", bg=BG, fg=MUTED).pack(side="left")
        self.category_var = tk.StringVar(value="全部分类")
        self.cat_combo = AnimatedSelect(parent, self.category_var, ["全部分类"],
                                        command=self.refresh_table, width=150)
        self.cat_combo.pack(side="left", padx=(4, 8))
        self.installed_var = tk.BooleanVar(value=False)
        ToggleSwitch(parent, self.installed_var, command=self.refresh_table).pack(
            side="left", padx=(2, 4))
        tk.Label(parent, text="仅已安装", bg=BG, fg=MUTED).pack(side="left", padx=(0, 8))

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
        animator = getattr(self, "_log_animator", None)
        if self.log_visible and not force:
            self.log_visible = False
            self.log_toggle_btn.configure(text="日志")
            if animator is None:
                self.log_frame.place_forget()
                return
            def shrink(t):
                h = max(0, int(178 * (1 - t)))
                self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=h)
            animator.animate(200, shrink, self.log_frame.place_forget)
            return
        self.log_visible = True
        self.log_toggle_btn.configure(text="收起日志")
        self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=0)
        self.log_frame.lift()
        if animator is None:
            self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=178)
            return
        def grow(t):
            self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0,
                                 height=int(178 * t))
        animator.animate(220, grow)

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
            warn = False
            if ver:
                if getattr(ver, "cache_missing_after_restart", False):
                    warn = True
                    msg += ("\n\nSteam 重启后没有找到 appinfo 缓存，"
                            "名称可能未持久化。\n请完全退出 Steam 后点击“重新应用”。")
                elif getattr(ver, "verify_failed", []):
                    warn = True
                    msg += (f"\n\n重启后名称异常：{len(ver.verify_failed)} 个。\n"
                            "请退出 Steam 后重新应用。")
                else:
                    msg += ("\n\n重启后校验完成："
                            f"名称 {ver.verified_names} 个，"
                            f"ACF {ver.verified_acfs} 个。")
                if getattr(ver, "warnings", []):
                    msg += "\n" + "\n".join(ver.warnings[:5])
            if warn:
                messagebox.showwarning("应用结果需检查", msg, parent=self.root)
            else:
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
        elif name == "collection_action":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log(f"收藏集操作完成：{result}")
            messagebox.showinfo("收藏集已更新", str(result), parent=self.root)
            toast(self, "收藏集已更新", "success")
        elif name == "plan_apply":
            self.refresh_table()
            self.refresh_sidebar()
            self.refresh_inspector()
            self.update_status()
            self._append_log(f"分类方案已应用：{result}")
            messagebox.showinfo("方案已应用", f"已应用分类方案：\n{result}", parent=self.root)
            toast(self, "分类方案已应用", "success")
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
        local_ids = {g.appid for g in games}
        try:
            live_collections = collection_plan.read_live_collections(self.ctx)
        except Exception:
            live_collections = []
        collection_counts = {
            item.name: len(set(item.appids) & local_ids)
            for item in live_collections
        }
        if hasattr(self, "cat_combo"):
            values = ["全部分类"] + [item.name for item in live_collections]
            self.cat_combo.configure(values=values)
            if self.category_var.get() not in values:
                self.category_var.set("全部分类")
        covered = set()
        for item in live_collections:
            covered |= set(item.appids)
        uncategorized = sum(1 for g in games if g.appid not in covered)
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
        add("section:collections", "Steam 收藏集", ("section",))
        for item in live_collections:
            add(f"collection:{item.name}", f"● {item.name}   {collection_counts.get(item.name, 0)}")
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
        self._collection_filter = None
        self._collection_name = ""
        try:
            if iid.startswith("collection:"):
                name = iid[len("collection:"):]
                try:
                    live = {c.name: c for c in collection_plan.read_live_collections(self.ctx)}
                except Exception:
                    live = {}
                item = live.get(name)
                self._collection_filter = set(item.appids) if item else set()
                self._collection_name = name
                self.filter_var.set("全部")
                self.category_var.set("全部分类")
            elif iid == "all":
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
        collection_map: dict[int, list[str]] = {}
        try:
            for item in collection_plan.read_live_collections(self.ctx):
                for appid in item.appids:
                    collection_map.setdefault(appid, []).append(item.name)
        except Exception:
            collection_map = {}
        rows: list[ui_widgets.TableRow] = []
        for g in self.ctx.games:
            rec = self.ctx.state.get_app(g.appid)
            zh = str(rec.get("zh_name") or "")
            src = str(rec.get("source") or "")
            cats = collection_map.get(g.appid, [])
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
            if cat_filter not in ("全部分类", "全部收藏集") and cat_filter not in cats:
                continue
            if self._collection_filter is not None and g.appid not in self._collection_filter:
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
            self.on_add_to_collection()
        else:
            NameEditDialog(self, game, rec)

    def on_table_context(self, event: tk.Event, appid: int) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=FG,
                       activebackground="#2a475e", activeforeground=FG)
        menu.add_command(label="编辑名称", command=lambda: self._open_name(appid))
        rec = self.ctx.state.get_app(appid) if self.ctx else {}
        menu.add_command(
            label="取消跳过" if rec.get("skipped") else "跳过此游戏（不再补全原名）",
            command=lambda: self._toggle_skipped(appid),
        )
        menu.add_separator()
        menu.add_command(label="翻译选中", command=self.on_translate)
        menu.add_command(label="应用选中名称", command=lambda: self.on_apply(True, "names"))
        menu.add_separator()
        menu.add_command(label="添加到收藏集…", command=self.on_add_to_collection)
        menu.add_command(label="从当前收藏集移除", command=self.on_remove_from_collection)
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
                ver = service.do_verify(self.ctx, wait=30,
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
            lines.append("  注意：将清除所有分类收藏集（包括非本程序创建的）。")
            lines.append("  这会让所有游戏变为未分类状态。")
            lines.append("  收藏夹、已隐藏和智能过滤收藏集保留。")
            lines.append("  若只需要删去部分收藏集，请直接在 Steam 中进行操作。")
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
            ver = service.do_verify(self.ctx, wait=30, log=lambda m: self.bus.put(("log", m))) if restart else None
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

    def _collection_selected(self) -> set[int]:
        return self.table.active_ids() if hasattr(self, "table") else set()

    def _run_collection_action(self, label: str, fn) -> None:
        if not self.ctx:
            return
        self._append_log(f"{label} ...")
        self.run_task("collection_action", fn)

    def on_new_collection(self) -> None:
        if not self.ctx:
            return
        name = simpledialog.askstring("新建收藏集", "收藏集名称：", parent=self.root)
        if not name or not name.strip():
            return
        ids = sorted(self._collection_selected())
        restart = bool(self.settings.get("restart_steam", True))
        self._run_collection_action(
            "新建收藏集",
            lambda: collection_actions.create_collection(
                self.ctx, name.strip(), ids, restart=restart,
                log=lambda m: self.bus.put(("log", m)),
            ),
        )

    def on_rename_collection(self) -> None:
        if not self.ctx or not self._collection_name:
            messagebox.showinfo("未选择收藏集", "请先在左侧选择一个收藏集。", parent=self.root)
            return
        new_name = simpledialog.askstring(
            "重命名收藏集", f"当前：{self._collection_name}\n新名称：", parent=self.root
        )
        if not new_name or not new_name.strip():
            return
        old = self._collection_name
        restart = bool(self.settings.get("restart_steam", True))
        self._run_collection_action(
            "重命名收藏集",
            lambda: collection_actions.rename_collection(
                self.ctx, old, new_name.strip(), restart=restart,
                log=lambda m: self.bus.put(("log", m)),
            ),
        )
        self._collection_name = new_name.strip()

    def on_delete_collection(self) -> None:
        if not self.ctx or not self._collection_name:
            messagebox.showinfo("未选择收藏集", "请先在左侧选择一个收藏集。", parent=self.root)
            return
        name = self._collection_name
        if not messagebox.askyesno(
            "删除收藏集",
            f"确定删除收藏集“{name}”？\n只会删除收藏集，不会删除游戏；操作前会自动备份。",
            parent=self.root, icon="warning", default="no",
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        self._run_collection_action(
            "删除收藏集",
            lambda: collection_actions.delete_collection(
                self.ctx, name, restart=restart,
                log=lambda m: self.bus.put(("log", m)),
            ),
        )
        self._collection_name = ""

    def on_add_to_collection(self) -> None:
        if not self.ctx:
            return
        ids = sorted(self._collection_selected())
        if not ids:
            messagebox.showinfo("未选择游戏", "请先在表格中选择游戏。", parent=self.root)
            return
        try:
            live = collection_plan.read_live_collections(self.ctx)
        except Exception:
            live = []
        names = [c.name for c in live]
        prompt = "目标收藏集名称：\n已有：" + ("、".join(names[:12]) if names else "无")
        target = simpledialog.askstring("添加到收藏集", prompt, parent=self.root)
        if not target or not target.strip():
            return
        target = target.strip()
        restart = bool(self.settings.get("restart_steam", True))
        exists = any(c.name.casefold() == target.casefold() for c in live)
        if exists:
            self._run_collection_action(
                "添加到收藏集",
                lambda: collection_actions.add_games(
                    self.ctx, target, ids, restart=restart,
                    log=lambda m: self.bus.put(("log", m)),
                ),
            )
        else:
            self._run_collection_action(
                "新建收藏集并加入",
                lambda: collection_actions.create_collection(
                    self.ctx, target, ids, restart=restart,
                    log=lambda m: self.bus.put(("log", m)),
                ),
            )

    def on_remove_from_collection(self) -> None:
        if not self.ctx or not self._collection_name:
            messagebox.showinfo("未选择收藏集", "请先在左侧选择一个收藏集。", parent=self.root)
            return
        ids = sorted(self._collection_selected())
        if not ids:
            messagebox.showinfo("未选择游戏", "请先在表格中选择游戏。", parent=self.root)
            return
        name = self._collection_name
        restart = bool(self.settings.get("restart_steam", True))
        self._run_collection_action(
            "从收藏集移除",
            lambda: collection_actions.remove_games(
                self.ctx, name, ids, restart=restart,
                log=lambda m: self.bus.put(("log", m)),
            ),
        )

    def on_restore_backup(self) -> None:
        if not self.ctx:
            return
        chosen = filedialog.askopenfilename(
            title="选择备份中的 cloud-storage-namespace-1.json",
            filetypes=[("Steam 收藏集备份", "cloud-storage-namespace-1.json"), ("JSON", "*.json")],
            parent=self.root,
        )
        if not chosen:
            return
        backup_dir = Path(chosen).parent
        if not messagebox.askyesno(
            "还原收藏集备份",
            f"将从以下备份还原：\n{backup_dir}\n\n当前状态会先备份。确定继续？",
            parent=self.root, icon="warning", default="no",
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        self._run_collection_action(
            "还原收藏集备份",
            lambda: collection_actions.restore_backup(
                self.ctx, backup_dir, restart=restart,
                log=lambda m: self.bus.put(("log", m)),
            ),
        )

    def on_export_plan(self) -> None:
        if not self.ctx:
            return
        try:
            plan = collection_plan.build_plan(self.ctx)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        if not plan.collections:
            messagebox.showinfo("没有收藏集", "当前 Steam 中没有可导出的静态收藏集。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            title="导出分类方案", defaultextension=".zip",
            filetypes=[("Steam 统译分类方案", "*.zip")], parent=self.root,
            initialfile="Steam分类方案.zip",
        )
        if not path:
            return
        try:
            collection_plan.export_plan(plan, Path(path))
            self._append_log(f"分类方案已导出：{path}")
            messagebox.showinfo("导出完成", f"已导出 {len(plan.collections)} 个收藏集：\n{path}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def on_import_plan(self) -> None:
        if not self.ctx:
            return
        path = filedialog.askopenfilename(
            title="导入分类方案", filetypes=[("Steam 统译分类方案", "*.zip")], parent=self.root,
        )
        if not path:
            return
        try:
            plan = collection_plan.read_plan(Path(path))
            preview = collection_plan.match_plan(self.ctx, plan)
        except Exception as exc:
            messagebox.showerror("无法导入", str(exc), parent=self.root)
            return
        if not preview.collections:
            messagebox.showinfo("空方案", "方案中没有可用收藏集。", parent=self.root)
            return
        PlanImportDialog(self, preview, self._apply_plan_preview)

    def _apply_plan_preview(self, preview, restart: bool) -> None:
        if not self.ctx:
            return
        def work():
            return collection_plan.apply_preview(
                self.ctx, preview, restart=restart,
                log=lambda m: self.bus.put(("log", m)),
                progress=lambda m: self.bus.put(("progress", (0, 0, m))),
            )
        self.run_task("plan_apply", work)

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
        tk.Label(header, text="Steam 统译 v3.1.2", bg=PANEL, fg="#ffffff",
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
        body.pack(fill="both", expand=True, padx=12, pady=(8, 6))
        self._inspector_animator = Animator(self.root)
        self._log_animator = Animator(self.root)
        self._wipe_animator = Animator(self.root)
        self._slide_animator = Animator(self.root)
        self._slide_overlay = None
        self._slide_ticket = 0
        sidebar_surface = RoundedSurface(body, width=208, radius=14, padding=10)
        sidebar_surface.pack(side="left", fill="y", padx=(0, 8))
        sidebar_surface.pack_propagate(False)
        sidebar = sidebar_surface.inner
        tk.Label(sidebar, text="导航", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(8, 4))
        sb_wrap = tk.Frame(sidebar, bg=PANEL)
        sb_wrap.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 6))
        self.sidebar = SidebarList(sb_wrap, command=self._on_sidebar)
        self.sidebar.pack(fill="both", expand=True)

        self.inspector = RoundedSurface(body, width=320, radius=14, padding=10)
        self.inspector.pack(side="right", fill="y", padx=(8, 0))
        self.inspector.pack_propagate(False)
        self._build_inspector(self.inspector.inner)

        main_surface = RoundedSurface(body, radius=14, padding=10)
        main_surface.pack(side="left", fill="both", expand=True)
        main_surface.pack_propagate(False)
        main = main_surface.inner
        self.main_frame = main
        self.scene_overlay = tk.Canvas(main, bg="#101821", highlightthickness=0, bd=0)
        self.scene_overlay.place_forget()
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
        m.add_separator()
        m.add_command(label="设置", command=self.on_settings)
        m = add("帮助")
        m.add_command(label="使用说明", command=self._show_help)
        m.add_command(label="关于", command=self._show_about)

    def _build_tabs(self, parent):
        self.tabbar = TabBar(parent, [
            ("names", "翻译名称"),
            ("categories", "分类整理"),
            ("both", "全部"),
        ], self._set_workspace)
        self.tabbar.pack(side="left", fill="x", expand=True)
        ttk.Button(parent, text="参考源", style="Small.TButton",
                   command=self._toggle_inspector).pack(side="right", padx=(8, 0))

    def _build_action_bar(self, parent):
        self.action_frames = {}; self.buttons = {}
        def make(space, specs, rows=None):
            frame = tk.Frame(parent, bg=BG); self.action_frames[space] = frame
            groups = rows if rows is not None else [specs]
            for row_specs in groups:
                row = tk.Frame(frame, bg=BG)
                row.pack(side="top", fill="x", pady=(0, 4))
                for key, text, command, style_name in row_specs:
                    kind = "primary" if "Accent" in style_name or "Primary" in style_name else "secondary"
                    b = MotionButton(row, text, command, kind=kind)
                    b.pack(side="left", padx=(0, 6)); self.buttons[key] = b
        make("names", [
            ("auto_translate", "自动翻译", self.on_translate, "Primary.TButton"),
            ("apply_names", "应用全部名称", lambda: self.on_apply(False, "names"), ""),
            ("apply_sel_names", "应用选中名称", lambda: self.on_apply(True, "names"), ""),
        ])
        make("categories", [], rows=[
            [
                ("plan_import", "导入方案…", self.on_import_plan, "Primary.TButton"),
                ("plan_export", "导出方案…", self.on_export_plan, ""),
                ("restore_backup", "还原备份…", self.on_restore_backup, ""),
            ],
            [
                ("new_collection", "新建收藏集", self.on_new_collection, ""),
                ("rename_collection", "重命名", self.on_rename_collection, ""),
                ("delete_collection", "删除", self.on_delete_collection, ""),
                ("add_to_collection", "添加到收藏集…", self.on_add_to_collection, ""),
                ("remove_from_collection", "从收藏集移除", self.on_remove_from_collection, ""),
            ],
        ])
        make("both", [
            ("apply_all", "应用全部名称", lambda: self.on_apply(False, "names"), "Primary.TButton"),
            ("apply_sel_all", "应用选中名称", lambda: self.on_apply(True, "names"), ""),
        ])
        right = tk.Frame(parent, bg=BG); right.pack(side="right")
        tools = AnimatedMenuButton(right, "⚙ 工具", [
            ("导出清单", None, self.on_export),
            ("导出备份", None, self.on_export_backup),
            ("导入备份", None, self.on_import_backup),
            None,
            ("补全原名", None, lambda: self._start_originals_fetch(force=True)),
            ("跳过未知原名", None, self._skip_unknown_originals),
            ("补全简介", None, self.on_fetch_descriptions),
            None,
            ("设置", None, self.on_settings),
        ])
        tools.pack(side="left", padx=(0, 4))
        danger = AnimatedMenuButton(right, "⚠ 复原", [
            ("只复原游戏名称…", None, lambda: self.on_undo_all("names")),
        ])
        danger.pack(side="left")

    def _build_inspector(self, parent):
        head = tk.Frame(parent, bg=PANEL); head.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(head, text="参考源", bg=PANEL, fg="#ffffff",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(head, text="收起", style="Small.TButton",
                   command=self._toggle_inspector).pack(side="right")
        self.inspector_notebook = ttk.Notebook(parent)
        self.inspector_notebook.pack(fill="both", expand=True, padx=6, pady=4)
        name_tab = tk.Frame(self.inspector_notebook, bg=BG)
        self.inspector_notebook.add(name_tab, text="译名参考")
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
        self._inspector_candidates = []

    def _set_workspace(self, workspace: str, initial: bool = False) -> None:
        if workspace not in ("names", "categories", "both"):
            return
        # Snapshot transitions are disabled: Windows DPI scaling makes a
        # cropped screenshot visually unreliable.  The tab capsule still
        # animates, while content changes atomically and without a flash.
        self._cleanup_slide_overlay()
        self._slide_ticket += 1
        self._apply_workspace(workspace)

    def _cleanup_slide_overlay(self) -> None:
        overlay = getattr(self, "_slide_overlay", None)
        self._slide_overlay = None
        if overlay is not None:
            try:
                overlay.place_forget()
                overlay.destroy()
            except Exception:
                pass

    def _fit_preview_image(self, image, width: int, height: int):
        if image.size == (width, height):
            return image
        try:
            from PIL import Image
            return image.resize((width, height), Image.LANCZOS)
        except Exception:
            return image

    def _slide_workspace(self, workspace: str) -> None:
        main = getattr(self, "main_frame", None)
        if main is None:
            self._apply_workspace(workspace)
            return
        try:
            if not main.winfo_viewable() or main.winfo_width() < 150:
                self._apply_workspace(workspace)
                return
            self._cleanup_slide_overlay()
            self._slide_animator.cancel()
            self._slide_ticket += 1
            ticket = self._slide_ticket
            from PIL import ImageGrab, ImageTk
            main.update_idletasks()
            x = main.winfo_rootx()
            y = main.winfo_rooty()
            w = max(150, main.winfo_width())
            h = max(100, main.winfo_height())
            image = ImageGrab.grab((x, y, x + w, y + h))
            image = self._fit_preview_image(image, w, h)
            photo = ImageTk.PhotoImage(image)
            overlay = tk.Canvas(main, bg=BG, highlightthickness=0, bd=0)
            overlay.place(x=0, y=0, width=w, height=h)
            overlay.create_image(0, 0, image=photo, anchor="nw")
            overlay.photo = photo
            overlay.tk.call("raise", overlay._w)
            self._slide_overlay = overlay
            self._apply_workspace(workspace)

            def move(t):
                if ticket != self._slide_ticket or self._slide_overlay is not overlay:
                    return
                overlay.place(x=int(-w * t), y=0, width=w, height=h)

            def done():
                if ticket != self._slide_ticket:
                    return
                self._cleanup_slide_overlay()
                self._slide_animator = Animator(self.root)

            self._slide_animator.animate(260, move, done)
        except Exception:
            self._cleanup_slide_overlay()
            self._apply_workspace(workspace)

    def _apply_workspace(self, workspace: str) -> None:
        self.workspace = workspace
        for key, frame in getattr(self, "action_frames", {}).items():
            if key == workspace:
                if not frame.winfo_ismapped():
                    frame.pack(side="left")
            else:
                frame.pack_forget()
        try:
            self.tabbar.set_active(workspace)
        except Exception:
            pass
        if hasattr(self, "table"):
            self.table.set_profile(workspace)
        self._update_action_state()
        if getattr(self, "ctx", None):
            self.refresh_table()

    def _wipe_to(self, workspace: str) -> None:
        if not hasattr(self, "scene_overlay") or not hasattr(self, "main_frame"):
            self._apply_workspace(workspace)
            return
        main = self.main_frame
        width = max(200, main.winfo_width() or 900)
        canvas = self.scene_overlay
        canvas.place(x=width, y=0, width=width, relheight=1)
        canvas.tk.call("raise", canvas._w)
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, main.winfo_height() or 600,
                                fill="#101821", outline="")
        canvas.create_rectangle(2, 0, 5, main.winfo_height() or 600,
                                fill="#2f80b7", outline="")
        self._wipe_animator.cancel()

        def cover(t):
            canvas.place(x=int(width * (1 - t)), y=0, width=width, relheight=1)

        def covered():
            try:
                self._apply_workspace(workspace)
            except Exception:
                canvas.place_forget()
                raise

            def reveal(t):
                canvas.place(x=int(-width * t), y=0, width=width, relheight=1)

            def done():
                canvas.place_forget()

            self._wipe_animator.animate(210, reveal, done)

        self._wipe_animator.animate(150, cover, covered)

    def _toggle_inspector(self) -> None:
        if not hasattr(self, "inspector"):
            return
        if self.inspector_visible:
            start = self.inspector.winfo_width() or 320
            def shrink(t):
                try:
                    self.inspector.configure(width=max(0, int(320 * (1 - t))))
                except Exception:
                    pass
            def done():
                self.inspector.pack_forget()
                self.inspector_visible = False
            self._inspector_animator.animate(180, shrink, done)
        else:
            self.inspector.configure(width=0)
            self.inspector.pack(side="right", fill="y", padx=(8, 0), before=None)
            self.inspector_visible = True
            def grow(t):
                try:
                    self.inspector.configure(width=int(320 * t))
                except Exception:
                    pass
            self._inspector_animator.animate(220, grow)

    def _update_action_state(self) -> None:
        if not hasattr(self, "buttons"):
            return
        selected = bool(self.table.active_ids()) if hasattr(self, "table") else False
        any_names = any(str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
                        for g in self.ctx.games) if self.ctx else False
        has_collection = bool(getattr(self, "_collection_name", ""))
        for key, button in self.buttons.items():
            enabled = True
            if key.startswith("apply_sel") or key in ("batch", "add_to_collection"):
                enabled = selected
            elif key in ("rename_collection", "delete_collection", "remove_from_collection"):
                enabled = has_collection
            elif key == "undo_batch":
                enabled = self.last_batch_snapshot is not None
            elif key == "apply_names":
                enabled = any_names
            elif key == "apply_all":
                enabled = any_names
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
        messagebox.showinfo("关于", "Steam 统译 v3.1.2\n游戏名汉化 / 多标签分类 / 缓存级复原",
                            parent=self.root)


def main() -> None:
    from . import paths

    paths.ensure_data()
    root = tk.Tk()
    SteamZhApp(root)
    root.mainloop()

