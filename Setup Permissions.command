#!/bin/bash
# PhotoCatalog — Permission Setup
# Double-click this file after installing PhotoCatalog if it can't access
# Photos, external drives, or control Photos.app.

echo ""
echo "PhotoCatalog — Permission Setup"
echo "================================"
echo ""
echo "PhotoCatalog needs three permissions to work correctly:"
echo ""
echo "  1. Automation → Photos   (to add duplicates to a Photos album)"
echo "  2. Photos                (to read your Photos library)"
echo "  3. Full Disk Access      (to access photos on external drives)"
echo ""
echo "Opening System Settings › Privacy & Security…"
echo "Grant access to PhotoCatalog in each panel that opens."
echo ""

SW_VERS=$(sw_vers -productVersion | cut -d. -f1)

if [ "$SW_VERS" -ge 13 ]; then
    BASE="x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
else
    BASE="x-apple.systempreferences:com.apple.preference.security"
fi

open "${BASE}?Privacy_Automation"
sleep 0.5
open "${BASE}?Privacy_Photos"
sleep 0.5
open "${BASE}?Privacy_AllFiles"

echo "If PhotoCatalog was previously denied, toggle the switch off then back on."
echo ""
echo "Done — you can close this window."
