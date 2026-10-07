"""Reference implementation for Task T_11.

Task contract:
- data.csv has: user_id, order_id, order_date, amount, status.
- Keep only orders whose order_date is in calendar year 2023.
- Keep only orders whose status == "completed".
- First group by user_id and take each user's maximum order amount.
- Then compute the arithmetic mean of those per-user maximum amounts.
- Return:
      {"answer": number}

This intentionally follows the required two-stage plan:
    filter -> per-user max -> average of maxima
rather than averaging all eligible orders directly.

The function accepts a pandas DataFrame so task_quality_scorecard.py can
perform resampling, mutation, FM-alignment, and robustness experiments.
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
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_11."""
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
    work["amount"] = pd.to_numeric(
        work["amount"],
        errors="raise",
    )

    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible = work.loc[
        mask,
        ["user_id", "amount"],
    ].copy()

    if eligible.empty:
        raise ValueError(
            "No rows satisfy year=2023 and status='completed'"
        )

    if eligible["user_id"].isna().any():
        raise ValueError("Eligible rows contain missing user_id values")

    # Stage 1: maximum completed-order amount for each user.
    per_user_max = (
        eligible.groupby("user_id", dropna=False)["amount"]
        .max()
    )

    if per_user_max.empty:
        raise ValueError("No per-user maxima could be computed")

    # Stage 2: average those per-user maximum amounts.
    answer = float(per_user_max.mean())

    return {"answer": answer}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1])
    print(json.dumps(reference(data), ensure_ascii=False))
