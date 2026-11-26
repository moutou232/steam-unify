"""Entry point for the Steam Chinese-name helper.

Run without arguments for the GUI. Useful CLI options:

    python run.py --scan
    python run.py --autofix
    python run.py --report path.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from steam_zh import apply, category_state, guard, service, steamenv
from steam_zh.state import State


def cmd_scan() -> int:
    settings = State.load_settings()
    ctx = service.load_context(settings)
    for warning in getattr(ctx, "warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    for g in ctx.games:
        rec = ctx.state.get_app(g.appid)
        print(
            f"{g.appid}\t{g.name}\t{rec.get('zh_name','')}\t"
            f"{rec.get('source','')}\t"
            f"{' / '.join(category_state.get_categories(rec))}"
        )
    c = service.counts(ctx)
    print(f"# total={c['total']} missing={c['missing']} applied={c['applied']}", file=sys.stderr)
    return 0


def cmd_autofix() -> int:
    settings = State.load_settings()
    ctx = service.load_context(settings)
    res = service.do_autofix(
        ctx,
        restart=bool(settings.get("restart_steam", True)),
        lock_appinfo=bool(settings.get("lock_appinfo")),
        log=lambda m: print(m, file=sys.stderr),
    )
    print(f"applied={res.names_applied} collections={res.collections_changed}", file=sys.stderr)
    return 0


def cmd_report(path: str) -> int:
    settings = State.load_settings()
    ctx = service.load_context(settings)
    service.export_report(ctx, Path(path))
    print(f"written {path}", file=sys.stderr)
    return 0


def cmd_drift() -> int:
    settings = State.load_settings()
    ctx = service.load_context(settings)
    report = guard.scan_drift(ctx.env, ctx.state)
    print(report.summary())
    if report.drifted:
        for appid in sorted(report.drifted, key=lambda a: str(a)):
            rec = ctx.state.get_app(appid)
            zh = str(rec.get("zh_name") or "")
            mark = " [刚被 Steam 刷新]" if appid in report.refreshed_drifted else ""
            print(f"{appid}\t{zh}\t{report.drifted[appid]}{mark}")
    if report.missing:
        print(f"# missing={len(report.missing)}", file=sys.stderr)
    locked, total = guard.lock_state(ctx.env)
    if locked:
        print(f"# locked {locked}/{total} appinfo files", file=sys.stderr)
    return 0


def cmd_guard_repair(restart: bool, lock: bool) -> int:
    settings = State.load_settings()
    ctx = service.load_context(settings)
    log = lambda m: print(m, file=sys.stderr)
    report = guard.scan_drift(ctx.env, ctx.state)
    log(report.summary())
    if not report.count and not lock:
        return 0
    res = guard.repair_drift(
        ctx.env, ctx.state, report, restart=restart, lock=lock,
        allow_close=restart, log=log,
    )
    print(
        f"repaired={res.repaired} locked={res.locked} failed={len(res.failed)}",
        file=sys.stderr,
    )
    return 0 if not res.failed else 2


def _ensure_stdio() -> None:
    """Windowed builds have no usable stdout; route CLI output to a file.

    The packaged exe is built with ``--windowed``: the inherited std handles are
    invalid, so ``print`` either raises or blocks.  Every CLI option writes to
    ``<data>/cli_output.txt`` instead.  Running from source keeps the console.
    """
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and sys.stdout is not None and sys.stderr is not None:
        return
    import io

    if frozen and len(sys.argv) > 1:
        try:
            from steam_zh import paths

            stream = open(paths.data_dir() / "cli_output.txt", "w", encoding="utf-8")
        except Exception:
            stream = io.StringIO()
    else:
        # GUI mode: keep messages in memory so invalid handles are never used.
        stream = io.StringIO()
    sys.stdout = stream
    sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    from steam_zh import paths

    _ensure_stdio()
    paths.ensure_data()
    parser = argparse.ArgumentParser(description="Steam 统译 (Steam Unify)")
    parser.add_argument("--scan", action="store_true", help="扫描并打印清单，不写文件")
    parser.add_argument("--autofix", action="store_true", help="静默修复已应用的中文名")
    parser.add_argument("--drift", action="store_true",
                        help="只读检查哪些中文名被 Steam 覆盖了")
    parser.add_argument("--guard-repair", action="store_true",
                        help="回写被 Steam 覆盖的中文名（不重启 Steam）")
    parser.add_argument("--restart", action="store_true",
                        help="配合 --guard-repair：回写后重新启动 Steam")
    parser.add_argument("--lock", action="store_true",
                        help="配合 --guard-repair：把写过的 appinfo 文件设为只读")
    parser.add_argument("--report", metavar="CSV", help="导出清单到 CSV")
    args = parser.parse_args(argv)
    if args.scan:
        return cmd_scan()
    if args.autofix:
        return cmd_autofix()
    if args.drift:
        return cmd_drift()
    if args.guard_repair:
        return cmd_guard_repair(restart=bool(args.restart), lock=bool(args.lock))
    if args.report:
        return cmd_report(args.report)
    from steam_zh.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
