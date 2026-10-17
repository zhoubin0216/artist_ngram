from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import ARTIST_BOOK, GETTY_DIR, ensure_directories


CURATED_ARTISTS = [
    "Claude Monet",
    "Vincent van Gogh",
    "Pablo Picasso",
    "Jacques-Louis David",
    "William-Adolphe Bouguereau",
    "Camille Pissarro",
    "Caspar David Friedrich",
    "Edgar Degas",
    "Édouard Manet",
    "Edvard Munch",
    "Francisco Goya",
    "Georges Braque",
    "Georges Seurat",
    "Gustav Klimt",
    "Henri Matisse",
    "J. M. W. Turner",
    "Jean-Auguste-Dominique Ingres",
    "Paul Cézanne",
    "Paul Gauguin",
    "Pierre-Auguste Renoir",
    "Rosa Bonheur",
    "Salvador Dalí",
    "Umberto Boccioni",
    "Wassily Kandinsky",
    "Marc Chagall",
]


OUTPUT_COLUMNS = [
    "artist_id",
    "wikidata_qid",
    "canonical_name",
    "birth_year",
    "death_year",
    "nationality",
    "movement_ids",
    "movement_names",
    "ulan_id",
    "source_url",
]


def load_artist_dictionary(workbook: Path = ARTIST_BOOK) -> pd.DataFrame:
    artists = pd.read_excel(workbook, sheet_name="artists", header=3, dtype=str).fillna("")
    artists = artists.rename(columns={"artist_id": "wikidata_qid"})
    artists.insert(0, "artist_id", artists["wikidata_qid"])
    if artists["artist_id"].duplicated().any():
        raise ValueError("Artist dictionary contains duplicate artist_id values")
    if (artists["artist_id"].str.strip() == "").any():
        raise ValueError("Artist dictionary contains blank artist_id values")
    return artists[OUTPUT_COLUMNS].reset_index(drop=True)


def select_artists(
    *,
    scope: str,
    limit: int | None = None,
    start_index: int = 0,
    workbook: Path = ARTIST_BOOK,
) -> pd.DataFrame:
    artists = load_artist_dictionary(workbook)
    if start_index < 0:
        raise ValueError("start_index must be non-negative")
    if scope == "all":
        selected = artists.iloc[start_index:]
        if limit is not None:
            selected = selected.head(limit)
        return selected.reset_index(drop=True)
    if scope != "pilot":
        raise ValueError(f"Unknown Getty artist scope: {scope}")

    order = {name.casefold(): index for index, name in enumerate(CURATED_ARTISTS)}
    sample = artists[artists["canonical_name"].str.casefold().isin(order)].copy()
    sample["pilot_order"] = sample["canonical_name"].str.casefold().map(order)
    sample = sample.sort_values("pilot_order").iloc[start_index:]
    if limit is not None:
        sample = sample.head(limit)
    expected = min(
        len(CURATED_ARTISTS) - start_index,
        limit if limit is not None else len(CURATED_ARTISTS),
    )
    if len(sample) != expected:
        raise ValueError(
            f"Requested {expected} pilot artists but found {len(sample)} curated rows"
        )
    return sample[OUTPUT_COLUMNS].reset_index(drop=True)


def select_sample(limit: int, workbook: Path = ARTIST_BOOK) -> pd.DataFrame:
    return select_artists(scope="pilot", limit=limit, workbook=workbook)


def main() -> None:
    parser = argparse.ArgumentParser(description="Select the Getty pilot artists")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output", type=Path, default=GETTY_DIR / "sample_artists.csv")
    args = parser.parse_args()
    ensure_directories()
    sample = select_artists(scope="pilot", limit=args.limit)
    sample.to_csv(args.output, index=False, encoding="utf-8")
    print(f"Wrote {len(sample)} artists to {args.output}")


if __name__ == "__main__":
    main()
