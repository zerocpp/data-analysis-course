"""FM10/FM13 taxonomy 扩展验收测试。

覆盖：
- 正式 taxonomy 12 -> 14（FAILURE_MODE_COUNT、EXPECTED_FAILURE_MODE_CODES）
- slug 映射（FM10_DUPLICATE_RECORD_HANDLING / FM13_NUMERICAL_PRECISION_ROUNDING）
- workspace validate：FM10 / FM13 接受，FM15 / FM00 拒绝
- Internal Error 仍不得进入 FM 分类
- 新增两个真实 manual workspace 的 reference regression（含错误路径判别）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from codesetarena.constants import (
    EXPECTED_FAILURE_MODE_CODES,
    FAILURE_MODE_COUNT,
    FAILURE_MODE_FULL_KEYS,
    FAILURE_TAXONOMY_VERSION,
    failure_mode_full_key,
)
from codesetarena.main.outcome import matrix_label
from codesetarena.main.project import import_module_from_path, load_project
from codesetarena.main.validation import validate_project

REPO = Path(__file__).resolve().parents[1]
FM10_WS = REPO / "workspace" / "manual" / "DA02_FM10_T1_A"
FM13_WS = REPO / "workspace" / "manual" / "DA06_FM13_T1_A"


# ---------------------------------------------------------------------------
# 1-2. Taxonomy 14 类 + FM10/FM13 注册
# ---------------------------------------------------------------------------


def test_taxonomy_count_is_14():
    assert FAILURE_TAXONOMY_VERSION == "2.0"
    assert FAILURE_MODE_COUNT == 14
    assert EXPECTED_FAILURE_MODE_CODES == frozenset(
        {f"FM{i:02d}" for i in range(1, 15)}
    )


def test_fm10_fm13_registered_with_slugs():
    assert "FM10" in EXPECTED_FAILURE_MODE_CODES
    assert "FM13" in EXPECTED_FAILURE_MODE_CODES
    assert failure_mode_full_key("FM10") == "FM10_DUPLICATE_RECORD_HANDLING"
    assert failure_mode_full_key("FM13") == "FM13_NUMERICAL_PRECISION_ROUNDING"
    assert {"FM10_DUPLICATE_RECORD_HANDLING", "FM13_NUMERICAL_PRECISION_ROUNDING"} <= FAILURE_MODE_FULL_KEYS
    # 旧 12 类 slug 保持既有 truth-table 写法（不破坏既有标注）
    assert failure_mode_full_key("FM05") == "FM05_JOIN"
    assert failure_mode_full_key("FM11") == "FM11_PLANNING_DECOMPOSITION"


# ---------------------------------------------------------------------------
# 3-5. Validator：FM10/FM13 PASS，FM15 REJECT
# ---------------------------------------------------------------------------


def test_validate_accepts_fm10():
    errors = validate_project(load_project(FM10_WS))
    assert errors == [], errors


def test_validate_accepts_fm13():
    errors = validate_project(load_project(FM13_WS))
    assert errors == [], errors


def test_validate_rejects_fm15(tmp_path: Path):
    from tests.test_phase2_and_outcome import make_workspace as _mw

    ws = _mw(tmp_path / "ws", task={"task_id": "T", "expected_failure_mode": "FM15"})
    errors = validate_project(load_project(ws))
    assert errors
    assert "不是正式 Failure Mode" in errors[0]


def test_validate_rejects_fm00(tmp_path: Path):
    from tests.test_phase2_and_outcome import make_workspace as _mw

    ws = _mw(tmp_path / "ws2", task={"task_id": "T", "expected_failure_mode": "FM00"})
    errors = validate_project(load_project(ws))
    assert errors
    assert "不是正式 Failure Mode" in errors[0]


# ---------------------------------------------------------------------------
# 6. Internal Error 不得进入 FM 分类
# ---------------------------------------------------------------------------


def test_internal_error_labels_never_fm(tmp_path: Path):
    import json
    import shutil
    from itertools import chain

    from codesetarena.main.budget import Budget
    from codesetarena.main.llm import LLMTimeout
    from codesetarena.main.runner import run_project

    class TimeoutLLM:
        def complete(self, messages):
            raise LLMTimeout("llm request exceeded deadline")

    # 复制到 tmp_path 再执行，避免污染仓库内真实 workspace
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("agent.py", ".env", ".env.example", "task.json", "data.csv", "reference.py"):
        shutil.copy(FM10_WS / name, ws / name)

    result = run_project(
        ws,
        llm=TimeoutLLM(),
        budget=Budget(timeout_seconds=60, max_tokens=1),
    )
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    label = matrix_label(record)
    assert label.startswith("INTERNAL_")
    assert record["outcome_type"] == "internal_error"
    # Internal Error 不能携带 Failure Mode 语义
    assert record["expected_failure_mode"] is None or record["expected_failure_mode"] == "FM10"


# ---------------------------------------------------------------------------
# 7-8. 新 Task reference regression（真实 workspace 文件）
# ---------------------------------------------------------------------------


def test_fm10_reference_gold_and_wrong_paths():
    ref = import_module_from_path(str(FM10_WS / "reference.py"), "_fm10_ref")
    df = pd.read_csv(FM10_WS / "data.csv")
    assert ref.reference(df) == 300

    wrong_subset = int(df.drop_duplicates(subset=["order_id"])["amount"].sum())
    no_dedup = int(df["amount"].sum())
    assert wrong_subset == 250 and wrong_subset != 300
    assert no_dedup == 470 and no_dedup != 300


def test_fm13_reference_gold_and_wrong_paths():
    ref = import_module_from_path(str(FM13_WS / "reference.py"), "_fm13_ref")
    df = pd.read_csv(FM13_WS / "data.csv")
    assert ref.reference(df) == 6.39

    naive_round = sum(round(float(v), 2) for v in df["amount"])
    sum_first = round(float(df["amount"].sum()), 2)
    assert naive_round != 6.39 and sum_first != 6.39
    # 用户要求的判别值：错误 Python round ≈ 6.36；错误先求和后舍入 = 6.38
    assert abs(round(naive_round, 2) - 6.36) < 1e-9
    assert round(sum_first, 2) == 6.38