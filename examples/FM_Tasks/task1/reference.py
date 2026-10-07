from decimal import Decimal
from typing import Any, Dict

import pandas as pd


REQUIRED_COLUMNS = {
    "order_id",
    "order_date",
    "region",
    "channel",
    "amount",
    "status",
}


def reference(df: pd.DataFrame) -> Dict[str, Any]:
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
        & (work["region"] == "NE")
        & (work["channel"] == "online")
        & (work["status"] == "completed")
    )

    total = sum(
        (
            Decimal(str(v))
            for v in work.loc[mask, "amount"].tolist()
        ),
        Decimal("0"),
    )

    return {
        "answer": float(total)
    }