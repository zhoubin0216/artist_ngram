from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
import yaml


ROOT = Path(__file__).resolve().parents[2]
GETTY_DIR = ROOT / "data" / "getty"
RAW_DIR = GETTY_DIR / "raw"
QUERY_DIR = ROOT / "queries" / "getty"
CONFIG_PATH = ROOT / "configs" / "getty.yaml"
ARTIST_BOOK = ROOT / "outputs" / "艺术家字典_v1_首批.xlsx"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_directories() -> None:
    GETTY_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "sparql").mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "ulan").mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "person").mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalise_name(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text.casefold())
    return " ".join(text.split())


def sparql_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def read_template(name: str) -> str:
    return (QUERY_DIR / name).read_text(encoding="utf-8")


def chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def query_sparql(
    query: str,
    *,
    raw_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    ensure_directories()
    output = RAW_DIR / "sparql" / raw_name
    if output.exists():
        return json.loads(output.read_text(encoding="utf-8"))
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": config["user_agent"],
    }
    error: Exception | None = None
    for attempt in range(1, int(config["request_retries"]) + 1):
        try:
            response = requests.get(
                config["endpoint"],
                params={"query": query},
                headers=headers,
                timeout=int(config["request_timeout_seconds"]),
            )
            response.raise_for_status()
            payload = response.json()
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            time.sleep(float(config.get("request_delay_seconds", 0)))
            return payload
        except (requests.RequestException, ValueError) as exc:
            error = exc
            if attempt < int(config["request_retries"]):
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Getty SPARQL request failed after retries: {error}")


def fetch_ulan(ulan_id: str, config: dict[str, Any]) -> dict[str, Any]:
    ensure_directories()
    if not str(ulan_id).strip():
        return {}
    output = RAW_DIR / "ulan" / f"{ulan_id}.json"
    if output.exists():
        return json.loads(output.read_text(encoding="utf-8"))
    url = config["ulan_url_template"].format(ulan_id=ulan_id)
    return fetch_json_record(url, output, config)


def fetch_provenance_record(uri: str, config: dict[str, Any]) -> dict[str, Any]:
    ensure_directories()
    record_id = uri.rstrip("/").rsplit("/", 1)[-1]
    output = RAW_DIR / "person" / f"{record_id}.json"
    if output.exists():
        return json.loads(output.read_text(encoding="utf-8"))
    return fetch_json_record(uri, output, config)


def fetch_json_record(
    url: str, output: Path, config: dict[str, Any]
) -> dict[str, Any]:
    error: Exception | None = None
    for attempt in range(1, int(config["request_retries"]) + 1):
        try:
            response = requests.get(
                url,
                headers={
                    "Accept": "application/ld+json",
                    "User-Agent": config["user_agent"],
                },
                timeout=int(config["request_timeout_seconds"]),
            )
            response.raise_for_status()
            payload = response.json()
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return payload
        except (requests.RequestException, ValueError) as exc:
            error = exc
            if attempt < int(config["request_retries"]):
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Getty JSON-LD request failed after retries for {url}: {error}")


def ulan_names(payload: dict[str, Any], ulan_id: str) -> list[str]:
    subject_ids = {
        f"http://vocab.getty.edu/ulan/{ulan_id}",
        f"https://vocab.getty.edu/ulan/{ulan_id}",
    }
    for node in payload.get("@graph", []):
        if node.get("@id") not in subject_ids:
            continue
        values: list[str] = []
        for key in (
            "http://www.w3.org/2004/02/skos/core#prefLabel",
            "http://www.w3.org/2004/02/skos/core#altLabel",
            "http://www.w3.org/2000/01/rdf-schema#label",
        ):
            raw = node.get(key, [])
            raw_values = raw if isinstance(raw, list) else [raw]
            for item in raw_values:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, dict) and item.get("@value"):
                    values.append(str(item["@value"]))
        return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))
    return []


def binding_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        rows.append({key: str(value.get("value", "")) for key, value in binding.items()})
    return rows


def year_from_value(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?<!\d)(1\d{3}|20\d{2})(?!\d)", str(value))
    return int(match.group(1)) if match else None


def write_parquet_and_csv(df: pd.DataFrame, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(stem.with_suffix(".csv"), index=False, encoding="utf-8")
    df.to_parquet(stem.with_suffix(".parquet"), index=False)
