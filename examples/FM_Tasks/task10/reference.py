"""Reference implementation for Task T_10.

Task contract:
- data.csv has: order_id, order_date, amount, status.
- Duplicate records may exist:
    * exact duplicate rows
    * the same order_id may appear with both "completed" and "refunded"
- Keep rows whose order_date is in calendar year 2023.
- Keep rows whose status == "completed".
- Count UNIQUE order_id values after those filters.
- Return:
      {"answer": integer}

Important:
The task requires COUNT(DISTINCT order_id) over the filtered completed
records. A conflicting refunded row for the same order_id does not cancel
an existing completed row, because the filtering condition is applied to
records first and the resulting order_id values are then deduplicated.

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
    "order_date",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_10.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, e.g.:
            {"answer": 7}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, order_date cannot be parsed,
            or an eligible completed row has a missing order_id.
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

    # Apply the task filters first.
    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible = work.loc[
        mask,
        ["order_id"],
    ].copy()

    # An eligible order must have an identifier in order to be counted
    # as a unique order.
    if eligible["order_id"].isna().any():
        raise ValueError(
            "Eligible completed rows contain missing order_id values"
        )

    # Core requirement: count distinct order_id values.
    unique_order_count = int(
        eligible["order_id"]
        .astype(str)
        .nunique(dropna=True)
    )

    return {"answer": unique_order_count}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    # Read order_id as string so identifier semantics are preserved.
    data = pd.read_csv(sys.argv[1], dtype={"order_id": str})
    print(json.dumps(reference(data), ensure_ascii=False))
