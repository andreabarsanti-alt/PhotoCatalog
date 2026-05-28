# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for PhotoCatalog.app
#
# Build:
#   pip install pyinstaller
#   pyinstaller PhotoCatalog.spec
#
# Output: dist/PhotoCatalog.app

import subprocess
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Pull the version from the package so the DMG name stays in sync
version = subprocess.check_output(
    [".venv/bin/python3", "-c",
     "from PhotoCatalog import __version__; print(__version__)"],
    text=True,
).strip()

# collect_all() grabs the package's datas, binaries, and all submodule imports.
# This is necessary for packages that PyInstaller can't fully trace statically
# (e.g. osxphotos uses importlib, dynamic attribute access, and pyobjc bridges).
_packages = [
    "osxphotos",
    "osxmetadata",
    "photoscript",
    "cgmetadata",
    # pyobjc framework bundles — each contains .so bridges loaded at runtime
    "objc",
    "Foundation",
    "AppKit",
    "Cocoa",
    "CoreServices",
    "AVFoundation",
    "Quartz",
    "Vision",
    "Photos",
]

extra_datas, extra_binaries, extra_hiddenimports = [], [], []
for pkg in _packages:
    d, b, h = collect_all(pkg)
    extra_datas += d
    extra_binaries += b
    extra_hiddenimports += h

a = Analysis(
    ["photocatalog_gui.py"],
    pathex=["."],
    binaries=extra_binaries,
    datas=[
        # Include the entire PhotoCatalog package (modules + any data files)
        ("PhotoCatalog", "PhotoCatalog"),
    ] + extra_datas,
    hiddenimports=extra_hiddenimports + [
        # imaging
        "PIL",
        "PIL.Image",
        "pillow_heif",
        "imagehash",
        # stdlib used at runtime
        "sqlite3",
        "http.server",
        "urllib.request",
        "webbrowser",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoCatalog",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,       # native arch
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PhotoCatalog",
)

app = BUNDLE(
    coll,
    name="PhotoCatalog.app",
    icon=None,              # swap in an .icns file here when you have one
    bundle_identifier="com.andreabarsanti.photocatalog",
    version=version,
    info_plist={
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    },
)
