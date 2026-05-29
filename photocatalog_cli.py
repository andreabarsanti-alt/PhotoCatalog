#!/usr/bin/env python3
"""
CLI helper for PhotoCatalog — invoked by the GUI as a subprocess.

Placed as a plain binary (PhotoCatalogCLI) in Contents/MacOS/ alongside the
main PhotoCatalog app binary. Because it is NOT the CFBundleExecutable, macOS
does not go through app-launch infrastructure for it, so no Dock icon or extra
window appears when the GUI spawns it.

Usage:
    PhotoCatalogCLI build-catalog [args...]
    PhotoCatalogCLI find-duplicates [args...]
    PhotoCatalogCLI serve-duplicates [args...]
"""
import sys

if len(sys.argv) < 2:
    print("Usage: PhotoCatalogCLI <subcmd> [args...]", file=sys.stderr)
    sys.exit(1)

subcmd = sys.argv[1]
sys.argv = [sys.argv[0]] + sys.argv[2:]

if subcmd == "build-catalog":
    from PhotoCatalog.build_catalog import main
elif subcmd == "find-duplicates":
    from PhotoCatalog.find_duplicates import main
elif subcmd == "serve-duplicates":
    from PhotoCatalog.serve_duplicates import main
else:
    print(f"Unknown sub-command: {subcmd}", file=sys.stderr)
    sys.exit(1)

main()
