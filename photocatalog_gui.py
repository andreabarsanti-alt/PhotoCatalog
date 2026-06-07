#!/usr/bin/env python3
"""
PhotoCatalog GUI — visual launcher for build_catalog, find_duplicates, serve_duplicates.

Normal usage (development):
    .venv/bin/python3 photocatalog_gui.py
Or double-click PhotoCatalog.command in Finder.

When bundled as a PyInstaller .app this same file is also the CLI passthrough used
by the subprocesses the GUI spawns:
    PhotoCatalog.app/Contents/MacOS/PhotoCatalog --photocatalog-cli <subcmd> [args…]
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# CLI passthrough — used by the bundled .app to run sub-commands without a
# separate Python interpreter.  Must be first, before any heavy imports.
# ---------------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] == "--photocatalog-cli":
    # Tell macOS this is a background helper — prevents a new Dock icon / window
    # from appearing each time the GUI spawns a subprocess.
    try:
        import AppKit  # noqa: PLC0415
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyProhibited
        )
    except Exception:
        pass

    _known = {"build-catalog", "find-duplicates", "serve-duplicates", "compute-hashes"}
    _rest = sys.argv[2:]
    # Strip any bootloader-injected flags before the actual subcommand
    while _rest and _rest[0].startswith("-") and _rest[0] not in _known:
        _rest = _rest[1:]
    subcmd = _rest[0] if _rest else ""
    sys.argv = [sys.argv[0]] + (_rest[1:] if _rest else [])
    if subcmd == "build-catalog":
        from PhotoCatalog.build_catalog import main
    elif subcmd == "find-duplicates":
        from PhotoCatalog.find_duplicates import main
    elif subcmd == "serve-duplicates":
        from PhotoCatalog.serve_duplicates import main
    elif subcmd == "compute-hashes":
        from PhotoCatalog.enrich import main
    else:
        print(f"Unknown sub-command: {subcmd!r}  (sys.argv was: {sys.argv!r})", file=sys.stderr)
        sys.exit(1)
    main()
    sys.exit(0)

# ---------------------------------------------------------------------------
# Normal GUI mode
# ---------------------------------------------------------------------------
import glob
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from PhotoCatalog import __version__

SCRIPT_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = str(SCRIPT_DIR / ".venv" / "bin" / "python3")
DEFAULT_CATALOG_DIR = Path.home() / "Pictures" / "PhotoCatalogs"
GITHUB_REPO = "andreabarsanti-alt/PhotoCatalog"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
INSTALL_DIR = Path("/Applications")
PREFS_FILE = Path.home() / ".photocatalog_prefs.json"


def _python_cmd() -> list[str]:
    """Return the correct Python invocation prefix depending on environment."""
    if getattr(sys, "frozen", False):
        # Use the sibling PhotoCatalogCLI binary — it's NOT the CFBundleExecutable,
        # so macOS does not go through app-launch infrastructure for it.
        cli = Path(sys.executable).parent / "PhotoCatalogCLI"
        return [str(cli)]
    return [VENV_PYTHON, "-m"]


def _module_to_subcmd(module: str) -> str:
    """Map a Python module path to a CLI sub-command name."""
    return module.split(".")[-1].replace("_", "-")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class PhotoCatalogApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PhotoCatalog  v{__version__}")
        self.geometry("1000x720")
        self.minsize(800, 560)

        if not getattr(sys, "frozen", False) and not Path(VENV_PYTHON).exists():
            messagebox.showerror(
                "venv not found",
                f"Could not find:\n  {VENV_PYTHON}\n\n"
                "Run:  python3 -m venv .venv && .venv/bin/pip install -e .",
            )
            self.destroy()
            return

        self._output_q: queue.Queue[str | None] = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._latest_version: str | None = None
        self._update_asset_url: str | None = None

        self._build_db_bar()
        self._build_notebook()
        self._build_output()
        self._poll_output()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Silently check for updates in background
        threading.Thread(target=self._fetch_latest_release, daemon=True).start()

    # ── shared DB bar ─────────────────────────────────────────────────────────

    def _load_prefs(self) -> dict:
        try:
            return json.loads(PREFS_FILE.read_text())
        except Exception:
            return {}

    def _save_prefs(self, prefs: dict):
        try:
            PREFS_FILE.write_text(json.dumps(prefs, indent=2))
        except Exception:
            pass

    def _find_catalogs(self) -> list[str]:
        if not DEFAULT_CATALOG_DIR.exists():
            return []
        return [str(p) for p in sorted(DEFAULT_CATALOG_DIR.rglob("*.db"))]

    def _find_photo_libraries(self) -> list[str]:
        found = []
        for root in [Path.home() / "Pictures", Path.home()]:
            if root.exists():
                found += [str(p) for p in sorted(root.glob("*.photoslibrary")) if p.is_dir()]
        return found

    def _merged_catalog_values(self, prefs: dict | None = None) -> list[str]:
        if prefs is None:
            prefs = self._load_prefs()
        recent: list[str] = prefs.get("recent_catalogs", [])
        scanned = self._find_catalogs()
        seen = set(recent)
        return recent + [c for c in scanned if c not in seen]

    def _record_catalog_used(self, db_path: str):
        if not db_path:
            return
        prefs = self._load_prefs()
        recent: list[str] = prefs.get("recent_catalogs", [])
        if db_path in recent:
            recent.remove(db_path)
        recent.insert(0, db_path)
        prefs["recent_catalogs"] = recent[:10]
        self._save_prefs(prefs)
        self._update_combo_values(prefs)

    def _update_combo_values(self, prefs: dict | None = None):
        self._db_combo["values"] = self._merged_catalog_values(prefs)

    def _refresh_catalog_info(self):
        path = self.db_path.get().strip()
        if not path or not Path(path).exists():
            self._catalog_info.config(text="")
            return
        try:
            conn = sqlite3.connect(path, timeout=0.5)
            try:
                count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
                sources = [r[0] for r in conn.execute(
                    "SELECT DISTINCT source_type FROM photos ORDER BY source_type"
                ).fetchall()]
            finally:
                conn.close()
            info = f"{count:,} photos"
            if sources:
                info += "  |  " + ", ".join(sources)
            self._catalog_info.config(text=info)
        except Exception:
            self._catalog_info.config(text="")

    def _build_db_bar(self):
        frame = ttk.LabelFrame(self, text="Catalog Database (.db file)", padding=6)
        frame.pack(fill="x", padx=12, pady=(12, 4))
        frame.columnconfigure(0, weight=1)

        values = self._merged_catalog_values()
        initial = values[0] if values else str(DEFAULT_CATALOG_DIR / "MyCatalog" / "MyCatalog.db")

        self.db_path = tk.StringVar(value=initial)
        self._db_combo = ttk.Combobox(frame, textvariable=self.db_path, values=values)
        self._db_combo.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._db_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_catalog_info())
        self._db_combo.bind("<FocusOut>", lambda _: self._refresh_catalog_info())

        ttk.Button(frame, text="Browse…", command=self._browse_db).grid(row=0, column=1)

        self._catalog_info = ttk.Label(frame, text="", foreground="gray")
        self._catalog_info.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self.after(0, self._refresh_catalog_info)

    def _browse_db(self):
        init = Path(self.db_path.get()).parent
        if not init.exists():
            init = DEFAULT_CATALOG_DIR if DEFAULT_CATALOG_DIR.exists() else Path.home()
        p = filedialog.askopenfilename(
            title="Open catalog database",
            initialdir=str(init),
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
        )
        if p:
            self.db_path.set(p)
            self._refresh_catalog_info()

    # ── notebook ──────────────────────────────────────────────────────────────

    def _build_notebook(self):
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="x", padx=12, pady=4)
        self._build_ingest_tab()
        self._build_find_tab()
        self._build_browse_tab()
        self._build_export_clean_tab()
        self._build_edit_tab()
        self._build_about_tab()

    # ── Add to Catalog tab ────────────────────────────────────────────────────

    def _build_ingest_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Add to Catalog  ")
        tab.columnconfigure(0, weight=1)

        # ── action bar (buttons at top) ──────────────────────────────────────
        action_bar = ttk.Frame(tab)
        action_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        action_bar.columnconfigure(0, weight=1)

        left = ttk.Frame(action_bar)
        left.grid(row=0, column=0, sticky="w")
        self._fresh = tk.BooleanVar()
        ttk.Checkbutton(left, text="Fresh start", variable=self._fresh).pack(
            side="left", padx=(0, 8)
        )
        self._gen_hashes = tk.BooleanVar()
        ttk.Checkbutton(left, text="Compute hashes", variable=self._gen_hashes).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(left, text="▶  Add to Catalog", command=self._run_ingest).pack(side="left")

        right = ttk.Frame(action_bar)
        right.grid(row=0, column=1, sticky="e")
        self._hash_fresh = tk.BooleanVar()
        ttk.Checkbutton(right, text="Fresh start", variable=self._hash_fresh).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(right, text="▶  Compute Hashes", command=self._run_compute_hashes).pack(
            side="left"
        )
        ttk.Button(right, text="▶  Compute Fuzzy Hashes",
                   command=self._run_compute_fuzzy_hashes).pack(side="left", padx=(10, 0))

        # ── source type ──────────────────────────────────────────────────────
        sf = ttk.LabelFrame(tab, text="Source type", padding=6)
        sf.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._source = tk.StringVar(value="MacPhotos")
        for val in ("MacPhotos", "Lightroom", "Folder"):
            ttk.Radiobutton(
                sf, text=val, value=val, variable=self._source,
                command=self._refresh_ingest,
            ).pack(side="left", padx=16)

        # ── source path ──────────────────────────────────────────────────────
        self._pf = ttk.LabelFrame(tab, text="Source path", padding=6)
        self._pf.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self._pf.columnconfigure(0, weight=1)
        self._src_hint = ttk.Label(
            self._pf, foreground="gray",
            text="Optional — leave blank to use the default Photos library",
        )
        self._src_hint.grid(row=0, column=0, columnspan=2, sticky="w")
        self._src_path = tk.StringVar()
        ttk.Entry(self._pf, textvariable=self._src_path).grid(
            row=1, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(self._pf, text="Browse…", command=self._browse_src).grid(row=1, column=1)

        # ── iCloud options ───────────────────────────────────────────────────
        self._ic_f = ttk.LabelFrame(tab, text="iCloud options (MacPhotos only)", padding=6)
        self._ic_f.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        self._ic_f.columnconfigure(1, weight=1)

        self._download = tk.BooleanVar()
        ttk.Checkbutton(
            self._ic_f,
            text="Download iCloud-only photos to a local folder",
            variable=self._download,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(self._ic_f, text="Download dir:").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        self._dl_dir = tk.StringVar()
        ttk.Entry(self._ic_f, textvariable=self._dl_dir).grid(
            row=1, column=1, sticky="ew", padx=(6, 6), pady=(4, 0)
        )
        ttk.Button(self._ic_f, text="Browse…", command=self._browse_dl_dir).grid(
            row=1, column=2, pady=(4, 0)
        )

        ttk.Label(self._ic_f, text="Limit:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        lim_row = ttk.Frame(self._ic_f)
        lim_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self._dl_limit = tk.StringVar()
        ttk.Entry(lim_row, textvariable=self._dl_limit, width=8).pack(side="left")
        ttk.Label(lim_row, text="  photos  (blank = no limit)", foreground="gray").pack(
            side="left"
        )

    def _refresh_ingest(self):
        st = self._source.get()
        if st == "MacPhotos":
            self._src_hint.config(
                text="Optional — leave blank to use the default Photos library"
            )
            self._ic_f.grid()
        else:
            hint = (
                "Required — path to the .lrcat file"
                if st == "Lightroom"
                else "Required — root folder to scan for photos"
            )
            self._src_hint.config(text=hint)
            self._ic_f.grid_remove()

    def _browse_src(self):
        st = self._source.get()
        if st == "Lightroom":
            p = filedialog.askopenfilename(
                title="Select Lightroom catalog (.lrcat)",
                filetypes=[("Lightroom catalog", "*.lrcat"), ("All", "*.*")],
            )
        elif st == "MacPhotos":
            p = self._pick_photos_library()
        else:
            p = filedialog.askdirectory(title="Select folder to scan")
        if p:
            self._src_path.set(p)

    def _pick_photos_library(self) -> str:
        """Show a dialog listing discovered .photoslibrary bundles, with a Browse fallback."""
        libs = self._find_photo_libraries()

        dlg = tk.Toplevel(self)
        dlg.title("Select Photos Library")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        result = [""]

        if libs:
            ttk.Label(dlg, text="Found Photos libraries:", padding=(12, 10, 12, 4)).pack(anchor="w")
            var = tk.StringVar(value=libs[0])
            for lib in libs:
                ttk.Radiobutton(
                    dlg, text=Path(lib).name, value=lib, variable=var,
                ).pack(anchor="w", padx=24, pady=2)

            def _ok():
                result[0] = var.get()
                dlg.destroy()

            btn_row = ttk.Frame(dlg, padding=(12, 8, 12, 12))
            btn_row.pack(fill="x")
            ttk.Button(btn_row, text="Select", command=_ok).pack(side="right", padx=(4, 0))
        else:
            ttk.Label(
                dlg,
                text="No .photoslibrary bundles found in ~/Pictures.\nBrowse manually:",
                padding=(12, 10, 12, 4),
                justify="left",
            ).pack(anchor="w")
            btn_row = ttk.Frame(dlg, padding=(12, 4, 12, 12))
            btn_row.pack(fill="x")

        def _browse_manual():
            dlg.destroy()
            try:
                r = subprocess.run(
                    ["osascript", "-e",
                     'POSIX path of (choose folder with prompt "Select Photos Library (.photoslibrary)")'],
                    capture_output=True, text=True,
                )
                result[0] = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                result[0] = filedialog.askdirectory(title="Select Photos library (.photoslibrary)")

        ttk.Button(btn_row, text="Browse…", command=_browse_manual).pack(side="left")
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="right")

        dlg.wait_window()
        return result[0]

    def _browse_dl_dir(self):
        p = filedialog.askdirectory(title="Select iCloud download directory")
        if p:
            self._dl_dir.set(p)

    def _run_ingest(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        Path(db).parent.mkdir(parents=True, exist_ok=True)

        st = self._source.get()
        src = self._src_path.get().strip()
        if st != "MacPhotos" and not src:
            messagebox.showerror("Error", f"Source path is required for {st}.")
            return

        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["build-catalog", "--source", st, "--db", db]
        else:
            cmd = prefix + ["PhotoCatalog.build_catalog", "--source", st, "--db", db]
        if src:
            cmd += ["--path", src]
        if self._fresh.get():
            cmd.append("--fresh")
        if not self._gen_hashes.get():
            cmd.append("--no-hash")
        if self._download.get():
            cmd.append("--download")
        dl = self._dl_dir.get().strip()
        if dl:
            cmd += ["--download-dir", dl]
        lim = self._dl_limit.get().strip()
        if lim:
            cmd += ["--download-limit", lim]

        self._run_cmd(cmd, caffeinate=True)

    def _run_compute_hashes(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["compute-hashes", "--db", db]
        else:
            cmd = prefix + ["PhotoCatalog.enrich", "--db", db]
        if self._hash_fresh.get():
            cmd.append("--reset")
        self._run_cmd(cmd, caffeinate=True)

    def _run_compute_fuzzy_hashes(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["compute-hashes", "--db", db, "--fuzzy"]
        else:
            cmd = prefix + ["PhotoCatalog.enrich", "--db", db, "--fuzzy"]
        if self._hash_fresh.get():
            cmd.append("--reset")
        self._run_cmd(cmd, caffeinate=True)

    # ── Find Duplicates tab ───────────────────────────────────────────────────

    def _build_find_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Find Duplicates  ")
        tab.columnconfigure(0, weight=1)

        # ── Signals ──────────────────────────────────────────────────────────
        sf = ttk.LabelFrame(tab, text="Signals  (photos must match on checked signals to be grouped)", padding=8)
        sf.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        _SIG_LABELS = [
            ("phash", "pHash",    "Exact perceptual hash match  (pixel-level duplicates)"),
            ("fuzzy", "Fuzzy",    "Near-identical hash  (light edits, re-compression, slight crops)"),
            ("dims",  "Dims",     "Same image dimensions  (rotation-aware)"),
            ("date",  "Date",     "Same original date"),
            ("type",  "Type",     "Same file type"),
            ("size",  "Size",     "Same file size"),
            ("name",  "Name",     "Common filename stem + date minute"),
        ]
        self._signal_vars: dict[str, tk.BooleanVar] = {}
        for sig, short, desc in _SIG_LABELS:
            var = tk.BooleanVar(value=(sig == "phash"))
            self._signal_vars[sig] = var
            row = ttk.Frame(sf)
            row.pack(anchor="w", pady=1)
            ttk.Checkbutton(row, text=short, variable=var, width=7).pack(side="left")
            ttk.Label(row, text=f"— {desc}", foreground="gray").pack(side="left")

        # ── AND / OR ─────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(tab, text="Logic", padding=8)
        lf.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._logic = tk.StringVar(value="OR")
        ttk.Radiobutton(lf, text="OR   — group photos that match on ANY checked signal",
                        value="OR",  variable=self._logic).pack(anchor="w")
        ttk.Radiobutton(lf, text="AND — group photos that match on ALL checked signals",
                        value="AND", variable=self._logic).pack(anchor="w")

        # ── Fresh options ────────────────────────────────────────────────────
        ff = ttk.LabelFrame(tab, text="Fresh start", padding=8)
        ff.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self._keep_resolved = tk.BooleanVar(value=True)
        ttk.Radiobutton(ff,
                        text="Keep resolved groups  — re-run only on photos not yet reviewed",
                        variable=self._keep_resolved, value=True).pack(anchor="w")
        ttk.Radiobutton(ff,
                        text="Clear everything  — start completely fresh",
                        variable=self._keep_resolved, value=False).pack(anchor="w")

        ttk.Button(tab, text="▶  Find Duplicates", command=self._run_find).grid(
            row=3, column=0, pady=10
        )

    def _run_find(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        signals = [sig for sig, var in self._signal_vars.items() if var.get()]
        if not signals:
            messagebox.showerror("Error", "Select at least one signal.")
            return
        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["find-duplicates", "--db", db]
        else:
            cmd = prefix + ["PhotoCatalog.find_duplicates", "--db", db]
        cmd += ["--signal"] + signals
        cmd += ["--logic", self._logic.get()]
        if self._keep_resolved.get():
            cmd.append("--keep-resolved")
        self._run_cmd(cmd, caffeinate=True)

    # ── Browse Duplicates tab ─────────────────────────────────────────────────

    def _build_browse_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Review Duplicates  ")
        tab.columnconfigure(0, weight=1)

        info = ttk.LabelFrame(tab, text="Web UI", padding=8)
        info.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            info,
            text=(
                "Starts a local web server and opens your browser to the duplicate viewer.\n"
                "Left pane: scrollable list of duplicate groups, filterable by signal.\n"
                "Right pane: photo thumbnails, full metadata, and Keep / Discard buttons."
            ),
            justify="left",
        ).pack(anchor="w")

        port_row = ttk.Frame(tab)
        port_row.grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Label(port_row, text="Port:").pack(side="left")
        self._port = tk.StringVar(value="8765")
        ttk.Entry(port_row, textvariable=self._port, width=7).pack(side="left", padx=(8, 0))

        btn_row = ttk.Frame(tab)
        btn_row.grid(row=2, column=0, sticky="w")
        ttk.Button(btn_row, text="▶  Launch Web UI", command=self._run_browse).pack(side="left")
        ttk.Button(
            btn_row, text="Open browser",
            command=lambda: webbrowser.open(f"http://127.0.0.1:{self._port.get()}"),
        ).pack(side="left", padx=(10, 0))

        ttk.Label(
            tab,
            text="The server runs until you click Stop below or close this window.",
            foreground="gray",
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))


    # ── Export & Clean tab ────────────────────────────────────────────────────

    def _build_export_clean_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Export & Clean  ")
        tab.columnconfigure(0, weight=1)

        # ── Export section ───────────────────────────────────────────────────
        ef = ttk.LabelFrame(tab, text="Export Catalog", padding=10)
        ef.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ef.columnconfigure(1, weight=1)

        ttk.Label(
            ef,
            text="Copy non-discarded photos to a folder organized by date (YYYY/MM/DD).\n"
                 "Original filenames are preserved. All embedded metadata is kept.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(ef, text="Include:").grid(row=1, column=0, sticky="w")
        self._export_scope = tk.StringVar(value="all")
        scope_f = ttk.Frame(ef)
        scope_f.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(scope_f, text="Keep + undecided", value="all",
                        variable=self._export_scope).pack(side="left")
        ttk.Radiobutton(scope_f, text="Keep-flagged only", value="keep",
                        variable=self._export_scope).pack(side="left", padx=(16, 0))

        ttk.Label(ef, text="Export to:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._export_dest = tk.StringVar()
        ttk.Entry(ef, textvariable=self._export_dest).grid(
            row=2, column=1, sticky="ew", padx=(8, 6), pady=(8, 0)
        )
        ttk.Button(
            ef, text="Browse…",
            command=lambda: self._export_dest.set(
                filedialog.askdirectory(title="Export photos to…") or self._export_dest.get()
            ),
        ).grid(row=2, column=2, pady=(8, 0))

        ttk.Label(
            ef,
            text="Note: MacPhotos edits (adjustments) are not applied — originals are copied.\n"
                 "iCloud-only photos (no local file) are skipped.",
            foreground="gray", justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Button(ef, text="▶  Export photos", command=self._run_export).grid(
            row=4, column=0, columnspan=3, sticky="e", pady=(10, 0)
        )

        # ── Clean section ────────────────────────────────────────────────────
        cf = ttk.LabelFrame(tab, text="Clean Catalog", padding=10)
        cf.grid(row=1, column=0, sticky="ew")
        ttk.Label(
            cf,
            text="Move Discarded photos out of the catalog (by source: move files,\n"
                 "add to Photos album, or mark in Lightroom) and remove from the catalog.",
            justify="left",
        ).pack(anchor="w")
        ttk.Button(cf, text="🗑  Clean Catalog…", command=self._show_clean_dialog).pack(
            anchor="e", pady=(8, 0)
        )

    def _run_export(self):
        db_path = self.db_path.get().strip()
        if not db_path or not Path(db_path).exists():
            messagebox.showerror("Error", "Catalog database not found.")
            return
        dest = self._export_dest.get().strip()
        if not dest:
            messagebox.showerror("Error", "Please choose an export destination folder.")
            return
        self._record_catalog_used(db_path)

        scope = self._export_scope.get()

        # Query count first for confirmation
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        if scope == "keep":
            count = conn.execute("""
                SELECT COUNT(*) FROM photos p
                JOIN decisions d ON d.photo_id = p.id
                WHERE d.action = 'keep'
            """).fetchone()[0]
        else:
            count = conn.execute("""
                SELECT COUNT(*) FROM photos p
                WHERE p.id NOT IN (
                    SELECT photo_id FROM decisions WHERE action = 'discard'
                )
            """).fetchone()[0]
        conn.close()

        if count == 0:
            messagebox.showinfo("Export", "No photos match the selected scope.")
            return

        label = "Keep-flagged" if scope == "keep" else "Keep + undecided"
        if not messagebox.askyesno(
            "Export Catalog",
            f"Export {count:,} photos ({label}) to:\n{dest}\n\nProceed?",
        ):
            return

        Path(dest).mkdir(parents=True, exist_ok=True)
        self._clear()
        self._status.config(text="Running…")
        self._append(f"Exporting {count:,} photos to {dest}…\n\n")

        def worker():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            if scope == "keep":
                rows = conn.execute("""
                    SELECT p.id, p.source_file, p.source_type,
                           p.file_name, p.original_filename, p.date_original,
                           p.mp_is_live
                    FROM   photos p
                    JOIN   decisions d ON d.photo_id = p.id
                    WHERE  d.action = 'keep'
                    ORDER  BY p.date_original
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT p.id, p.source_file, p.source_type,
                           p.file_name, p.original_filename, p.date_original,
                           p.mp_is_live
                    FROM   photos p
                    WHERE  p.id NOT IN (
                        SELECT photo_id FROM decisions WHERE action = 'discard'
                    )
                    ORDER  BY p.date_original
                """).fetchall()
            conn.close()

            dest_path = Path(dest)
            copied = skipped = failed = 0

            for i, r in enumerate(rows):
                if self._cancel.is_set():
                    self._output_q.put("  Cancelled.\n")
                    break
                src_str = r["source_file"] or ""

                # Skip iCloud-only photos
                if src_str.startswith("macphotos://"):
                    self._output_q.put(f"  Skipped (iCloud-only): {r['original_filename'] or r['file_name']}\n")
                    skipped += 1
                    continue

                src = Path(src_str)
                if not src.exists():
                    self._output_q.put(f"  Skipped (not on disk): {src.name}\n")
                    skipped += 1
                    continue

                # Destination name: prefer original_filename (preserves original name for MacPhotos)
                name = r["original_filename"] or r["file_name"] or src.name

                # Date-based folder
                date_str = (r["date_original"] or "")[:10]
                if date_str and len(date_str) == 10:
                    y, m, d = date_str.split("-")
                    folder = dest_path / y / m / d
                else:
                    folder = dest_path / "NoDate"
                folder.mkdir(parents=True, exist_ok=True)

                # Collision-safe name
                dst = folder / name
                if dst.exists():
                    stem, suffix = Path(name).stem, Path(name).suffix
                    n = 1
                    while dst.exists():
                        dst = folder / f"{stem}_{n}{suffix}"
                        n += 1

                try:
                    shutil.copy2(str(src), str(dst))
                    copied += 1
                    # Live Photo: copy companion .MOV so the pair re-imports correctly
                    if r["mp_is_live"]:
                        mov_src = src.with_suffix(".MOV")
                        if mov_src.exists():
                            shutil.copy2(str(mov_src), str(folder / mov_src.name))
                    if copied % 100 == 0:
                        self._output_q.put(f"  {copied}/{len(rows)} copied…\n")
                except Exception as e:
                    self._output_q.put(f"  Failed: {src.name} — {e}\n")
                    failed += 1

            summary = f"\nDone — {copied:,} copied"
            if skipped:
                summary += f", {skipped} skipped"
            if failed:
                summary += f", {failed} failed"
            summary += f"\nDestination: {dest}\n"
            self._output_q.put(summary)
            self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()

    def _run_browse(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        port = self._port.get().strip() or "8765"
        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["serve-duplicates", "--db", db, "--port", port]
        else:
            cmd = prefix + ["PhotoCatalog.serve_duplicates", "--db", db, "--port", port]
        self._run_cmd(cmd)

    # ── Clean Catalog ─────────────────────────────────────────────────────────

    @staticmethod
    def _hs(n: int) -> str:
        if n < 1024:            return f"{n} B"
        if n < 1_048_576:       return f"{n/1024:.0f} KB"
        if n < 1_073_741_824:   return f"{n/1_048_576:.1f} MB"
        return f"{n/1_073_741_824:.2f} GB"

    @staticmethod
    def _db_delete(conn: sqlite3.Connection, ids: list[int]) -> None:
        """Delete photos from decisions, duplicate_groups, and photos tables."""
        for i in range(0, len(ids), 500):
            batch = ids[i : i + 500]
            ph = ",".join("?" * len(batch))
            conn.execute(f"DELETE FROM decisions        WHERE photo_id IN ({ph})", batch)
            conn.execute(f"DELETE FROM duplicate_groups WHERE photo_id IN ({ph})", batch)
            conn.execute(f"DELETE FROM photos           WHERE id       IN ({ph})", batch)
        conn.commit()

    def _show_clean_dialog(self):
        db_path = self.db_path.get().strip()
        if not db_path:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        if not Path(db_path).exists():
            messagebox.showerror("Error", f"Database not found:\n{db_path}")
            return
        self._record_catalog_used(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT p.id, p.source_file, p.source_type, p.source_catalog,
                   p.original_filename, p.file_name, p.mp_uuid,
                   p.date_original, p.file_size
            FROM   photos p
            JOIN   decisions d ON d.photo_id = p.id
            WHERE  d.action = 'discard'
            ORDER  BY p.source_type, p.date_original
        """).fetchall()
        conn.close()

        if not rows:
            messagebox.showinfo("Clean Catalog", "No photos are marked as Discard.")
            return

        folder_rows = [r for r in rows if r["source_type"] == "Folder"]
        mac_rows    = [r for r in rows if r["source_type"] == "MacPhotos"]
        lr_rows     = [r for r in rows if r["source_type"] == "Lightroom"]

        dlg = tk.Toplevel(self)
        dlg.title("Clean Catalog")
        dlg.geometry("640x560")
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        dlg.columnconfigure(0, weight=1)

        pad = {"padx": 14, "pady": 6}
        row_idx = 0

        # ── Section 1: Folders ────────────────────────────────────────────────
        if folder_rows:
            with_date  = [r for r in folder_rows if r["date_original"]]
            no_date    = [r for r in folder_rows if not r["date_original"]]
            total_b    = sum(r["file_size"] or 0 for r in folder_rows)

            ff = ttk.LabelFrame(dlg, text=f"Folders  —  {len(folder_rows)} photos  ({self._hs(total_b)})", padding=10)
            ff.grid(row=row_idx, column=0, sticky="ew", **pad)
            ff.columnconfigure(0, weight=1)
            row_idx += 1

            ttk.Label(ff, text=f"  •  {len(with_date)} with date  →  destination/YYYY/MM/DD/").grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(ff, text=f"  •  {len(no_date)} without date  →  destination/NoDate/").grid(row=1, column=0, columnspan=2, sticky="w")

            dest_var = tk.StringVar()
            ttk.Entry(ff, textvariable=dest_var).grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
            ttk.Button(
                ff, text="Browse…",
                command=lambda: dest_var.set(
                    filedialog.askdirectory(title="Move discarded Folder photos to…", parent=dlg) or dest_var.get()
                ),
            ).grid(row=2, column=1, pady=(6, 0))

            ttk.Button(
                ff, text=f"Move {len(folder_rows)} files  →",
                command=lambda rows=folder_rows, dv=dest_var: self._execute_clean_folder(dlg, db_path, rows, dv),
            ).grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # ── Section 2: MacPhotos ──────────────────────────────────────────────
        if mac_rows:
            mac_by_cat: dict[str, list] = {}
            for r in mac_rows:
                mac_by_cat.setdefault(r["source_catalog"] or "", []).append(r)
            cat_paths = sorted(mac_by_cat.keys())
            n_libs = len(cat_paths)

            mf = ttk.LabelFrame(
                dlg,
                text=f"MacPhotos  —  {len(mac_rows)} photos  ({n_libs} librar{'ies' if n_libs != 1 else 'y'})",
                padding=10,
            )
            mf.grid(row=row_idx, column=0, sticky="ew", **pad)
            mf.columnconfigure(0, weight=1)
            row_idx += 1

            mf_row = 0
            for cat_idx, cat_path in enumerate(cat_paths):
                cat_rows = mac_by_cat[cat_path]
                local_cat = [r for r in cat_rows if not (r["source_file"] or "").startswith("macphotos://")]
                cloud_cat = [r for r in cat_rows if     (r["source_file"] or "").startswith("macphotos://")]

                # ".../Foo.photoslibrary/database/Photos.sqlite" → "Foo"
                try:
                    lib_name = Path(cat_path).parent.parent.name.removesuffix(".photoslibrary")
                except Exception:
                    lib_name = cat_path or "Unknown"

                if cat_idx > 0:
                    ttk.Separator(mf, orient="horizontal").grid(
                        row=mf_row, column=0, sticky="ew", pady=(6, 4)
                    )
                    mf_row += 1

                cf = ttk.Frame(mf)
                cf.grid(row=mf_row, column=0, sticky="ew")
                cf.columnconfigure(1, weight=1)
                mf_row += 1

                if n_libs > 1:
                    ttk.Label(cf, text=lib_name, font=("", 11, "bold")).grid(
                        row=0, column=0, columnspan=2, sticky="w"
                    )
                ttk.Label(cf, text="Add to Photos album:").grid(row=1, column=0, sticky="w", pady=(4 if n_libs > 1 else 0, 0))
                mac_album_var = tk.StringVar(value="PhotoCatalog Discards")
                ttk.Entry(cf, textvariable=mac_album_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4 if n_libs > 1 else 0, 0))

                detail = f"{len(local_cat)} local"
                if cloud_cat:
                    detail += f"  +  {len(cloud_cat)} iCloud-only"
                ttk.Label(cf, text=detail, foreground="gray").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
                warn = "⚠  Photos.app must have this library open." if n_libs > 1 else "⚠  Photos.app must be running."
                ttk.Label(cf, text=warn, foreground="#ff9500").grid(
                    row=3, column=0, columnspan=2, sticky="w", pady=(2, 0)
                )
                ttk.Button(
                    cf, text=f"Add {len(cat_rows)} to album  →",
                    command=lambda rows=cat_rows, av=mac_album_var: self._execute_clean_macphotos(dlg, db_path, rows, av),
                ).grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # ── Section 3: Lightroom ──────────────────────────────────────────────
        if lr_rows:
            catalogs = {r["source_catalog"] for r in lr_rows if r["source_catalog"]}

            lf = ttk.LabelFrame(dlg, text=f"Lightroom  —  {len(lr_rows)} photos  ({len(catalogs)} catalog{'s' if len(catalogs) != 1 else ''})", padding=10)
            lf.grid(row=row_idx, column=0, sticky="ew", **pad)
            lf.columnconfigure(1, weight=1)
            row_idx += 1

            lr_mode = tk.StringVar(value="collection")
            ttk.Radiobutton(lf, text="Add to collection:", variable=lr_mode, value="collection").grid(row=0, column=0, sticky="w")
            lr_collection_var = tk.StringVar(value="PhotoCatalog Discards")
            ttk.Entry(lf, textvariable=lr_collection_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))

            ttk.Radiobutton(lf, text="Add keyword:", variable=lr_mode, value="keyword").grid(row=1, column=0, sticky="w", pady=(4, 0))
            lr_keyword_var = tk.StringVar(value="photocatalog-discard")
            ttk.Entry(lf, textvariable=lr_keyword_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

            ttk.Label(lf, text="⚠  Close Lightroom before applying.", foreground="#ff9500").grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
            )
            ttk.Button(
                lf, text=f"Mark {len(lr_rows)} in Lightroom  →",
                command=lambda rows=lr_rows, mv=lr_mode, cv=lr_collection_var, kv=lr_keyword_var:
                    self._execute_clean_lightroom(dlg, db_path, rows, mv, cv, kv),
            ).grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # ── Close button ──────────────────────────────────────────────────────
        ttk.Button(dlg, text="Close", command=dlg.destroy).grid(
            row=row_idx, column=0, sticky="e", padx=14, pady=(4, 12)
        )

    # ── Clean execution helpers ───────────────────────────────────────────────

    def _execute_clean_folder(self, dlg: tk.Toplevel, db_path: str,
                              rows: list, dest_var: tk.StringVar):
        dest = dest_var.get().strip()
        if not dest:
            messagebox.showerror("Error", "Please choose a destination folder.", parent=dlg)
            return
        dest_path = Path(dest)
        dlg.destroy()
        self._clear()
        self._status.config(text="Running…")
        self._append(f"Moving {len(rows)} Folder photos to {dest}…\n\n")

        def worker():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            total = len(rows)
            moved, failed, missing = 0, 0, 0

            for r in rows:
                if self._cancel.is_set():
                    self._output_q.put("  Cancelled.\n")
                    break
                src  = Path(r["source_file"])
                name = r["original_filename"] or r["file_name"] or src.name
                if not src.exists():
                    self._output_q.put(f"  Not found on disk — removing from catalog: {name}\n")
                    self._db_delete(conn, [r["id"]])
                    missing += 1
                    continue
                date_str = (r["date_original"] or "")[:10]
                if date_str and len(date_str) == 10:
                    y, m, d = date_str.split("-")
                    folder = dest_path / y / m / d
                else:
                    folder = dest_path / "NoDate"
                folder.mkdir(parents=True, exist_ok=True)
                dst = folder / name
                if dst.exists():
                    stem, suffix = Path(name).stem, Path(name).suffix
                    n = 1
                    while dst.exists():
                        dst = folder / f"{stem}_{n}{suffix}"
                        n += 1
                try:
                    shutil.move(str(src), str(dst))
                    self._db_delete(conn, [r["id"]])   # remove immediately — safe to stop
                    moved += 1
                    # Log every file for small batches; throttle to every 50 for large ones
                    if total <= 200 or moved % 50 == 0:
                        self._output_q.put(f"  {moved}/{total}  {src.name}  →  {dst.relative_to(dest_path)}\n")
                    self.after(0, lambda n=moved, t=total: self._status.config(
                        text=f"Moving {n} / {t}…"))
                except Exception as e:
                    self._output_q.put(f"  Failed: {src.name} — {e}\n")
                    failed += 1

            conn.close()
            summary = f"\nDone — moved {moved}"
            if failed:
                summary += f", failed {failed}"
            if missing:
                summary += f"  ({missing} already missing from disk, removed from catalog)"
            self._output_q.put(summary + "\n")
            self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()

    def _execute_clean_macphotos(self, dlg: tk.Toplevel, db_path: str,
                                 rows: list, album_var: tk.StringVar):
        album_name = album_var.get().strip() or "PhotoCatalog Discards"
        dlg.destroy()
        if not messagebox.askyesno(
            "Photos.app Ready?",
            "Before proceeding, make sure Photos.app is:\n\n"
            "  • Open\n"
            "  • Fully loaded (all photos visible, no spinner)\n"
            "  • Idle (iCloud sync complete)\n\n"
            "First run only: macOS may show an 'Allow PhotoCatalog to control\n"
            "Photos?' prompt — watch for it and click OK.\n"
            "Or check System Settings → Privacy & Security →\n"
            "Automation to verify the permission is granted.\n\n"
            "Is Photos.app ready?",
        ):
            return
        self._clear()
        self._status.config(text="Running…")
        self._append(f"Adding {len(rows)} MacPhotos discards to album '{album_name}'…\n\n")

        def worker():
            import photoscript

            # 1. Check Photos is running (triggers Automation TCC prompt on first run)
            self._output_q.put("  Checking Photos.app access…\n")
            try:
                if not photoscript.run_script("photosLibraryIsRunning"):
                    self._output_q.put("  Error: Photos.app is not running — please open it and try again.\n")
                    self._output_q.put(None)
                    return
            except Exception as e:
                self._output_q.put(f"  Error checking Photos.app: {e}\n")
                self._output_q.put(None)
                return

            # 2. Find or create album
            self._output_q.put(f"  Setting up album '{album_name}'…\n")
            try:
                lib = photoscript.PhotosLibrary()
                album = lib.album(album_name) or lib.create_album(album_name)
                self._output_q.put("  Album ready.\n")
            except Exception as e:
                self._output_q.put(f"  Error setting up album: {e}\n")
                self._output_q.put(None)
                return

            # 3. Add photos in batches of 10 (photoscript handles retry internally)
            total = len(rows)
            added, failed_count = 0, 0
            all_ids_done: list[int] = []
            BATCH = 10

            uuid_rows = [(r["mp_uuid"], r) for r in rows if r["mp_uuid"]]
            for r in (r for r in rows if not r["mp_uuid"]):
                self._output_q.put(f"  No UUID — skipping: {r['original_filename'] or r['file_name']}\n")

            for batch_start in range(0, len(uuid_rows), BATCH):
                if self._cancel.is_set():
                    self._output_q.put("  Cancelled.\n")
                    break
                batch = uuid_rows[batch_start : batch_start + BATCH]

                photos = []
                batch_rows: dict[str, dict] = {}
                for uuid, row in batch:
                    try:
                        p = photoscript.Photo(uuid)
                        photos.append(p)
                        batch_rows[p._uuid] = row
                    except ValueError:
                        self._output_q.put(f"  Not found in Photos: {row['original_filename'] or row['file_name']}\n")
                        failed_count += 1

                if not photos:
                    continue

                try:
                    added_photos = album.add(photos)
                    added_uuids = {p._uuid for p in added_photos}
                    added += len(added_photos)
                    failed_count += len(photos) - len(added_photos)
                    all_ids_done.extend(batch_rows[u]["id"] for u in added_uuids if u in batch_rows)
                except Exception as e:
                    self._output_q.put(f"  Batch failed: {e}\n")
                    failed_count += len(photos)

                processed = batch_start + len(batch)
                if processed % 100 < BATCH or processed >= len(uuid_rows):
                    self._output_q.put(f"  {added} / {total} added…\n")
                self.after(0, lambda n=added, t=total: self._status.config(
                    text=f"Adding to album {n} / {t}…"))

            # Delete from catalog only after all Photos work is done
            if all_ids_done:
                self._output_q.put(f"  Updating catalog ({len(all_ids_done)} entries)…\n")
                conn = sqlite3.connect(db_path)
                self._db_delete(conn, all_ids_done)
                conn.close()

            self._output_q.put(f"\nDone — added {added} to '{album_name}'")
            if failed_count:
                self._output_q.put(f", {failed_count} not found in Photos")
            self._output_q.put("\n")
            self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()

    def _execute_clean_lightroom(self, dlg: tk.Toplevel, db_path: str,
                                 rows: list, mode_var: tk.StringVar,
                                 collection_var: tk.StringVar, keyword_var: tk.StringVar):
        mode = mode_var.get()
        name = (collection_var if mode == "collection" else keyword_var).get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a collection or keyword name.", parent=dlg)
            return
        dlg.destroy()
        self._clear()
        self._status.config(text="Running…")
        label = "collection" if mode == "collection" else "keyword"
        self._append(f"Marking {len(rows)} Lightroom discards with {label} '{name}'…\n\n")

        def worker():
            from PhotoCatalog.sources.lightroom import add_to_collection, add_keyword
            from collections import defaultdict

            # Group by catalog path (there may be multiple .lrcat files)
            by_cat: dict[str, list] = defaultdict(list)
            for r in rows:
                by_cat[r["source_catalog"] or ""].append(r)

            total_photos = len(rows)
            total_added, total_skipped = 0, []
            all_ids_done: list[int] = []  # accumulate; delete from DB only after all catalogs done
            for lrcat_path, cat_rows in by_cat.items():
                if self._cancel.is_set():
                    self._output_q.put("  Cancelled.\n")
                    break
                if not lrcat_path or not Path(lrcat_path).exists():
                    self._output_q.put(f"  Catalog not found: {lrcat_path or '(unknown)'}\n")
                    total_skipped.extend(r["source_file"] for r in cat_rows)
                    continue
                source_files = [r["source_file"] for r in cat_rows]
                try:
                    if mode == "collection":
                        added, skipped = add_to_collection(lrcat_path, source_files, name)
                    else:
                        added, skipped = add_keyword(lrcat_path, source_files, name)
                    self._output_q.put(f"  {Path(lrcat_path).name}: {added} marked, {len(skipped)} not found\n")
                    total_added += added
                    total_skipped.extend(skipped)
                    self.after(0, lambda n=total_added, t=total_photos: self._status.config(
                        text=f"Marking {n} / {t}…"))
                    all_ids_done.extend(r["id"] for r in cat_rows if r["source_file"] not in skipped)
                except Exception as e:
                    self._output_q.put(f"  Error writing {Path(lrcat_path).name}: {e}\n")

            # Delete from catalog only after all Lightroom work is done
            if all_ids_done:
                self._output_q.put(f"  Updating catalog ({len(all_ids_done)} entries)…\n")
                conn = sqlite3.connect(db_path)
                self._db_delete(conn, all_ids_done)
                conn.close()

            self._output_q.put(f"\nDone — {total_added} marked as {label} '{name}'")
            if total_skipped:
                self._output_q.put(f", {len(total_skipped)} not matched in catalog")
            self._output_q.put("\n")
            self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()

    # ── Edit Catalog tab ─────────────────────────────────────────────────────

    def _build_edit_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Edit Catalog  ")
        tab.columnconfigure(0, weight=1)

        rf = ttk.LabelFrame(tab, text="Change Catalog Root", padding=10)
        rf.grid(row=0, column=0, sticky="ew")
        rf.columnconfigure(1, weight=1)

        ttk.Label(
            rf,
            text="Rewrites file paths in the catalog when a drive is remounted at a different\n"
                 "location. Select an import to scope the rewrite to that source only.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # ── Imports list ─────────────────────────────────────────────────────
        ttk.Label(rf, text="Imports:").grid(row=1, column=0, sticky="nw")
        lf = ttk.Frame(rf)
        lf.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))
        lf.columnconfigure(0, weight=1)
        self._roots_lb = tk.Listbox(
            lf, height=5, selectmode="browse",
            exportselection=False, font=("Menlo", 11),
        )
        vsb = ttk.Scrollbar(lf, orient="vertical",   command=self._roots_lb.yview)
        hsb = ttk.Scrollbar(lf, orient="horizontal", command=self._roots_lb.xview)
        self._roots_lb.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._roots_lb.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self._roots_lb.bind("<<ListboxSelect>>", self._on_root_select)

        btn_col = ttk.Frame(rf)
        btn_col.grid(row=1, column=2, sticky="ne", padx=(6, 0))
        ttk.Button(btn_col, text="↺", width=3, command=self._refresh_roots).pack(pady=(0, 4))
        ttk.Button(btn_col, text="×", width=3, command=self._clear_root_scope).pack()

        # ── Scope indicator ──────────────────────────────────────────────────
        self._scope_lbl = ttk.Label(rf, text="Scope: all imports", foreground="gray", font=("", 10))
        self._scope_lbl.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 8))

        # ── Old / New prefix ─────────────────────────────────────────────────
        ttk.Label(rf, text="Old prefix:").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self._rewrite_old = tk.StringVar()
        ttk.Entry(rf, textvariable=self._rewrite_old).grid(
            row=3, column=1, sticky="ew", padx=(8, 6), pady=(0, 4)
        )
        ttk.Button(
            rf, text="Browse…",
            command=lambda: self._rewrite_old.set(
                filedialog.askdirectory(title="Select old root folder") or self._rewrite_old.get()
            ),
        ).grid(row=3, column=2, pady=(0, 4))

        ttk.Label(rf, text="New prefix:").grid(row=4, column=0, sticky="w", pady=(0, 4))
        self._rewrite_new = tk.StringVar()
        ttk.Entry(rf, textvariable=self._rewrite_new).grid(
            row=4, column=1, sticky="ew", padx=(8, 6), pady=(0, 4)
        )
        ttk.Button(
            rf, text="Browse…",
            command=lambda: self._rewrite_new.set(
                filedialog.askdirectory(title="Select new root folder") or self._rewrite_new.get()
            ),
        ).grid(row=4, column=2, pady=(0, 4))

        # ── Info label + action buttons ──────────────────────────────────────
        self._rewrite_info = ttk.Label(rf, text="", foreground="gray")
        self._rewrite_info.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btn_row = ttk.Frame(rf)
        btn_row.grid(row=5, column=2, sticky="e", pady=(6, 0))
        ttk.Button(btn_row, text="Preview", command=self._preview_rewrite).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="Apply  →", command=self._apply_rewrite).pack(side="left")

        # State
        self._rewrite_scope_cat: str = ""
        self._roots_data: list[tuple[str, str, str]] = []  # (source_catalog, file_prefix, label)

        self.db_path.trace_add("write", lambda *_: self.after(100, self._refresh_roots))
        self.after(200, self._refresh_roots)

    @staticmethod
    def _import_info(source_type: str, cat: str) -> tuple[str, str]:
        """Return (file_prefix, scope_label) for a source_catalog import.

        file_prefix is the path component shared by both source_catalog and
        source_file for this import — what the user will type as "Old prefix".
        """
        if source_type == "MacPhotos":
            # cat = …/Foo.photoslibrary/database/Photos.sqlite
            # both source_catalog and source_file live under the bundle root
            lib_root = str(Path(cat).parent.parent)
            name = Path(lib_root).name.removesuffix(".photoslibrary")
            return lib_root, f"MacPhotos: {name}"
        if source_type == "Lightroom":
            # cat = …/catalog.lrcat  — files live anywhere on the same volume
            parts = Path(cat).parts
            vol_root = str(Path(*parts[:3])) if len(parts) >= 3 else str(Path(cat).parent)
            return vol_root, f"Lightroom: {Path(cat).stem}"
        # Folder: source_catalog IS the root folder
        return cat, f"Folder: {Path(cat).name}"

    def _refresh_roots(self):
        db_path = self.db_path.get().strip()
        self._roots_lb.delete(0, "end")
        self._roots_data = []
        self._rewrite_scope_cat = ""
        if not db_path or not Path(db_path).exists():
            return
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute("""
                SELECT source_type, source_catalog, COUNT(*)
                FROM photos
                WHERE source_catalog IS NOT NULL
                GROUP BY source_catalog
                ORDER BY source_type, source_catalog
            """).fetchall()
            conn.close()
        except Exception:
            return
        badges = {"MacPhotos": "Photos ", "Lightroom": "LR     ", "Folder": "Folder "}
        for source_type, cat, n in rows:
            file_prefix, label = self._import_info(source_type, cat)
            badge  = badges.get(source_type, source_type[:7].ljust(7))
            name   = label.split(": ", 1)[1]
            line   = f"{badge}  {name:<28}  {n:>7,}   {file_prefix}"
            self._roots_lb.insert("end", line)
            self._roots_data.append((cat, file_prefix, label))

    def _on_root_select(self, _event=None):
        sel = self._roots_lb.curselection()
        if not sel or sel[0] >= len(self._roots_data):
            return
        cat, file_prefix, label = self._roots_data[sel[0]]
        self._rewrite_old.set(file_prefix)
        self._rewrite_scope_cat = cat
        self._scope_lbl.config(text=f"Scope: {label}", foreground="#007aff")
        self._rewrite_info.config(text="")

    def _clear_root_scope(self):
        self._roots_lb.selection_clear(0, "end")
        self._rewrite_scope_cat = ""
        self._scope_lbl.config(text="Scope: all imports", foreground="gray")
        self._rewrite_old.set("")
        self._rewrite_info.config(text="")

    def _rewrite_count(self, conn: sqlite3.Connection, old: str) -> int:
        scope = self._rewrite_scope_cat
        if scope:
            return conn.execute(
                "SELECT COUNT(*) FROM photos "
                "WHERE source_catalog = ? "
                "  AND (source_file LIKE ? || '/%' OR source_file = ? "
                "    OR source_catalog LIKE ? || '/%' OR source_catalog = ?)",
                (scope, old, old, old, old),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM photos "
            "WHERE source_file LIKE ? || '/%' OR source_file = ? "
            "   OR source_catalog LIKE ? || '/%' OR source_catalog = ?",
            (old, old, old, old),
        ).fetchone()[0]

    def _preview_rewrite(self):
        db_path = self.db_path.get().strip()
        old = self._rewrite_old.get().rstrip("/")
        if not db_path or not Path(db_path).exists():
            self._rewrite_info.config(text="No catalog selected.", foreground="red")
            return
        if not old:
            self._rewrite_info.config(text="Enter old prefix first.", foreground="red")
            return
        try:
            conn = sqlite3.connect(db_path)
            n = self._rewrite_count(conn, old)
            conn.close()
            self._rewrite_info.config(text=f"{n:,} photos would be updated.", foreground="gray")
        except Exception as e:
            self._rewrite_info.config(text=f"Error: {e}", foreground="red")

    def _apply_rewrite(self):
        db_path = self.db_path.get().strip()
        old = self._rewrite_old.get().rstrip("/")
        new = self._rewrite_new.get().rstrip("/")
        if not db_path or not Path(db_path).exists():
            messagebox.showerror("Error", "No catalog selected.")
            return
        if not old or not new:
            messagebox.showerror("Error", "Both old and new prefix are required.")
            return
        if old == new:
            messagebox.showinfo("No change", "Old and new prefix are identical.")
            return
        scope = self._rewrite_scope_cat
        try:
            conn = sqlite3.connect(db_path)
            n = self._rewrite_count(conn, old)
            conn.close()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if n == 0:
            messagebox.showinfo("No match", f"No photos found with prefix:\n{old}")
            return
        scope_note = (
            f"\nScoped to: {self._scope_lbl.cget('text').removeprefix('Scope: ')}\n"
            if scope else ""
        )
        if not messagebox.askyesno(
            "Confirm Rewrite",
            f"Update {n:,} photo paths?{scope_note}\n\n"
            f"  {old}\n  →  {new}\n\n"
            "This cannot be undone.",
        ):
            return
        # CASE expression handles both prefix match (sub-paths) and exact match
        # (needed for Folder imports where source_catalog = the prefix itself).
        n_len = len(old)
        sql_scoped = """
            UPDATE photos SET
              source_file = CASE
                WHEN source_file LIKE ? || '/%' OR source_file = ?
                THEN ? || SUBSTR(source_file, ? + 1) ELSE source_file END,
              source_catalog = CASE
                WHEN source_catalog LIKE ? || '/%' OR source_catalog = ?
                THEN ? || SUBSTR(source_catalog, ? + 1) ELSE source_catalog END
            WHERE source_catalog = ?
              AND (source_file    LIKE ? || '/%' OR source_file    = ?
                OR source_catalog LIKE ? || '/%' OR source_catalog = ?)
        """
        sql_global = """
            UPDATE photos SET
              source_file = CASE
                WHEN source_file LIKE ? || '/%' OR source_file = ?
                THEN ? || SUBSTR(source_file, ? + 1) ELSE source_file END,
              source_catalog = CASE
                WHEN source_catalog LIKE ? || '/%' OR source_catalog = ?
                THEN ? || SUBSTR(source_catalog, ? + 1) ELSE source_catalog END
            WHERE source_file    LIKE ? || '/%' OR source_file    = ?
               OR source_catalog LIKE ? || '/%' OR source_catalog = ?
        """
        try:
            conn = sqlite3.connect(db_path)
            if scope:
                conn.execute(sql_scoped,
                    (old, old, new, n_len, old, old, new, n_len,
                     scope, old, old, old, old))
            else:
                conn.execute(sql_global,
                    (old, old, new, n_len, old, old, new, n_len,
                     old, old, old, old))
            conn.commit()
            conn.close()
            self._rewrite_info.config(text=f"Done — {n:,} photos updated.", foreground="green")
            self._refresh_roots()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── About / Updates tab ───────────────────────────────────────────────────

    def _build_about_tab(self):
        tab = ttk.Frame(self._nb, padding=14)
        self._nb.add(tab, text="  About  ")
        tab.columnconfigure(0, weight=1)

        ttk.Label(tab, text="PhotoCatalog", font=("", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(tab, text=f"Version  {__version__}", foreground="gray").grid(
            row=1, column=0, sticky="w", pady=(2, 12)
        )
        ttk.Label(
            tab,
            text="Finds and safely removes duplicate photos across Mac Photos,\n"
                 "Lightroom Classic, and disk folders.",
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 12))

        sep = ttk.Separator(tab, orient="horizontal")
        sep.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(tab, text="Updates", font=("", 13, "bold")).grid(
            row=4, column=0, sticky="w", pady=(0, 6)
        )

        status_row = ttk.Frame(tab)
        status_row.grid(row=5, column=0, sticky="w", pady=(0, 8))
        self._update_status = ttk.Label(status_row, text="Checking for updates…")
        self._update_status.pack(side="left")

        btn_row = ttk.Frame(tab)
        btn_row.grid(row=6, column=0, sticky="w", pady=(0, 8))
        ttk.Button(btn_row, text="Check for updates", command=self._check_updates_manual).pack(
            side="left"
        )
        self._dl_btn = ttk.Button(
            btn_row, text="Download & Install", command=self._download_and_install
        )
        self._dl_btn.pack(side="left", padx=(10, 0))
        self._dl_btn.pack_forget()  # hidden until an update is found

        self._dl_progress = ttk.Label(tab, text="", foreground="gray")
        self._dl_progress.grid(row=7, column=0, sticky="w")

        sep2 = ttk.Separator(tab, orient="horizontal")
        sep2.grid(row=8, column=0, sticky="ew", pady=12)

        ttk.Label(tab, text="GitHub repository", font=("", 11)).grid(
            row=9, column=0, sticky="w"
        )
        link = ttk.Label(
            tab,
            text=f"https://github.com/{GITHUB_REPO}",
            foreground="#007aff",
            cursor="hand2",
        )
        link.grid(row=10, column=0, sticky="w", pady=(2, 0))
        link.bind("<Button-1>", lambda _: webbrowser.open(f"https://github.com/{GITHUB_REPO}"))

    # ── Update logic ──────────────────────────────────────────────────────────

    def _fetch_latest_release(self):
        """Background thread: fetch latest release from GitHub API."""
        try:
            import ssl
            try:
                import certifi
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                ssl_ctx = ssl.create_default_context()
            req = urllib.request.Request(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"PhotoCatalog/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=8, context=ssl_ctx) as r:
                data = json.loads(r.read())

            tag = data.get("tag_name", "").lstrip("v")
            assets = data.get("assets", [])
            dmg_asset = next(
                (a for a in assets if a["name"].endswith(".dmg")), None
            )
            self._latest_version = tag
            self._update_asset_url = dmg_asset["browser_download_url"] if dmg_asset else None
            self.after(0, self._refresh_update_ui)
        except Exception as e:
            msg = f"Could not check for updates: {e}"
            self.after(0, lambda: self._update_status.config(text=msg))

    def _check_updates_manual(self):
        self._update_status.config(text="Checking…")
        self._dl_btn.pack_forget()
        threading.Thread(target=self._fetch_latest_release, daemon=True).start()

    def _refresh_update_ui(self):
        latest = self._latest_version
        if not latest:
            self._update_status.config(text="No release information available.")
            return

        if latest == __version__:
            self._update_status.config(
                text=f"You're up to date  (v{__version__})"
            )
            self._dl_btn.pack_forget()
        else:
            self._update_status.config(
                text=f"v{latest} is available!  (you have v{__version__})"
            )
            if self._update_asset_url:
                self._dl_btn.pack(side="left", padx=(10, 0))
            else:
                self._update_status.config(
                    text=self._update_status.cget("text") + "  — no DMG asset found on release"
                )

    def _download_and_install(self):
        if not self._update_asset_url:
            messagebox.showerror("Error", "No DMG download URL available.")
            return

        fname = self._update_asset_url.split("/")[-1]
        dest = Path.home() / "Downloads" / fname

        self._dl_btn.config(state="disabled")
        self._dl_progress.config(text=f"Downloading {fname}…")

        def download():
            try:
                import ssl
                try:
                    import certifi
                    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                except ImportError:
                    ssl_ctx = ssl.create_default_context()

                req = urllib.request.Request(
                    self._update_asset_url,
                    headers={"User-Agent": f"PhotoCatalog/{__version__}"},
                )
                with urllib.request.urlopen(req, context=ssl_ctx) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = min(100, downloaded * 100 // total)
                                txt = f"Downloading {fname}…  {pct}%"
                            else:
                                txt = f"Downloading {fname}…  {downloaded // 1_048_576} MB"
                            self.after(0, lambda t=txt: self._dl_progress.config(text=t))
                self.after(0, lambda: self._install_dmg(dest))
            except Exception as e:
                err = str(e)
                self.after(0, lambda: (
                    self._dl_progress.config(text=f"Download failed: {err}"),
                    self._dl_btn.config(state="normal"),
                    messagebox.showerror("Download failed", err),
                ))

        threading.Thread(target=download, daemon=True).start()

    def _install_dmg(self, dmg_path: Path):
        self._dl_progress.config(text="Mounting DMG…")
        try:
            result = subprocess.run(
                ["hdiutil", "attach", "-nobrowse", str(dmg_path)],
                capture_output=True, text=True, check=True,
            )
            mount_point = None
            for line in result.stdout.strip().splitlines():
                if "/Volumes/" in line:
                    mount_point = line.split("\t")[-1].strip()

            if not mount_point:
                raise RuntimeError("Could not determine mount point from hdiutil output.")

            apps = glob.glob(os.path.join(mount_point, "*.app"))
            if not apps:
                raise RuntimeError("No .app bundle found inside the DMG.")

            app_src = apps[0]
            app_name = os.path.basename(app_src)
            dest_path = str(INSTALL_DIR / app_name)

            self._dl_progress.config(text=f"Installing to {dest_path}…")

            # Try direct copy first; fall back to osascript for admin prompt
            try:
                if os.path.exists(dest_path):
                    shutil.rmtree(dest_path)
                shutil.copytree(app_src, dest_path)
            except PermissionError:
                script = (
                    f'do shell script "rm -rf {dest_path!r} && '
                    f'cp -R {app_src!r} {dest_path!r}" '
                    f"with administrator privileges"
                )
                subprocess.run(["osascript", "-e", script], check=True)

            subprocess.run(["hdiutil", "detach", mount_point], capture_output=True)

            self._dl_progress.config(text="Installed. Relaunching…")
            self.after(800, lambda: self._relaunch(dest_path))

        except Exception as e:
            self._dl_progress.config(text=f"Install failed: {e}")
            self._dl_btn.config(state="normal")
            # Fall back: just open the DMG so user can install manually
            subprocess.run(["open", str(dmg_path)])
            messagebox.showinfo(
                "Manual install",
                f"Automatic install failed:\n{e}\n\n"
                "The DMG has been opened. Drag PhotoCatalog to /Applications manually.",
            )

    def _on_close(self):
        # Terminate any running subprocess (e.g. serve-duplicates) before exiting.
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        # Force-exit so any remaining daemon threads don't keep the process alive.
        import os as _os
        _os._exit(0)

    def _relaunch(self, app_path: str):
        subprocess.Popen(["open", "-n", app_path])
        self.destroy()

    # ── output pane ───────────────────────────────────────────────────────────

    def _build_output(self):
        frame = ttk.LabelFrame(self, text="Output", padding=4)
        frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(0, 4))
        self._status = ttk.Label(bar, text="Ready")
        self._status.pack(side="left")
        ttk.Button(bar, text="Stop", command=self._stop).pack(side="right", padx=(4, 0))
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="right")

        self._log = scrolledtext.ScrolledText(
            frame,
            height=12,
            font=("Menlo", 11),
            state="disabled",
            wrap="word",
            bg="#1c1c1e",
            fg="#e5e5ea",
            insertbackground="#e5e5ea",
        )
        self._log.pack(fill="both", expand=True)

    def _clear(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")
        self._cancel.clear()

    def _stop(self):
        self._cancel.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._status.config(text="Stopping…")
        # If a thread-based worker is blocked in an AppleScript/IO call it won't
        # check _cancel until that call returns.  Force-unblock the UI after 15 s.
        def _force_stop():
            if self._cancel.is_set():
                self._output_q.put("  (worker unresponsive — UI force-stopped; background thread will exit when Photos.app responds)\n")
                self._output_q.put(None)
        self.after(15000, _force_stop)

    def _append(self, text: str):
        self._log.config(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    def _poll_output(self):
        try:
            while True:
                item = self._output_q.get_nowait()
                if item is None:
                    if self._cancel.is_set():
                        self._status.config(text="Stopped")
                    else:
                        rc = self._proc.returncode if self._proc else 0
                        self._status.config(text=f"Done  (exit {rc})" if rc else "Done")
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.after(80, self._poll_output)

    # ── subprocess runner ─────────────────────────────────────────────────────

    def _run_cmd(self, cmd: list[str], caffeinate: bool = False):
        if self._proc and self._proc.poll() is None:
            if not messagebox.askyesno(
                "Already running",
                "A command is already running. Stop it and start the new one?",
            ):
                return
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

        self._record_catalog_used(self.db_path.get().strip())
        self._clear()
        self._status.config(text="Running…")
        display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self._append(f"$ {display}\n\n")

        # Create the process here (main thread) so self._proc is set before
        # _on_close or _stop can race against it.
        proc = subprocess.Popen(
            (["caffeinate", "-i"] if caffeinate else []) + cmd,
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._proc = proc

        def worker():
            # Use local `proc` reference so stdout reading is not affected by
            # self._proc being reassigned when the next command starts.
            for line in proc.stdout:
                self._output_q.put(line)
            proc.wait()
            # Only signal completion if we are still the current process;
            # prevents a spurious "Done" when this proc was stopped early.
            if self._proc is proc:
                self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    PhotoCatalogApp().mainloop()
