# PhotoCatalog — build, package, and release automation
#
# Requirements:
#   pip install pyinstaller            (build .app)
#   brew install create-dmg            (build .dmg with drop-to-install UI)
#   brew install gh                    (publish GitHub releases)

PYTHON  := .venv/bin/python3
VERSION := $(shell $(PYTHON) -c "from PhotoCatalog import __version__; print(__version__)")
APP     := dist/PhotoCatalog.app
DMG     := dist/PhotoCatalog-$(VERSION).dmg
STAGE   := dist/dmg_stage

.PHONY: all build dmg release clean

all: build

# ── Build .app bundle ────────────────────────────────────────────────────────
build:
	@echo "Building PhotoCatalog.app  (version $(VERSION))…"
	$(PYTHON) -m PyInstaller --noconfirm PhotoCatalog.spec
	@echo "Done → $(APP)"

# ── Package into DMG ─────────────────────────────────────────────────────────
dmg: build
	@echo "Creating DMG…"
	@mkdir -p dist
	@rm -f "$(DMG)"
	@rm -rf "$(STAGE)"
	@mkdir -p "$(STAGE)"
	@cp -R "$(APP)" "$(STAGE)/"
	@if command -v create-dmg >/dev/null 2>&1; then \
		create-dmg \
			--volname "PhotoCatalog $(VERSION)" \
			--window-size 600 300 \
			--icon-size 100 \
			--icon "PhotoCatalog.app" 150 150 \
			--app-drop-link 450 150 \
			--no-internet-enable \
			"$(DMG)" \
			"$(STAGE)"; \
	else \
		echo "create-dmg not found — building plain DMG (no drag-to-install arrow)."; \
		echo "Run:  brew install create-dmg  for a nicer DMG."; \
		hdiutil create -volname "PhotoCatalog $(VERSION)" \
			-srcfolder "$(STAGE)" \
			-ov -format UDZO \
			"$(DMG)"; \
	fi
	@rm -rf "$(STAGE)"
	@echo "Done → $(DMG)"

# ── Publish GitHub release ───────────────────────────────────────────────────
# Usage:
#   make release                  — tag current version and upload DMG
#   make release NOTES="…"       — add custom release notes
release: dmg
	@echo "Ensuring repo is public…"
	gh repo edit --visibility public --accept-visibility-change-consequences
	@echo "Tagging v$(VERSION) and creating GitHub release…"
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push origin "v$(VERSION)"
	gh release create "v$(VERSION)" \
		"$(DMG)" \
		--title "PhotoCatalog v$(VERSION)" \
		$(if $(NOTES),--notes "$(NOTES)",--generate-notes)
	@echo "Release published: https://github.com/$$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$(VERSION)"

# ── Bump version (edit __init__.py and pyproject.toml) ──────────────────────
# Usage:  make bump VERSION=0.0.2
bump:
	@[ -n "$(VERSION)" ] || (echo "Usage: make bump VERSION=x.y.z"; exit 1)
	sed -i '' 's/__version__ = ".*"/__version__ = "$(VERSION)"/' PhotoCatalog/__init__.py
	sed -i '' 's/^version = .*/version = "$(VERSION)"/' pyproject.toml
	@echo "Version bumped to $(VERSION). Commit and then run: make release"

# ── Clean build artefacts ────────────────────────────────────────────────────
clean:
	rm -rf dist build __pycache__ *.egg-info
