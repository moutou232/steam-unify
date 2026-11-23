"""Dialogs for v3 collection plans."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import ui_widgets
from . import ui_theme as T
from .ui_components import MotionButton, ToggleSwitch

BG = ui_widgets.BG
PANEL = ui_widgets.PANEL
PANEL2 = ui_widgets.PANEL2
FG = ui_widgets.FG
MUTED = ui_widgets.MUTED
ACCENT = ui_widgets.ACCENT


class PlanImportDialog(tk.Toplevel):
    def __init__(self, app, preview, on_confirm):
        super().__init__(app.root)
        self.app = app
        self.preview = preview
        self.on_confirm = on_confirm
        self.title("导入分类方案")
        self.configure(bg=BG)
        self.geometry("760x560")
        self.minsize(680, 480)
        self.transient(app.root)
        self.grab_set()
        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text=f"分类方案：{preview.plan.name}", bg=PANEL, fg="#ffffff",
                 font=("Microsoft YaHei UI", 13, "bold"), anchor="w").pack(fill="x", padx=14, pady=10)
        summary = (
            f"匹配游戏 {preview.matched_games} · 未匹配 {preview.unmatched_games} · "
            f"同名冲突 {preview.conflicts} · 本地库 {preview.local_only}"
        )
        tk.Label(self, text=summary, bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(self, text="双击集合可切换：合并 / 替换 / 跳过；同名集合默认合并。",
                 bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=14, pady=(0, 6))
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=4)
        style = ttk.Style(self)
        style.configure("Plan.Treeview", background=T.SURFACE, fieldbackground=T.SURFACE,
                        foreground=T.TEXT, rowheight=32, borderwidth=0)
        style.configure("Plan.Treeview.Heading", background=T.SURFACE_2,
                        foreground=T.TEXT, relief="flat", borderwidth=0)
        style.map("Plan.Treeview",
                  background=[("selected", T.SELECTION)],
                  foreground=[("selected", "#ffffff")])
        style.map("Plan.Treeview.Heading",
                  background=[("active", T.SURFACE_3)])
        self.tree = ttk.Treeview(body, columns=("local", "missing", "action"),
                                 show="tree headings", selectmode="browse",
                                 style="Plan.Treeview")
        self.tree.heading("#0", text="收藏集")
        self.tree.heading("local", text="匹配")
        self.tree.heading("missing", text="未匹配")
        self.tree.heading("action", text="操作")
        self.tree.column("#0", width=320)
        self.tree.column("local", width=100, anchor="center")
        self.tree.column("missing", width=100, anchor="center")
        self.tree.column("action", width=100, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._cycle)
        self._render()
        actions = tk.Frame(self, bg=BG)
        actions.pack(fill="x", padx=14, pady=4)
        ttk.Button(actions, text="全部合并", command=lambda: self._all("merge")).pack(side="left", padx=3)
        ttk.Button(actions, text="全部替换", command=lambda: self._all("replace")).pack(side="left", padx=3)
        ttk.Button(actions, text="全部跳过", command=lambda: self._all("skip")).pack(side="left", padx=3)
        self.restart_var = tk.BooleanVar(value=True)
        ToggleSwitch(actions, self.restart_var).pack(side="right", padx=(6, 4))
        tk.Label(actions, text="应用后重启 Steam", bg=BG, fg=FG).pack(side="right")
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(4, 12))
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="应用方案到 Steam", style="Accent.TButton",
                   command=self._confirm).pack(side="right", padx=4)

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for idx, item in enumerate(self.preview.collections):
            action = {"merge": "合并", "replace": "替换", "skip": "跳过"}.get(item.action, item.action)
            self.tree.insert("", "end", iid=str(idx), text=item.plan.name,
                             values=(len(item.appids), len(item.unmatched), action))
        if self.preview.collections:
            self.tree.selection_set("0")

    def _cycle(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        item = self.preview.collections[idx]
        order = ["merge", "replace", "skip"]
        item.action = order[(order.index(item.action) + 1) % len(order)] if item.action in order else "merge"
        self._render()
        self.tree.selection_set(str(idx))

    def _all(self, action):
        for item in self.preview.collections:
            item.action = action
        self._render()

    def _confirm(self):
        self.on_confirm(self.preview, bool(self.restart_var.get()))
        self.destroy()
