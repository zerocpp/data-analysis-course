"""Deterministic smoke agent: run(task, df, llm) -> JSON str."""
import json


def run(task, df, llm):
    total = float(df["amount"].sum()) if "amount" in df.columns else 0
    return json.dumps({"answer": round(total, 2)})
