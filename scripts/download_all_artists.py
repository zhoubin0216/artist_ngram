#!/usr/bin/env python3
"""Bounded parallel full-dictionary extraction with pilot reuse and progress snapshots."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time

import ngram_sample as source
import download_artist_counts as counts


def queries_for(manifest, filename):
    return {a["query"] for a in manifest["artists"] if a.get("shard") == filename}


def validate_manifest(path):
    m = source.load_manifest(path)
    if len({a["qid"] for a in m["artists"]}) != len(m["artists"]):
        raise ValueError("Duplicate artist ID")
    if len({a["query"] for a in m["artists"]}) != len(m["artists"]):
        raise ValueError("Duplicate artist query")
    shards = {s["file"]: s for s in m["shards"]}
    if len(shards) != len(m["shards"]):
        raise ValueError("Duplicate shard")
    for a in m["artists"]:
        s = shards[a["shard"]]
        if not 2 <= a["n"] <= 5 or a["n"] != s["n"]:
            raise ValueError("Unsupported/inconsistent token count")
        if a["query"] < s["lower"] or (s["upper"] is not None and a["query"] >= s["upper"]):
            raise ValueError("Query outside shard bounds")
        if m["totals"][str(a["n"])]["file"] != f"totalcounts-{a['n']}":
            raise ValueError("Inconsistent annual totals")
    return m


def load_seeds(m, directory):
    old = validate_manifest(directory / "manifest.json")
    for key in ("corpus", "version", "start_year", "end_year", "matching"):
        if old[key] != m[key]:
            raise ValueError("Pilot differs in " + key)
    full_artists = {a["qid"]: a for a in m["artists"]}
    full_shards = {s["file"]: s for s in m["shards"]}
    for a in old["artists"]:
        if a["qid"] in full_artists:
            b = full_artists[a["qid"]]
            if any(a[k] != b[k] for k in ("query", "n", "shard")):
                raise ValueError("Pilot artist query changed: " + a["qid"])
    seeds = {}
    for old_meta in old["shards"]:
        filename = old_meta["file"]
        if filename not in full_shards:
            continue
        meta = full_shards[filename]
        if any(meta[k] != old_meta[k] for k in ("url", "size", "etag", "md5", "lower", "upper", "n")):
            raise ValueError("Pilot source object changed: " + filename)
        payload = counts.read_checkpoint(counts.checkpoint_path(directory, old_meta), old_meta, queries_for(old, filename))
        selected = set(payload["queries"]) & queries_for(m, filename)
        seeds[filename] = {**payload, "queries": sorted(selected),
                           "found": {q: v for q, v in payload["found"].items() if q in selected},
                           "reused_from": "outputs/" + directory.name}
    return seeds


def verify_overlap(payload, seed):
    if not seed:
        return
    for query in set(seed["queries"]) & set(payload["queries"]):
        if payload["found"].get(query) != seed["found"].get(query):
            raise ValueError("Full extraction disagrees with verified pilot: " + query)


def prepare_checkpoints(m, output, seeds):
    completed = {}
    for meta in m["shards"]:
        filename = meta["file"]
        queries = queries_for(m, filename)
        path = counts.checkpoint_path(output, meta)
        seed = seeds.get(filename)
        if not path.exists() and seed and queries <= set(seed["queries"]):
            payload = {**seed, "queries": sorted(queries),
                       "found": {q: v for q, v in seed["found"].items() if q in queries}}
            source.save_json(path, {"sha256": counts.digest(payload), "payload": payload})
        if path.exists():
            payload = counts.read_checkpoint(path, meta, queries)
            verify_overlap(payload, seed)
            completed[filename] = payload
            owned = output / ".downloads" / filename
            if owned.exists():
                source.check_file(owned, meta)
                owned.unlink()
    return completed


def collect(seeds, completed):
    found, checked = {}, set()
    for payload in [*seeds.values(), *completed.values()]:
        checked.update(payload["queries"])
        found.update(payload["found"])
    return found, checked


def snapshot(m, output, seeds, completed, totals, total_hashes, failures, workers):
    found, checked = collect(seeds, completed)
    rows, artists = counts.build_rows(m, found, totals)
    for a in artists:
        if a["query"] not in checked:
            a["status"] = "not_downloaded"
    data = output / "artist_year_counts.jsonl"
    temporary = data.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(data)
    source.save_json(output / "artists.json", artists)
    complete = len(completed) == len(m["shards"]) and not failures
    report = {"updated_at": source.utc_now(), "run_status": "complete" if complete else "incomplete",
              "corpus": m["corpus"], "version": m["version"], "start_year": m["start_year"], "end_year": m["end_year"],
              "artist_count": len(artists), "row_count": len(rows),
              "status_counts": dict(Counter(a["status"] for a in artists)),
              "completed_shards": len(completed), "total_shards": len(m["shards"]),
              "missing_names": [a["query"] for a in artists if a["status"] == "not_found"],
              "failed_shards": failures, "workers": workers,
              "selection": "Complete current dictionary, including the unchanged 50-artist pilot; one original full-name query per artist.",
              "matching": m["matching"],
              "year_policy": "Only observed raw records in the requested window; no zero filling, smoothing or normalization.",
              "query_policy": "Same original-name, whitespace-token-count policy as the artist pilot. Punctuation/tokenization warnings remain for manual review. No new aliases or Viewer tokenization expansion.",
              "volume_policy": "Volumes containing each exact ngram per publication year; no book titles or deduplication across artists/aliases.",
              "totalcounts_policy": "Unmodified corpus-wide totalcounts-n files, including all original years and year,match_count,page_count,volume_count fields.",
              "source": m["source"], "license": m["license"],
              "pilot_queries_verified": len(collect(seeds, {})[1]),
              "shards": [{"file": filename, **{k: p[k] for k in ("source", "raw_sha256", "scanned_records", "extracted_at")},
                          "reused_from": p.get("reused_from")} for filename, p in sorted(completed.items())],
              "totalcounts_sha256": total_hashes,
              "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
              "artists_sha256": hashlib.sha256((output / "artists.json").read_bytes()).hexdigest()}
    source.save_json(output / "report.json", report)
    return report


def extract_worker(meta, queries, output, existing, seed):
    filename = meta["file"]
    print(f"START {filename}: {len(queries)} queries, {meta['size'] / 1024**2:.1f} MiB", flush=True)
    for attempt in range(2):
        try:
            payload = counts.extract_one(meta, queries, output, existing)
            verify_overlap(payload, seed)
            return payload
        except Exception as error:
            if attempt:
                raise
            print(f"RETRY SHARD {filename}: {error}", flush=True)
            time.sleep(2)


def run(manifest_path, output, existing, pilot=None, workers=4, max_gib=130, use_processes=False):
    m = validate_manifest(manifest_path)
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if not math.isfinite(max_gib) or max_gib <= 0:
        raise ValueError("max-gib must be finite and positive")
    if (output.resolve() == existing.resolve()
            or (pilot is not None and output.resolve() == pilot.resolve())
            or existing.resolve() == (output / ".downloads").resolve()):
        raise ValueError("Output and source/scratch directories must be separate")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_copy = output / "manifest.json"
        if manifest_copy.exists() and source.read_json(manifest_copy) != m:
            raise ValueError("Output belongs to a different manifest")
        seeds = load_seeds(m, pilot) if pilot is not None else {}
        completed = prepare_checkpoints(m, output, seeds)
        pending = [s for s in m["shards"] if s["file"] not in completed]
        def staged_bytes(meta):
            owned = output / ".downloads" / meta["file"]
            partial = owned.with_suffix(owned.suffix + ".part")
            path = owned if owned.exists() else partial
            return min(path.stat().st_size, meta["size"]) if path.exists() else 0
        # Consume already staged work first. Its bytes already reduce free disk;
        # do not charge the first active batch for those same bytes twice.
        pending.sort(key=lambda meta: not bool(staged_bytes(meta)))
        needed = sum(s["size"] for s in pending if not (existing / s["file"]).exists())
        if needed + sum(s["size"] for s in m["totals"].values()) > max_gib * 1024**3:
            raise ValueError(f"Remaining transfer upper bound {needed / 1024**3:.3f} GiB exceeds budget")
        staged_first_batch = sum(staged_bytes(s) for s in pending[:workers])
        space = min(workers, len(pending)) * max((s["size"] for s in pending), default=0) + 1024**3 - staged_first_batch
        if pending and space > shutil.disk_usage(output).free:
            raise ValueError("Insufficient disk for concurrent shards plus 1 GiB reserve; reduce workers")
        source.save_json(manifest_copy, m)
        print(f"Full dictionary: {len(m['artists'])} artists, {len(m['shards'])} shards; "
              f"{len(completed)} complete checkpoints reused; {len(collect(seeds, {})[1])} pilot queries verified; "
              f"remaining transfer <= {needed / 1024**3:.3f} GiB; workers={workers}", flush=True)
        totals, total_hashes = {}, {}
        directory = output / "totalcounts"
        directory.mkdir(exist_ok=True)
        for n, meta in m["totals"].items():
            target = directory / meta["file"]
            if not target.exists():
                parents = [existing] + ([pilot / "totalcounts"] if pilot is not None else [])
                for parent in parents:
                    cached = parent / meta["file"]
                    if cached.exists():
                        source.check_file(cached, meta)
                        shutil.copyfile(cached, target)
                        break
            total_hashes[meta["file"]] = source.download(meta, directory)
            totals[n] = source.parse_totals(target.read_text(encoding="utf-8"))
        failures = {}
        started = time.monotonic()
        initial_done = len(completed)

        def progress(state="running"):
            found, checked = collect(seeds, completed)
            value = {"updated_at": source.utc_now(), "state": state,
                     "completed_shards": len(completed), "total_shards": len(m["shards"]),
                     "checked_artists": len(checked), "total_artists": len(m["artists"]),
                     "matched_queries": len(found), "failed_shards": failures,
                     "workers": workers, "elapsed_seconds_this_run": round(time.monotonic() - started),
                     "free_disk_gib": round(shutil.disk_usage(output).free / 1024**3, 2)}
            source.save_json(output / "progress.json", value)
            print("PROGRESS " + json.dumps(value, ensure_ascii=False), flush=True)

        snapshot(m, output, seeds, completed, totals, total_hashes, failures, workers)
        progress()
        # Processes keep gzip/text scanning from contending on one Python GIL.
        # Threads remain available for the synthetic fault-injection tests.
        executor_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        with executor_type(max_workers=workers) as executor:
            futures = {executor.submit(extract_worker, meta, queries_for(m, meta["file"]),
                                       output, existing, seeds.get(meta["file"])): meta for meta in pending}
            for future in as_completed(futures):
                meta = futures[future]
                filename = meta["file"]
                try:
                    completed[filename] = future.result()
                    failures.pop(filename, None)
                    print(f"DONE {filename}: matched {len(completed[filename]['found'])}/{len(completed[filename]['queries'])}", flush=True)
                except Exception as error:
                    failures[filename] = f"{type(error).__name__}: {error}"
                    print(f"FAILED {filename}: {failures[filename]}", flush=True)
                progress()
                if (len(completed) - initial_done) % 10 == 0 or failures:
                    snapshot(m, output, seeds, completed, totals, total_hashes, failures, workers)
        report = snapshot(m, output, seeds, completed, totals, total_hashes, failures, workers)
        progress(report["run_status"])
        print("FINAL " + json.dumps({k: report[k] for k in ("run_status", "artist_count", "row_count", "status_counts")}), flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Defaults to the published output manifest, then the local download plan")
    parser.add_argument("--output", type=Path, default=source.ROOT / "outputs/ngram_artists_full")
    parser.add_argument("--existing-raw", type=Path, default=source.ROOT / "data/ngram/raw")
    parser.add_argument("--pilot", type=Path, help="Optional pilot directory to reuse; not required for the published full dataset")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-gib", type=float, default=130)
    args = parser.parse_args()
    if args.manifest is None:
        published = args.output / "manifest.json"
        args.manifest = published if published.exists() else source.ROOT / "data/ngram/artists_full_manifest.json"
    try:
        report = run(args.manifest, args.output, args.existing_raw, args.pilot, args.workers, args.max_gib, use_processes=True)
        return 0 if report["run_status"] == "complete" else 2
    except (OSError, ValueError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
