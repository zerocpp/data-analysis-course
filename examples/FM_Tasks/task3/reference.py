"""Reference implementation for Task T_03.

Task contract:
- data.csv has: order_id, order_date, product_name, amount, status.
- order_id is a STRING.
- Keep rows where amount > 100 and status == "finished".
- Among those rows, find the lexicographically smallest order_id.
- Return the corresponding product_name as:
      {"answer": "product_name"}

Important:
Lexicographic comparison is performed on the full order_id string.
For example, "ORD_10" is lexicographically smaller than "ORD_9".
Do NOT extract the numeric suffix and compare it numerically.

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
    "product_name",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_03.

    Args:
        df: Input table as a pandas DataFrame.

    Returns:
        JSON-serializable dict, for example:
            {"answer": "Monitor"}

    Raises:
        TypeError: If df is not a pandas DataFrame.
        ValueError: If required columns are missing, filtering yields no rows,
                    or the minimum order_id maps to multiple product names.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()

    # amount must be numeric for the strict > 100 filter.
    work["amount"] = pd.to_numeric(work["amount"], errors="raise")

    # order_id is explicitly defined by the task as a string.
    if work["order_id"].isna().any():
        raise ValueError("order_id contains missing values")

    # Preserve string semantics; do not parse numeric suffixes.
    work["order_id"] = work["order_id"].astype(str)

    mask = (
        (work["amount"] > 100)
        & work["status"].astype(str).str.strip().eq("finished")
    )

    eligible = work.loc[
        mask,
        ["order_id", "product_name"],
    ].copy()

    if eligible.empty:
        raise ValueError(
            "No rows satisfy amount > 100 and status == 'finished'"
        )

    min_order_id = eligible["order_id"].min()

    matches = eligible.loc[
        eligible["order_id"] == min_order_id,
        "product_name",
    ]

    if matches.isna().any():
        raise ValueError(
            f"The lexicographically smallest order_id {min_order_id!r} "
            "has a missing product_name"
        )

    product_names = matches.astype(str).drop_duplicates().tolist()

    if len(product_names) != 1:
        raise ValueError(
            "Ambiguous gold answer: the lexicographically smallest "
            f"order_id {min_order_id!r} maps to multiple product names: "
            f"{product_names}"
        )

    return {"answer": product_names[0]}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data_FM03.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    # Explicit dtype keeps order_id as a string even if future IDs look numeric.
    data = pd.read_csv(sys.argv[1], dtype={"order_id": str})
    print(json.dumps(reference(data), ensure_ascii=False))
