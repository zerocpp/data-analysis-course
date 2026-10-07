"""Reference implementation for Task T_08.

Task contract:
- data.csv has: order_id, order_date, amount, status.
- amount may contain missing values represented as:
    * "NA"
    * empty string
    * "null"
- Keep only rows whose order_date is in calendar year 2023.
- Keep only rows whose status == "completed".
- Exclude rows whose amount is missing.
- Compute the arithmetic mean of the remaining amount values.
- Return:
      {"answer": number}

Important:
Missing amount rows must be EXCLUDED from the denominator of the mean.
They must not be treated as 0.

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

MISSING_AMOUNT_TOKENS = {
    "",
    "na",
    "null",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_08.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, e.g.:
            {"answer": 200.0}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, dates cannot be parsed,
            a non-missing amount is non-numeric, or no non-missing eligible
            amount remains after filtering.
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

    # First apply the task's date/status filter.
    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible = work.loc[
        mask,
        ["amount"],
    ].copy()

    if eligible.empty:
        raise ValueError(
            "No rows satisfy year=2023 and status='completed'"
        )

    # Normalize the three missing-value representations required by the task.
    amount_text = eligible["amount"].astype("string")
    normalized = amount_text.str.strip()

    missing_mask = (
        normalized.isna()
        | normalized.str.lower().isin(MISSING_AMOUNT_TOKENS)
    )

    non_missing = normalized.loc[~missing_mask]

    if non_missing.empty:
        raise ValueError(
            "All eligible amount values are missing; mean is undefined"
        )

    # Any value that is not one of the declared missing markers must be numeric.
    try:
        numeric_amount = pd.to_numeric(
            non_missing,
            errors="raise",
        )
    except Exception as exc:
        raise ValueError(
            f"Eligible amount contains a non-numeric non-missing value: {exc}"
        ) from exc

    mean_amount = float(numeric_amount.mean())

    return {"answer": mean_amount}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    # keep_default_na=False is intentional:
    # reference() itself should recognize NA/blank/null according to the task.
    data = pd.read_csv(sys.argv[1], keep_default_na=False)
    print(json.dumps(reference(data), ensure_ascii=False))
