"""
Local web visualizer for duplicate groups.

Run from the repo root:
    python -m PhotoCatalog.serve_duplicates --db catalog.db
    python -m PhotoCatalog.serve_duplicates --db catalog.db --port 9000

Opens a two-pane browser UI:
  left  — scrollable list of all duplicate groups (filterable by strategy)
  right — photo cards with image preview + full metadata for the selected group
"""
import argparse
import io
import json
import mimetypes
import signal
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .db import connect, ensure_decisions_table, is_external_path, cache_db_locally, sync_db_back

# ---------------------------------------------------------------------------
# Globals set at startup
# ---------------------------------------------------------------------------
_DB_PATH: str = ""
_VIDEO_EXTS   = {"MOV", "MP4", "M4V", "AVI", "MKV", "MPEG", "MPG", "3GP", "WEBM"}
_BROWSER_EXTS = {"JPG", "JPEG", "PNG", "GIF", "WEBP", "BMP"}  # natively displayed by browsers
_HEIC_EXTS    = {"HEIC", "HEIF"}
_RAW_EXTS     = {"DNG", "CR2", "CR3", "NEF", "ARW", "ORF", "RW2", "RAF", "TIFF", "TIF"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _conn():
    return connect(_DB_PATH)


def _all_groups() -> list[dict]:
    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy,
               dg.group_id,
               COUNT(*)              AS num_photos,
               MIN(dg.match_info)   AS match_info,
               GROUP_CONCAT(p.original_filename, '||') AS names,
               COUNT(CASE WHEN dec.action = 'discard' THEN 1 END) AS discard_count,
               MAX(CASE WHEN UPPER(p.file_type) IN
                   ('MOV','MP4','M4V','AVI','MKV','MPEG','MPG','3GP','WEBM')
                   THEN 1 ELSE 0 END) AS has_video
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        GROUP  BY dg.strategy, dg.group_id
        ORDER  BY dg.strategy, dg.group_id
    """).fetchall()
    db.close()
    result = []
    for r in rows:
        names = list(dict.fromkeys((r["names"] or "").split("||")))[:2]
        label = " / ".join(n for n in names if n)
        try:
            mi = json.loads(r["match_info"] or "{}")
        except Exception as e:
            print(f"Warning: could not parse match_info for group {r['group_id']}: {e}", file=sys.stderr)
            mi = {}
        result.append({
            "strategy":     r["strategy"],
            "group_id":     r["group_id"],
            "num_photos":   r["num_photos"],
            "discard_count": r["discard_count"],
            "label":        label or f"group {r['group_id']}",
            "score":        mi.get("score"),
            "signals":      {k: mi[k] for k in ("phash","fuzzy","dims","date","type","size","name") if k in mi},
            "has_video":    bool(r["has_video"]),
        })
    result.sort(key=lambda g: (
        -(g["score"] or 0),
        g["group_id"],
    ))
    return result


_COLS = (
    # identity
    "id", "source_type", "source_file", "file_name", "original_filename",
    # file
    "file_type", "file_size",
    # dimensions
    "image_width", "image_height",
    # dates
    "date_original", "date_created", "date_modified",
    # camera / EXIF
    "make", "model", "lens_model",
    "focal_length", "aperture", "iso", "shutter_speed",
    # location
    "latitude", "longitude",
    # curation
    "caption", "keywords", "has_adjustments",
    # Mac Photos
    "mp_uuid", "mp_favorite", "mp_score", "mp_is_live", "mp_is_burst",
    # Lightroom curation
    "lr_rating", "lr_pick", "lr_color_labels",
    # Lightroom develop
    "lr_white_balance", "lr_temperature", "lr_tint",
    "lr_exposure", "lr_contrast", "lr_highlights", "lr_shadows",
    "lr_whites", "lr_blacks", "lr_clarity", "lr_vibrance", "lr_saturation",
    "lr_sharpness", "lr_luminance_smoothing", "lr_color_noise_reduction",
    "lr_vignette_amount", "lr_dehaze", "lr_texture", "lr_grayscale",
    "lr_crop_top", "lr_crop_left", "lr_crop_bottom", "lr_crop_right", "lr_crop_angle",
    "lr_post_crop_vignette",
    # computed
    "perceptual_hash",
    # housekeeping
    "ingested_at",
)

_LABELS = {
    "id":                      "ID",
    "source_type":             "Source",
    "source_file":             "Path",
    "file_name":               "Filename (disk)",
    "original_filename":       "Filename (original)",
    "file_type":               "Type",
    "file_size":               "Size",
    "image_width":             "Width",
    "image_height":            "Height",
    "date_original":           "Date taken",
    "date_created":            "Date added",
    "date_modified":           "Date modified",
    "make":                    "Make",
    "model":                   "Model",
    "lens_model":              "Lens",
    "focal_length":            "Focal length",
    "aperture":                "Aperture",
    "iso":                     "ISO",
    "shutter_speed":           "Shutter",
    "latitude":                "Latitude",
    "longitude":               "Longitude",
    "caption":                 "Caption",
    "keywords":                "Keywords",
    "has_adjustments":         "Edited",
    "mp_uuid":                 "MP UUID",
    "mp_favorite":             "Favorite",
    "mp_score":                "MP score",
    "mp_is_live":              "Live photo",
    "mp_is_burst":             "Burst",
    "lr_rating":               "LR rating",
    "lr_pick":                 "LR pick",
    "lr_color_labels":         "LR color",
    "lr_white_balance":        "White balance",
    "lr_temperature":          "Temperature",
    "lr_tint":                 "Tint",
    "lr_exposure":             "Exposure",
    "lr_contrast":             "Contrast",
    "lr_highlights":           "Highlights",
    "lr_shadows":              "Shadows",
    "lr_whites":               "Whites",
    "lr_blacks":               "Blacks",
    "lr_clarity":              "Clarity",
    "lr_vibrance":             "Vibrance",
    "lr_saturation":           "Saturation",
    "lr_sharpness":            "Sharpness",
    "lr_luminance_smoothing":  "Luminance NR",
    "lr_color_noise_reduction":"Color NR",
    "lr_vignette_amount":      "Vignette",
    "lr_dehaze":               "Dehaze",
    "lr_texture":              "Texture",
    "lr_grayscale":            "Grayscale",
    "lr_crop_top":             "Crop top",
    "lr_crop_left":            "Crop left",
    "lr_crop_bottom":          "Crop bottom",
    "lr_crop_right":           "Crop right",
    "lr_crop_angle":           "Crop angle",
    "lr_post_crop_vignette":   "Post-crop vignette",
    "perceptual_hash":         "pHash",
    "ingested_at":             "Ingested at",
}



def _group_detail(strategy: str, group_id: int) -> dict:
    db = _conn()
    sel = ", ".join(f"p.{c}" for c in _COLS)
    rows = db.execute(f"""
        SELECT {sel}, dg.match_info
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        WHERE  dg.strategy = ? AND dg.group_id = ?
        ORDER  BY p.id
    """, (strategy, group_id)).fetchall()
    db.close()

    photos = [dict(r) for r in rows]
    raw_mi: dict = {}
    if photos:
        try:
            raw_mi = json.loads(photos[0].get("match_info") or "{}")
        except Exception as e:
            print(f"Warning: could not parse match_info: {e}", file=sys.stderr)

    # Separate the signals dict from the rest of match_info for the UI
    signals = {k: raw_mi[k] for k in ("phash","fuzzy","dims","date","type","size","name") if k in raw_mi}
    match_info = {k: v for k, v in raw_mi.items() if k not in signals}
    if signals:
        match_info["signals"] = signals

    # Annotate each photo with a display path and whether it's local
    for p in photos:
        sf = p.get("source_file") or ""
        p["is_cloud"] = sf.startswith("macphotos://")
        p["is_local"] = not p["is_cloud"] and Path(sf).exists()
        ext = (p.get("file_type") or "").upper()
        p["is_video"] = ext in _VIDEO_EXTS
        p["is_heic"]  = ext in _HEIC_EXTS

    # Fetch current keep/discard decisions for these photos
    photo_ids = [p["id"] for p in photos]
    decisions: dict[int, str] = {}
    if photo_ids:
        placeholders = ",".join("?" * len(photo_ids))
        db2 = _conn()
        for row in db2.execute(
            f"SELECT photo_id, action FROM decisions WHERE photo_id IN ({placeholders})",
            photo_ids,
        ):
            decisions[row["photo_id"]] = row["action"]
        db2.close()

    return {"match_info": match_info, "photos": photos, "labels": _LABELS, "decisions": decisions}


def _group_filter(allowed: list[dict] | None) -> set[tuple] | None:
    """Convert [{strategy, group_id}] list to a set of tuples, or None = all groups."""
    if allowed is None:
        return None
    return {(g["strategy"], g["group_id"]) for g in allowed}


def _apply_prefer(pattern: str, field: str, allowed: set | None = None,
                  negate: bool = False) -> dict:
    """For each group, discard undecided photos based on whether `field` matches pattern.

    negate=False (default): discard photos that do NOT match — "prefer matching"
    negate=True:            discard photos that DO match    — "avoid matching"

    field: 'filename' checks original_filename / file_name
            'path'     checks source_file
            'type'     checks file_type
    """
    import re
    from collections import defaultdict
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}")

    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy, dg.group_id,
               p.id AS photo_id, p.file_name, p.original_filename, p.source_file, p.file_type
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        WHERE  dec.photo_id IS NULL
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    discarded = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        if len(photos) <= 1:
            continue
        matching, non_matching = [], []
        for p in photos:
            if field == "path":
                target = p["source_file"] or ""
            elif field == "type":
                target = p["file_type"] or ""
            else:
                target = p["original_filename"] or p["file_name"] or Path(p["source_file"]).name or ""
            (matching if rx.search(target) else non_matching).append(p)
        if not matching or not non_matching:
            continue
        # negate=False → discard non-matching (prefer matching)
        # negate=True  → discard matching     (avoid matching)
        to_discard = matching if negate else non_matching
        for p in to_discard:
            db.execute("""
                INSERT INTO decisions (photo_id, action) VALUES (?, 'discard')
                ON CONFLICT (photo_id) DO UPDATE
                    SET action = 'discard', decided_at = datetime('now')
            """, (p["photo_id"],))
            discarded += 1

    db.commit()
    db.close()
    return {"ok": True, "discarded": discarded}


def _apply_prefer_filename(pattern: str, allowed: set | None = None, negate: bool = False) -> dict:
    return _apply_prefer(pattern, "filename", allowed, negate=negate)


def _apply_prefer_path(pattern: str, allowed: set | None = None, negate: bool = False) -> dict:
    return _apply_prefer(pattern, "path", allowed, negate=negate)


def _apply_prefer_type(pattern: str, allowed: set | None = None, negate: bool = False) -> dict:
    return _apply_prefer(pattern, "type", allowed, negate=negate)


def _keep_best(metric: str, allowed: set | None = None) -> dict:
    """Among undecided photos in each group, discard all but the best by metric.

    metric: 'resolution' → max(width, height); 'size' → file_size
    Ties are left untouched.
    """
    from collections import defaultdict
    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy, dg.group_id,
               p.id AS photo_id, p.file_size, p.image_width, p.image_height
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        WHERE  dec.photo_id IS NULL
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    def score(p: dict) -> int:
        if metric == "resolution":
            return max(p["image_width"] or 0, p["image_height"] or 0)
        return p["file_size"] or 0

    discarded = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        if len(photos) <= 1:
            continue
        best = max(score(p) for p in photos)
        if best == 0:
            continue
        for p in photos:
            if score(p) < best:
                db.execute("""
                    INSERT INTO decisions (photo_id, action) VALUES (?, 'discard')
                    ON CONFLICT (photo_id) DO UPDATE
                        SET action = 'discard', decided_at = datetime('now')
                """, (p["photo_id"],))
                discarded += 1

    db.commit()
    db.close()
    return {"ok": True, "discarded": discarded}


def _keep_has_original_filename(allowed: set | None = None) -> dict:
    """In each group, discard photos that have no original_filename when at least one does."""
    from collections import defaultdict
    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy, dg.group_id,
               p.id AS photo_id, p.original_filename
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        WHERE  dec.photo_id IS NULL
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    discarded = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        if len(photos) <= 1:
            continue
        has_orig = [p for p in photos if p["original_filename"]]
        no_orig  = [p for p in photos if not p["original_filename"]]
        if not has_orig or not no_orig:
            continue
        for p in no_orig:
            db.execute("""
                INSERT INTO decisions (photo_id, action) VALUES (?, 'discard')
                ON CONFLICT (photo_id) DO UPDATE
                    SET action = 'discard', decided_at = datetime('now')
            """, (p["photo_id"],))
            discarded += 1

    db.commit()
    db.close()
    return {"ok": True, "discarded": discarded}


def _keep_by_filename_length(prefer: str, allowed: set | None = None) -> dict:
    """Discard all but the photo with the shortest/longest filename stem in each group.

    prefer: 'shorter' | 'longer'
    Uses original_filename when available (falls back to file_name).
    Ties are left untouched.
    """
    from collections import defaultdict
    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy, dg.group_id,
               p.id AS photo_id, p.file_name, p.original_filename
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        WHERE  dec.photo_id IS NULL
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    def stem_len(p) -> int:
        lens = [len(Path(n).stem) for n in (p["original_filename"], p["file_name"]) if n]
        if not lens:
            return 0
        return min(lens) if prefer == "shorter" else max(lens)

    discarded = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        if len(photos) <= 1:
            continue
        target = min(stem_len(p) for p in photos) if prefer == "shorter" else max(stem_len(p) for p in photos)
        if target == 0:
            continue
        for p in photos:
            if stem_len(p) != target:
                db.execute("""
                    INSERT INTO decisions (photo_id, action) VALUES (?, 'discard')
                    ON CONFLICT (photo_id) DO UPDATE
                        SET action = 'discard', decided_at = datetime('now')
                """, (p["photo_id"],))
                discarded += 1

    db.commit()
    db.close()
    return {"ok": True, "discarded": discarded}


def _keep_by_date(prefer: str, date_field: str, allowed: set | None = None) -> dict:
    """Discard all but the oldest/newest photo (by date_field) in each undecided group.

    prefer:     'oldest' | 'newest'
    date_field: 'date_original' (date taken) | 'date_created' (date added)
    Photos with a NULL date_field are left untouched.
    Ties are left untouched.
    """
    if date_field not in ("date_original", "date_created"):
        raise ValueError(f"Invalid date_field: {date_field!r}")
    from collections import defaultdict
    db = _conn()
    rows = db.execute(f"""
        SELECT dg.strategy, dg.group_id,
               p.id AS photo_id, p.{date_field} AS dt
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
        WHERE  dec.photo_id IS NULL
          AND  p.{date_field} IS NOT NULL
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    discarded = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        if len(photos) <= 1:
            continue
        target = min(p["dt"] for p in photos) if prefer == "oldest" else max(p["dt"] for p in photos)
        for p in photos:
            if p["dt"] != target:
                db.execute("""
                    INSERT INTO decisions (photo_id, action) VALUES (?, 'discard')
                    ON CONFLICT (photo_id) DO UPDATE
                        SET action = 'discard', decided_at = datetime('now')
                """, (p["photo_id"],))
                discarded += 1

    db.commit()
    db.close()
    return {"ok": True, "discarded": discarded}


def _keep_unique(allowed: set | None = None) -> dict:
    """In groups where exactly one photo is not discarded, mark it keep."""
    from collections import defaultdict
    db = _conn()
    rows = db.execute("""
        SELECT dg.strategy, dg.group_id, p.id AS photo_id,
               COALESCE(dec.action, '') AS action
        FROM   duplicate_groups dg
        JOIN   photos p ON p.id = dg.photo_id
        LEFT JOIN decisions dec ON dec.photo_id = p.id
    """).fetchall()

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["group_id"])].append(r)

    kept = 0
    for key, photos in groups.items():
        if allowed is not None and key not in allowed:
            continue
        survivors = [p for p in photos if p["action"] != "discard"]
        if len(survivors) == 1 and survivors[0]["action"] != "keep":
            db.execute("""
                INSERT INTO decisions (photo_id, action) VALUES (?, 'keep')
                ON CONFLICT (photo_id) DO UPDATE
                    SET action = 'keep', decided_at = datetime('now')
            """, (survivors[0]["photo_id"],))
            kept += 1

    db.commit()
    db.close()
    return {"ok": True, "kept": kept}


def _decision_stats() -> dict:
    db = _conn()
    rows = db.execute("""
        SELECT d.action, COUNT(*) AS n, COALESCE(SUM(p.file_size), 0) AS total_bytes
        FROM   decisions d
        JOIN   photos p ON p.id = d.photo_id
        GROUP  BY d.action
    """).fetchall()
    db.close()
    stats = {"keep_count": 0, "discard_count": 0, "discard_bytes": 0, "keep_bytes": 0}
    for r in rows:
        if r["action"] == "keep":
            stats["keep_count"]  = r["n"]
            stats["keep_bytes"]  = r["total_bytes"]
        elif r["action"] == "discard":
            stats["discard_count"] = r["n"]
            stats["discard_bytes"] = r["total_bytes"]
    return stats


# ---------------------------------------------------------------------------
# Image serving
# ---------------------------------------------------------------------------

def _image_response(path: str) -> tuple[int, bytes, str]:
    """Return (status, body, content_type)."""
    p = Path(path)
    if not p.exists():
        return 404, b"not found", "text/plain"

    ext = p.suffix.upper().lstrip(".")

    if ext in _BROWSER_EXTS:
        mime, _ = mimetypes.guess_type(path)
        try:
            return 200, p.read_bytes(), mime or "application/octet-stream"
        except Exception as e:
            return 500, str(e).encode(), "text/plain"

    # All non-browser-native formats (HEIC, DNG, RAW, TIFF…) → convert to JPEG
    if ext in _HEIC_EXTS or ext in _RAW_EXTS:
        try:
            import pillow_heif
            from PIL import Image
            pillow_heif.register_heif_opener()
            img = Image.open(path)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=85)
            return 200, buf.getvalue(), "image/jpeg"
        except Exception as e:
            return 415, f"Cannot convert {ext}: {e}".encode(), "text/plain"

    # Fallback for anything else
    mime, _ = mimetypes.guess_type(path)
    try:
        return 200, p.read_bytes(), mime or "application/octet-stream"
    except Exception as e:
        return 500, str(e).encode(), "text/plain"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence request logs

    def do_GET(self):
        parsed  = urllib.parse.urlparse(self.path)
        qs      = urllib.parse.parse_qs(parsed.query)
        path    = parsed.path

        if path == "/" or path == "":
            self._send(200, _HTML.encode(), "text/html; charset=utf-8")

        elif path == "/api/groups":
            data = json.dumps(_all_groups()).encode()
            self._send(200, data, "application/json")

        elif path.startswith("/api/group/"):
            # /api/group/{strategy}/{group_id}
            parts = path.split("/")
            if len(parts) >= 5:
                strategy = parts[3]
                try:
                    gid = int(parts[4])
                    data = json.dumps(_group_detail(strategy, gid)).encode()
                    self._send(200, data, "application/json")
                except Exception as e:
                    self._send(500, str(e).encode(), "text/plain")
            else:
                self._send(400, b"bad request", "text/plain")

        elif path == "/api/stats":
            data = json.dumps(_decision_stats()).encode()
            self._send(200, data, "application/json")

        elif path == "/image":
            file_path = qs.get("path", [""])[0]
            status, body, ct = _image_response(file_path)
            self._send(status, body, ct)

        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/reset-decisions":
            try:
                db = _conn()
                db.execute("DELETE FROM decisions")
                db.commit()
                db.close()
                self._send(200, b'{"ok":true}', "application/json")
            except Exception as e:
                self._send(500, str(e).encode(), "text/plain")
        elif parsed.path == "/api/decide":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length))
                photo_id = int(payload["photo_id"])
                action   = payload.get("action")   # "keep" | "discard" | null
                db = _conn()
                if action in ("keep", "discard"):
                    db.execute("""
                        INSERT INTO decisions (photo_id, action)
                        VALUES (?, ?)
                        ON CONFLICT (photo_id) DO UPDATE
                            SET action = excluded.action, decided_at = datetime('now')
                    """, (photo_id, action))
                else:
                    db.execute("DELETE FROM decisions WHERE photo_id = ?", (photo_id,))
                db.commit()
                db.close()
                self._send(200, b'{"ok":true}', "application/json")
            except Exception as e:
                self._send(500, str(e).encode(), "text/plain")
        elif parsed.path == "/api/auto-select":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length))
                action = payload.get("action")
                allowed = _group_filter(payload.get("groups"))
                negate  = bool(payload.get("negate", False))
                if action == "prefer_filename":
                    result = _apply_prefer_filename(payload.get("pattern", ""), allowed, negate=negate)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "prefer_path":
                    result = _apply_prefer_path(payload.get("pattern", ""), allowed, negate=negate)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "prefer_type":
                    result = _apply_prefer_type(payload.get("pattern", ""), allowed, negate=negate)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "prefer_resolution":
                    result = _keep_best("resolution", allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "prefer_bigger":
                    result = _keep_best("size", allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action in ("prefer_shorter_filename", "prefer_longer_filename"):
                    prefer = "shorter" if "shorter" in action else "longer"
                    result = _keep_by_filename_length(prefer, allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "prefer_has_original_filename":
                    result = _keep_has_original_filename(allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action in ("prefer_oldest_taken", "prefer_newest_taken",
                                "prefer_oldest_added", "prefer_newest_added"):
                    prefer = "oldest" if "oldest" in action else "newest"
                    field  = "date_original" if "taken" in action else "date_created"
                    result = _keep_by_date(prefer, field, allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                elif action == "keep_unique":
                    result = _keep_unique(allowed)
                    self._send(200, json.dumps(result).encode(), "application/json")
                else:
                    self._send(400, b'{"error":"unknown action"}', "application/json")
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
            except Exception as e:
                self._send(500, str(e).encode(), "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, status: int, body: bytes, ct: str):
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Frontend HTML / CSS / JS (single-file SPA)
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PhotoCatalog — Duplicates</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       font-size: 13px; background: #f0f0f0; display: flex;
       flex-direction: column; height: 100vh; overflow: hidden; }

#topbar { background: #1c1c1e; color: #fff; padding: 10px 16px;
          display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }
.action-btn { padding: 4px 12px; border-radius: 7px; border: 1.5px solid #007aff;
              color: #007aff; background: transparent; font-size: 11px; font-weight: 600;
              cursor: pointer; }
.action-btn:hover { background: rgba(255,255,255,.1); }
#topbar h1 { font-size: 15px; font-weight: 600; }
#topbar span { color: #8e8e93; font-size: 12px; }

#main { display: flex; flex: 1; overflow: hidden; }

/* ── left pane ── */
#left { width: 300px; min-width: 200px; background: #fff;
        border-right: 1px solid #d1d1d6;
        display: flex; flex-direction: column; flex-shrink: 0; }

#filters { padding: 8px 10px; border-bottom: 1px solid #e5e5ea; }

/* signal filter badges — 4-column grid → two rows of four */
#sig-filters { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
.sf { padding: 3px 0; border-radius: 10px; border: 1.5px solid #d1d1d6;
      background: #fff; color: #6e6e73; font-size: 11px; font-weight: 600;
      cursor: pointer; user-select: none; transition: all .12s;
      text-align: center; }
.sf:hover { border-color: #007aff; color: #007aff; }
.sf.on    { background: #007aff; border-color: #007aff; color: #fff; }
.sf.on.sf-phash  { background: #b45309; border-color: #b45309; }
.sf.on.sf-fuzzy  { background: #7c3aed; border-color: #7c3aed; }
.sf.on.sf-unique { background: #6e6e73; border-color: #6e6e73; }
.sf.on.sf-video  { background: #6d28d9; border-color: #6d28d9; }

#group-list { flex: 1; overflow-y: auto; }
.group-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #f2f2f7;
              display: flex; align-items: flex-start; gap: 8px; }
.group-item:hover  { background: #f2f2f7; }
.group-item.active { background: #e8f0fe; }

/* signal dots in list items */
.sig-dots { display: inline-flex; gap: 3px; align-items: center; margin-top: 3px; }
.sig-dot { width: 7px; height: 7px; border-radius: 50%; }
.sig-on  { background: #1a7f37; }
.sig-off { background: #d1d1d6; }
.sig-dot.sd-phash.sig-on { background: #b45309; }
.sig-dot.sd-fuzzy.sig-on { background: #7c3aed; }

.group-info { flex: 1; min-width: 0; }
.group-info .name { font-size: 12px; color: #1c1c1e; white-space: nowrap;
                    overflow: hidden; text-overflow: ellipsis; }
.group-info .sub  { font-size: 11px; color: #8e8e93; margin-top: 1px; }

/* ── right pane ── */
#right { flex: 1; overflow-y: auto; padding: 20px; background: #f0f0f0; }

#placeholder { display: flex; align-items: center; justify-content: center;
               height: 100%; color: #8e8e93; font-size: 15px; }

#group-header { margin-bottom: 16px; }
#group-header h2 { font-size: 16px; color: #1c1c1e; font-weight: 600; }

/* signal grid in group header */
.sig-grid { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.sig-cell { display: flex; flex-direction: column; align-items: center;
            background: #fff; border-radius: 8px; padding: 7px 12px;
            font-size: 12px; font-weight: 600; min-width: 52px;
            box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.sig-cell .sig-lbl { color: #6e6e73; margin-top: 2px; font-size: 10px; font-weight: 400; }
.sig-cell.sig-yes  { border: 1.5px solid #1a7f37; color: #1a7f37; }
.sig-cell.sig-yes.sig-phash { border-color: #b45309; color: #b45309; }
.sig-cell.sig-yes.sig-fuzzy { border-color: #7c3aed; color: #7c3aed; }
.sig-cell.sig-no   { border: 1.5px solid #d1d1d6; opacity: .45; }

/* ── photo cards ── */
#photo-grid { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }

.photo-card { background: #fff; border-radius: 12px; overflow: hidden;
              box-shadow: 0 1px 4px rgba(0,0,0,.10); width: 280px; flex-shrink: 0; }
.photo-thumb { width: 100%; height: 200px; object-fit: cover;
               background: #e5e5ea; display: block; }
.thumb-placeholder { width: 100%; height: 200px; display: flex; flex-direction: column;
                     align-items: center; justify-content: center;
                     background: #e5e5ea; color: #8e8e93; font-size: 13px; gap: 8px; }
.thumb-placeholder .icon { font-size: 40px; }
video.photo-thumb { background: #000; }
.photo-meta { padding: 12px; }
.photo-meta table { width: 100%; border-collapse: collapse; }
.photo-meta td { padding: 3px 0; vertical-align: top; font-size: 12px; line-height: 1.4; }
.photo-meta td:first-child { color: #8e8e93; width: 40%; padding-right: 8px; white-space: nowrap; }
.photo-meta td:last-child  { color: #1c1c1e; word-break: break-all; }
.diff-row td:last-child { color: #c0392b; font-weight: 600; }
.source-badge { display: inline-block; padding: 1px 7px; border-radius: 8px;
                font-size: 10px; font-weight: 600; background: #e5e5ea; color: #3c3c43; }

/* ── keep / discard decisions ── */
.photo-card.card-keep    { border: 2.5px solid #1a7f37; }
.photo-card.card-discard { opacity: .4; border: 2.5px solid #c0392b; }

.decide-row { display: flex; gap: 6px; padding: 8px 12px; border-top: 1px solid #f2f2f7; }
.decide-btn { flex: 1; padding: 5px 0; border-radius: 7px; border: 1.5px solid;
              font-size: 12px; font-weight: 600; cursor: pointer; background: #fff;
              transition: background .1s, color .1s; }
.decide-btn.btn-keep    { border-color: #1a7f37; color: #1a7f37; }
.decide-btn.btn-keep.active,
.decide-btn.btn-keep:hover    { background: #1a7f37; color: #fff; }
.decide-btn.btn-discard { border-color: #c0392b; color: #c0392b; }
.decide-btn.btn-discard.active,
.decide-btn.btn-discard:hover { background: #c0392b; color: #fff; }
.decide-btn.btn-clear   { flex: 0; padding: 5px 10px;
                          border-color: #d1d1d6; color: #8e8e93; }
.decide-btn.btn-clear:hover { background: #f2f2f7; }

</style>
</head>
<body>

<div id="topbar">
  <div style="display:flex;align-items:center;gap:16px;flex:1">
    <h1>PhotoCatalog — Duplicate Visualizer</h1>
    <span id="topbar-count">Loading…</span>
    <span id="topbar-stats" style="font-size:12px;color:#8e8e93"></span>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#fff;cursor:pointer;white-space:nowrap;margin-left:auto">
      <input type="checkbox" id="hide-discarded" onchange="toggleHideDiscarded(this.checked)">
      Hide discarded
    </label>
  </div>
  <div style="display:flex;gap:8px;margin-top:6px;align-items:center">
    <button class="action-btn" onclick="resetDecisions()" style="border-color:#c0392b;color:#c0392b">Reset selection</button>
    <button class="action-btn" onclick="keepUnique()">Keep unique</button>
  </div>
  <div style="display:flex;gap:8px;margin-top:4px;align-items:center;flex-wrap:wrap">
    <button class="action-btn" onclick="togglePrefer('filename')">Prefer filename&hellip;</button>
    <button class="action-btn" onclick="togglePrefer('path')">Prefer path&hellip;</button>
    <button class="action-btn" onclick="togglePrefer('type')">Prefer type&hellip;</button>
    <button class="action-btn" onclick="togglePrefer('date')">Prefer date&hellip;</button>
    <button class="action-btn" onclick="instantAction('prefer_resolution')">Prefer higher resolution</button>
    <button class="action-btn" onclick="instantAction('prefer_bigger')">Prefer bigger</button>
  </div>
  <div id="prefer-row" style="display:none;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
    <span id="prefer-label" style="color:#8e8e93;font-size:11px"></span>
    <input id="prefer-input" type="text" placeholder='Python regex, case-insensitive'
           style="padding:4px 8px;border-radius:6px;border:1px solid #555;background:#2c2c2e;
                  color:#fff;font-size:12px;width:220px;outline:none"
           onkeydown="if(event.key==='Enter')applyPrefer();if(event.key==='Escape')togglePrefer(null)">
    <button id="prefer-not-btn" class="action-btn" onclick="toggleNegate()"
            title="NOT: discard files that match instead of files that don't"
            style="font-family:monospace;letter-spacing:0.5px">NOT</button>
    <button class="action-btn" onclick="applyPrefer()">Apply</button>
    <button class="action-btn" onclick="togglePrefer(null)"
            style="border-color:#555;color:#8e8e93">Cancel</button>
    <span id="prefer-len-sep" style="display:none;color:#8e8e93;font-size:11px;margin-left:4px">or by length:</span>
    <button id="prefer-shorter-btn" class="action-btn" style="display:none"
            onclick="applyFilenameLength('shorter')">Shorter</button>
    <button id="prefer-longer-btn"  class="action-btn" style="display:none"
            onclick="applyFilenameLength('longer')">Longer</button>
    <button id="prefer-has-orig-btn" class="action-btn" style="display:none"
            onclick="applyHasOriginal()" title="Keep photos that have an original filename; discard those that don't">Has original</button>
  </div>
  <div id="date-row" style="display:none;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
    <span style="color:#8e8e93;font-size:11px">Date taken:</span>
    <button class="action-btn" onclick="applyDate('oldest','date_original')">Earlier</button>
    <button class="action-btn" onclick="applyDate('newest','date_original')">Later</button>
    <span style="color:#8e8e93;font-size:11px;margin-left:8px">Date added:</span>
    <button class="action-btn" onclick="applyDate('oldest','date_created')">Earlier</button>
    <button class="action-btn" onclick="applyDate('newest','date_created')">Later</button>
    <button class="action-btn" onclick="togglePrefer(null)"
            style="border-color:#555;color:#8e8e93;margin-left:4px">Cancel</button>
  </div>
</div>

<div id="main">
  <div id="left">
    <div id="filters">
      <div id="sig-filters">
        <span class="sf sf-phash" data-sig="phash">pHash</span>
        <span class="sf sf-fuzzy" data-sig="fuzzy">Fuzzy</span>
        <span class="sf" data-sig="dims">Dims</span>
        <span class="sf" data-sig="date">Date</span>
        <span class="sf" data-sig="type">Type</span>
        <span class="sf" data-sig="size">Size</span>
        <span class="sf" data-sig="name">Name</span>
        <span class="sf sf-unique" id="btn-unique" title="Show groups resolved to a single survivor">Unique</span>
        <span class="sf sf-video"  id="btn-video"  title="Show only groups containing videos">Video</span>
      </div>
    </div>
    <div id="group-list"></div>
  </div>

  <div id="right">
    <div id="placeholder">← Select a group to view details</div>
    <div id="group-detail" style="display:none"></div>
  </div>
</div>

<script>
let allGroups      = [];
let activeKey      = null;
let activeSignals  = new Set();
let currentDecisions = {};   // photo_id -> action, for photos in the active group
let showResolved   = false;  // when false, hide groups with ≤1 non-discarded photo
let showOnlyVideo  = false;

const SIGNALS   = ['phash','fuzzy','dims','date','type','size','name'];
const SIG_LABEL = {phash:'pHash', fuzzy:'Fuzzy', dims:'Dims', date:'Date', type:'Type', size:'Size', name:'Name'};

// ── load data ────────────────────────────────────────────────────────────────
function refreshGroups() {
  return fetch('/api/groups').then(r => r.json()).then(data => {
    allGroups = data;
    renderList();
  });
}
refreshGroups();
loadStats();

function loadStats() {
  fetch('/api/stats').then(r => r.json()).then(s => {
    const el = document.getElementById('topbar-stats');
    if (!el) return;
    if (s.discard_count === 0 && s.keep_count === 0) { el.textContent = ''; return; }
    const parts = [];
    if (s.keep_count)    parts.push(s.keep_count + ' kept');
    if (s.discard_count) parts.push(s.discard_count + ' discarded (' + humanBytes(s.discard_bytes) + ' saved)');
    el.textContent = parts.join(' · ');
  });
}

// ── signal filter badges ─────────────────────────────────────────────────────
document.querySelectorAll('.sf[data-sig]').forEach(btn => {
  btn.addEventListener('click', () => {
    const sig = btn.dataset.sig;
    if (activeSignals.has(sig)) {
      activeSignals.delete(sig);
      btn.classList.remove('on');
    } else {
      activeSignals.add(sig);
      btn.classList.add('on');
    }
    renderList();
  });
});

document.getElementById('btn-unique').addEventListener('click', function() {
  showResolved = !showResolved;
  this.classList.toggle('on', showResolved);
  renderList();
});

document.getElementById('btn-video').addEventListener('click', function() {
  showOnlyVideo = !showOnlyVideo;
  this.classList.toggle('on', showOnlyVideo);
  renderList();
});

// ── render left pane ─────────────────────────────────────────────────────────
function getFilteredGroups() {
  return allGroups.filter(g => {
    const sigs = g.signals || {};
    if (![...activeSignals].every(s => sigs[s] === true)) return false;
    if (showOnlyVideo && !g.has_video) return false;
    const survivors = g.num_photos - (g.discard_count || 0);
    if (!showResolved && survivors <= 1) return false;
    if (hideDiscarded && survivors <= 1) return false;
    return true;
  });
}

function renderList() {
  const filtered = getFilteredGroups();

  // sort by number of photos descending
  filtered.sort((a, b) => b.num_photos - a.num_photos);

  document.getElementById('topbar-count').textContent =
    filtered.length + (activeSignals.size ? ' / ' + allGroups.length : '') + ' groups';

  const list = document.getElementById('group-list');
  const savedScroll = list.scrollTop;
  list.innerHTML = '';
  filtered.forEach(g => {
    const item = document.createElement('div');
    item.className = 'group-item' + (isActive(g) ? ' active' : '');
    item.dataset.strategy = g.strategy;
    item.dataset.group_id = g.group_id;

    const sigs = g.signals || {};
    const dots = SIGNALS.map(k =>
      `<span class="sig-dot sd-${k} ${sigs[k] ? 'sig-on' : 'sig-off'}" title="${SIG_LABEL[k]}"></span>`
    ).join('');

    item.innerHTML =
      `<div class="group-info">
         <div class="name">${escHtml(g.label)}</div>
         <div class="sub">
           ${g.num_photos} photos
           <span class="sig-dots">${dots}</span>
         </div>
       </div>`;
    item.addEventListener('click', () => loadGroup(g.strategy, g.group_id));
    list.appendChild(item);
  });
  list.scrollTop = savedScroll;
}

function isActive(g) {
  return activeKey === g.strategy + '/' + g.group_id;
}

// ── right pane ───────────────────────────────────────────────────────────────
function loadGroup(strategy, group_id) {
  activeKey = strategy + '/' + group_id;
  document.querySelectorAll('.group-item').forEach(el =>
    el.classList.toggle('active',
      el.dataset.strategy === strategy && +el.dataset.group_id === group_id));

  document.getElementById('placeholder').style.display = 'none';
  const detail = document.getElementById('group-detail');
  detail.style.display = 'block';
  detail.innerHTML = '<div style="padding:40px;color:#8e8e93">Loading…</div>';

  fetch(`/api/group/${strategy}/${group_id}`)
    .then(r => r.json())
    .then(data => renderDetail(strategy, group_id, data));
}

function renderDetail(strategy, group_id, data) {
  const { match_info, photos, labels, decisions } = data;
  const signals = match_info.signals || {};

  const allVals = {};
  const NO_DIFF = new Set(['source_file','perceptual_hash','ingested_at','mp_uuid']);
  for (const col of Object.keys(labels)) {
    if (!NO_DIFF.has(col)) allVals[col] = photos.map(p => String(p[col] ?? ''));
  }

  const sigGrid = SIGNALS.map(k => {
    const on = signals[k];
    const accent = (k === 'phash' || k === 'fuzzy') && on ? `sig-${k}` : '';
    return `<div class="sig-cell ${on ? 'sig-yes' : 'sig-no'} ${accent}">
              ${on ? '✓' : '✗'}
              <span class="sig-lbl">${SIG_LABEL[k]}</span>
            </div>`;
  }).join('');

  // store photos and decisions for the active group
  window._currentPhotos = photos;
  currentDecisions = Object.assign({}, decisions || {});

  let html = `
    <div id="group-header">
      <h2>#${group_id} &mdash; ${photos.length} photos</h2>
      <div class="sig-grid">${sigGrid}</div>
    </div>
    <div id="photo-grid">`;

  for (const p of photos) html += renderCard(p, allVals, labels, decisions || {});
  html += '</div>';
  document.getElementById('group-detail').innerHTML = html;
}

function renderCard(p, allVals, labels, decisions) {
  const decision = (decisions || {})[p.id] || null;
  const cardClass = decision === 'keep'    ? 'photo-card card-keep'
                  : decision === 'discard' ? 'photo-card card-discard'
                  : 'photo-card';
  let thumbHtml;
  if (p.is_cloud) {
    thumbHtml = `<div class="thumb-placeholder"><div class="icon">&#9729;</div>iCloud only</div>`;
  } else if (p.is_video) {
    thumbHtml = `<video class="photo-thumb" src="/image?path=${enc(p.source_file)}"
                  controls preload="metadata" muted></video>`;
  } else if (p.is_local) {
    thumbHtml = `<div class="thumb-wrap"><img class="photo-thumb" src="/image?path=${enc(p.source_file)}"
                  loading="lazy" onerror="this.parentNode.innerHTML=noThumb()"></div>`;
  } else {
    thumbHtml = `<div class="thumb-placeholder"><div class="icon">&#128444;</div>File not found</div>`;
  }

  // Show every column that has a label and a non-empty value
  const NO_DIFF = new Set(['source_file','perceptual_hash','ingested_at','mp_uuid']);
  let rows = '';
  for (const col of Object.keys(labels)) {
    if (col === 'id') continue;   // already shown in header row
    const raw = p[col];
    if (raw === null || raw === undefined || raw === '') continue;
    let val = String(raw);
    if (col === 'file_size') val = humanBytes(+raw);
    if (col === 'perceptual_hash') val = val.slice(0, 16) + '…';
    const isDiff = !NO_DIFF.has(col) && allVals[col] && new Set(allVals[col]).size > 1;
    rows += `<tr class="${isDiff ? 'diff-row' : ''}">
               <td>${escHtml(labels[col] || col)}</td><td>${escHtml(val)}</td>
             </tr>`;
  }

  const srcBadge = `<span class="source-badge">${escHtml(p.source_type || '')}</span>`;
  const keepActive    = decision === 'keep'    ? ' active' : '';
  const discardActive = decision === 'discard' ? ' active' : '';
  const decideHtml = `
    <div class="decide-row">
      <button class="decide-btn btn-keep${keepActive}"       onclick="decide(${p.id},'keep')">Keep</button>
      <button class="decide-btn btn-discard${discardActive}" onclick="decide(${p.id},'discard')">Discard</button>
      <button class="decide-btn btn-clear"                   onclick="decide(${p.id},null)" title="Clear">✕</button>
    </div>`;
  const hiddenStyle = (hideDiscarded && decision === 'discard') ? ' style="display:none"' : '';
  return `<div class="${cardClass}" id="card-${p.id}"${hiddenStyle}>
    ${thumbHtml}
    <div class="photo-meta"><table>
      <tr><td>ID</td><td>${p.id} ${srcBadge}</td></tr>${rows}
    </table></div>
    ${decideHtml}
  </div>`;
}

function decide(photoId, action) {
  fetch('/api/decide', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({photo_id: photoId, action: action})
  }).then(r => r.json()).then(() => {
    // update in-memory decision
    if (action) currentDecisions[photoId] = action;
    else delete currentDecisions[photoId];

    // update card visual
    const card = document.getElementById('card-' + photoId);
    if (card) {
      card.className = action === 'keep'    ? 'photo-card card-keep'
                     : action === 'discard' ? 'photo-card card-discard'
                     : 'photo-card';
      card.querySelector('.btn-keep')?.classList.toggle('active',    action === 'keep');
      card.querySelector('.btn-discard')?.classList.toggle('active', action === 'discard');
      if (hideDiscarded) card.style.display = action === 'discard' ? 'none' : '';
    }

    // update discard_count for this group in allGroups and re-render list
    if (activeKey) {
      const photos = window._currentPhotos || [];
      const discardCount = photos.filter(p => currentDecisions[p.id] === 'discard').length;
      const [st, gid] = activeKey.split('/');
      const g = allGroups.find(g => g.strategy === st && g.group_id === +gid);
      if (g) { g.discard_count = discardCount; renderList(); }
    }
    loadStats();
  });
}


function resetDecisions() {
  if (!confirm('Clear ALL keep/discard decisions?')) return;
  fetch('/api/reset-decisions', {method: 'POST'})
    .then(r => r.json())
    .then(() => {
      loadStats();
      refreshGroups();
      if (activeKey) {
        const [strategy, group_id] = activeKey.split('/');
        loadGroup(strategy, +group_id);
      }
    });
}

let hideDiscarded = false;
function toggleHideDiscarded(on) {
  hideDiscarded = on;
  document.querySelectorAll('.photo-card.card-discard').forEach(c => {
    c.style.display = on ? 'none' : '';
  });
  renderList();
}

function visibleGroups() {
  return getFilteredGroups().map(g => ({strategy: g.strategy, group_id: g.group_id}));
}

function instantAction(action) {
  fetch('/api/auto-select', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, groups: visibleGroups()})
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert('Error: ' + data.error); return; }
    loadStats();
    refreshGroups();
    if (activeKey) {
      const [strategy, group_id] = activeKey.split('/');
      loadGroup(strategy, +group_id);
    }
  });
}

function keepUnique() { instantAction('keep_unique'); }

let _preferField  = null;
let _preferNegate = false;

function _updatePreferLabel() {
  const baseLabels = {
    filename: 'filename',
    path:     'full path',
    type:     'file type (e.g. DNG, JPEG)',
  };
  const base = baseLabels[_preferField] || '';
  const verb = _preferNegate ? 'Discard matching' : 'Keep matching';
  document.getElementById('prefer-label').textContent = verb + ' ' + base + ':';
  const btn = document.getElementById('prefer-not-btn');
  btn.style.background    = _preferNegate ? '#ff453a' : '';
  btn.style.borderColor   = _preferNegate ? '#ff453a' : '';
  btn.style.color         = _preferNegate ? '#fff'    : '';
}

function toggleNegate() {
  _preferNegate = !_preferNegate;
  _updatePreferLabel();
}

function togglePrefer(field) {
  const row     = document.getElementById('prefer-row');
  const dateRow = document.getElementById('date-row');
  const isOpen  = field === 'date' ? dateRow.style.display !== 'none'
                                   : (_preferField === field && row.style.display !== 'none');
  // close everything
  row.style.display     = 'none';
  dateRow.style.display = 'none';
  _preferField  = null;
  _preferNegate = false;
  if (!field || isOpen) return;

  _preferField = field;

  if (field === 'date') {
    dateRow.style.display = 'flex';
    return;
  }

  const isFilename = field === 'filename';
  document.getElementById('prefer-input').value = '';
  _updatePreferLabel();
  document.getElementById('prefer-len-sep').style.display      = isFilename ? '' : 'none';
  document.getElementById('prefer-shorter-btn').style.display  = isFilename ? '' : 'none';
  document.getElementById('prefer-longer-btn').style.display   = isFilename ? '' : 'none';
  document.getElementById('prefer-has-orig-btn').style.display = isFilename ? '' : 'none';
  row.style.display = 'flex';
  document.getElementById('prefer-input').focus();
}

function applyPrefer() {
  const pattern = document.getElementById('prefer-input').value.trim();
  if (!pattern || !_preferField) return;
  const actionMap = {filename: 'prefer_filename', path: 'prefer_path', type: 'prefer_type'};
  const action = actionMap[_preferField] || 'prefer_filename';
  fetch('/api/auto-select', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, pattern, negate: _preferNegate, groups: visibleGroups()})
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert('Error: ' + data.error); return; }
    togglePrefer(null);
    loadStats();
    refreshGroups();
    if (activeKey) {
      const [strategy, group_id] = activeKey.split('/');
      loadGroup(strategy, +group_id);
    }
  });
}

function applyHasOriginal() {
  togglePrefer(null);
  instantAction('prefer_has_original_filename');
}

function applyFilenameLength(prefer) {
  togglePrefer(null);
  instantAction(prefer === 'shorter' ? 'prefer_shorter_filename' : 'prefer_longer_filename');
}

function applyDate(prefer, field) {
  const action = prefer === 'oldest'
    ? (field === 'date_original' ? 'prefer_oldest_taken' : 'prefer_oldest_added')
    : (field === 'date_original' ? 'prefer_newest_taken' : 'prefer_newest_added');
  togglePrefer(null);
  instantAction(action);
}

function noThumb() {
  return '<div class="thumb-placeholder"><div class="icon">&#9888;</div>Cannot load</div>';
}
function humanBytes(n) {
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n/1024).toFixed(0) + ' KB';
  if (n < 1073741824) return (n/1048576).toFixed(1) + ' MB';
  return (n/1073741824).toFixed(2) + ' GB';
}
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function enc(s) { return encodeURIComponent(s); }
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _DB_PATH
    parser = argparse.ArgumentParser(
        description="Serve a local web UI for browsing duplicate groups."
    )
    parser.add_argument("--db",   required=True, help="Path to the catalog SQLite database.")
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765).")
    args = parser.parse_args()

    original_db = args.db

    # For serve_duplicates, cache the DB once at startup and point all per-request
    # _conn() calls at the local copy.  sync_db_back() is called at clean shutdown.
    # (connect() is not used here because it wraps per-connection — too expensive
    #  to copy the whole DB back on every HTTP request.)
    _DB_PATH = cache_db_locally(original_db)
    ensure_decisions_table(_conn())

    # SIGTERM (sent by the GUI's Stop button) must trigger the finally block so
    # sync_db_back() runs.  Raising SystemExit propagates through finally; the
    # default SIGTERM handler would kill the process without any cleanup.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Serving at {url}  (Ctrl-C to stop)")

    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if _DB_PATH != original_db:
            sync_db_back(_DB_PATH, original_db)


if __name__ == "__main__":
    main()
