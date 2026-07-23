"""Loads the pre-extracted BCCWJ frequency subset (see
scripts/extract_bccwj_subset.py) as an in-memory commonness lookup for the
selection algorithm's set-cover tie-break. Auxiliary signal only -- not
stored in the DB, not a primary input. Absence of a match is expected and
handled by callers via a plain dict.get(...).
"""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrequencyInfo:
    core_rank: int | None
    frequency: int | None
    pmw: float | None


def load_bccwj_frequency_subset(path: Path) -> dict[str, FrequencyInfo]:
    lookup: dict[str, FrequencyInfo] = {}
    if not path.exists():
        return lookup

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            lemma = row.get("lemma", "")
            if not lemma:
                continue

            def _int_or_none(v: str | None) -> int | None:
                try:
                    return int(v) if v else None
                except ValueError:
                    return None

            def _float_or_none(v: str | None) -> float | None:
                try:
                    return float(v) if v else None
                except ValueError:
                    return None

            lookup[lemma] = FrequencyInfo(
                core_rank=_int_or_none(row.get("core_rank")),
                frequency=_int_or_none(row.get("frequency")),
                pmw=_float_or_none(row.get("pmw")),
            )

    return lookup
