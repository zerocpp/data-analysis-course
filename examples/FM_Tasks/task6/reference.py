"""Reference implementation for Task T_06.

Task contract:
- data.csv has: order_id, user_id, region, order_date, amount, status.
- Keep only rows whose order_date is in calendar year 2023.
- Keep only rows whose status == "completed".
- Group by region.
- For each region, count DISTINCT user_id values.
- Return the region with the largest distinct-user count as:
      {"answer": "region"}

Important:
This task explicitly requires COUNT(DISTINCT user_id), not order count
and not COUNT(user_id).

The function accepts a pandas DataFrame so task_quality_scorecard.py can
perform resampling, mutation, FM-alignment, and robustness experiments.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "order_id",
    "user_id",
    "region",
    "order_date",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_06.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, for example:
            {"answer": "R3"}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, dates cannot be parsed,
            no rows satisfy the filters, eligible rows contain missing
            region/user_id values, or multiple regions tie for the
            maximum distinct-user count.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()

    work["order_date"] = pd.to_datetime(
        work["order_date"],
        errors="raise",
    )

    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible = work.loc[
        mask,
        ["user_id", "region"],
    ].copy()

    if eligible.empty:
        raise ValueError(
            "No rows satisfy year=2023 and status='completed'"
        )

    if eligible["user_id"].isna().any():
        raise ValueError("Eligible rows contain missing user_id values")

    if eligible["region"].isna().any():
        raise ValueError("Eligible rows contain missing region values")

    # Core requirement: count DISTINCT users within each region.
    distinct_counts = (
        eligible.groupby("region", dropna=False)["user_id"]
        .nunique(dropna=True)
    )

    max_count = distinct_counts.max()
    winners = distinct_counts[
        distinct_counts.eq(max_count)
    ].index.astype(str).tolist()

    # The task asks for exactly one region and provides no tie-break rule.
    if len(winners) != 1:
        raise ValueError(
            "Ambiguous gold answer: multiple regions tie for the maximum "
            f"distinct-user count ({int(max_count)}): {winners}. "
            "The task statement defines no tie-breaker."
        )

    return {"answer": winners[0]}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1])
    print(json.dumps(reference(data), ensure_ascii=False))
