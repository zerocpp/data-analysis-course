"""Reference implementation for Task T_13.

Task contract:
- data.csv has: order_id, order_date, amount, status.
- Keep only rows whose order_date is in calendar year 2023.
- Keep only rows whose status == "completed".
- Compute the arithmetic mean of amount.
- Round the final result to 2 decimal places using conventional
  decimal half-up rounding ("四舍五入").
- Return:
      {"answer": number}

Important:
Rounding is applied only to the final average, not to each individual
amount before averaging.

The function accepts a pandas DataFrame so task_quality_scorecard.py can
perform resampling, mutation, FM-alignment, and robustness experiments.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "order_id",
    "order_date",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_13.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, e.g.:
            {"answer": 57.97}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, dates/amounts cannot be parsed,
            or no row satisfies the filters.
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

    # Keep amount values as decimal text for deterministic money arithmetic.
    try:
        amount_decimal = work["amount"].map(
            lambda v: Decimal(str(v))
        )
    except Exception as exc:
        raise ValueError(
            f"Failed to parse amount as decimal: {exc}"
        ) from exc

    mask = (
        (work["order_date"] >= pd.Timestamp("2023-01-01"))
        & (work["order_date"] < pd.Timestamp("2024-01-01"))
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible_amounts = amount_decimal.loc[mask]

    if eligible_amounts.empty:
        raise ValueError(
            "No rows satisfy year=2023 and status='completed'"
        )

    total = sum(eligible_amounts.tolist(), Decimal("0"))
    mean = total / Decimal(len(eligible_amounts))

    # Chinese "四舍五入" is implemented explicitly as ROUND_HALF_UP.
    rounded = mean.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    # The task requires a JSON numeric value, not a string.
    return {"answer": float(rounded)}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1])
    print(json.dumps(reference(data), ensure_ascii=False))
