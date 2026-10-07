"""Reference implementation for Task T_07.

Task contract:
- data.csv has: order_id, order_time, amount, status.
- order_time is an ISO timestamp with an explicit UTC offset.
- Convert every timestamp to Asia/Shanghai (UTC+8) local time.
- Keep rows whose LOCAL DATE is 2023-01-01.
- Keep only rows whose status == "completed".
- Sum amount.
- Return:
      {"answer": number}

Important:
Filtering must happen AFTER timezone conversion. Comparing the original
calendar date or stripping timezone information first would be incorrect.

The function accepts a pandas DataFrame so task_quality_scorecard.py can
perform resampling, mutation, FM-alignment, and robustness experiments.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "order_id",
    "order_time",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_07.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, e.g.:
            {"answer": 350.0}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, timestamps/amounts cannot
            be parsed, or order_time contains missing values.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()

    if work["order_time"].isna().any():
        raise ValueError("order_time contains missing values")

    # Parse all offset-aware timestamps onto one absolute timeline (UTC),
    # then convert to the required UTC+8 / Asia/Shanghai local time.
    try:
        utc_time = pd.to_datetime(
            work["order_time"],
            errors="raise",
            utc=True,
        )
    except Exception as exc:
        raise ValueError(
            f"Failed to parse order_time as offset-aware ISO timestamps: {exc}"
        ) from exc

    local_time = utc_time.dt.tz_convert("Asia/Shanghai")

    try:
        work["amount"] = pd.to_numeric(
            work["amount"],
            errors="raise",
        )
    except Exception as exc:
        raise ValueError(f"Failed to parse amount as numeric: {exc}") from exc

    # Filter by LOCAL calendar date after timezone conversion.
    target_date = pd.Timestamp("2023-01-01").date()

    mask = (
        local_time.dt.date.eq(target_date)
        & work["status"].astype(str).str.strip().eq("completed")
    )

    eligible_amounts = work.loc[mask, "amount"]

    # No eligible rows means the sum is numerically zero.
    total = sum(
        (Decimal(str(v)) for v in eligible_amounts.tolist()),
        Decimal("0"),
    )

    return {"answer": float(total)}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1])
    print(json.dumps(reference(data), ensure_ascii=False))
