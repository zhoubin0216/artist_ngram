"""Collect OpenAlex title/abstract mentions for the existing Wikidata artist dictionary.

Art-focused scope; consult README.md. Searches only the canonical
full English name in title/abstract. All work types are preserved.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.openalex.org/works"
START_YEAR, END_YEAR = 1800, 2019
SELECT = "id,doi,title,publication_year,type,language,abstract_inverted_index,topics,primary_topic"
FIELDS = ("artist_id", "canonical_name", "birth_year", "death_year", "active_start", "active_end", "nationality", "movement_ids", "movement_names", "ulan_id", "source_url")
VISUAL_ARTS_SUBFIELDS = {"visual arts and performing arts"}
VISUAL_ARTS_SUBFIELD_ID = "1213"  # Official OpenAlex Visual Arts and Performing Arts subfield.


def is_visual_arts_topic(topic: dict | None) -> bool:
    """Recognize the official subfield by ID; support name-only historical snapshots."""
    sub = (topic or {}).get("subfield") or {}
    id_value = str(sub.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
    return id_value == VISUAL_ARTS_SUBFIELD_ID or (sub.get("display_name") or "").casefold() in VISUAL_ARTS_SUBFIELDS


def art_membership(work: dict) -> tuple[bool, bool]:
    """(any visual-arts topic, primary visual-arts topic), never inferred from title keywords."""
    primary = is_visual_arts_topic(work.get("primary_topic"))
    any_topic = primary or any(is_visual_arts_topic(t) for t in (work.get("topics") or []))
    return any_topic, primary


def art_filter(scope: str) -> str:
    if scope == "any":
        return f"topics.subfield.id:{VISUAL_ARTS_SUBFIELD_ID}"
    if scope == "primary":
        return f"primary_topic.subfield.id:{VISUAL_ARTS_SUBFIELD_ID}"
    raise ValueError(f"Unknown art topic scope: {scope}")


def quality_flags(row: dict, artist: dict) -> list[str]:
    """Non-destructive QA flags. Absence of flags does NOT confirm entity identity."""
    flags = []
    birth = str(artist.get("birth_year") or "").strip()
    if birth.isdigit() and row["publication_year"] < int(birth):
        flags.append("publication_before_artist_birth")
    if row["match_in_abstract"] and len(row["abstract"]) > 4000:
        flags.append("unusually_long_abstract")
    if row["is_visual_arts"] and not row["primary_is_visual_arts"]:
        flags.append("visual_arts_secondary_topic_only")
    return flags


def normalize(text: str) -> str:
    """Normalize spacing, accents and punctuation for conservative name verification."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def contains_full_name(text: str, name: str) -> bool:
    value, needle = normalize(text), normalize(name)
    return bool(needle and re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", value))


def restore_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    indexed = [(int(pos), token) for token, positions in inverted.items() for pos in positions]
    if not indexed:
        return ""
    words = [""] * (max(p for p, _ in indexed) + 1)
    for pos, token in indexed:
        words[pos] = token
    return " ".join(words).strip()


def read_artists(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Artist dictionary CSV not found: {path}. Run the JS dictionary builder first.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValueError(f"Wrong dictionary columns in {path}: {reader.fieldnames}")
        artists = [row for row in reader if row["artist_id"] and row["canonical_name"]]
    if len({a['artist_id'] for a in artists}) != len(artists):
        raise ValueError("Duplicate Wikidata artist IDs in dictionary")
    return artists


def classify(topics: list | None) -> tuple[bool, bool]:
    """A broad Arts & Humanities proxy; the narrow Visual Arts tag is separate."""
    arts_humanities, visual_arts = False, False
    for topic in topics or []:
        field = (topic.get("field") or {}).get("display_name", "").casefold()
        subfield = (topic.get("subfield") or {}).get("display_name", "").casefold()
        arts_humanities |= field == "arts and humanities"
        visual_arts |= subfield in VISUAL_ARTS_SUBFIELDS
    return arts_humanities, visual_arts


def make_row(work: dict, artist: dict, topic_scope: str = "any") -> dict | None:
    title = work.get("title") or ""
    abstract = restore_abstract(work.get("abstract_inverted_index"))
    name = artist["canonical_name"]
    in_title = contains_full_name(title, name)
    in_abstract = contains_full_name(abstract, name)
    if not (in_title or in_abstract):
        return None  # Search-engine candidates must be verifiable in retrieved fields.
    year = work.get("publication_year")
    if not isinstance(year, int) or not START_YEAR <= year <= END_YEAR:
        return None
    if work.get("language") != "en" or not work.get("id"):
        return None
    any_art, primary_art = art_membership(work)
    if not (primary_art if topic_scope == "primary" else any_art):
        return None
    arts_humanities, visual_arts = classify(work.get("topics"))
    primary = work.get("primary_topic") or {}
    row = {
        "artist_id": artist["artist_id"],
        "artist_name": name,
        "work_id": work["id"],
        "doi": work.get("doi"),
        "publication_year": year,
        "type": work.get("type") or "unknown",
        "language": "en",
        "title": title,
        "abstract": abstract,
        "has_abstract": bool(abstract),
        "match_in_title": in_title,
        "match_in_abstract": in_abstract,
        "is_arts_humanities": arts_humanities,
        "is_visual_arts": bool(any_art),
        "primary_is_visual_arts": bool(primary_art),
        "topic_scope": topic_scope,
        "primary_field": (primary.get("field") or {}).get("display_name", ""),
        "primary_subfield": (primary.get("subfield") or {}).get("display_name", ""),
    }
    row["risk_flags"] = quality_flags(row, artist)
    return row


def request_page(session: requests.Session, params: dict, key: str, delay: float) -> dict:
    headers = {"User-Agent": "artist-ngram-research/1.0 (academic reproducibility)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    for attempt in range(6):
        if delay:
            time.sleep(delay)
        try:
            response = session.get(API, params=params, headers=headers, timeout=50)
        except requests.RequestException as ex:
            if attempt == 5:
                raise RuntimeError(f"OpenAlex network failure: {ex}") from ex
            time.sleep(min(2 ** attempt, 30))
            continue
        if response.status_code == 429:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                raise RuntimeError("OpenAlex daily credit budget exhausted. Resume on another day.")
            if attempt == 5:
                raise RuntimeError("OpenAlex request throttled repeatedly (429). Stop and resume later.")
            try:
                wait = min(float(response.headers.get("Retry-After", 2 ** attempt)), 60)
            except ValueError:
                wait = min(2 ** attempt, 60)
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            if attempt == 5:
                response.raise_for_status()
            time.sleep(min(2 ** attempt, 30))
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAlex HTTP {response.status_code}: {response.text[:500]}")
        return response.json()
    raise RuntimeError("Could not retrieve an OpenAlex page")


def collect_one(session: requests.Session, artist: dict, out_path: Path,
                api_key: str, delay: float, max_pages: int | None,
                topic_scope: str = "any", corpus: str = "core") -> dict:
    tmp = out_path.with_suffix(".tmp")
    candidates_path = out_path.parent.parent / "candidates" / out_path.name
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    candidates_tmp = candidates_path.with_suffix(".tmp")
    seen: set[str] = set()
    cursor = "*"
    accepted = rejected = pages = candidate_count = 0
    total_search_hits = None
    complete = True
    try:
        with tmp.open("w", encoding="utf-8") as stream, \
             candidates_tmp.open("w", encoding="utf-8") as candidate_stream:
            while cursor:
                params = {
                    "search.title_and_abstract": f'"{artist["canonical_name"].replace(chr(34), "")}"',
                    "filter": (f"from_publication_date:{START_YEAR}-01-01,"
                               f"to_publication_date:{END_YEAR}-12-31,language:en,"
                               f"{art_filter(topic_scope)}"),
                    "per_page": 100,
                    "cursor": cursor,
                    "select": SELECT,
                }
                if corpus == "core":
                    params["corpus"] = "core"
                data = request_page(session, params, api_key, delay)
                pages += 1
                total_search_hits = (data.get("meta") or {}).get("count", total_search_hits)
                works = data.get("results") or []
                for work in works:
                    row = make_row(work, artist, topic_scope)
                    candidate_stream.write(json.dumps({
                        "artist_id": artist["artist_id"],
                        "query_name": artist["canonical_name"],
                        "locally_verified": row is not None,
                        "work": work,
                    }, ensure_ascii=False) + "\n")
                    candidate_count += 1
                    if row is None:
                        rejected += 1
                        continue
                    if row["work_id"] in seen:
                        continue
                    seen.add(row["work_id"])
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    accepted += 1
                cursor = (data.get("meta") or {}).get("next_cursor") if works else None
                if max_pages is not None and pages >= max_pages and cursor:
                    complete = False
                    break
        os.replace(candidates_tmp, candidates_path)
        os.replace(tmp, out_path)
    except Exception:
        candidates_tmp.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        raise
    return {"artist_id": artist["artist_id"], "artist_name": artist["canonical_name"],
            "status": "complete" if complete else "partial", "pages": pages,
            "api_search_hits": total_search_hits, "accepted_works": accepted,
            "unverified_candidates": rejected, "api_candidates_saved": candidate_count,
            "topic_scope": topic_scope, "art_subfield_id": VISUAL_ARTS_SUBFIELD_ID,
            "corpus": corpus, "candidate_archive_complete": complete}


def save_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def summarize(work_dir: Path, artists: list[dict]) -> None:
    """Regenerate results from complete and partial artist files, including zeros at year-level only when present."""
    stats_path = work_dir / "collection_status.json"
    status = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    yearly: dict[tuple[str, int], Counter] = defaultdict(Counter)
    by_type: dict[tuple[str, int, str], int] = defaultdict(int)
    artist_name = {a["artist_id"]: a["canonical_name"] for a in artists}
    for artist in artists:
        qid = artist["artist_id"]
        file = work_dir / "raw" / f"{qid}.jsonl"
        if not file.exists():
            continue
        seen: set[str] = set()
        with file.open(encoding="utf-8") as stream:
            for line in stream:
                work = json.loads(line)
                work_id = work["work_id"]
                if work_id in seen:
                    continue
                seen.add(work_id)
                year = int(work["publication_year"])
                c = yearly[(qid, year)]
                c["art_works_count"] += 1
                c["primary_visual_arts_count"] += int(work.get("primary_is_visual_arts", False))
                c["flagged_works_count"] += int(bool(work.get("risk_flags")))
                c["arts_humanities_count"] += int(work["is_arts_humanities"])
                c["visual_arts_count"] += int(work["is_visual_arts"])
                c["with_abstract_count"] += int(work["has_abstract"])
                c["title_match_count"] += int(work["match_in_title"])
                by_type[(qid, year, work["type"])] += 1
    annual = []
    for (qid, year), c in sorted(yearly.items()):
        annual.append({"artist_id": qid, "artist_name": artist_name[qid], "year": year,
                       "is_complete": int(status.get(qid, {}).get("status") == "complete"),
                       **{k: c[k] for k in ["art_works_count", "primary_visual_arts_count",
                                           "flagged_works_count", "with_abstract_count", "title_match_count"]}})
    write_csv(work_dir / "artist_year.csv", ["artist_id", "artist_name", "year", "is_complete",
              "art_works_count", "primary_visual_arts_count", "flagged_works_count",
              "with_abstract_count", "title_match_count"], annual)
    type_rows = [{"artist_id": k[0], "year": k[1], "type": k[2], "works_count": n}
                 for k, n in sorted(by_type.items())]
    write_csv(work_dir / "artist_year_type.csv", ["artist_id", "year", "type", "works_count"], type_rows)
    status_rows = [status[a["artist_id"]] for a in artists if a["artist_id"] in status]
    write_csv(work_dir / "artist_collection_status.csv", ["artist_id", "artist_name", "status", "pages",
              "api_search_hits", "accepted_works", "unverified_candidates", "api_candidates_saved",
              "topic_scope", "art_subfield_id", "corpus", "candidate_archive_complete"], status_rows)
    print(f"Summary: {len(status_rows)} artists attempted; "
          f"{sum(s['status'] == 'complete' for s in status_rows)} completed; "
          f"{len(annual)} artist-year rows; outputs in {work_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dictionary", type=Path, default=ROOT / "outputs" / "artist_dictionary.csv")
    parser.add_argument("--mode", choices=["pilot", "full"], default="pilot")
    parser.add_argument("--topic-scope", choices=["any", "primary"], default="any",
                        help="Art topic filter: any of top 3 topics (default), or only primary topic")
    parser.add_argument("--corpus", choices=["core", "all"], default="core",
                        help="core (default, curated) or all OpenAlex works; separate result folders")
    parser.add_argument("--artists", help="Comma-separated Wikidata QIDs (e.g. Q296,Q5582)")
    parser.add_argument("--limit-artists", type=int, help="Only first N selected artists")
    parser.add_argument("--max-pages", type=int, help="PILOT ONLY: cap retrieval pages, creating partial records")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between requests")
    parser.add_argument("--summary-only", action="store_true", help="Rebuild summaries without API calls")
    args = parser.parse_args()
    if args.mode == "full" and args.max_pages is not None:
        parser.error("--max-pages is not supported for --mode full: it would undercount results")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be >= 1")
    if args.limit_artists is not None and args.limit_artists < 1:
        parser.error("--limit-artists must be >= 1")
    all_artists = read_artists(args.dictionary)
    artists = list(all_artists)
    wanted = [v.strip() for v in args.artists.split(",") if v.strip()] if args.artists else None
    if wanted:
        index = {a["artist_id"]: a for a in artists}
        absent = set(wanted) - index.keys()
        if absent:
            parser.error(f"QIDs not in dictionary: {sorted(absent)}")
        artists = [index[q] for q in dict.fromkeys(wanted)]
    if args.limit_artists:
        artists = artists[:args.limit_artists]
    # A new path prevents accidentally reusing earlier all-discipline data.
    work_dir = ROOT / "data" / "openalex_art" / args.corpus / args.topic_scope / args.mode
    (work_dir / "raw").mkdir(parents=True, exist_ok=True)
    status_file = work_dir / "collection_status.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    if args.summary_only:
        summarize(work_dir, all_artists)
        return 0
    print(f"Selected {len(artists)} artists; years {START_YEAR}-{END_YEAR}; "
          f"English canonical full name only; Visual Arts subfield={VISUAL_ARTS_SUBFIELD_ID}; "
          f"topic_scope={args.topic_scope}; corpus={args.corpus}; all work types; mode={args.mode}")
    key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not key:
        print("WARNING: OPENALEX_API_KEY is not set; keyless usage has a small daily budget.")
    session = requests.Session()
    for idx, artist in enumerate(artists, 1):
        qid = artist["artist_id"]
        out_path = work_dir / "raw" / f"{qid}.jsonl"
        if out_path.exists() and status.get(qid, {}).get("status") == "complete":
            print(f"[{idx}/{len(artists)}] {artist['canonical_name']} -- cached complete")
            continue
        try:
            result = collect_one(session, artist, out_path, key, args.delay, args.max_pages,
                                 args.topic_scope, args.corpus)
        except Exception as ex:
            print(f"STOP at {artist['canonical_name']} ({qid}): {ex}", file=sys.stderr)
            save_json(status_file, status)
            summarize(work_dir, all_artists)
            return 1
        status[qid] = result
        save_json(status_file, status)
        print(f"[{idx}/{len(artists)}] {artist['canonical_name']}: "
              f"{result['accepted_works']} verified matches, {result['pages']} pages, {result['status']}")
    summarize(work_dir, all_artists)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
