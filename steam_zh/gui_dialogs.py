"""Dialogs for the Steam Chinese-name helper.

Kept separate from the main window so `gui.py` stays focused on layout and
task orchestration.  All category edits go through `category_state` so the
three-label cap is enforced in exactly one place.
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import (
    apply,
    category_model,
    category_state,
    providers,
    scoring,
    service,
    ui_widgets,
)
from .state import State
from .translate import probe

APP_TITLE = "Steam 统译 v2.0.4 (Steam Unify)"
BG = ui_widgets.BG
PANEL = ui_widgets.PANEL
PANEL2 = ui_widgets.PANEL2
FG = ui_widgets.FG
MUTED = ui_widgets.MUTED
ACCENT = ui_widgets.ACCENT
GREEN = "#a4d007"
ORANGE = "#e5a00d"
RED = "#d94126"
CATEGORY_COLORS = ui_widgets.CATEGORY_COLORS

SOURCE_LABEL = {
    "official": "官方",
    "dictionary": "词典",
    "steam": "商店",
    "machine": "机翻",
    "manual": "手动",
    "already": "已中文",
    "original": "原文",
    "": "待翻译",
}

STATUS_FILTERS = [
    "全部", "已翻译", "待翻译", "待复原", "已复原", "已跳过", "未分类", "机翻", "官方/词典",
    "手动", "已应用", "已安装", "已拥有",
]


class NameEditDialog(tk.Toplevel):
    def __init__(self, app, game, record):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.record = record
        self.title(f"编辑 - {record.get('orig_name') or game.name}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        pad = {"padx": 12, "pady": 6}
        tk.Label(
            self, text=record.get("orig_name") or game.name, bg=BG, fg=FG,
            font=("Microsoft YaHei UI", 11, "bold"), wraplength=520, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        tk.Label(
            self, text=f"AppID {game.appid}  ·  类型 {game.type}", bg=BG, fg=MUTED
        ).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        tk.Label(self, text="中文名", bg=BG, fg=FG).grid(row=2, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar(value=str(record.get("zh_name") or ""))
        entry = tk.Entry(
            self, textvariable=self.name_var, width=44, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        )
        entry.grid(row=2, column=1, sticky="we", **pad)
        entry.focus_set()
        self.lock_var = tk.BooleanVar(value=bool(record.get("locked")))
        tk.Checkbutton(
            self, text="锁定中文名（自动翻译不覆盖）", variable=self.lock_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        src = str(record.get("source") or "")
        info = f"来源：{SOURCE_LABEL.get(src, src or '待翻译')}"
        if record.get("detail"):
            info += f"  ·  {record['detail']}"
        tk.Label(self, text=info, bg=BG, fg=MUTED, wraplength=520, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", **pad
        )
        if record.get("error"):
            tk.Label(self, text=record["error"], bg=BG, fg=ORANGE, wraplength=520).grid(
                row=5, column=0, columnspan=2, sticky="w", **pad
            )
        btns = tk.Frame(self, bg=BG)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 14), padx=12)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self.save).pack(
            side="left", padx=4
        )

    def save(self) -> None:
        rec = self.record
        name = self.name_var.get().strip()
        old_name = str(rec.get("zh_name") or "")
        rec["zh_name"] = name
        if name and name != old_name:
            rec["source"] = "manual"
            rec["detail"] = "手动修改"
            rec["translated_at"] = int(time.time())
        elif not name:
            rec["source"] = ""
            rec["detail"] = ""
        rec["locked"] = bool(self.lock_var.get())
        rec["error"] = ""
        if self.app.ctx:
            self.app.ctx.state.save()
        self.app.refresh_table()
        self.app.log(f"已修改 {self.game.name} → {name or '(空)'}")
        self.destroy()


class CategoryPickerDialog(tk.Toplevel):
    def __init__(self, app, game, record):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.record = record
        self.categories = category_model.category_names()
        self.selected = category_state.get_categories(record)
        self.vars: dict[str, tk.BooleanVar] = {}
        self.check_buttons: list[tuple[str, tk.Checkbutton]] = []
        self.title(f"分类 - {record.get('orig_name') or game.name}")
        self.configure(bg=BG)
        self.geometry("660x700")
        self.minsize(560, 560)
        self.transient(app.root)
        self.grab_set()

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text=record.get("orig_name") or game.name, bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"), anchor="w",
        ).pack(side="left", padx=14, pady=10)
        tk.Label(
            head, text="最多 3 个分类，第一个为主分类", bg=PANEL, fg=MUTED,
        ).pack(side="right", padx=14)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(body, text="已选（可上下调整顺序）", bg=BG, fg=FG).pack(anchor="w")
        sel_row = tk.Frame(body, bg=BG)
        sel_row.pack(fill="x", pady=(4, 8))
        self.sel_list = tk.Listbox(
            sel_row, height=4, bg=PANEL2, fg=FG, relief="flat",
            selectbackground="#2f5c7a", activestyle="none", exportselection=False,
        )
        self.sel_list.pack(side="left", fill="x", expand=True)
        sel_btns = tk.Frame(sel_row, bg=BG)
        sel_btns.pack(side="left", padx=(8, 0))
        ttk.Button(sel_btns, text="↑", width=3, command=lambda: self._move(-1)).pack(pady=1)
        ttk.Button(sel_btns, text="↓", width=3, command=lambda: self._move(1)).pack(pady=1)
        ttk.Button(sel_btns, text="移除", width=5, command=self._remove_sel).pack(pady=1)

        tk.Label(body, text="搜索分类", bg=BG, fg=MUTED).pack(anchor="w", pady=(4, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_checks())
        tk.Entry(
            body, textvariable=self.search_var, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(fill="x", pady=(3, 8), ipady=4)

        checks = tk.Frame(body, bg=BG)
        checks.pack(fill="both", expand=True)
        for i, cat in enumerate(self.categories):
            var = tk.BooleanVar(value=cat in self.selected)
            self.vars[cat] = var
            cb = tk.Checkbutton(
                checks, text=cat, variable=var, command=lambda c=cat: self._on_check(c),
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, anchor="w",
            )
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=4, pady=2)
            self.check_buttons.append((cat, cb))

        rec_row = tk.Frame(body, bg=BG)
        rec_row.pack(fill="x", pady=(8, 4))
        self.rec_var = tk.StringVar(value="正在计算推荐 ...")
        tk.Label(
            rec_row, textvariable=self.rec_var, bg=BG, fg=ACCENT,
            wraplength=520, justify="left",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(rec_row, text="填入推荐", command=self._use_recommendation).pack(side="right")

        self.lock_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            body, text="锁定分类（自动分类不覆盖）", variable=self.lock_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self.save).pack(
            side="right", padx=4
        )
        self._refresh_sel()
        self._recommend()

    def _refresh_sel(self) -> None:
        self.sel_list.delete(0, "end")
        for i, cat in enumerate(self.selected[:3]):
            self.sel_list.insert("end", f"{i + 1}. {cat}")
        for cat, var in self.vars.items():
            var.set(cat in self.selected)

    def _on_check(self, cat: str) -> None:
        if self.vars[cat].get():
            if cat not in self.selected:
                if len(self.selected) >= 3:
                    self.vars[cat].set(False)
                    messagebox.showinfo("最多 3 个", "每个游戏最多 3 个分类。", parent=self)
                    return
                self.selected.append(cat)
        else:
            if cat in self.selected:
                self.selected.remove(cat)
        self._refresh_sel()

    def _remove_sel(self) -> None:
        sel = self.sel_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.selected):
            self.selected.pop(idx)
            self._refresh_sel()

    def _move(self, delta: int) -> None:
        sel = self.sel_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if 0 <= j < len(self.selected):
            self.selected[i], self.selected[j] = self.selected[j], self.selected[i]
            self._refresh_sel()
            self.sel_list.selection_set(j)

    def _filter_checks(self) -> None:
        q = self.search_var.get().strip().lower()
        for cat, cb in self.check_buttons:
            if not q or q in cat.lower():
                cb.grid()
            else:
                cb.grid_remove()

    def _recommend(self) -> None:
        try:
            res = scoring.classify(
                self.game,
                description=getattr(self.game, "description", "") or "",
                extra_names=[str(self.record.get("orig_name") or "")],
            )
        except Exception as exc:
            self.rec_var.set(f"推荐失败：{exc}")
            return
        self.recommendation = res
        bits = [f"{cat}({res.scores.get(cat, 0):.1f})" for cat in res.categories]
        reasons = "；".join(
            f"{c}: {'/'.join(r[:2])}" for c, r in list(res.reasons.items())[:3]
        )
        self.rec_var.set("推荐：" + "、".join(bits) + ("\n" + reasons if reasons else ""))

    def _use_recommendation(self) -> None:
        res = getattr(self, "recommendation", None)
        if not res:
            return
        self.selected = list(res.categories[:3])
        self._refresh_sel()

    def save(self) -> None:
        cats = category_state.normalize_categories(self.selected)
        category_state.set_categories(
            self.record, cats, source="manual", locked=bool(self.lock_var.get())
        )
        if self.app.ctx:
            self.app.ctx.state.save()
        self.app.refresh_table()
        self.app.refresh_sidebar()
        self.app.log(f"已设置 {self.game.name} 分类：{' / '.join(cats) or '未分类'}")
        self.destroy()


class BatchCategoryDialog(tk.Toplevel):
    def __init__(self, app, appids: set[int], mode: str = "append"):
        super().__init__(app.root)
        self.app = app
        self.appids = sorted(appids)
        self.categories = category_model.category_names()
        self.targets: list[str] = []
        self.vars: dict[str, tk.BooleanVar] = {}
        self.title(f"批量分类 - 已选 {len(self.appids)} 个游戏")
        self.configure(bg=BG)
        self.geometry("700x650")
        self.minsize(560, 500)
        self.transient(app.root)
        self.grab_set()

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text=f"批量分类 · {len(self.appids)} 个游戏", bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=14, pady=10)
        tk.Label(head, text="最多 3 个目标分类", bg=PANEL, fg=MUTED).pack(
            side="right", padx=14
        )

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(mode_row, text="模式", bg=BG, fg=FG).pack(side="left")
        self.mode_var = tk.StringVar(value=mode)
        for value, label in (("append", "追加"), ("replace", "替换"), ("remove", "移除")):
            tk.Radiobutton(
                mode_row, text=label, value=value, variable=self.mode_var,
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, command=self._refresh,
            ).pack(side="left", padx=(10, 2))

        tk.Label(body, text="目标分类", bg=BG, fg=FG).pack(anchor="w")
        checks = tk.Frame(body, bg=BG)
        checks.pack(fill="x", pady=(4, 8))
        for i, cat in enumerate(self.categories):
            var = tk.BooleanVar(value=False)
            self.vars[cat] = var
            tk.Checkbutton(
                checks, text=cat, variable=var, command=lambda c=cat: self._on_check(c),
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, anchor="w",
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=3, pady=2)

        tk.Label(body, text="预览", bg=BG, fg=FG).pack(anchor="w", pady=(6, 0))
        self.preview_var = tk.StringVar(value="")
        tk.Label(
            body, textvariable=self.preview_var, bg=PANEL, fg=MUTED,
            justify="left", anchor="nw", wraplength=640, padx=10, pady=8,
        ).pack(fill="x", pady=4)

        self.trunc_var = tk.StringVar(value="")
        tk.Label(
            body, textvariable=self.trunc_var, bg=BG, fg=ORANGE,
            justify="left", anchor="nw", wraplength=640,
        ).pack(fill="x")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="应用批量分类", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=4)
        self._refresh()

    def _on_check(self, cat: str) -> None:
        if self.vars[cat].get():
            if len(self.targets) >= 3:
                self.vars[cat].set(False)
                messagebox.showinfo("最多 3 个", "目标分类最多选择 3 个。", parent=self)
                return
            if cat not in self.targets:
                self.targets.append(cat)
        else:
            if cat in self.targets:
                self.targets.remove(cat)
        self._refresh()

    def _refresh(self) -> None:
        mode = self.mode_var.get()
        if mode != "remove" and not self.targets:
            self.preview_var.set("请选择 1-3 个目标分类。")
            self.trunc_var.set("")
            return
        try:
            preview = service.batch_preview(self.app.ctx, self.appids, self.targets, mode)
        except Exception as exc:
            self.preview_var.set(f"无法预览：{exc}")
            self.trunc_var.set("")
            return
        mode_label = {
            "append": "追加（保留原分类）",
            "replace": "替换（只保留目标）",
            "remove": "移除目标分类",
        }.get(mode, mode)
        self.preview_var.set(
            f"选中 {len(self.appids)} 个；将修改 {len(preview.changed)} 个；"
            f"超限截断 {len(preview.truncated)} 个；其中原本锁定 {len(preview.locked)} 个。\n"
            f"模式：{mode_label}"
        )
        if preview.truncated:
            lines = [
                f"{self.app.games_by_id[p.appid].name}: 丢弃 {' / '.join(p.dropped)}"
                for p in preview.truncated[:8]
            ]
            self.trunc_var.set("将丢弃的分类：\n" + "\n".join(lines))
        else:
            self.trunc_var.set("")

    def apply(self) -> None:
        mode = self.mode_var.get()
        if mode != "remove" and not self.targets:
            messagebox.showinfo("提示", "请先选择目标分类。", parent=self)
            return
        try:
            preview = service.batch_preview(self.app.ctx, self.appids, self.targets, mode)
        except Exception as exc:
            messagebox.showerror("批量分类", str(exc), parent=self)
            return
        if not preview.changed:
            messagebox.showinfo("提示", "没有需要修改的游戏。", parent=self)
            return
        if not messagebox.askyesno(
            "确认批量分类",
            f"将修改 {len(preview.changed)} 个游戏（共选中 {len(self.appids)} 个）。\n"
            + (f"有 {len(preview.truncated)} 个游戏超过 3 个分类，将截断。\n" if preview.truncated else "")
            + "继续？",
            parent=self,
        ):
            return
        snapshot = service.batch_apply(self.app.ctx, preview)
        self.app.last_batch_snapshot = snapshot
        self.app._update_undo_button()
        self.app.refresh_table()
        self.app.refresh_sidebar()
        self.app.log(
            f"批量分类完成：修改 {len(preview.changed)} 个，截断 {len(preview.truncated)} 个"
        )
        self.destroy()


class SourcePriorityDialog(tk.Toplevel):
    def __init__(self, app, order: list[str], on_save):
        super().__init__(app.root)
        self.app = app
        self.on_save = on_save
        self.order = providers.normalize_priority_order(order)
        self.title("参考源优先级")
        self.configure(bg=BG)
        self.geometry("560x500")
        self.minsize(500, 420)
        self.transient(app.root)
        self.grab_set()
        tk.Label(
            self, text="手动选择（始终最高，不可调整）", bg=PANEL, fg=GREEN,
            anchor="w", padx=14, pady=8,
        ).pack(fill="x")
        tk.Label(
            self, text="靠前的来源有结果时优先使用；没有结果时自动使用下一个。",
            bg=BG, fg=MUTED, anchor="w", padx=14, pady=4,
        ).pack(fill="x")
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=4)
        self.lb = tk.Listbox(
            body, bg=PANEL2, fg=FG, selectbackground="#2a475e",
            selectforeground="#ffffff", relief="flat", activestyle="none",
            font=("Microsoft YaHei UI", 10),
        )
        self.lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._render()

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=14, pady=4)
        ttk.Button(buttons, text="上移", command=lambda: self._move(-1)).pack(side="left", padx=3)
        ttk.Button(buttons, text="下移", command=lambda: self._move(1)).pack(side="left", padx=3)
        ttk.Button(buttons, text="恢复默认", command=self._reset).pack(side="left", padx=3)
        self.reselect_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self, text="保存后按新顺序重选已有自动译名（不自动写入 Steam）",
            variable=self.reselect_var, bg=BG, fg=FG, selectcolor=PANEL2,
            activebackground=BG, activeforeground=FG, anchor="w",
        ).pack(fill="x", padx=14, pady=4)
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(4, 12))
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="right", padx=4)

    def _render(self):
        self.lb.delete(0, "end")
        for i, source in enumerate(self.order, 1):
            self.lb.insert("end", f"{i}. {providers.LABELS.get(source, source)}")
        if self.lb.size():
            self.lb.selection_set(0)

    def _move(self, delta: int):
        sel = self.lb.curselection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if j < 0 or j >= len(self.order):
            return
        self.order[i], self.order[j] = self.order[j], self.order[i]
        self._render()
        self.lb.selection_set(j)
        self.lb.see(j)

    def _reset(self):
        self.order = list(providers.DEFAULT_SOURCE_ORDER)
        self._render()

    def _save(self):
        self.on_save(list(self.order), bool(self.reselect_var.get()))
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("设置")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        s = app.settings
        pad = {"padx": 10, "pady": 4}
        self.vars: dict[str, tk.Variable] = {}
        rows = [
            ("steam_root", "Steam 目录（留空自动）", str(s.get("steam_root") or "")),
            ("account", "账号目录（留空自动）", str(s.get("account") or "")),
            ("proxy", "代理（如 http://127.0.0.1:7890，留空直连）", str(s.get("proxy") or "")),
        ]
        r = 0
        for key, label, value in rows:
            tk.Label(self, text=label, bg=BG, fg=FG).grid(row=r, column=0, sticky="w", **pad)
            v = tk.StringVar(value=value)
            self.vars[key] = v
            tk.Entry(
                self, textvariable=v, width=46, bg=PANEL2, fg=FG,
                insertbackground=FG, relief="flat",
            ).grid(row=r, column=1, sticky="we", **pad)
            r += 1
        for key in ("include_software", "all_known", "use_network", "auto_translate_new",
                    "restart_steam", "lock_appinfo"):
            self.vars[key] = tk.BooleanVar(value=bool(s.get(key)))
        checks = [
            ("include_software", "包含软件/工具（不只是游戏）"),
            ("all_known", "显示 appinfo 中所有已知游戏（数量很多）"),
            ("auto_translate_new", "启动扫描时自动联网翻译新增/待翻译游戏（并自动分类）"),
            ("use_network", "允许联网翻译 / 补全简介"),
            ("restart_steam", "应用后自动启动 Steam"),
            ("lock_appinfo", "应用后把 appinfo.vdf 设为只读（仅旧版单文件缓存）"),
        ]
        for key, label in checks:
            tk.Checkbutton(
                self, text=label, variable=self.vars[key], bg=BG, fg=FG,
                selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
            ).grid(row=r, column=0, columnspan=2, sticky="w", **pad)
            r += 1
        self._source_priority = providers.normalize_priority_order(
            s.get("source_priority")
        )
        self._reselect_priority = False
        tk.Label(self, text="参考源优先级", bg=BG, fg=FG).grid(
            row=r, column=0, sticky="w", **pad
        )
        priority_frame = tk.Frame(self, bg=BG)
        priority_frame.grid(row=r, column=1, sticky="we", **pad)
        self.priority_summary = tk.StringVar()
        tk.Label(priority_frame, textvariable=self.priority_summary, bg=BG, fg=MUTED,
                 anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Button(priority_frame, text="调整…", style="Small.TButton",
                   command=self.open_source_priority).pack(side="right")
        self._update_priority_summary()
        r += 1
        tk.Label(self, text="备份保留数量", bg=BG, fg=FG).grid(row=r, column=0, sticky="w", **pad)
        self.vars["backup_keep"] = tk.IntVar(value=int(s.get("backup_keep", 10)))
        tk.Spinbox(
            self, from_=1, to=50, textvariable=self.vars["backup_keep"], width=6,
            bg=PANEL2, fg=FG, relief="flat",
        ).grid(row=r, column=1, sticky="w", **pad)
        r += 1
        tk.Label(self, text="启动窗口", bg=BG, fg=FG).grid(
            row=r, column=0, sticky="nw", **pad
        )
        self.vars["window_mode"] = tk.StringVar(
            value=str(s.get("window_mode") or "maximized")
        )
        win_frame = tk.Frame(self, bg=BG)
        win_frame.grid(row=r, column=1, sticky="w", **pad)
        for value, label in (
            ("maximized", "最大化（默认，保留标题栏）"),
            ("fullscreen", "无边框全屏"),
            ("windowed", "窗口化（记住大小）"),
        ):
            tk.Radiobutton(
                win_frame, text=label, value=value, variable=self.vars["window_mode"],
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG,
            ).pack(anchor="w")
        r += 1
        btns = tk.Frame(self, bg=BG)
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(10, 12), padx=10)
        ttk.Button(btns, text="测试联网翻译", command=self.test_network).pack(side="left", padx=4)
        ttk.Button(btns, text="打开备份目录", command=self.open_backups).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="保存并重载", style="Accent.TButton", command=self.save).pack(
            side="left", padx=4
        )

    def _update_priority_summary(self) -> None:
        labels = [providers.LABELS.get(s, s) for s in self._source_priority[:4]]
        self.priority_summary.set(" > ".join(labels) + (" > …" if len(self._source_priority) > 4 else ""))

    def open_source_priority(self) -> None:
        SourcePriorityDialog(self.app, self._source_priority, self._apply_priority)

    def _apply_priority(self, order: list[str], reselect: bool) -> None:
        self._source_priority = providers.normalize_priority_order(order)
        self._reselect_priority = reselect
        self._update_priority_summary()

    def open_backups(self) -> None:
        apply.BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(apply.BACKUP_ROOT))
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self)

    def test_network(self) -> None:
        proxy = str(self.vars["proxy"].get() or "").strip() or None
        self.app.log("正在测试翻译源（可能需要几秒）...")

        def work():
            res = probe(proxy)
            self.app.bus.put((
                "log",
                f"联网测试：Steam 商店={'可用' if res['steam'] else '不可用'}，"
                f"MyMemory={'可用' if res['mymemory'] else '不可用'}",
            ))

        threading.Thread(target=work, daemon=True).start()

    def save(self) -> None:
        new = {
            "steam_root": str(self.vars["steam_root"].get()).strip(),
            "account": str(self.vars["account"].get()).strip(),
            "proxy": str(self.vars["proxy"].get()).strip(),
            "include_software": bool(self.vars["include_software"].get()),
            "all_known": bool(self.vars["all_known"].get()),
            "auto_translate_new": bool(self.vars["auto_translate_new"].get()),
            "use_network": bool(self.vars["use_network"].get()),
            "restart_steam": bool(self.vars["restart_steam"].get()),
            "lock_appinfo": bool(self.vars["lock_appinfo"].get()),
            "backup_keep": int(self.vars["backup_keep"].get() or 10),
            "window_mode": str(self.vars["window_mode"].get() or "maximized"),
            "source_priority": list(self._source_priority),
        }
        old = dict(self.app.settings)
        self.app.settings.update(new)
        if self.app.ctx:
            self.app.ctx.state.save_settings(self.app.settings)
        else:
            State.load().save_settings(self.app.settings)
        reselected = 0
        if self._reselect_priority and self.app.ctx:
            reselected = providers.reselect_cached_candidates(
                self.app.ctx.state, self._source_priority
            )
            self.app.refresh_table()
            self.app.refresh_sidebar()
            self.app.refresh_inspector()
            self.app.log(
                f"已按新顺序重选 {reselected} 个自动译名；"
                "点“应用名称”后才会写入 Steam"
            )
        self.destroy()
        need_reload = any(
            old.get(k) != new.get(k)
            for k in ("steam_root", "account", "include_software", "all_known")
        )
        if need_reload:
            self.app.reload()
        else:
            self.app.update_status()
        self.app.apply_window_mode()
