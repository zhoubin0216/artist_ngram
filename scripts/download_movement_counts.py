#!/usr/bin/env python3
"""Plan and extract the 15 reviewed movement queries, retaining raw counts only."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlencode

import ngram_sample as source
from download_artist_counts import checkpoint_path, extract_one, read_checkpoint

# Primary candidates from the reviewed M01-M15 query table, not alias expansion.
PRIMARY = [
    ("M01", "neoclassicism", "新古典主义", "Neoclassicism"),
    ("M02", "romanticism", "浪漫主义", "Romanticism"),
    ("M03", "realism", "现实主义", "Realism"),
    ("M04", "academic_art", "学院派艺术", "academic art"),
    ("M05", "impressionism", "印象派", "Impressionism"),
    ("M06", "post_impressionism", "后印象派", "Post-Impressionism"),
    ("M07", "symbolism", "象征主义", "Symbolism"),
    ("M08", "art_nouveau", "新艺术运动", "Art Nouveau"),
    ("M09", "fauvism", "野兽派", "Fauvism"),
    ("M10", "expressionism", "表现主义", "Expressionism"),
    ("M11", "cubism", "立体主义", "Cubism"),
    ("M12", "futurism", "未来主义", "Futurism"),
    ("M13", "dada", "达达主义", "Dadaism"),
    ("M14", "surrealism", "超现实主义", "Surrealism"),
    ("M15", "abstract_art", "抽象艺术", "abstract art"),
]


def normalize_query(requested):
    url = "https://books.google.com/ngrams/json?" + urlencode({
        "content": requested, "year_start": 1800, "year_end": 2019,
        "corpus": 26, "smoothing": 0})  # English (2019), corresponds to 20200217.
    with source.request(url) as response:
        result = json.load(response)
    if len(result) != 1 or result[0].get("type") != "NGRAM" or result[0].get("parent"):
        raise ValueError("Ambiguous/absent Viewer normalization: " + requested)
    query = result[0]["ngram"]
    expected = "Post - Impressionism" if requested == "Post-Impressionism" else requested
    if query != expected:
        raise ValueError(f"Unexpected tokenization {requested!r} -> {query!r}; review before planning")
    return query, {"url": url, "requested_query": requested, "returned_ngram": query,
                   "type": "NGRAM", "checked_at": source.utc_now(),
                   "purpose": "Tokenization metadata only; Viewer frequencies are not used as raw counts."}


def plan(root, path):
    if path.exists():
        raise ValueError("Manifest already exists; reuse it or choose a new path")
    entities = source.read_json(root / "data/wikidata/movements_entities.json")["entities"]
    indexes, shards, totals, movements = {}, {}, {}, []
    for movement_id, slug, chinese, requested in PRIMARY:
        query, evidence = normalize_query(requested)
        n = len(query.split())
        if not 1 <= n <= 5:
            raise ValueError("Unsupported normalized token count")
        print(f"{movement_id}: {requested} -> {query} ({n}-gram)", flush=True)
        if n not in indexes:
            url = f"{source.BASE}/eng-{n}-ngrams_exports.html"
            with source.request(url) as response:
                html = response.read().decode("utf-8")
            indexes[n] = source.parse_index(html, n)
            cache = root / "data/ngram/indexes" / f"eng-{n}.html"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8")
            totals[str(n)] = source.remote_metadata(f"{source.BASE}/totalcounts-{n}")
        location = source.locate(indexes[n], query)
        if location["url"] not in shards:
            shards[location["url"]] = {**source.remote_metadata(location["url"]), **location, "n": n}
        qid = source.MOVEMENTS[slug]
        movements.append({"movement_id": movement_id, "wikidata_qid": qid,
                          "movement_slug": slug, "name_zh": chinese,
                          "dictionary_label": entities[qid]["labels"]["en"]["value"],
                          "requested_query": requested, "query": query, "n": n,
                          "shard": shards[location["url"]]["file"], "status": "planned",
                          "normalization": evidence})
    manifest = {"schema": 1, "entity_type": "movement", "created_at": source.utc_now(),
                "corpus": "eng", "version": "20200217", "start_year": 1800, "end_year": 2019,
                "matching": "Case-sensitive exact normalized ngram; one reviewed primary query per movement; no alias aggregation.",
                "source": "https://storage.googleapis.com/books/ngrams/books/datasetsv3.html",
                "license": "CC BY 3.0; Google Books Ngram data", "movements": movements,
                "shards": list(shards.values()), "totals": totals}
    manifest["download_bytes"] = sum(x["size"] for x in [*shards.values(), *totals.values()])
    source.save_json(path, manifest)
    load_manifest(path)
    print(f"Planned {len(movements)} movements, {len(shards)} shards, "
          f"{manifest['download_bytes'] / 1024**3:.3f} GiB total compressed bytes", flush=True)
    return manifest


def load_manifest(path):
    m = source.read_json(path)
    if (m.get("schema"), m.get("entity_type"), m.get("corpus"), m.get("version")) != (1, "movement", "eng", "20200217"):
        raise ValueError("Expected movement manifest schema 1 / eng / 20200217")
    if not 1800 <= m["start_year"] <= m["end_year"] <= 2019:
        raise ValueError("Invalid year range")
    shards = {}
    for item in [*m["shards"], *m["totals"].values()]:
        if not re.fullmatch(r"(?:[1-5]-\d+-of-\d+\.gz|totalcounts-[1-5])", item["file"]):
            raise ValueError("Unsafe filename")
        if item["url"] != source.BASE + "/" + item["file"] or item["size"] <= 0:
            raise ValueError("Unexpected URL/size")
    for item in m["shards"]:
        if item["file"] in shards or not item["file"].startswith(f"{item['n']}-"):
            raise ValueError("Duplicate or inconsistent shard")
        shards[item["file"]] = item
    ids, query_keys = set(), set()
    for row in m["movements"]:
        if not re.fullmatch(r"M\d{2}", row["movement_id"]) or row["movement_id"] in ids:
            raise ValueError("Invalid or duplicate movement ID")
        ids.add(row["movement_id"])
        n, q = row["n"], row["query"]
        if type(n) is not int or not 1 <= n <= 5 or len(q.split()) != n or " ".join(q.split()) != q:
            raise ValueError("Invalid normalized query/token count")
        if (n, q) in query_keys:
            raise ValueError("Duplicate movement query")
        query_keys.add((n, q))
        shard = shards[row["shard"]]
        if shard["n"] != n or q < shard["lower"] or (shard["upper"] is not None and q >= shard["upper"]):
            raise ValueError("Query outside shard bounds or wrong n")
        if m["totals"][str(n)]["file"] != f"totalcounts-{n}":
            raise ValueError("Missing/inconsistent totalcounts")
    return m


def build_rows(manifest, found, totals):
    rows, movements = [], []
    for movement in manifest["movements"]:
        n, query = movement["n"], movement["query"]
        counts = found.get((n, query))
        selected = [] if counts is None else sorted((int(y), c) for y, c in counts.items()
                   if manifest["start_year"] <= int(y) <= manifest["end_year"])
        for year, (matches, volumes) in selected:
            if year not in totals[str(n)]:
                raise ValueError("Missing annual denominator")
            if not 0 <= volumes <= matches <= totals[str(n)][year]:
                raise ValueError("Counts inconsistent with annual denominator")
            rows.append({"movement_id": movement["movement_id"], "query": query, "n": n,
                         "year": year, "match_count": matches, "volume_count": volumes})
        status = "not_found" if counts is None else "matched" if selected else "matched_outside_window"
        movements.append({**movement, "status": status, "rows": len(selected),
                          "match_count_sum": sum(c[0] for _, c in selected)})
    return rows, movements


def run(manifest_path, output, existing, max_gib):
    m = load_manifest(manifest_path)
    if not math.isfinite(max_gib) or max_gib <= 0:
        raise ValueError("max-gib must be finite and positive")
    if existing.resolve() == (output / ".downloads").resolve():
        raise ValueError("Raw cache must not equal disposable scratch directory")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot = output / "manifest.json"
        if snapshot.exists() and source.read_json(snapshot) != m:
            raise ValueError("Output belongs to another manifest")
        queries = {s["file"]: {x["query"] for x in m["movements"] if x["shard"] == s["file"]} for s in m["shards"]}
        needed = 0
        for meta in m["shards"]:
            cp = checkpoint_path(output, meta)
            if cp.exists():
                read_checkpoint(cp, meta, queries[meta["file"]])
            elif not (existing / meta["file"]).exists():
                needed += meta["size"]
        for meta in m["totals"].values():
            if not (output / "totalcounts" / meta["file"]).exists() and not (existing / meta["file"]).exists():
                needed += meta["size"]
        if needed > max_gib * 1024**3:
            raise ValueError(f"Needs up to {needed / 1024**3:.3f} GiB transfer; exceeds budget")
        source.save_json(snapshot, m)
        print(f"{len(m['movements'])} movements; {len(m['shards'])} shards; remaining transfer <= {needed / 1024**3:.3f} GiB", flush=True)
        directory = output / "totalcounts"
        directory.mkdir(exist_ok=True)
        totals, total_hashes = {}, {}
        for n, meta in m["totals"].items():
            target, cached = directory / meta["file"], existing / meta["file"]
            if not target.exists() and cached.exists():
                source.check_file(cached, meta)
                shutil.copyfile(cached, target)
            total_hashes[meta["file"]] = source.download(meta, directory)
            totals[n] = source.parse_totals(target.read_text(encoding="utf-8"))
        found, scans = {}, []
        for index, meta in enumerate(m["shards"], 1):
            print(f"[{index}/{len(m['shards'])}] {meta['file']} ({meta['size'] / 1024**2:.1f} MiB): " + ", ".join(sorted(queries[meta["file"]])), flush=True)
            payload = extract_one(meta, queries[meta["file"]], output, existing)
            for q, years in payload["found"].items():
                found[(meta["n"], q)] = years
            scans.append({**{k: payload[k] for k in ("source", "raw_sha256", "scanned_records", "extracted_at")}, "n": meta["n"]})
            print(f"  matched {len(payload['found'])}/{len(queries[meta['file']])}; checkpoint verified", flush=True)
        rows, movements = build_rows(m, found, totals)
        data = output / "movement_year_counts.jsonl"
        temporary = data.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(data)
        source.save_json(output / "movements.json", movements)
        report = {"completed_at": source.utc_now(), "corpus": m["corpus"], "version": m["version"],
                  "start_year": m["start_year"], "end_year": m["end_year"], "movement_count": len(movements),
                  "row_count": len(rows), "status_counts": dict(Counter(x["status"] for x in movements)),
                  "missing_queries": [x["query"] for x in movements if x["status"] == "not_found"],
                  "selection": "All 15 reviewed primary queries, preserving M01-M15 IDs and dictionary QIDs.",
                  "matching": m["matching"],
                  "year_policy": "Only source-observed records in 1800-2019. No zero filling, smoothing or normalization.",
                  "normalization_policy": "Viewer metadata only confirms tokenization; every count comes from a checksum-verified raw v3 shard. Requested and normalized queries are preserved in movements.json.",
                  "interpretation": "Term visibility, not art-only visibility or artist totals. No semantic disambiguation; ambiguous and cross-disciplinary uses remain. Abstract art is a broad category, not a single coequal movement.",
                  "volume_policy": "Volumes containing the exact ngram per publication year. No book titles or deduplication across aliases/movements.",
                  "totalcounts_policy": "Unmodified corpus-wide totalcounts-n files, all original years and year,match_count,page_count,volume_count fields.",
                  "source": m["source"], "license": m["license"], "shards": scans,
                  "totalcounts_sha256": total_hashes,
                  "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                  "movements_sha256": hashlib.sha256((output / "movements.json").read_bytes()).hexdigest()}
        source.save_json(output / "report.json", report)
        print(json.dumps({k: report[k] for k in ("movement_count", "row_count", "status_counts")}), flush=True)
        print(output, flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "download"))
    parser.add_argument("--root", type=Path, default=source.ROOT)
    parser.add_argument("--manifest", type=Path, default=source.ROOT / "data/ngram/movement_15_manifest.json")
    parser.add_argument("--output", type=Path, default=source.ROOT / "outputs/ngram_movement_15")
    parser.add_argument("--max-gib", type=float, default=8)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan(args.root, args.manifest)
        else:
            run(args.manifest, args.output, args.root / "data/ngram/raw", args.max_gib)
    except (OSError, ValueError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
