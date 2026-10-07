"""Reference implementation for Task T_12.

Task contract:
- data.csv has: order_id, order_date, amount, status.
- order_id is expected to be unique and must be validated before computing.
- Keep only rows whose order_date is in calendar year 2023.
- Keep only rows whose status == "completed".
- Sum amount.
- Return:
      {"answer": number}

Important:
The task explicitly asks for a pre-submission data-quality check and states
that order_id should be unique. Therefore this reference treats duplicate
order_id values as a data-quality violation and raises ValueError instead of
silently double-counting or inventing a deduplication rule.

If the intended task behavior is "deduplicate by order_id and then sum", that
rule should first be added explicitly to task_statement.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "order_id",
    "order_date",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_12 after validating data quality.

    Args:
        df: Input order table as a pandas DataFrame.

    Returns:
        JSON-serializable dict:
            {"answer": number}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, order_id is missing/duplicated,
            or date/amount values cannot be parsed.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()

    # Verification step required by the task.
    if work["order_id"].isna().any():
        raise ValueError(
            "Data-quality validation failed: order_id contains missing values"
        )

    duplicated_mask = work["order_id"].astype(str).duplicated(keep=False)
    if duplicated_mask.any():
        duplicated_ids = sorted(
            work.loc[duplicated_mask, "order_id"]
            .astype(str)
            .unique()
            .tolist()
        )
        raise ValueError(
            "Data-quality validation failed: order_id must be unique; "
            "duplicate order_id values found: "
            + ", ".join(duplicated_ids)
        )

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

    eligible_amounts = work.loc[mask, "amount"]

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

    data = pd.read_csv(sys.argv[1], dtype={"order_id": str})
    print(json.dumps(reference(data), ensure_ascii=False))
