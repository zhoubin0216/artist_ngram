#!/usr/bin/env python3
"""Bounded, resumable Google Books v3 download and exact-name smoke validation.

Python standard library only. Run with --help; see docs/ngram_workflow.md.
"""
import argparse
import base64
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import html
from http.client import HTTPException
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://storage.googleapis.com/books/ngrams/books/20200217/eng"
ANCHORS = ["Q296", "Q5582", "Q483992", "Q241732", "Q104884"]
MOVEMENTS = {
    "neoclassicism": "Q14378", "romanticism": "Q37068", "realism": "Q10857409",
    "academic_art": "Q189458", "impressionism": "Q40415", "post_impressionism": "Q166713",
    "symbolism": "Q164800", "art_nouveau": "Q34636", "fauvism": "Q166593",
    "expressionism": "Q80113", "cubism": "Q42934", "futurism": "Q131221",
    "dada": "Q6034", "surrealism": "Q39427", "abstract_art": "Q128115",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def request(url, method="GET", headers=None):
    return urlopen(Request(url, method=method, headers={
        "User-Agent": "artist-ngram-project/1.0", "Accept-Encoding": "identity", **(headers or {})
    }), timeout=90)


def remote_metadata(url):
    with request(url, "HEAD") as response:
        hashes = ",".join(response.headers.get_all("x-goog-hash", []))
        match = re.search(r"(?:^|,)\s*md5=([^,\s]+)", hashes)
        if not match or not response.headers.get("ETag"):
            raise ValueError("Server must supply MD5 and ETag: " + url)
        return {"url": url, "file": url.rsplit("/", 1)[1],
                "size": int(response.headers["Content-Length"]),
                "etag": response.headers["ETag"], "md5": match[1]}


def check_file(path, metadata):
    if not path.exists() or path.stat().st_size != metadata["size"]:
        raise ValueError("Incomplete file: " + str(path))
    md5, sha = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
    if base64.b64encode(md5.digest()).decode() != metadata["md5"]:
        raise ValueError("MD5 mismatch: " + str(path))
    return sha.hexdigest()


def download(metadata, directory, attempts=3):
    """Pin bytes with If-Match, resume only matching .part metadata, verify before rename."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / metadata["file"]
    if target.exists():
        return check_file(target, metadata)
    partial = target.with_suffix(target.suffix + ".part")
    sidecar = partial.with_suffix(partial.suffix + ".json")
    identity = {k: metadata[k] for k in ("url", "size", "etag", "md5")}
    if partial.exists() and (not sidecar.exists() or read_json(sidecar) != identity):
        raise ValueError("Partial file belongs to another download; move it aside: " + str(partial))
    save_json(sidecar, identity)
    for attempt in range(attempts):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > metadata["size"]:
                raise ValueError("Oversized partial file: " + str(partial))
            if offset < metadata["size"]:
                headers = {"If-Match": metadata["etag"]}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with request(metadata["url"], headers=headers) as response:
                    if response.headers.get("ETag") != metadata["etag"]:
                        raise ValueError("Remote ETag changed; regenerate the plan")
                    if response.status == 206:
                        expected = f"bytes {offset}-{metadata['size'] - 1}/{metadata['size']}"
                        if response.headers.get("Content-Range") != expected:
                            raise ValueError("Unexpected Content-Range")
                        mode = "ab" if offset else "wb"
                    elif response.status == 200:
                        mode = "wb"  # A server may ignore Range: restart, never append.
                    else:
                        raise ValueError(f"Unexpected HTTP status: {response.status}")
                    with partial.open(mode) as output:
                        transferred = offset if mode == "ab" else 0
                        last_update = time.monotonic()
                        for chunk in iter(lambda: response.read(1024 * 1024), b""):
                            output.write(chunk)
                            transferred += len(chunk)
                            if time.monotonic() - last_update >= 10:
                                print(f"  {transferred / 1024**2:.1f} / {metadata['size'] / 1024**2:.1f} MiB", flush=True)
                                last_update = time.monotonic()
            sha = check_file(partial, metadata)
            partial.replace(target)
            sidecar.unlink(missing_ok=True)
            return sha
        except (OSError, URLError, HTTPException, ValueError) as error:
            if isinstance(error, HTTPError) and error.code in (403, 404, 412, 416):
                raise
            if attempt + 1 == attempts:
                raise
            print(f"Retry {attempt + 1}/{attempts}: {error}", file=sys.stderr, flush=True)
            time.sleep(attempt + 1)
    raise RuntimeError("Download exhausted")


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.href, self.label = [], None, ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.label = dict(attrs).get("href"), ""

    def handle_data(self, data):
        if self.href:
            self.label += data

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            if self.href.endswith(".gz"):
                self.links.append((self.label, self.href.replace("http:", "https:", 1)))
            self.href = None


def parse_index(source, n):
    parser = IndexParser()
    parser.feed(source)
    links = parser.links
    if not links or any(a[0] >= b[0] for a, b in zip(links, links[1:])):
        raise ValueError("Index boundaries must be strictly increasing")
    if any(not re.fullmatch(re.escape(BASE) + rf"/{n}-\d+-of-\d+\.gz", url) for _, url in links):
        raise ValueError("Unexpected shard URL in official index")
    return links


def locate(links, query):
    index = bisect_right([label for label, _ in links], query) - 1
    if index < 0:
        raise ValueError("Query precedes first index boundary: " + query)
    return {"url": links[index][1], "lower": links[index][0],
            "upper": links[index + 1][0] if index + 1 < len(links) else None}


def parse_year(value):
    match = re.match(r"^[+-]?\d{4,}", value or "")
    return int(match[0]) if match else None


def modal(values):
    return min(Counter(values), key=lambda value: (-values.count(value), value)) if values else None


def load_artists(directory):
    artists = {}
    for slug, movement in MOVEMENTS.items():
        for row in read_json(directory / f"{slug}.json")["results"]["bindings"]:
            value = lambda key: row.get(key, {}).get("value", "")
            qid = value("artist").rsplit("/", 1)[-1]
            if not qid:
                continue
            artist = artists.setdefault(qid, {"qid": qid, "query": " ".join(value("artistLabel").split()),
                                               "births": [], "deaths": [], "movements": set()})
            artist["movements"].add(slug)
            for field, key in (("births", "birth"), ("deaths", "death")):
                year = parse_year(value(key))
                if year is not None:
                    artist[field].append(year)
    entities = read_json(directory / "sanity_entities.json")["entities"]
    for qid, movement in (("Q241732", "realism"), ("Q104884", "romanticism")):
        if qid not in artists:
            entity = entities[qid]
            artist = {"qid": qid, "query": entity["labels"]["en"]["value"], "movements": {movement}}
            for field, prop in (("births", "P569"), ("deaths", "P570")):
                artist[field] = [parse_year(s["mainsnak"]["datavalue"]["value"]["time"])
                                 for s in entity["claims"].get(prop, []) if "datavalue" in s["mainsnak"]]
                artist[field] = [y for y in artist[field] if y is not None]
            artists[qid] = artist
    labels = Counter(a["query"].lower() for a in artists.values())
    result = []
    for artist in artists.values():
        if len(artist["query"].split()) < 2 or labels[artist["query"].lower()] != 1:
            continue
        artist["birth_year"] = modal(artist.pop("births"))
        artist["death_year"] = modal(artist.pop("deaths"))
        artist["movements"] = sorted(artist["movements"])
        artist["n"] = len(artist["query"].split())
        artist["query_warning"] = ("Review punctuation/tokenization; no aliases are inferred."
                                   if any(not (c.isalpha() or c == " ") for c in artist["query"]) else None)
        result.append(artist)
    return sorted(result, key=lambda a: (a["query"], a["qid"]))


def choose_sample(artists, size, ids=None):
    lookup = {a["qid"]: a for a in artists}
    if ids:
        selected = list(dict.fromkeys(ids.split(",")))
        if any(qid not in lookup for qid in selected):
            raise ValueError("Unknown or excluded QID in --artists")
        return [lookup[qid] for qid in selected]
    if not 1 <= size <= len(artists):
        raise ValueError(f"sample-size must be between 1 and {len(artists)}")
    selected = [lookup[qid] for qid in ANCHORS if qid in lookup][:size]
    queues = {m: [a for a in artists if m in a["movements"]] for m in MOVEMENTS}
    # Deterministic movement round-robin; a convenience sample, not random inference.
    while len(selected) < size:
        for queue in queues.values():
            while queue and queue[0] in selected:
                queue.pop(0)
            if queue and len(selected) < size:
                selected.append(queue.pop(0))
    return selected


def plan(args):
    selected = choose_sample(load_artists(args.root / "data/wikidata"), args.sample_size, args.artists)
    indexes, shards, totals = {}, {}, {}
    for artist in selected:
        n = artist["n"]
        if not 2 <= n <= 5:
            artist["status"] = "unsupported_token_count"
            continue
        if n not in indexes:
            url = f"{BASE}/eng-{n}-ngrams_exports.html"
            with request(url) as response:
                source = response.read().decode("utf-8")
            indexes[n] = parse_index(source, n)
            cache = args.root / "data/ngram/indexes" / f"eng-{n}.html"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(source, encoding="utf-8")
            totals[str(n)] = remote_metadata(f"{BASE}/totalcounts-{n}")
        location = locate(indexes[n], artist["query"])
        if location["url"] not in shards:
            meta = remote_metadata(location["url"])
            shards[location["url"]] = {**meta, **location, "n": n}
        artist["shard"] = shards[location["url"]]["file"]
        artist["status"] = "planned"
    result = {"schema": 1, "created_at": utc_now(), "corpus": "eng", "version": "20200217",
              "start_year": args.start_year, "end_year": args.end_year,
              "matching": "case-sensitive exact full name; no POS variants or alias summation",
              "source": "https://storage.googleapis.com/books/ngrams/books/datasetsv3.html",
              "license": "CC BY 3.0; Google Books Ngram data",
              "artists": selected, "shards": list(shards.values()), "totals": totals}
    result["download_bytes"] = sum(m["size"] for m in [*result["shards"], *totals.values()])
    save_json(args.manifest, result)
    print(f"Planned {len(selected)} artists, {len(shards)} shards, "
          f"{result['download_bytes'] / 1024**3:.3f} GiB. No raw shards downloaded.")
    print(args.manifest)


def load_manifest(path):
    manifest = read_json(path)
    if manifest.get("schema") != 1 or manifest.get("version") != "20200217" or manifest.get("corpus") != "eng":
        raise ValueError("Only eng / 20200217 / schema 1 is supported")
    if not 1800 <= manifest["start_year"] <= manifest["end_year"] <= 2019:
        raise ValueError("Year range must be within 1800–2019")
    for item in [*manifest["shards"], *manifest["totals"].values()]:
        if not re.fullmatch(r"(?:[2-5]-\d+-of-\d+\.gz|totalcounts-[2-5])", item["file"]):
            raise ValueError("Unsafe filename in manifest")
        if item["url"] != BASE + "/" + item["file"] or item["size"] <= 0:
            raise ValueError("Unexpected URL or size in manifest")
    return manifest


def download_plan(args):
    manifest = load_manifest(args.manifest)
    directory = args.root / "data/ngram/raw"
    items = [*manifest["totals"].values(), *manifest["shards"]]
    # Conservative budget: count entire uncached objects even when a .part exists.
    needed = sum(m["size"] for m in items if not (directory / m["file"]).exists())
    if needed > args.max_gib * 1024**3:
        raise ValueError(f"Download needs {needed / 1024**3:.3f} GiB; exceeds --max-gib {args.max_gib}. "
                         "Plan fewer artists, or explicitly increase the budget.")
    if needed + 128 * 1024**2 > shutil.disk_usage(args.root).free:
        raise ValueError("Insufficient free disk space (including 128 MiB safety reserve)")
    for item in items:
        print(f"Downloading/verifying {item['file']} ({item['size'] / 1024**2:.1f} MiB)", flush=True)
        item["sha256"] = download(item, directory)
        item["verified_at"] = utc_now()
        save_json(args.manifest, manifest)
    print("Download complete; MD5 verified. Next: validate.")


def parse_totals(text):
    totals = {}
    for cell in text.split():
        fields = cell.split(",")
        if len(fields) != 4:
            raise ValueError("Expected totalcounts: year,match_count,page_count,volume_count")
        year, count, pages, volumes = map(int, fields)
        if year in totals or count <= 0 or min(pages, volumes) < 0:
            raise ValueError("Invalid or duplicate totalcounts year")
        totals[year] = count
    return totals


def parse_record(line):
    cells = line.rstrip("\n\r").split("\t")
    if len(cells) < 2:
        raise ValueError("Expected v3 ngram TAB year,count,volume [...]")
    years = {}
    for cell in cells[1:]:
        if len(cell.split(",")) != 3:
            raise ValueError("Expected v3 year,count,volume; v2 is not supported")
        year, count, volume = map(int, cell.split(","))
        if year in years or not 0 <= volume <= count:
            raise ValueError("Duplicate year or invalid counts")
        years[year] = (count, volume)
    return cells[0], years


def scan_shard(path, metadata, queries):
    found, lines = {}, 0
    # v3 shard rows are NOT internally sorted. Always scan through gzip EOF/CRC.
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            lines += 1
            if lines % 1_000_000 == 0:
                print(f"  scanned {lines:,} records", flush=True)
            query = line.split("\t", 1)[0]
            if query < metadata["lower"] or (metadata["upper"] is not None and query >= metadata["upper"]):
                raise ValueError("Record outside planned lexical shard bounds: " + query)
            if query in queries:
                if query in found:
                    raise ValueError("Duplicate ngram record: " + query)
                _, found[query] = parse_record(line)
    return found, lines


def make_series(artist, counts, totals, first, last):
    rows = []
    for year in range(first, last + 1):
        if year not in totals:
            raise ValueError(f"Missing {artist['n']}-gram denominator for {year}")
        count, volume = counts.get(year, (0, 0))
        if count > totals[year]:
            raise ValueError("Name count exceeds corpus denominator")
        rows.append({"qid": artist["qid"], "query": artist["query"], "n": artist["n"], "year": year,
                     "match_count": count, "volume_count": volume, "total_match_count": totals[year],
                     "frequency": count / totals[year], "per_billion": count / totals[year] * 1e9})
    nonzero = [r for r in rows if r["match_count"]]
    birth = artist["birth_year"]
    before = [r for r in rows if birth is not None and r["year"] < birth and r["match_count"]]
    total = sum(r["match_count"] for r in rows)
    summary = {**artist, "status": "matched" if nonzero else "matched_outside_window",
               "first_nonzero_year": nonzero[0]["year"] if nonzero else None,
               "peak_year": max(nonzero, key=lambda r: r["frequency"])["year"] if nonzero else None,
               "nonzero_years": len(nonzero), "total_matches_in_window": total,
               "prebirth_nonzero_years": len(before),
               "prebirth_match_share": sum(r["match_count"] for r in before) / total if total else None,
               "manual_review": bool(before or artist.get("query_warning") or not nonzero or birth is None)}
    return rows, summary


def html_report(report, series):
    cards = []
    for artist in report["artists"]:
        rows = [r for r in series if r["qid"] == artist["qid"]]
        graph = ""
        if rows:
            peak = max(r["per_billion"] for r in rows) or 1
            points = " ".join(f"{40 + i * 690 / max(1, len(rows)-1):.1f},{160-r['per_billion']/peak*130:.1f}"
                              for i, r in enumerate(rows))
            graph = (f'<svg viewBox="0 0 760 195" role="img" aria-label="Annual exact-name frequency">'
                     f'<text x="40" y="18">max {peak:.3g} / billion</text>'
                     f'<path d="M40 30 V160 H730" fill="none" stroke="#aaa"/>'
                     f'<polyline points="{points}" fill="none" stroke="#2463ad" stroke-width="1.6"/>'
                     f'<text x="40" y="184">{rows[0]["year"]}</text>'
                     f'<text x="695" y="184">{rows[-1]["year"]}</text></svg>')
        details = {k: artist.get(k) for k in ("status", "nonzero_years", "first_nonzero_year", "peak_year",
                   "prebirth_nonzero_years", "prebirth_match_share", "manual_review", "query_warning")}
        cards.append(f'<section><h2>{html.escape(artist["query"])} ({html.escape(artist["qid"])})</h2>{graph}'
                     f'<pre>{html.escape(json.dumps(details, ensure_ascii=False, indent=2))}</pre></section>')
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>Ngram 小样本验证</title>'
            '<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 24px;color:#223}'
            'section{border-top:1px solid #ddd;padding:18px 0}svg{max-width:760px;width:100%}'
            'pre{white-space:pre-wrap;background:#f4f6f8;padding:12px}</style>'
            '<h1>Google Books Ngram 小样本验证</h1>'
            f'<p>eng / 20200217 · {report["start_year"]}–{report["end_year"]} · '
            '大小写敏感、完整姓名精确匹配 · 每十亿同阶 n-gram 出现次数 · 未平滑</p>'
            '<p>每张图独立纵轴。出生前命中仅作为人工检查提示；未命中不代表艺术家没有影响力。'
            '未下载与未命中不补零。此报告验证数据流程，不证明姓名已完成消歧。</p>'
            f'<p>覆盖状态：{html.escape(json.dumps(report["status_counts"]))}</p>'
            + ''.join(cards) + '<footer>Source: Google Books Ngram, CC BY 3.0. '
            '<a href="https://storage.googleapis.com/books/ngrams/books/datasetsv3.html">Official datasets</a>'
            '</footer></html>')


def validate(args):
    manifest = load_manifest(args.manifest)
    directory = args.root / "data/ngram/raw"
    found, available, scans, totals, hashes = {}, set(), {}, {}, {}
    for shard in manifest["shards"]:
        path = directory / shard["file"]
        if not path.exists():
            continue
        hashes[shard["file"]] = check_file(path, shard)
        queries = {a["query"] for a in manifest["artists"] if a.get("shard") == shard["file"]}
        print(f"Scanning {shard['file']} to EOF ({len(queries)} queries)", flush=True)
        matches, lines = scan_shard(path, shard, queries)
        found.update(matches)
        available.add(shard["file"])
        scans[shard["file"]] = {"lines": lines, "matched_queries": len(matches)}
        n = str(shard["n"])
        if n not in totals:
            meta = manifest["totals"][n]
            hashes[meta["file"]] = check_file(directory / meta["file"], meta)
            totals[n] = parse_totals((directory / meta["file"]).read_text(encoding="utf-8"))
    series, summaries = [], []
    for artist in manifest["artists"]:
        if artist["status"] == "unsupported_token_count":
            summaries.append(artist)
        elif artist["shard"] not in available:
            summaries.append({**artist, "status": "not_downloaded"})
        elif artist["query"] not in found:
            summaries.append({**artist, "status": "not_found", "manual_review": True})
        else:
            rows, summary = make_series(artist, found[artist["query"]], totals[str(artist["n"])],
                                        manifest["start_year"], manifest["end_year"])
            series.extend(rows)
            summaries.append(summary)
    report = {"validated_at": utc_now(), "corpus": "eng", "version": "20200217",
              "start_year": manifest["start_year"], "end_year": manifest["end_year"],
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "sha256": hashes, "scans": scans, "artists": summaries,
              "status_counts": dict(Counter(a["status"] for a in summaries))}
    args.output.mkdir(parents=True, exist_ok=True)
    save_json(args.output / "report.json", report)
    temp = args.output / "annual.jsonl.tmp"
    with temp.open("w", encoding="utf-8") as output:
        for row in series:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(args.output / "annual.jsonl")
    (args.output / "report.html").write_text(html_report(report, series), encoding="utf-8")
    print(json.dumps(report["status_counts"], ensure_ascii=False))
    print(args.output / "report.html")
    if args.require_all and any(a["status"] != "matched" for a in summaries):
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "download", "validate"])
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root")
    parser.add_argument("--manifest", type=Path, help="Default: data/ngram/sample_manifest.json")
    parser.add_argument("--output", type=Path, help="Default: outputs/ngram_sample")
    parser.add_argument("--sample-size", type=int, default=25, help="plan: deterministic sample with five anchors")
    parser.add_argument("--artists", help="plan: comma-separated QIDs; overrides sample size")
    parser.add_argument("--start-year", type=int, default=1800)
    parser.add_argument("--end-year", type=int, default=2019)
    parser.add_argument("--max-gib", type=float, default=1, help="download: hard preflight byte budget")
    parser.add_argument("--require-all", action="store_true", help="validate: exit 2 unless all queries match in range")
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    args.manifest = args.manifest or args.root / "data/ngram/sample_manifest.json"
    args.output = args.output or args.root / "outputs/ngram_sample"
    try:
        if not 1800 <= args.start_year <= args.end_year <= 2019:
            raise ValueError("20200217 workflow requires 1800 <= start-year <= end-year <= 2019")
        if not 0 < args.max_gib < float("inf"):
            raise ValueError("max-gib must be positive and finite")
        if args.command == "plan":
            plan(args)
        elif args.command == "download":
            download_plan(args)
        else:
            return validate(args)
        return 0
    except (OSError, ValueError, KeyError, EOFError, HTTPException, zlib.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
