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

_KNOWN = {"build-catalog", "find-duplicates", "serve-duplicates"}

# Strip any leading Python-interpreter flags that the bootloader may inject
# (e.g. -B / --pythonfaulthandler) before looking for the subcommand.
_args = sys.argv[1:]
while _args and _args[0].startswith("-") and _args[0] not in _KNOWN:
    _args = _args[1:]

if not _args:
    print("Usage: PhotoCatalogCLI <subcmd> [args...]", file=sys.stderr)
    print(f"  (received sys.argv: {sys.argv!r})", file=sys.stderr)
    sys.exit(1)

subcmd = _args[0]
sys.argv = [sys.argv[0]] + _args[1:]

if subcmd == "build-catalog":
    from PhotoCatalog.build_catalog import main
elif subcmd == "find-duplicates":
    from PhotoCatalog.find_duplicates import main
elif subcmd == "serve-duplicates":
    from PhotoCatalog.serve_duplicates import main
else:
    print(f"Unknown sub-command: {subcmd!r}", file=sys.stderr)
    print(f"  (full sys.argv was: {sys.argv!r})", file=sys.stderr)
    sys.exit(1)

main()
