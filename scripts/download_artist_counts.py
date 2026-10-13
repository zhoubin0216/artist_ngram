#!/usr/bin/env python3
"""Sequential, resumable key-count extraction; retains no newly downloaded shards."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

import ngram_sample as source


def digest(value):
    value = json.loads(json.dumps(value, ensure_ascii=False))
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def checkpoint_path(output, meta):
    return output / "checkpoints" / (meta["file"] + ".json")


def read_checkpoint(path, meta, queries):
    saved = source.read_json(path)
    payload = saved["payload"]
    if digest(payload) != saved["sha256"]:
        raise ValueError("Checkpoint checksum mismatch: " + str(path))
    identity = {k: meta[k] for k in ("url", "size", "etag", "md5", "lower", "upper")}
    if payload["source"] != identity or payload["queries"] != sorted(queries):
        raise ValueError("Checkpoint belongs to another selection/source: " + str(path))
    if not set(payload["found"]).issubset(queries):
        raise ValueError("Unexpected query in checkpoint")
    for values in payload["found"].values():
        for year, counts in values.items():
            int(year)
            if len(counts) != 2 or any(type(x) is not int for x in counts) or not 0 <= counts[1] <= counts[0]:
                raise ValueError("Invalid checkpoint count")
    return payload


def extract_one(meta, queries, output, existing):
    checkpoint = checkpoint_path(output, meta)
    scratch = output / ".downloads"
    owned = scratch / meta["file"]
    if checkpoint.exists():
        payload = read_checkpoint(checkpoint, meta, queries)
        # Recover a crash between durable checkpoint creation and scratch cleanup.
        if owned.exists():
            source.check_file(owned, meta)
            owned.unlink()
        return payload
    cached = existing / meta["file"]
    if cached.exists():
        path = cached
        sha = source.check_file(path, meta)
        print("  reusing existing raw shard (will preserve)", flush=True)
    else:
        if meta["size"] + 512 * 1024**2 > shutil.disk_usage(output).free:
            raise ValueError("Insufficient free space for one shard plus 512 MiB reserve")
        sha = source.download(meta, scratch)
        path = owned
    found, lines = source.scan_shard(path, meta, queries)
    payload = {"source": {k: meta[k] for k in ("url", "size", "etag", "md5", "lower", "upper")},
               "queries": sorted(queries), "raw_sha256": sha, "scanned_records": lines,
               "extracted_at": source.utc_now(), "found": found}
    source.save_json(checkpoint, {"sha256": digest(payload), "payload": payload})
    verified = read_checkpoint(checkpoint, meta, queries)
    if path == owned:
        owned.unlink()  # Only this workflow's exact verified scratch object.
    return verified


def build_rows(manifest, found, totals):
    rows, artists = [], []
    for artist in manifest["artists"]:
        query = artist["query"]
        counts = found.get(query)
        selected = [] if counts is None else sorted(
            (int(y), c) for y, c in counts.items()
            if manifest["start_year"] <= int(y) <= manifest["end_year"])
        for year, (matches, volumes) in selected:
            if year not in totals[str(artist["n"])]:
                raise ValueError("Missing annual denominator")
            if not 0 <= volumes <= matches <= totals[str(artist["n"])][year]:
                raise ValueError("Counts inconsistent with annual denominator")
            rows.append({"artist_id": artist["qid"], "query": query, "n": artist["n"],
                         "year": year, "match_count": matches, "volume_count": volumes})
        status = ("unsupported_token_count" if "shard" not in artist else
                  "not_found" if counts is None else
                  "matched" if selected else "matched_outside_window")
        artists.append({**artist, "status": status, "rows": len(selected),
                        "match_count_sum": sum(c[0] for _, c in selected)})
    return rows, artists


def run(manifest_path, output, existing, max_gib):
    manifest = source.load_manifest(manifest_path)
    if not math.isfinite(max_gib) or max_gib <= 0:
        raise ValueError("max-gib must be finite and positive")
    output.mkdir(parents=True, exist_ok=True)
    if existing.resolve() == (output / ".downloads").resolve():
        raise ValueError("Existing raw cache must not be the disposable scratch directory")
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot = output / "manifest.json"
        if snapshot.exists() and source.read_json(snapshot) != manifest:
            raise ValueError("Output directory belongs to a different manifest")
        queries = {m["file"]: {a["query"] for a in manifest["artists"]
                   if a.get("shard") == m["file"]} for m in manifest["shards"]}
        needed = 0
        for meta in manifest["shards"]:
            path = checkpoint_path(output, meta)
            if path.exists():
                read_checkpoint(path, meta, queries[meta["file"]])
            elif not (existing / meta["file"]).exists():
                needed += meta["size"]
        for meta in manifest["totals"].values():
            if not (output / "totalcounts" / meta["file"]).exists() and not (existing / meta["file"]).exists():
                needed += meta["size"]
        if needed > max_gib * 1024**3:
            raise ValueError(f"Needs up to {needed / 1024**3:.3f} GiB transfer; exceeds budget")
        print(f"{len(manifest['artists'])} artists; {len(manifest['shards'])} shards; "
              f"remaining transfer <= {needed / 1024**3:.3f} GiB; sequential extraction", flush=True)
        source.save_json(snapshot, manifest)
        totals, total_hashes = {}, {}
        directory = output / "totalcounts"
        directory.mkdir(exist_ok=True)
        for n, meta in manifest["totals"].items():
            target = directory / meta["file"]
            cached = existing / meta["file"]
            if not target.exists() and cached.exists():
                source.check_file(cached, meta)
                shutil.copyfile(cached, target)
            total_hashes[meta["file"]] = source.download(meta, directory)
            totals[n] = source.parse_totals(target.read_text(encoding="utf-8"))
        found, scans = {}, []
        for index, meta in enumerate(manifest["shards"], 1):
            print(f"[{index}/{len(manifest['shards'])}] {meta['file']} "
                  f"({meta['size'] / 1024**2:.1f} MiB): {', '.join(sorted(queries[meta['file']]))}", flush=True)
            payload = extract_one(meta, queries[meta["file"]], output, existing)
            found.update(payload["found"])
            scans.append({k: payload[k] for k in ("source", "raw_sha256", "scanned_records", "extracted_at")})
            print(f"  extracted {len(payload['found'])}/{len(queries[meta['file']])} exact names; checkpoint verified", flush=True)
        rows, artists = build_rows(manifest, found, totals)
        data = output / "artist_year_counts.jsonl"
        temporary = data.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(data)
        source.save_json(output / "artists.json", artists)
        report = {"completed_at": source.utc_now(), "corpus": manifest["corpus"],
                  "version": manifest["version"], "start_year": manifest["start_year"],
                  "end_year": manifest["end_year"], "artist_count": len(artists),
                  "row_count": len(rows), "status_counts": dict(Counter(a["status"] for a in artists)),
                  "missing_names": [a["query"] for a in artists if a["status"] == "not_found"],
                  "selection": "Five anchor artists, then deterministic movement round-robin; not a random sample.",
                  "matching": manifest["matching"],
                  "year_policy": "Only source-observed year rows in the requested range; no zero filling, smoothing, normalization or entity disambiguation.",
                  "volume_policy": "Volumes containing this exact ngram in this publication year; not book titles, not distinct works or sums across aliases.",
                  "totalcounts_policy": "Unmodified corpus-wide year,match_count,page_count,volume_count files, one per n; original full year coverage retained.",
                  "license": manifest["license"], "source": manifest["source"],
                  "shards": scans, "totalcounts_sha256": total_hashes,
                  "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                  "artists_sha256": hashlib.sha256((output / "artists.json").read_bytes()).hexdigest()}
        source.save_json(output / "report.json", report)
        print(json.dumps({k: report[k] for k in ("artist_count", "row_count", "status_counts")}), flush=True)
        print(output, flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-raw", type=Path, default=source.ROOT / "data/ngram/raw")
    parser.add_argument("--max-gib", type=float, default=20)
    args = parser.parse_args()
    try:
        run(args.manifest, args.output, args.existing_raw, args.max_gib)
    except (OSError, ValueError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
