"""Deterministic reference gold: reference(df) -> JSON str."""
import pandas as pd


def reference(df):
    total = float(df["amount"].sum()) if "amount" in df.columns else 0
    return '{"answer": ' + str(round(total, 2)) + '}'
