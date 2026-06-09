"""
Find duplicate photos and store results in the duplicate_groups table.

Run from the repo root:
    python -m PhotoCatalog.find_duplicates --db catalog.db
    python -m PhotoCatalog.find_duplicates --db catalog.db --signal phash fuzzy --logic OR
    python -m PhotoCatalog.find_duplicates --db catalog.db --signal phash dims date --logic AND --keep-resolved

Every run writes to strategy='groups' and clears existing unresolved groups first.
match_info always contains all 8 signal results so the review UI can filter freely.
"""
import argparse
import json
import signal
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from .db import connect, init_db


_FUZZY_HAMMING = 10   # Hamming distance threshold for perceptual_hash_small (64-bit)
_BLANK_BITS    = 10   # hashes with fewer set bits than this are considered degenerate
VALID_SIGNALS  = ('phash', 'fuzzy', 'dims', 'date', 'type', 'size', 'name', 'blank')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _is_blank(p) -> bool:
    h = p["perceptual_hash"]
    return bool(h) and bin(int(h, 16)).count('1') < _BLANK_BITS


def _stem(filename: str | None) -> str | None:
    if not filename:
        return None
    return Path(filename).stem.lower() or None


def _resolved_photo_ids(conn: sqlite3.Connection) -> set[int]:
    """
    Return all photo IDs that belong to resolved 'groups' strategy groups.
    A group is resolved if it has exactly one photo with action='keep'.
    """
    rows = conn.execute("""
        SELECT dg.photo_id
        FROM   duplicate_groups dg
        WHERE  dg.strategy = 'groups'
          AND  dg.group_id IN (
              SELECT g.group_id
              FROM   duplicate_groups g
              LEFT JOIN decisions d ON d.photo_id = g.photo_id AND d.action = 'keep'
              WHERE  g.strategy = 'groups'
              GROUP  BY g.group_id
              HAVING SUM(d.photo_id IS NOT NULL) = 1
          )
    """).fetchall()
    return {r[0] for r in rows}


def _max_group_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(group_id) FROM duplicate_groups WHERE strategy = 'groups'"
    ).fetchone()
    return row[0] or 0


# ---------------------------------------------------------------------------
# Signal scoring  (always computes all 7 signals for match_info)
# ---------------------------------------------------------------------------

def _score_group(pids: list, photos: dict) -> tuple[int, dict]:
    """Return (score, signals_dict) for a group of photo IDs."""
    group = [photos[pid] for pid in pids]

    hashes = [p["perceptual_hash"] for p in group if p["perceptual_hash"] is not None]
    phash = len(hashes) >= 2 and len(set(hashes)) == 1

    def _dim(p):
        w, h = p["image_width"], p["image_height"]
        return (min(w, h), max(w, h)) if w and h else None
    dims = [_dim(p) for p in group]
    dims_ok = all(d is not None for d in dims) and len(set(dims)) == 1

    dates = [p["date_original"] for p in group]
    date_ok = all(d is not None for d in dates) and len(set(dates)) == 1

    types = [(p["file_type"] or "").upper() for p in group]
    type_ok = all(types) and len(set(types)) == 1

    sizes = [p["file_size"] for p in group]
    size_ok = all(s is not None for s in sizes) and len(set(sizes)) == 1

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

    small_hashes = [p["perceptual_hash_small"] for p in group if p["perceptual_hash_small"]]
    fuzzy = False
    if len(small_hashes) >= 2:
        ints = [int(h, 16) for h in small_hashes]
        for i in range(len(ints)):
            for j in range(i + 1, len(ints)):
                if _hamming(ints[i], ints[j]) <= _FUZZY_HAMMING:
                    fuzzy = True
                    break
            if fuzzy:
                break

    blank_ok = all(_is_blank(p) for p in group)

    n_other = sum([dims_ok, date_ok, type_ok, size_ok, name_ok, fuzzy])
    score   = (10 + n_other) if phash else n_other

    return score, {
        "phash": phash, "fuzzy": fuzzy, "dims": dims_ok,
        "date": date_ok, "type": type_ok, "size": size_ok, "name": name_ok,
        "blank": blank_ok,
    }


# ---------------------------------------------------------------------------
# Grouping algorithms
# ---------------------------------------------------------------------------

def _bucket_key(sig: str, p) -> object:
    """Return the bucket value for a single signal, or None if data is missing."""
    if sig == 'phash':
        return p["perceptual_hash"] or None
    if sig == 'dims':
        w, h = p["image_width"], p["image_height"]
        return (min(w, h), max(w, h)) if w and h else None
    if sig == 'date':
        return p["date_original"] or None
    if sig == 'type':
        return p["file_type"].upper() if p["file_type"] else None
    if sig == 'size':
        return p["file_size"] if p["file_size"] is not None else None
    if sig == 'name':
        d = p["date_original"]
        fn = p["file_name"] or p["original_filename"]
        if not d or not fn:
            return None
        s = Path(fn).stem.lower()
        return (s, d[:16]) if s else None
    if sig == 'blank':
        return True if _is_blank(p) else None
    return None


def _build_uf(photo_ids) -> tuple[dict, callable, callable]:
    parent = {pid: pid for pid in photo_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    return parent, find, union


def _union_bucket_idx(idx: dict, union) -> None:
    for ids in idx.values():
        if len(ids) >= 2:
            for i in range(1, len(ids)):
                union(ids[0], ids[i])


def _apply_fuzzy(photos: dict, union) -> None:
    """Union-find on fuzzy Hamming distance using band-based candidate generation."""
    small_list = [(pid, p["perceptual_hash_small"])
                  for pid, p in photos.items() if p["perceptual_hash_small"]]
    if len(small_list) < 2:
        return
    hash_ints: dict[int, int] = {pid: int(h, 16) for pid, h in small_list}
    seen: set[tuple] = set()
    band_idx: list[dict] = [defaultdict(list) for _ in range(8)]
    for pid, h_int in hash_ints.items():
        for band in range(8):
            band_idx[band][(h_int >> (band * 8)) & 0xFF].append(pid)
    for band in range(8):
        for bucket in band_idx[band].values():
            if len(bucket) < 2:
                continue
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    a, b = bucket[i], bucket[j]
                    key = (a, b) if a < b else (b, a)
                    if key not in seen:
                        seen.add(key)
                        if _hamming(hash_ints[a], hash_ints[b]) <= _FUZZY_HAMMING:
                            union(a, b)


def _collect_groups(photos: dict, find) -> list[list[int]]:
    components: dict = defaultdict(list)
    for pid in photos:
        components[find(pid)].append(pid)
    return [pids for pids in components.values() if len(pids) >= 2]


def _group_or(photos: dict, signals: list[str]) -> list[list[int]]:
    """Union-find: photos connected if they match on ANY selected signal."""
    parent, find, union = _build_uf(photos)

    for sig in signals:
        if sig == 'fuzzy':
            continue
        if sig == 'name':
            idx: dict = defaultdict(set)
            for pid, p in photos.items():
                d = p["date_original"]
                if not d:
                    continue
                dmin = d[:16]
                for fn in (p["file_name"], p["original_filename"]):
                    if fn:
                        s = Path(fn).stem.lower()
                        if s:
                            idx[(s, dmin)].add(pid)
            for ids_set in idx.values():
                ids = list(ids_set)
                if len(ids) >= 2:
                    for i in range(1, len(ids)):
                        union(ids[0], ids[i])
        else:
            idx = defaultdict(list)
            for pid, p in photos.items():
                val = _bucket_key(sig, p)
                if val is not None:
                    idx[val].append(pid)
            _union_bucket_idx(idx, union)

    if 'fuzzy' in signals:
        _apply_fuzzy(photos, union)

    return _collect_groups(photos, find)


def _group_and(photos: dict, signals: list[str]) -> list[list[int]]:
    """
    Compound bucketing: photos grouped only when they match on ALL selected signals.
    fuzzy (if selected) is applied as a secondary filter within each compound bucket.
    """
    bucket_sigs = [s for s in signals if s != 'fuzzy']
    has_fuzzy   = 'fuzzy' in signals

    if not bucket_sigs:
        # Only fuzzy selected — behave like OR
        return _group_or(photos, ['fuzzy'])

    # Build compound key per photo
    idx: dict = defaultdict(list)
    for pid, p in photos.items():
        parts = []
        valid = True
        for sig in bucket_sigs:
            if sig == 'name':
                # name signal: use (stem, date_minute) of the primary filename
                d = p["date_original"]
                fn = p["file_name"] or p["original_filename"]
                if not d or not fn:
                    valid = False
                    break
                s = Path(fn).stem.lower()
                if not s:
                    valid = False
                    break
                parts.append(('name', s, d[:16]))
            else:
                val = _bucket_key(sig, p)
                if val is None:
                    valid = False
                    break
                parts.append(val)
        if valid:
            idx[tuple(parts)].append(pid)

    if not has_fuzzy:
        return [ids for ids in idx.values() if len(ids) >= 2]

    # Within each compound bucket, additionally apply fuzzy union-find
    result = []
    for ids in idx.values():
        if len(ids) < 2:
            continue
        sub_photos = {pid: photos[pid] for pid in ids}
        sub_groups = _group_or(sub_photos, ['fuzzy'])
        result.extend(sub_groups)
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def find_groups(
    conn: sqlite3.Connection,
    signals: list[str],
    logic: str = 'OR',
    keep_resolved: bool = True,
) -> tuple[int, int]:
    """
    Find duplicate groups based on selected signals and AND/OR logic.

    signals:       subset of VALID_SIGNALS — signals used to form groups
    logic:         'OR'  — any signal match connects two photos
                   'AND' — all signals must match for photos to be grouped
    keep_resolved: if True, photos in resolved groups (exactly one 'keep' decision)
                   are excluded from the search and their groups are preserved
    """
    # ── Determine excluded photos ──────────────────────────────────────────
    excluded: set[int] = set()
    if keep_resolved:
        excluded = _resolved_photo_ids(conn)
        if excluded:
            print(f"  Preserving {len(excluded):,} photos in resolved groups")

    # ── Load candidates ────────────────────────────────────────────────────
    rows = conn.execute("""
        SELECT id, file_name, original_filename, date_original, file_type,
               file_size, image_width, image_height, perceptual_hash, perceptual_hash_small
        FROM   photos
    """).fetchall()
    photos = {r["id"]: r for r in rows if r["id"] not in excluded}
    print(f"  Loaded {len(photos):,} candidate photos")

    # ── Group ──────────────────────────────────────────────────────────────
    if logic == 'AND':
        groups_raw = _group_and(photos, signals)
    else:
        groups_raw = _group_or(photos, signals)
    print(f"  Found {len(groups_raw):,} raw groups")

    # ── Score and sort ─────────────────────────────────────────────────────
    scored = []
    for pids in tqdm(groups_raw, desc="Scoring groups", unit="group", leave=False):
        score, sigs = _score_group(pids, photos)
        scored.append((score, pids, sigs))
    scored.sort(key=lambda x: -x[0])

    # ── Remove legacy strategy rows from pre-v1.1.1 (unified/phash/metadata/filename)
    conn.execute(
        "DELETE FROM duplicate_groups WHERE strategy IN ('unified','phash','metadata','filename')"
    )

    # ── Clear old unresolved groups ────────────────────────────────────────
    if keep_resolved and excluded:
        # Delete only the non-resolved groups
        conn.execute("""
            DELETE FROM duplicate_groups
            WHERE  strategy = 'groups'
              AND  group_id NOT IN (
                  SELECT DISTINCT group_id FROM duplicate_groups
                  WHERE  strategy = 'groups' AND photo_id IN ({})
              )
        """.format(','.join('?' * len(excluded))), list(excluded))
        start_id = _max_group_id(conn) + 1
    else:
        conn.execute("DELETE FROM duplicate_groups WHERE strategy = 'groups'")
        start_id = 1

    # ── Insert ─────────────────────────────────────────────────────────────
    inserts = []
    for i, (score, pids, sigs) in enumerate(scored):
        gid = start_id + i
        info = json.dumps({"score": score, **sigs})
        for pid in pids:
            inserts.append((gid, 'groups', pid, info))

    conn.executemany(
        "INSERT INTO duplicate_groups (group_id, strategy, photo_id, match_info) VALUES (?,?,?,?)",
        inserts,
    )
    conn.commit()
    return len(scored), len(inserts)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT COUNT(DISTINCT group_id) AS groups, COUNT(*) AS photos
        FROM   duplicate_groups
        WHERE  strategy = 'groups'
    """).fetchone()
    if not rows or not rows["groups"]:
        print("No duplicate groups found.")
    else:
        print(f"\n{rows['groups']:,} groups, {rows['photos']:,} photos matched")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # SIGTERM (sent by the GUI's Stop button) must run finally blocks so the
    # local DB cache is synced back to the external drive before we exit.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))

    parser = argparse.ArgumentParser(
        description="Find duplicate photos and store results in duplicate_groups."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--signal", nargs="+", default=["phash"],
        choices=list(VALID_SIGNALS),
        metavar="SIGNAL",
        help=f"Signals used to form groups. One or more of: {', '.join(VALID_SIGNALS)}. Default: phash",
    )
    parser.add_argument(
        "--logic", default="OR", choices=["AND", "OR"],
        help="AND — all signals must match; OR — any signal suffices. Default: OR",
    )
    parser.add_argument(
        "--keep-resolved", action="store_true",
        help="Preserve groups that already have a unique 'keep' decision; re-run only on the rest.",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)
    try:
        signals = list(dict.fromkeys(args.signal))   # deduplicate, preserve order
        print(f"Signals : {', '.join(signals)}")
        print(f"Logic   : {args.logic}")
        print(f"DB      : {args.db}")
        print()
        groups, matched = find_groups(
            conn,
            signals=signals,
            logic=args.logic,
            keep_resolved=args.keep_resolved,
        )
        print(f"  → {groups:,} groups, {matched:,} photos matched")
        _print_summary(conn)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
