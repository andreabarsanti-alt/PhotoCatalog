#!/bin/bash
# PhotoCatalog — development launcher
# Double-click in Finder to open the GUI from the project venv.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/photocatalog_gui.py"
