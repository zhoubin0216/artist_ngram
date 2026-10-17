from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common import normalise_name, year_from_value


@dataclass(frozen=True)
class ScoredCandidate:
    row: dict[str, str]
    score: float
    evidence: list[str]


def score_candidate(
    artist: dict[str, Any],
    candidate: dict[str, str],
    ulan_aliases: list[str],
) -> ScoredCandidate:
    canonical = normalise_name(str(artist.get("canonical_name", "")))
    candidate_name = normalise_name(candidate.get("candidate_name"))
    preferred = normalise_name(candidate.get("getty_preferred_name"))
    aliases = {normalise_name(name) for name in ulan_aliases}
    evidence: list[str] = []
    score = 0.0

    if candidate_name == canonical or preferred == canonical:
        score += 0.45
        evidence.append("canonical_name_exact")
    elif candidate_name in aliases or preferred in aliases:
        score += 0.35
        evidence.append("ulan_name_exact")

    expected_birth = year_from_value(str(artist.get("birth_year", "")))
    expected_death = year_from_value(str(artist.get("death_year", "")))
    candidate_birth = year_from_value(candidate.get("birth_begin"))
    candidate_death = year_from_value(candidate.get("death_begin"))

    if expected_birth is not None and candidate_birth is not None:
        if expected_birth == candidate_birth:
            score += 0.25
            evidence.append("birth_year_exact")
        elif abs(expected_birth - candidate_birth) <= 1:
            score += 0.10
            evidence.append("birth_year_near")
    if expected_death is not None and candidate_death is not None:
        if expected_death == candidate_death:
            score += 0.20
            evidence.append("death_year_exact")
        elif abs(expected_death - candidate_death) <= 1:
            score += 0.08
            evidence.append("death_year_near")

    if candidate_name in aliases or preferred in aliases:
        score += 0.10
        evidence.append("ulan_alias_support")

    return ScoredCandidate(candidate, min(round(score, 3), 1.0), evidence)


def choose_candidate(
    artist: dict[str, Any],
    candidates: list[dict[str, str]],
    ulan_aliases: list[str],
    *,
    low_confidence_threshold: float,
    ambiguity_margin: float,
) -> dict[str, Any]:
    if not candidates:
        return {
            "getty_person_uri": "",
            "getty_preferred_name": "",
            "match_method": "unmatched",
            "match_confidence": 0.0,
            "manual_review": True,
            "candidate_count": 0,
            "candidate_uris": "",
            "matched_birth_year": "",
            "matched_death_year": "",
            "match_evidence": "no exact canonical or ULAN-supported name candidate",
            "source_url": "",
        }

    scored = sorted(
        (score_candidate(artist, candidate, ulan_aliases) for candidate in candidates),
        key=lambda item: (-item.score, item.row.get("person", "")),
    )
    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else -1.0
    ambiguous = len(scored) > 1 and best.score - second_score < ambiguity_margin
    low_confidence = best.score < low_confidence_threshold
    accepted = not ambiguous and not low_confidence
    row = best.row
    candidate_uris = sorted(
        {item.row.get("person", "") for item in scored if item.row.get("person", "")}
    )
    return {
        "getty_person_uri": row.get("person", "") if accepted else "",
        "getty_preferred_name": row.get("getty_preferred_name", "") if accepted else "",
        "match_method": "+".join(best.evidence) if accepted else "ambiguous_or_low_confidence",
        "match_confidence": best.score,
        "manual_review": bool(ambiguous or low_confidence or not accepted),
        "candidate_count": len({item.row.get("person", "") for item in scored}),
        "candidate_uris": ";".join(candidate_uris),
        "matched_birth_year": year_from_value(row.get("birth_begin")) or "",
        "matched_death_year": year_from_value(row.get("death_begin")) or "",
        "match_evidence": ";".join(best.evidence),
        "source_url": row.get("person", "") if accepted else "",
    }
