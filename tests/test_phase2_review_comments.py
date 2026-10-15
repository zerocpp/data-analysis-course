"""v10.1.14 两阶段导出验收（§19）—— review-comments.json 唯一审稿记录协议。

覆盖：

- P1 正常导出 / 缺 Task / 缺 Agent；
- P2 正常导出（含 ZIP 重校验）；
- P2 缺实验报告 / 缺 review-comments.json；
- review-comments 顶层非 object、缺分配 Task、多未知 Task、原始 JSON 重复 key、
  key 与内部 task_id 不一致、score 非法（0/6/3.5/"4"/true）、comments 非法、
  未定义字段；
- 10 Task 全合法端到端：官方 P2 初始包 -> 学生修改 -> export PASS -> ZIP 重校验 PASS。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bigdata_v10110_helpers import make_docx_bytes, make_workspace
from codesetarena.submission import contract as C
from codesetarena.submission.local import SubmissionBuildError, build_submission
from codesetarena.submission.archive import validate_submission_archive
from codesetarena.submission.review_comments import validate_review_comments

TEN_ASSIGNED = [f"AT{i:03d}" for i in (28, 35, 66, 86, 101, 115, 130, 165, 182, 192)]


def valid_review(assigned):
    return {at: {"task_id": at, "score": 4, "comments": f"{at} 考察边界处理，需改进。"}
            for at in assigned}


def make_p2(root: Path, *, assigned=None, review=None, report=True) -> Path:
    """P2 workspace（helper 六件套 + 新协议材料；可注入自定义 review-comments）。"""

    ws = make_workspace(root, student_id="20260001", phase="P2")
    if assigned is not None:
        (ws / "assignment.json").write_text(
            json.dumps({"assignment_id": "AR000001_R000023", "phase": "phase2",
                        "anonymous_tasks": assigned}, ensure_ascii=False) + "\n",
            encoding="utf-8")
    if review is not None:
        (ws / "review-comments.json").write_text(review, encoding="utf-8")
    if report is False:
        (ws / "实验报告.docx").unlink()
    return ws


def build(ws: Path, tmp_path: Path) -> dict:
    out = tmp_path / "BigData_P2_20260001.zip"
    return build_submission(ws, phase="phase2", student_id="20260001", output_path=out)


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_p1_valid_package_passes(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P1")
    record = build_submission(ws, phase="phase1", student_id="20260001")
    assert record["exit_code"] == 0, record
    assert record["package_path"].endswith("BigData_P1_20260001.zip")


def test_p1_missing_task_fails(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P1")
    (ws / "task.json").unlink()
    with pytest.raises(SubmissionBuildError):
        build_submission(ws, phase="phase1", student_id="20260001")


def test_p1_missing_agent_fails(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P1")
    (ws / "agent.py").unlink()
    with pytest.raises(SubmissionBuildError):
        build_submission(ws, phase="phase1", student_id="20260001")


# ---------------------------------------------------------------------------
# Phase 2：实验报告 / review-comments 存在性
# ---------------------------------------------------------------------------


def test_p2_valid_package_passes_and_revalidates(tmp_path):
    ws = make_p2(tmp_path / "ws")
    record = build(ws, tmp_path)
    assert record["exit_code"] == 0, record["local_validation"]
    result = validate_submission_archive(record["package_path"], phase="phase2")
    assert result["status"] == "PASS", result


def test_p2_missing_experiment_report_fails(tmp_path):
    ws = make_p2(tmp_path / "ws", report=False)
    record = build(ws, tmp_path)
    assert record["exit_code"] == 1
    assert record["local_validation"]["error_code"] == C.MISSING_EXPERIMENT_REPORT


def test_p2_empty_experiment_report_fails(tmp_path):
    ws = make_p2(tmp_path / "ws")
    (ws / "实验报告.docx").write_bytes(b"")
    record = build(ws, tmp_path)
    assert record["local_validation"]["error_code"] == C.INVALID_EXPERIMENT_REPORT


def test_p2_corrupt_experiment_report_fails(tmp_path):
    ws = make_p2(tmp_path / "ws")
    (ws / "实验报告.docx").write_bytes(b"not a docx at all")
    record = build(ws, tmp_path)
    assert record["local_validation"]["error_code"] == C.INVALID_EXPERIMENT_REPORT


def test_p2_missing_review_comments_fails(tmp_path):
    ws = make_p2(tmp_path / "ws")
    (ws / "review-comments.json").unlink()
    record = build(ws, tmp_path)
    assert record["exit_code"] == 1
    assert record["local_validation"]["error_code"] == C.MISSING_REVIEW_COMMENTS


def test_p2_missing_assignment_fails(tmp_path):
    ws = make_p2(tmp_path / "ws")
    (ws / "assignment.json").unlink()
    record = build(ws, tmp_path)
    assert record["local_validation"]["error_code"] == C.MISSING_ASSIGNMENT


# ---------------------------------------------------------------------------
# review-comments.json 结构校验（§7–§13）
# ---------------------------------------------------------------------------


def _review_result(tmp_path, raw, assigned=None):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / C.PHASE2_REVIEW_COMMENTS).write_text(raw, encoding="utf-8")
    return validate_review_comments(root, assigned if assigned is not None else ["AT028"])


def test_top_level_not_object(tmp_path):
    for raw in ("[{}]", "null", '"hello"', "42"):
        result = _review_result(tmp_path, raw)
        assert result["status"] == "FAIL", raw
        assert result["error_code"] == C.REVIEW_COMMENTS_NOT_OBJECT, raw


def test_malformed_json_fails(tmp_path):
    result = _review_result(tmp_path, "{not json")
    assert result["error_code"] == C.MALFORMED_JSON


def test_missing_one_assigned_task(tmp_path):
    raw = json.dumps(valid_review(["AT028", "AT035"]), ensure_ascii=False)
    result = _review_result(tmp_path, raw, assigned=["AT028", "AT035", "AT066"])
    assert result["status"] == "FAIL"
    assert result["error_code"] == C.MISSING_REVIEW_TASK
    assert any(e["code"] == C.MISSING_REVIEW_TASK and "AT066" in e["detail"]
               for e in result["errors"])


def test_unknown_extra_task(tmp_path):
    raw = json.dumps(valid_review(["AT028", "AT777"]), ensure_ascii=False)
    result = _review_result(tmp_path, raw, assigned=["AT028"])
    assert result["error_code"] == C.UNKNOWN_REVIEW_TASK


def test_duplicate_raw_json_key(tmp_path):
    raw = (
        '{"AT028": {"task_id": "AT028", "score": 5, "comments": "AAA"},\n'
        ' "AT028": {"task_id": "AT028", "score": 3, "comments": "BBB"}}'
    )
    result = _review_result(tmp_path, raw)
    assert result["status"] == "FAIL"
    assert result["error_code"] == C.DUPLICATE_REVIEW_TASK_ID


def test_key_inner_task_id_mismatch(tmp_path):
    raw = json.dumps({"AT028": {"task_id": "AT035", "score": 4, "comments": "x"}},
                     ensure_ascii=False)
    result = _review_result(tmp_path, raw)
    assert result["error_code"] == C.REVIEW_TASK_ID_MISMATCH


@pytest.mark.parametrize("score", [0, 6, 3.5, "4", None, True, False])
def test_invalid_scores(tmp_path, score):
    entry = {"task_id": "AT028", "score": score, "comments": "ok"}
    result = _review_result(tmp_path, json.dumps({"AT028": entry}, ensure_ascii=False))
    assert result["status"] == "FAIL", score
    assert result["error_code"] == C.INVALID_REVIEW_SCORE, score


@pytest.mark.parametrize("comments", [None, "", "   ", 123])
def test_invalid_comments(tmp_path, comments):
    entry = {"task_id": "AT028", "score": 4, "comments": comments}
    result = _review_result(tmp_path, json.dumps({"AT028": entry}, ensure_ascii=False))
    assert result["status"] == "FAIL", comments
    assert result["error_code"] == C.INVALID_REVIEW_COMMENTS, comments


def test_missing_and_unknown_fields(tmp_path):
    result = _review_result(tmp_path, json.dumps({"AT028": {"task_id": "AT028", "score": 4}}))
    codes = [e["code"] for e in result["errors"]]
    assert C.REVIEW_FIELD_MISSING in codes
    entry = {"task_id": "AT028", "score": 4, "comments": "ok", "extra": 1}
    result = _review_result(tmp_path, json.dumps({"AT028": entry}))
    assert C.REVIEW_FIELD_UNKNOWN in [e["code"] for e in result["errors"]]


def test_entry_not_object(tmp_path):
    result = _review_result(tmp_path, json.dumps({"AT028": ["not", "a", "dict"]}))
    assert result["error_code"] == C.INVALID_REVIEW_STRUCTURE


def test_boundary_scores_valid(tmp_path):
    entries = {f"AT{i:03d}": {"task_id": f"AT{i:03d}", "score": 1 + i % 5,
                              "comments": f"评审 {i}"}
               for i in range(1, 6)}
    result = _review_result(tmp_path, json.dumps(entries, ensure_ascii=False),
                            assigned=list(entries))
    assert result["status"] == "PASS", result


# ---------------------------------------------------------------------------
# 10 Task 全合法 + 端到端（官方 P2 初始包 -> 学生填写 -> export -> ZIP 重校验）
# ---------------------------------------------------------------------------


def test_ten_tasks_fully_valid(tmp_path):
    ws = make_p2(tmp_path / "ws", assigned=TEN_ASSIGNED,
                 review=json.dumps(valid_review(TEN_ASSIGNED), ensure_ascii=False))
    record = build(ws, tmp_path)
    assert record["exit_code"] == 0, record["local_validation"]
    result = validate_submission_archive(record["package_path"], phase="phase2")
    assert result["status"] == "PASS", result


def test_phase2_official_package_end_to_end(tmp_path):
    """官方 P2 初始包 -> 学生改 Task/Agent + 填实验报告 + 填合法 review-comments
    -> export PASS -> 产生最终 P2 ZIP -> 重新 validate ZIP -> PASS。"""

    from bigdata_v10110_helpers import cli

    ws = make_p2(tmp_path / "ws", assigned=TEN_ASSIGNED,
                 review=json.dumps(valid_review(TEN_ASSIGNED), ensure_ascii=False))

    # 学生修改 Task v2 / Agent v2（原地修改，无 v2 文件名）
    task = json.loads((ws / "task.json").read_text(encoding="utf-8"))
    task["description"] = "v2：加入 region 过滤语义"
    (ws / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    (ws / "agent.py").write_text(
        '"""Agent v2"""\nimport json\n\n\ndef run(task, df, llm):\n'
        '    total = float(df["amount"].sum()) if "amount" in df.columns else 0\n'
        '    return json.dumps({"answer": round(total, 2), "version": 2})\n',
        encoding="utf-8")
    # 学生基于模板填写实验报告（另存为正式名）
    (ws / "实验报告.docx").write_bytes(make_docx_bytes("Task v2/Agent v2 设计与失败改进"))

    code, out = cli(["submission", "build", "--input", str(ws),
                     "--student-id", "20260001"], cwd=tmp_path)
    assert code == 0, out
    assert "phase=phase2" in out, out
    final_zip = ws / "submission" / "BigData_P2_20260001.zip"
    assert final_zip.is_file()

    code, out = cli(["submission", "validate", "--input", str(final_zip)], cwd=tmp_path)
    assert code == 0, out
    assert "status=PASS" in out, out
