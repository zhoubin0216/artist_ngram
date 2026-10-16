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


def select_sample(limit: int, workbook: Path = ARTIST_BOOK) -> pd.DataFrame:
    artists = pd.read_excel(workbook, sheet_name="artists", header=3, dtype=str).fillna("")
    order = {name.casefold(): index for index, name in enumerate(CURATED_ARTISTS)}
    sample = artists[artists["canonical_name"].str.casefold().isin(order)].copy()
    sample["pilot_order"] = sample["canonical_name"].str.casefold().map(order)
    sample = sample.sort_values("pilot_order").head(limit)
    if len(sample) != limit:
        raise ValueError(f"Requested {limit} pilot artists but found {len(sample)} curated rows")
    sample = sample.rename(columns={"artist_id": "wikidata_qid"})
    sample.insert(0, "artist_id", sample["wikidata_qid"])
    columns = [
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
    return sample[columns].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Select the Getty pilot artists")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output", type=Path, default=GETTY_DIR / "sample_artists.csv")
    args = parser.parse_args()
    ensure_directories()
    sample = select_sample(args.limit)
    sample.to_csv(args.output, index=False, encoding="utf-8")
    print(f"Wrote {len(sample)} artists to {args.output}")


if __name__ == "__main__":
    main()
