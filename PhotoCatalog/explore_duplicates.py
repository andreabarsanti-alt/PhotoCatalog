"""
Browse duplicate groups populated by find_duplicates.py.

Run from the repo root:
    python -m PhotoCatalog.explore_duplicates --db catalog.db
    python -m PhotoCatalog.explore_duplicates --db catalog.db --strategy phash
    python -m PhotoCatalog.explore_duplicates --db catalog.db --strategy phash --group 3
    python -m PhotoCatalog.explore_duplicates --db catalog.db --report report.html

Default (no --strategy): print a summary of all strategies.
With --strategy: page through every group interactively (Enter = next, q = quit).
With --group N: jump straight to that group.
With --report: write a self-contained HTML file for offline review.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .db import connect, init_db


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

_STRATEGIES = ("phash", "metadata", "filename")

_PHOTO_COLS = (
    "id", "source_type", "source_file", "file_name", "original_filename",
    "file_type", "file_size", "image_width", "image_height",
    "date_original", "make", "model", "lens_model",
    "focal_length", "aperture", "iso", "shutter_speed",
    "latitude", "longitude", "caption", "keywords",
    "mp_favorite", "mp_score", "lr_rating", "lr_pick",
    "perceptual_hash",
)

_LABEL = {
    "id":                "ID",
    "source_type":       "Source",
    "source_file":       "Path",
    "file_name":         "Filename (disk)",
    "original_filename": "Filename (original)",
    "file_type":         "Type",
    "file_size":         "Size",
    "image_width":       "Width",
    "image_height":      "Height",
    "date_original":     "Date taken",
    "make":              "Make",
    "model":             "Model",
    "lens_model":        "Lens",
    "focal_length":      "Focal length",
    "aperture":          "Aperture",
    "iso":               "ISO",
    "shutter_speed":     "Shutter",
    "latitude":          "Latitude",
    "longitude":         "Longitude",
    "caption":           "Caption",
    "keywords":          "Keywords",
    "mp_favorite":       "Favorite",
    "mp_score":          "Score",
    "lr_rating":         "LR rating",
    "lr_pick":           "LR pick",
    "perceptual_hash":   "Phash",
}


def _summary(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT strategy,
               COUNT(DISTINCT group_id) AS groups,
               COUNT(*)                 AS photos
        FROM   duplicate_groups
        GROUP  BY strategy
        ORDER  BY strategy
    """).fetchall()


def _group_ids(conn: sqlite3.Connection, strategy: str) -> list[int]:
    rows = conn.execute("""
        SELECT DISTINCT group_id FROM duplicate_groups
        WHERE strategy = ?
        ORDER BY group_id
    """, (strategy,)).fetchall()
    return [r["group_id"] for r in rows]


def _group_photos(conn: sqlite3.Connection, strategy: str, group_id: int) -> list[sqlite3.Row]:
    cols = ", ".join(f"p.{c}" for c in _PHOTO_COLS)
    return conn.execute(f"""
        SELECT {cols}, dg.match_info
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        WHERE  dg.strategy = ?
          AND  dg.group_id = ?
        ORDER  BY p.id
    """, (strategy, group_id)).fetchall()


def _fmt(key: str, val) -> str:
    if val is None:
        return ""
    if key == "file_size" and isinstance(val, (int, float)):
        return _human_bytes(val)
    if key == "perceptual_hash" and isinstance(val, str):
        return val[:16] + "…"
    if key == "source_file" and isinstance(val, str):
        return val if val.startswith("macphotos://") else str(Path(val).name)
    return str(val)


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Terminal view  (rich if available, plain-text fallback)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False


def _print_summary_terminal(rows: list) -> None:
    if not rows:
        print("No duplicate groups in database. Run find_duplicates.py first.")
        return

    if _RICH:
        console = Console()
        t = Table(title="Duplicate summary", box=box.SIMPLE_HEAD)
        t.add_column("Strategy", style="bold cyan")
        t.add_column("Groups",   justify="right")
        t.add_column("Photos",   justify="right")
        for r in rows:
            t.add_row(r["strategy"], str(r["groups"]), str(r["photos"]))
        console.print(t)
    else:
        print(f"\n{'Strategy':<12}  {'Groups':>8}  {'Photos':>8}")
        print("-" * 34)
        for r in rows:
            print(f"{r['strategy']:<12}  {r['groups']:>8}  {r['photos']:>8}")
        print()


def _print_group_terminal(
    strategy: str,
    group_id: int,
    total_groups: int,
    photos: list,
) -> None:
    match_info = {}
    try:
        match_info = json.loads(photos[0]["match_info"] or "{}")
    except Exception as e:
        print(f"Warning: could not parse match_info for group {group_id}: {e}", file=sys.stderr)

    header = f"[{strategy}] Group {group_id}/{total_groups}  ({len(photos)} photos)"
    if match_info:
        header += "  match: " + ", ".join(f"{k}={v}" for k, v in match_info.items())

    if _RICH:
        console = Console()
        t = Table(title=header, box=box.SIMPLE_HEAD, show_lines=True)
        t.add_column("Field", style="dim", no_wrap=True)
        for i in range(len(photos)):
            t.add_column(f"Photo {i+1}", overflow="fold")

        for col in _PHOTO_COLS:
            values = [_fmt(col, p[col]) for p in photos]
            if all(v == "" for v in values):
                continue
            # Highlight differences
            unique = set(values)
            style = "bold yellow" if len(unique) > 1 else ""
            row = [_LABEL.get(col, col)] + values
            t.add_row(*row, style=style if len(unique) > 1 else None)

        console.print(t)
    else:
        print(f"\n{'='*60}")
        print(header)
        print(f"{'='*60}")
        for col in _PHOTO_COLS:
            values = [_fmt(col, p[col]) for p in photos]
            if all(v == "" for v in values):
                continue
            label = _LABEL.get(col, col)
            print(f"  {label:<20} " + "  |  ".join(f"{v:<30}" for v in values))
        print()


def _page_strategy(conn: sqlite3.Connection, strategy: str, start_group: Optional[int] = None) -> None:
    ids = _group_ids(conn, strategy)
    if not ids:
        print(f"No groups for strategy '{strategy}'.")
        return

    total = len(ids)
    start_idx = 0
    if start_group is not None:
        if start_group not in ids:
            print(f"Group {start_group} not found for strategy '{strategy}'.")
            return
        start_idx = ids.index(start_group)

    idx = start_idx
    while idx < total:
        gid = ids[idx]
        photos = _group_photos(conn, strategy, gid)
        _print_group_terminal(strategy, gid, total, photos)

        if idx == total - 1:
            print("(end of groups)")
            break

        try:
            key = input("  [Enter]=next  [q]=quit  [#]=jump to group  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if key == "q":
            break
        elif key.isdigit():
            target = int(key)
            if target in ids:
                idx = ids.index(target)
            else:
                print(f"  Group {target} not found.")
        else:
            idx += 1


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_HTML_CSS = """
body { font-family: system-ui, sans-serif; font-size: 13px; background: #f8f9fa; margin: 0; }
h1   { background: #343a40; color: #fff; margin: 0; padding: 12px 20px; font-size: 16px; }
h2   { margin: 20px 20px 6px; font-size: 14px; color: #495057; }
.summary { margin: 16px 20px; }
.summary table { border-collapse: collapse; }
.summary th, .summary td { border: 1px solid #dee2e6; padding: 6px 14px; text-align: right; }
.summary th { background: #e9ecef; text-align: left; }
.strategy-section { margin: 24px 20px 0; }
.strategy-section h2 { font-size: 15px; color: #212529; border-bottom: 2px solid #adb5bd; padding-bottom: 4px; }
.group { background: #fff; border: 1px solid #dee2e6; border-radius: 6px; margin: 12px 0; overflow: auto; }
.group-header { background: #e9ecef; padding: 8px 14px; font-weight: 600; font-size: 12px; color: #495057; }
.group table { border-collapse: collapse; width: 100%; }
.group td, .group th { border: 1px solid #dee2e6; padding: 5px 10px; vertical-align: top; white-space: nowrap; }
.group th { background: #f1f3f5; text-align: left; font-weight: normal; color: #868e96; width: 140px; }
.diff { background: #fff3cd; font-weight: 600; }
.cloud { color: #868e96; font-style: italic; }
"""

def _build_html(conn: sqlite3.Connection, path: str) -> None:
    summary = _summary(conn)

    parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'>",
             f"<title>PhotoCatalog — duplicates</title>",
             f"<style>{_HTML_CSS}</style></head><body>",
             "<h1>PhotoCatalog — duplicate groups</h1>"]

    # Summary table
    parts.append('<div class="summary"><table>')
    parts.append("<tr><th>Strategy</th><th>Groups</th><th>Photos</th></tr>")
    for r in summary:
        parts.append(f"<tr><td>{r['strategy']}</td><td>{r['groups']}</td><td>{r['photos']}</td></tr>")
    parts.append("</table></div>")

    for strategy in _STRATEGIES:
        ids = _group_ids(conn, strategy)
        if not ids:
            continue

        parts.append(f'<div class="strategy-section"><h2>{strategy} — {len(ids)} groups</h2>')
        for gid in ids:
            photos = _group_photos(conn, strategy, gid)
            match_info = {}
            try:
                match_info = json.loads(photos[0]["match_info"] or "{}")
            except Exception as e:
                print(f"Warning: could not parse match_info for group {gid}: {e}", file=sys.stderr)

            mi_str = "  |  ".join(f"{k}: {v}" for k, v in match_info.items())
            parts.append(f'<div class="group"><div class="group-header">Group {gid} &nbsp;·&nbsp; {len(photos)} photos &nbsp;·&nbsp; {mi_str}</div>')
            parts.append("<table>")

            for col in _PHOTO_COLS:
                values = [_fmt(col, p[col]) for p in photos]
                if all(v == "" for v in values):
                    continue
                unique = set(values)
                label = _LABEL.get(col, col)
                parts.append(f"<tr><th>{label}</th>")
                diff_class = " class='diff'" if len(unique) > 1 else ""
                for v in values:
                    cell = v if not v.startswith("macphotos://") else f"<span class='cloud'>{v}</span>"
                    parts.append(f"<td{diff_class}>{cell or '&nbsp;'}</td>")
                parts.append("</tr>")

            parts.append("</table></div>")

        parts.append("</div>")

    parts.append("</body></html>")

    Path(path).write_text("\n".join(parts), encoding="utf-8")
    print(f"Report written to: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browse duplicate groups from find_duplicates.py."
    )
    parser.add_argument("--db", required=True, help="Path to the catalog SQLite database.")
    parser.add_argument(
        "--strategy", choices=list(_STRATEGIES),
        help="Strategy to browse. Omit to show the overall summary.",
    )
    parser.add_argument(
        "--group", type=int, default=None,
        help="Jump directly to this group ID (requires --strategy).",
    )
    parser.add_argument(
        "--report", metavar="FILE.html",
        help="Write a self-contained HTML report instead of interactive mode.",
    )
    args = parser.parse_args()

    if args.group and not args.strategy:
        parser.error("--group requires --strategy")

    conn = connect(args.db)
    init_db(conn)

    try:
        if args.report:
            _build_html(conn, args.report)
        elif args.strategy:
            _page_strategy(conn, args.strategy, start_group=args.group)
        else:
            rows = _summary(conn)
            _print_summary_terminal(rows)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
