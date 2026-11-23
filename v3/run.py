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

from steam_zh import apply, category_state, service, steamenv
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


def main(argv: list[str] | None = None) -> int:
    from steam_zh import paths

    paths.ensure_data()
    parser = argparse.ArgumentParser(description="Steam 统译 (Steam Unify)")
    parser.add_argument("--scan", action="store_true", help="扫描并打印清单，不写文件")
    parser.add_argument("--autofix", action="store_true", help="静默修复已应用的中文名")
    parser.add_argument("--report", metavar="CSV", help="导出清单到 CSV")
    args = parser.parse_args(argv)
    if args.scan:
        return cmd_scan()
    if args.autofix:
        return cmd_autofix()
    if args.report:
        return cmd_report(args.report)
    from steam_zh.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
