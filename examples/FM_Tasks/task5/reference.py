"""Reference implementation for Task T_05 using the merged single-table format.

Merged data contract
--------------------
The original logical data consists of two entities:

users:
    user_id, user_type, city

orders:
    order_id, user_id, order_date, amount, status

They are stored in one data.csv with:
    record_type, entity_id, user_id, user_type, city,
    order_date, amount, status

Mapping:
- record_type == "user":
    entity_id = logical users.user_id
- record_type == "order":
    entity_id = logical orders.order_id
    user_id   = logical orders.user_id

Task:
1. Reconstruct users and orders from record_type.
2. Join orders.user_id = users.user_id.
3. Keep orders in calendar year 2023.
4. Keep users whose user_type == "VIP".
5. Keep orders whose status == "completed".
6. Find the row with the maximum amount.
7. Return its order_id as:
       {"answer": "order_id"}

The function accepts a pandas DataFrame so task_quality_scorecard.py can
perform resampling, mutation, FM-alignment, and robustness experiments.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "record_type",
    "entity_id",
    "user_id",
    "user_type",
    "city",
    "order_date",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the gold answer for Task T_05.

    Args:
        df: Merged user/order table.

    Returns:
        JSON-serializable dict, e.g.:
            {"answer": "O05"}

    Raises:
        TypeError:
            If df is not a pandas DataFrame.
        ValueError:
            If required columns are missing, entity records are malformed,
            eligible orders cannot be joined to a user, no eligible order
            exists, or multiple different order_ids tie for maximum amount.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("reference(df) expects a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    work = df.copy()
    record_type = work["record_type"].astype(str).str.strip().str.lower()

    # Reconstruct the logical users table.
    users = work.loc[
        record_type.eq("user"),
        ["entity_id", "user_type", "city"],
    ].copy()

    users = users.rename(columns={"entity_id": "user_id"})

    # Reconstruct the logical orders table.
    orders = work.loc[
        record_type.eq("order"),
        ["entity_id", "user_id", "order_date", "amount", "status"],
    ].copy()

    orders = orders.rename(columns={"entity_id": "order_id"})

    if users.empty:
        raise ValueError("No user records found (record_type='user')")

    if orders.empty:
        raise ValueError("No order records found (record_type='order')")

    if users["user_id"].isna().any():
        raise ValueError("User records contain missing entity_id/user_id")

    if orders["order_id"].isna().any():
        raise ValueError("Order records contain missing entity_id/order_id")

    if orders["user_id"].isna().any():
        raise ValueError("Order records contain missing user_id foreign keys")

    # A user master key should identify one user record.
    if users["user_id"].duplicated().any():
        duplicated = (
            users.loc[users["user_id"].duplicated(keep=False), "user_id"]
            .astype(str)
            .unique()
            .tolist()
        )
        raise ValueError(
            "Duplicate user master records for user_id: "
            + ", ".join(duplicated)
        )

    orders["order_date"] = pd.to_datetime(
        orders["order_date"],
        errors="raise",
    )
    orders["amount"] = pd.to_numeric(
        orders["amount"],
        errors="raise",
    )

    # Explicitly perform the logical join required by the task.
    joined = orders.merge(
        users,
        on="user_id",
        how="left",
        validate="many_to_one",
        indicator=True,
    )

    # Missing joins are a data-integrity error for this task rather than
    # silently treating the user as non-VIP.
    unmatched = joined.loc[
        joined["_merge"].ne("both"),
        ["order_id", "user_id"],
    ]
    if not unmatched.empty:
        pairs = [
            f"{row.order_id}->{row.user_id}"
            for row in unmatched.itertuples(index=False)
        ]
        raise ValueError(
            "Orders reference user_id values with no matching user record: "
            + ", ".join(pairs)
        )

    mask = (
        (joined["order_date"] >= pd.Timestamp("2023-01-01"))
        & (joined["order_date"] < pd.Timestamp("2024-01-01"))
        & joined["user_type"].astype(str).str.strip().eq("VIP")
        & joined["status"].astype(str).str.strip().eq("completed")
    )

    eligible = joined.loc[
        mask,
        ["order_id", "user_id", "order_date", "amount", "status", "user_type"],
    ].copy()

    if eligible.empty:
        raise ValueError(
            "No orders satisfy year=2023, user_type='VIP', "
            "status='completed'"
        )

    max_amount = eligible["amount"].max()
    winners = eligible.loc[
        eligible["amount"].eq(max_amount),
        "order_id",
    ].astype(str).drop_duplicates().tolist()

    # The task asks for one order_id but defines no tie-break rule.
    if len(winners) != 1:
        raise ValueError(
            "Ambiguous gold answer: multiple order_ids tie for maximum "
            f"amount {max_amount}: {winners}. "
            "The task statement defines no tie-breaker."
        )

    return {"answer": winners[0]}


if __name__ == "__main__":
    # Local smoke test:
    #   python reference.py data.csv
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python reference.py <data.csv>")

    data = pd.read_csv(sys.argv[1], dtype={"entity_id": str, "user_id": str})
    print(json.dumps(reference(data), ensure_ascii=False))
