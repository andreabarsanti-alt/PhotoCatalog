"""
Find duplicate photos and store results in the duplicate_groups table.

Run from the repo root:
    python -m PhotoCatalog.find_duplicates --db catalog.db              # unified (recommended)
    python -m PhotoCatalog.find_duplicates --db catalog.db --min-score 3

Scoring (stored in match_info.score):
    phash match                    →  10 + n_other   (highest tier)
    all 5 of dims/date/type/size/name match  →  5
    4 of 5                         →  4
    3 of 5                         →  3
    2 of 5                         →  2   (minimum kept by default)

Groups are assigned IDs in descending score order so group #1 is always the
strongest match. Re-running clears and rewrites the unified strategy only.

The three individual strategies (phash / metadata / filename) are kept for
debugging but the recommended workflow is the unified strategy.
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from .db import connect, init_db


# ---------------------------------------------------------------------------
# Unified strategy (recommended)
# ---------------------------------------------------------------------------

def find_unified(conn: sqlite3.Connection, min_score: int = 2) -> tuple[int, int]:
    """
    Find duplicates using a union-find across all three signals, then score
    each resulting group on 6 binary attributes.

    Candidate signals (used to connect photos into groups):
      - perceptual hash (exact match)
      - (min_dim, max_dim, date_original, file_type)  — same as old metadata strategy
      - (filename_stem, date_minute)                  — same as old filename strategy

    Scoring attributes (computed for every resulting group regardless of how it
    was found):
      phash  — all photos share the same non-null perceptual hash
      dims   — all share (min, max) of width/height
      date   — all share exact date_original
      type   — all share file_type
      size   — all share file_size
      name   — all share at least one common filename stem

    score = (10 + n_others) if phash else n_others   where n_others ∈ 0-5
    Groups with score < min_score are discarded.
    Groups are inserted in descending score order → group #1 = strongest.
    """
    conn.execute("DELETE FROM duplicate_groups WHERE strategy = 'unified'")

    rows = conn.execute("""
        SELECT id, file_name, original_filename, date_original, file_type,
               file_size, image_width, image_height, perceptual_hash
        FROM   photos
    """).fetchall()

    total = len(rows)
    print(f"  Loaded {total:,} photos")

    photos = {r["id"]: dict(r) for r in rows}

    # ── Union-Find ──────────────────────────────────────────────────────────
    parent = {pid: pid for pid in photos}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    def union_list(ids: list) -> None:
        for i in range(1, len(ids)):
            union(ids[0], ids[i])

    # Signal 1 — perceptual hash
    hash_idx: dict = defaultdict(list)
    for pid, p in photos.items():
        if p["perceptual_hash"]:
            hash_idx[p["perceptual_hash"]].append(pid)
    for ids in hash_idx.values():
        if len(ids) >= 2:
            union_list(ids)

    # Signal 2 — dimensions + date + type  (rotation-aware)
    meta_idx: dict = defaultdict(list)
    for pid, p in photos.items():
        w, h, d, t = p["image_width"], p["image_height"], p["date_original"], p["file_type"]
        if w and h and d and t:
            key = (min(w, h), max(w, h), d, t.upper())
            meta_idx[key].append(pid)
    for ids in meta_idx.values():
        if len(ids) >= 2:
            union_list(ids)

    # Signal 3 — filename stem + date minute
    stem_idx: dict = defaultdict(set)
    for pid, p in photos.items():
        d = p["date_original"]
        if not d:
            continue
        dmin = d[:16]
        for fn in (p["file_name"], p["original_filename"]):
            if fn:
                stem = Path(fn).stem.lower()
                if stem:
                    stem_idx[(stem, dmin)].add(pid)
    for ids_set in stem_idx.values():
        ids = list(ids_set)
        if len(ids) >= 2:
            union_list(ids)

    print("  Grouping connected components…")

    # ── Collect groups ───────────────────────────────────────────────────────
    components: dict = defaultdict(list)
    for pid in photos:
        components[find(pid)].append(pid)

    candidates = [pids for pids in components.values() if len(pids) >= 2]

    # ── Score ────────────────────────────────────────────────────────────────
    scored: list[tuple] = []
    for pids in tqdm(candidates, desc=f"Scoring {len(candidates):,} candidate groups", unit="group"):
        score, signals = _score_group(pids, photos)
        if score >= min_score:
            scored.append((score, pids, signals))

    scored.sort(key=lambda x: -x[0])   # descending: group #1 = strongest
    print(f"  Keeping {len(scored):,} groups with score ≥ {min_score}")

    # ── Insert ───────────────────────────────────────────────────────────────
    inserts = []
    for group_id, (score, pids, signals) in enumerate(scored, start=1):
        info = json.dumps({"score": score, **signals})
        for pid in pids:
            inserts.append((group_id, "unified", pid, info))

    conn.executemany(
        "INSERT INTO duplicate_groups (group_id, strategy, photo_id, match_info) VALUES (?,?,?,?)",
        inserts,
    )
    conn.commit()
    return len(scored), len(inserts)


def _score_group(pids: list, photos: dict) -> tuple[int, dict]:
    """Return (score, signals_dict) for a group of photo IDs."""
    group = [photos[pid] for pid in pids]

    # phash — at least 2 photos share the same non-null hash
    hashes = [p["perceptual_hash"] for p in group if p["perceptual_hash"] is not None]
    phash = len(hashes) >= 2 and len(set(hashes)) == 1

    # dims — rotation-aware
    def _dim(p):
        w, h = p["image_width"], p["image_height"]
        return (min(w, h), max(w, h)) if w and h else None
    dims = [_dim(p) for p in group]
    dims_ok = all(d is not None for d in dims) and len(set(dims)) == 1

    # date — exact match
    dates = [p["date_original"] for p in group]
    date_ok = all(d is not None for d in dates) and len(set(dates)) == 1

    # type
    types = [(p["file_type"] or "").upper() for p in group]
    type_ok = all(types) and len(set(types)) == 1

    # size
    sizes = [p["file_size"] for p in group]
    size_ok = all(s is not None for s in sizes) and len(set(sizes)) == 1

    # name — at least one common stem across all photos (checking both filenames)
    def _stems(p):
        out = set()
        for fn in (p["file_name"], p["original_filename"]):
            if fn:
                s = Path(fn).stem.lower()
                if s:
                    out.add(s)
        return out
    all_stems = [_stems(p) for p in group]
    common = all_stems[0].copy() if all_stems else set()
    for s in all_stems[1:]:
        common &= s
    name_ok = len(common) > 0

    n_other = sum([dims_ok, date_ok, type_ok, size_ok, name_ok])
    score   = (10 + n_other) if phash else n_other

    return score, {
        "phash": phash,
        "dims":  dims_ok,
        "date":  date_ok,
        "type":  type_ok,
        "size":  size_ok,
        "name":  name_ok,
    }


# ---------------------------------------------------------------------------
# Individual strategies (kept for debugging / targeted re-runs)
# ---------------------------------------------------------------------------

def find_by_phash(conn: sqlite3.Connection) -> tuple[int, int]:
    conn.execute("DELETE FROM duplicate_groups WHERE strategy = 'phash'")
    rows = conn.execute("""
        SELECT perceptual_hash, GROUP_CONCAT(id) AS ids
        FROM   photos
        WHERE  perceptual_hash IS NOT NULL
        GROUP  BY perceptual_hash
        HAVING COUNT(*) > 1
    """).fetchall()
    inserts = []
    group_id = 1
    for row in rows:
        photo_ids = [int(x) for x in row["ids"].split(",")]
        info = json.dumps({"hamming": 0})
        for pid in photo_ids:
            inserts.append((group_id, "phash", pid, info))
        group_id += 1
    conn.executemany(
        "INSERT INTO duplicate_groups (group_id, strategy, photo_id, match_info) VALUES (?,?,?,?)",
        inserts,
    )
    conn.commit()
    return group_id - 1, len(inserts)


def find_by_metadata(conn: sqlite3.Connection) -> tuple[int, int]:
    conn.execute("DELETE FROM duplicate_groups WHERE strategy = 'metadata'")
    rows = conn.execute("""
        SELECT
            MIN(image_width,  image_height) AS short_side,
            MAX(image_width,  image_height) AS long_side,
            date_original,
            file_type,
            GROUP_CONCAT(id)                AS ids
        FROM   photos
        WHERE  image_width   IS NOT NULL
          AND  image_height  IS NOT NULL
          AND  date_original IS NOT NULL
          AND  file_type     IS NOT NULL
        GROUP  BY short_side, long_side, date_original, file_type
        HAVING COUNT(*) > 1
    """).fetchall()
    inserts = []
    group_id = 1
    for row in rows:
        photo_ids = [int(x) for x in row["ids"].split(",")]
        info = json.dumps({
            "short_side":    row["short_side"],
            "long_side":     row["long_side"],
            "date_original": row["date_original"],
            "file_type":     row["file_type"],
        })
        for pid in photo_ids:
            inserts.append((group_id, "metadata", pid, info))
        group_id += 1
    conn.executemany(
        "INSERT INTO duplicate_groups (group_id, strategy, photo_id, match_info) VALUES (?,?,?,?)",
        inserts,
    )
    conn.commit()
    return group_id - 1, len(inserts)


def find_by_filename(conn: sqlite3.Connection) -> tuple[int, int]:
    conn.execute("DELETE FROM duplicate_groups WHERE strategy = 'filename'")
    rows = conn.execute("""
        SELECT id, source_type, file_name, original_filename, date_original
        FROM   photos
        WHERE  date_original IS NOT NULL
    """).fetchall()

    buckets: dict = defaultdict(list)
    for row in rows:
        date_key = row["date_original"][:16]
        s = _stem(row["file_name"])
        if s:
            buckets[(s, date_key)].append((row["id"], "file_name"))
        if row["source_type"] == "MacPhotos" and row["original_filename"]:
            s2 = _stem(row["original_filename"])
            if s2 and s2 != s:
                buckets[(s2, date_key)].append((row["id"], "original_filename"))

    inserts = []
    group_id = 1
    for (stem, date_key), entries in buckets.items():
        seen: dict = {}
        for pid, field in entries:
            if pid not in seen:
                seen[pid] = field
        if len(seen) < 2:
            continue
        info_base = {"stem": stem, "date_key": date_key}
        for pid, field in seen.items():
            inserts.append((group_id, "filename", pid, json.dumps({**info_base, "matched_field": field})))
        group_id += 1

    conn.executemany(
        "INSERT OR IGNORE INTO duplicate_groups (group_id, strategy, photo_id, match_info) VALUES (?,?,?,?)",
        inserts,
    )
    conn.execute("""
        DELETE FROM duplicate_groups
        WHERE  strategy = 'filename'
          AND  group_id IN (
              SELECT group_id FROM duplicate_groups
              WHERE  strategy = 'filename'
              GROUP  BY group_id HAVING COUNT(*) < 2)
    """)
    conn.commit()
    result = conn.execute("""
        SELECT COUNT(DISTINCT group_id) AS groups, COUNT(*) AS matched
        FROM   duplicate_groups WHERE strategy = 'filename'
    """).fetchone()
    return result["groups"], result["matched"]


def _stem(filename: str | None) -> str | None:
    if not filename:
        return None
    return Path(filename).stem.lower() or None


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT strategy,
               COUNT(DISTINCT group_id) AS groups,
               COUNT(*)                 AS photos
        FROM   duplicate_groups
        GROUP  BY strategy
        ORDER  BY strategy
    """).fetchall()
    if not rows:
        print("No duplicate groups found.")
        return
    print(f"\n{'Strategy':<12}  {'Groups':>8}  {'Photos':>14}")
    print("-" * 38)
    for r in rows:
        print(f"{r['strategy']:<12}  {r['groups']:>8}  {r['photos']:>14}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find duplicate photos and store results in duplicate_groups."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--strategy", default="unified",
        choices=["unified", "phash", "metadata", "filename", "all"],
        help="unified (default) runs the scored union-find. "
             "'all' runs unified + the three individual strategies.",
    )
    parser.add_argument(
        "--min-score", type=int, default=2, dest="min_score",
        help="Minimum confidence score to keep a group (default: 2, range 1-15).",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)

    run = {
        "unified":  [("unified",  lambda c: find_unified(c, args.min_score))],
        "phash":    [("phash",    find_by_phash)],
        "metadata": [("metadata", find_by_metadata)],
        "filename": [("filename", find_by_filename)],
        "all":      [
            ("unified",  lambda c: find_unified(c, args.min_score)),
            ("phash",    find_by_phash),
            ("metadata", find_by_metadata),
            ("filename", find_by_filename),
        ],
    }[args.strategy]

    try:
        for name, fn in run:
            print(f"Running {name} strategy…")
            groups, matched = fn(conn)
            print(f"  → {groups} groups, {matched} photos matched")
        _print_summary(conn)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
