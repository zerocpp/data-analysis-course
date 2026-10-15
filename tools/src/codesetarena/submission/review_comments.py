"""review-comments.json —— 唯一审稿记录协议校验（v10.1.14 §6–§13）。

Phase 2 学生成果冻结为：Task v2 + Agent v2 + 实验报告.docx + review-comments.json。
本模块是 review-comments.json 的**唯一**校验实现（Student Build / Student Validate /
Server Receive 全部复用；禁止第二套实现）。

协议（顶层必须是字典，一个匿名 Task ID 恰好一个条目）::

    {
      "<TASK_ID>": {
        "task_id": "<TASK_ID>",
        "score": 1~5,
        "comments": "非空评价"
      }
    }

红线：

- 顶层必须是 JSON 字典（REVIEW_COMMENTS_NOT_OBJECT）；
- **重复 key 必须在原始 JSON 解析层检出**（普通 json.loads 会静默保留最后一个，
  本模块用 ``object_pairs_hook`` 检出 -> DUPLICATE_REVIEW_TASK_ID）；
- key 集合必须与 Assignment 实际分配完全一致（MISSING_REVIEW_TASK /
  UNKNOWN_REVIEW_TASK）；数量不写死，以分配记录为准；
- key 与内部 task_id 必须一致（REVIEW_TASK_ID_MISMATCH）；
- 条目字段恰好为 {task_id, score, comments}（REVIEW_FIELD_MISSING /
  REVIEW_FIELD_UNKNOWN）；
- score 必须 ``type() is int`` 且 1~5（bool 是 int 子类，必须用 type 精确检查 ->
  INVALID_REVIEW_SCORE）；
- comments 必须为非空字符串（INVALID_REVIEW_COMMENTS）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import contract as C

REVIEW_ENTRY_FIELDS = ("task_id", "score", "comments")


class _DuplicateKeyError(ValueError):
    """object_pairs_hook 检出重复 key（原始 JSON 层）。"""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _no_duplicate_pairs(pairs: list) -> dict:
    seen: set = set()
    for key, _value in pairs:
        if key in seen:
            raise _DuplicateKeyError(str(key))
        seen.add(key)
    return dict(pairs)


def _fail(code: str, location: str, detail: str) -> dict[str, Any]:
    return {"code": code, "location": location, "detail": detail}


def validate_review_comments(root: str | Path, assigned_task_ids: list) -> dict[str, Any]:
    """校验 review-comments.json（只读、不修复；与服务器同一实现）。

    ``assigned_task_ids`` 来自包内 assignment.json（anonymous_tasks），是唯一
    分配权威；本函数不硬编码任务数量。
    返回统一 Validation Result（作为 phase2_extension 层的一部分）。
    """

    root = Path(root)
    errors: list[dict[str, Any]] = []
    location = C.PHASE2_REVIEW_COMMENTS

    def result(status: str, checks: dict | None = None) -> dict[str, Any]:
        return {
            "schema_version": C.SCHEMA_VERSION,
            "status": status,
            "validation_layer": C.LAYER_P2_EXTENSION,
            "error_code": errors[0]["code"] if errors else None,
            "error_count": len(errors),
            "errors": errors,
            "warnings": [],
            "checks": checks or {},
        }

    path = root / location
    if not path.is_file():
        errors.append(_fail(C.MISSING_REVIEW_COMMENTS, location,
                            "Phase 2 缺少 review-comments.json（唯一审稿记录）"))
        return result("FAIL")

    try:
        data = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=_no_duplicate_pairs)
    except _DuplicateKeyError as exc:
        errors.append(_fail(C.DUPLICATE_REVIEW_TASK_ID, location,
                            f"原始 JSON 存在重复 key：{exc.key!r} 出现了两次以上"
                            "（普通解析会静默保留最后一个，必须 FAIL）"))
        return result("FAIL")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        errors.append(_fail(C.MALFORMED_JSON, location,
                            f"review-comments.json 不是合法 JSON: {exc}"))
        return result("FAIL")

    if not isinstance(data, dict):
        errors.append(_fail(C.REVIEW_COMMENTS_NOT_OBJECT, location,
                            f"review-comments.json 顶层必须是 JSON 字典，"
                            f"当前为 {type(data).__name__}"))
        return result("FAIL")

    keys = set(data)
    assigned = [str(t).strip() for t in (assigned_task_ids or [])]
    assigned_set = set(assigned)
    for at in sorted(assigned_set - keys):
        errors.append(_fail(C.MISSING_REVIEW_TASK, location,
                            f"分配的匿名 Task {at} 缺少 review 条目"))
    for at in sorted(keys - assigned_set):
        errors.append(_fail(C.UNKNOWN_REVIEW_TASK, location,
                            f"review 条目 {at} 不在 Assignment 分配中"))

    for key in sorted(data):
        entry = data[key]
        where = f"{location}:{key}"
        if not isinstance(entry, dict):
            errors.append(_fail(C.INVALID_REVIEW_STRUCTURE, where,
                                f"review 条目必须是 JSON 对象，当前为 {type(entry).__name__}"))
            continue
        present = set(entry)
        required = set(REVIEW_ENTRY_FIELDS)
        for field in sorted(required - present):
            errors.append(_fail(C.REVIEW_FIELD_MISSING, where,
                                f"review 缺少字段 {field}"))
        for field in sorted(present - required):
            errors.append(_fail(C.REVIEW_FIELD_UNKNOWN, where,
                                f"review 含未定义字段 {field}（协议只允许 "
                                "task_id/score/comments）"))

        if "task_id" in entry:
            inner = entry["task_id"]
            if type(inner) is not str or inner.strip() != str(key):
                errors.append(_fail(C.REVIEW_TASK_ID_MISMATCH, where,
                                    f"顶层 key={key!r} 与内部 task_id={inner!r} 不一致"))

        if "score" in entry:
            score = entry["score"]
            if type(score) is not int or not 1 <= score <= 5:
                errors.append(_fail(C.INVALID_REVIEW_SCORE, where,
                                    f"score 必须是 1~5 的整数，当前为 {score!r}"
                                    "（bool/float/字符串一律拒绝）"))

        if "comments" in entry:
            comments = entry["comments"]
            if type(comments) is not str or not comments.strip():
                errors.append(_fail(C.INVALID_REVIEW_COMMENTS, where,
                                    "comments 必须是非空字符串（空白字符串视为未填写）"))

    return result("FAIL" if errors else "PASS",
                  checks={"assigned_task_count": len(assigned_set),
                          "review_entry_count": len(data)})
