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
    PhotoCatalogCLI compute-hashes [args...]
"""
import multiprocessing
import sys

# Required for frozen (PyInstaller) binaries: lets Python's multiprocessing
# recognise when this binary is being re-spawned as a worker subprocess and
# handle it correctly instead of running main code again.
multiprocessing.freeze_support()

# Python's multiprocessing resource tracker spawns:
#   <this binary> -c "from multiprocessing.resource_tracker import main;main(FD)"
# Detect and delegate to Python exec so it works correctly inside the frozen binary.
if len(sys.argv) >= 3 and sys.argv[1] == "-c":
    exec(sys.argv[2])  # noqa: S102
    sys.exit(0)

_KNOWN = {"build-catalog", "find-duplicates", "serve-duplicates", "compute-hashes"}

# The PyInstaller bootloader may inject flags like -B before the subcommand.
# Only strip flags that are NOT known subcommands.
_args = sys.argv[1:]
while _args and _args[0].startswith("-") and _args[0] not in _KNOWN:
    _args = _args[1:]

if not _args or _args[0] not in _KNOWN:
    known = ", ".join(sorted(_KNOWN))
    print(f"Usage: PhotoCatalogCLI <subcmd> [args...]", file=sys.stderr)
    print(f"  Known subcommands: {known}", file=sys.stderr)
    if _args:
        print(f"  Unknown sub-command: {_args[0]!r}", file=sys.stderr)
    else:
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
elif subcmd == "compute-hashes":
    from PhotoCatalog.enrich import main

main()
