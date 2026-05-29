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

    _known = {"build-catalog", "find-duplicates", "serve-duplicates"}
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
import subprocess
import tempfile
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
        self.geometry("780x720")
        self.minsize(660, 560)

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
        self._latest_version: str | None = None
        self._update_asset_url: str | None = None

        self._build_db_bar()
        self._build_notebook()
        self._build_output()
        self._poll_output()

        # Silently check for updates in background
        threading.Thread(target=self._fetch_latest_release, daemon=True).start()

    # ── shared DB bar ─────────────────────────────────────────────────────────

    def _build_db_bar(self):
        frame = ttk.LabelFrame(self, text="Catalog Database (.db file)", padding=6)
        frame.pack(fill="x", padx=12, pady=(12, 4))
        frame.columnconfigure(0, weight=1)

        self.db_path = tk.StringVar(
            value=str(DEFAULT_CATALOG_DIR / "MyCatalog" / "MyCatalog.db")
        )
        ttk.Entry(frame, textvariable=self.db_path).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(frame, text="Browse…", command=self._browse_db).grid(row=0, column=1)

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

    # ── notebook ──────────────────────────────────────────────────────────────

    def _build_notebook(self):
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="x", padx=12, pady=4)
        self._build_ingest_tab()
        self._build_find_tab()
        self._build_browse_tab()
        self._build_about_tab()

    # ── Build Catalog tab ─────────────────────────────────────────────────────

    def _build_ingest_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Build Catalog  ")
        tab.columnconfigure(0, weight=1)

        sf = ttk.LabelFrame(tab, text="Source type", padding=6)
        sf.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._source = tk.StringVar(value="MacPhotos")
        for val in ("MacPhotos", "Lightroom", "Folder"):
            ttk.Radiobutton(
                sf, text=val, value=val, variable=self._source,
                command=self._refresh_ingest,
            ).pack(side="left", padx=16)

        self._pf = ttk.LabelFrame(tab, text="Source path", padding=6)
        self._pf.grid(row=1, column=0, sticky="ew", pady=(0, 6))
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

        of = ttk.LabelFrame(tab, text="Options", padding=6)
        of.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        of.columnconfigure(1, weight=1)
        self._fresh = tk.BooleanVar()
        ttk.Checkbutton(
            of,
            text="Fresh start — drop and recreate the database before ingesting",
            variable=self._fresh,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(of, text="Hash threads:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        workers_row = ttk.Frame(of)
        workers_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self._workers = tk.StringVar(value="0")
        ttk.Spinbox(workers_row, textvariable=self._workers, from_=0, to=32, width=4).pack(
            side="left"
        )
        ttk.Label(
            workers_row, text="  (0 = auto, uses all available cores)", foreground="gray"
        ).pack(side="left")

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

        ttk.Button(tab, text="▶  Build Catalog", command=self._run_ingest).grid(
            row=4, column=0, pady=10
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
        else:
            title = (
                "Select Photos library (.photoslibrary)"
                if st == "MacPhotos"
                else "Select folder to scan"
            )
            p = filedialog.askdirectory(title=title)
        if p:
            self._src_path.set(p)

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
        if self._download.get():
            cmd.append("--download")
        dl = self._dl_dir.get().strip()
        if dl:
            cmd += ["--download-dir", dl]
        lim = self._dl_limit.get().strip()
        if lim:
            cmd += ["--download-limit", lim]
        w = self._workers.get().strip()
        if w and w != "0":
            cmd += ["--workers", w]

        self._run_cmd(cmd, caffeinate=True)

    # ── Find Duplicates tab ───────────────────────────────────────────────────

    def _build_find_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Find Duplicates  ")
        tab.columnconfigure(0, weight=1)

        sf = ttk.LabelFrame(tab, text="Strategy", padding=6)
        sf.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._strategy = tk.StringVar(value="unified")
        for val, label in [
            ("unified",  "Unified  (recommended) — combines perceptual hash + metadata + filename"),
            ("phash",    "Perceptual hash only — exact pixel-level matches"),
            ("metadata", "Metadata only — same dimensions + date + file type"),
            ("filename", "Filename only — same filename stem + date minute"),
        ]:
            ttk.Radiobutton(sf, text=label, value=val, variable=self._strategy).pack(
                anchor="w", pady=2
            )

        ttk.Button(tab, text="▶  Find Duplicates", command=self._run_find).grid(
            row=1, column=0, pady=10
        )

    def _run_find(self):
        db = self.db_path.get().strip()
        if not db:
            messagebox.showerror("Error", "Catalog database path is required.")
            return
        prefix = _python_cmd()
        if getattr(sys, "frozen", False):
            cmd = prefix + ["find-duplicates", "--db", db,
                            "--strategy", self._strategy.get()]
        else:
            cmd = prefix + ["PhotoCatalog.find_duplicates", "--db", db,
                            "--strategy", self._strategy.get()]
        self._run_cmd(cmd, caffeinate=True)

    # ── Browse Duplicates tab ─────────────────────────────────────────────────

    def _build_browse_tab(self):
        tab = ttk.Frame(self._nb, padding=10)
        self._nb.add(tab, text="  Browse Duplicates  ")
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
            req = urllib.request.Request(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"PhotoCatalog/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
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
            msg = f"Could not check for updates ({type(e).__name__})"
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
            def _progress(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(100, block_num * block_size * 100 // total_size)
                    self.after(0, lambda p=pct: self._dl_progress.config(
                        text=f"Downloading {fname}…  {p}%"
                    ))

            try:
                urllib.request.urlretrieve(self._update_asset_url, dest, _progress)
                self.after(0, lambda: self._install_dmg(dest))
            except Exception as e:
                self.after(0, lambda: (
                    self._dl_progress.config(text=f"Download failed: {e}"),
                    self._dl_btn.config(state="normal"),
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

    def _stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._status.config(text="Stopped")

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

        self._clear()
        self._status.config(text="Running…")
        display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self._append(f"$ {display}\n\n")

        def worker():
            self._proc = subprocess.Popen(
                (["caffeinate", "-i"] if caffeinate else []) + cmd,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in self._proc.stdout:
                self._output_q.put(line)
            self._proc.wait()
            self._output_q.put(None)

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    PhotoCatalogApp().mainloop()
