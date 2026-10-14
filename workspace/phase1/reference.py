"""Reference solver: the only input is `df`, the only output is the answer.

Only in-memory computation is allowed (imports limited to pandas / decimal).
Keep this file under the line limit in policy/runtime_policy.json.
"""


def reference(df):
    return int(df["amount"].sum())
