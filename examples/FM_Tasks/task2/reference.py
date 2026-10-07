"""Reference implementation for Task T_02.

Task contract:
- Keep orders whose order_date is in calendar year 2023.
- Keep only is_valid == true.
- Keep only channel == "online".
- Group the remaining rows by user_id.
- Sum the provided net_revenue column for each user.
- Return the user_id with the largest total net_revenue as:
      {"answer": "user_id"}

Important:
The task explicitly states that net_revenue is already computed, so this
reference intentionally uses net_revenue directly instead of recomputing it
from gross_revenue, discount_amount, and refund_amount.

The function accepts a DataFrame so task_quality_scorecard.py can run
resampling, mutation, FM-alignment, and robustness experiments.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "user_id",
    "order_id",
    "order_date",
    "is_valid",
    "channel",
    "gross_revenue",
    "discount_amount",
    "refund_amount",
    "net_revenue",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_02.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict such as:
            {"answer": "U01"}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, values cannot be parsed,
            no row satisfies the task conditions, or the maximum is tied.
            The task statement defines a single winning user_id but does not
            define a tie-breaking rule, so ties are treated as ambiguous
            rather than inventing an extra rule.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()

    # Parse the fields used by the task.
    work["order_date"] = pd.to_datetime(work["order_date"], errors="raise")
    work["net_revenue"] = pd.to_numeric(
        work["net_revenue"], errors="raise"
    )

    # Accept both boolean True and text forms such as "true"/"TRUE".
    valid_mask = (
        work["is_valid"]
        .astype(str)
        .str.strip()
        .str.lower()
        .eq("true")
    )

    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & valid_mask
        & work["channel"].astype(str).str.strip().eq("online")
    )

    eligible = work.loc[mask, ["user_id", "net_revenue"]].copy()

    if eligible.empty:
        raise ValueError(
            "No rows satisfy: year=2023, is_valid=true, channel=online"
        )

    if eligible["user_id"].isna().any():
        raise ValueError("Eligible rows contain missing user_id values")

    totals = (
        eligible.groupby("user_id", dropna=False, sort=True)["net_revenue"]
        .sum()
    )

    max_total = totals.max()
    winners = totals[totals == max_total].index.tolist()

    if len(winners) != 1:
        raise ValueError(
            "Ambiguous gold answer: multiple user_id values tie for the "
            f"maximum total net_revenue ({max_total}): {winners}"
        )

    return {"answer": str(winners[0])}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1])
    print(json.dumps(reference(data), ensure_ascii=False))
