"""Built Package Validation + TA Receive —— v10.1.16 两阶段总包（改造 B/§24/§28）。

与 Workspace Validation（``main.workspace_validation``）明确分层；两者共用
底层原语（safe extract / docx / review-comments / metadata schema），绝不实现
第二套并行规则。

Built Package Validation（``validate_built_package``）::

    bigdata_p1_{学号}.tar.gz -> 顶层恰好 Tasks_/Agent_/metadata.json
    bigdata_p2_{学号}.tar.gz -> 顶层恰好 5 个文件（+ review-comments + 实验报告）
    - metadata.json: workspace/student_id/task_id/agent_id(/review-comments_id)
    - Tasks_{学号}.tar.gz 内恰好 task.json + data.csv + reference.py；
      task.json 无 task_id / 身份字段；answer == reference(data.csv) 实算结果
    - Agent_{学号}.tar.gz 内恰好 agent.py

TA Receive（``receive_built_package``，§24/§25）::

    安全解压 -> validate -> 落入
    <out>/submissions/phase1/<sid>/{Tasks,Agent,metadata.json}（P2 + 2 文件）
    原始包永久留存 <out>/received/；同 sha 幂等 SKIP；同 名不同 sha -> FAIL。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..main.workspace_layout import EXPERIMENT_REPORT_FILENAME
from ..main.workspace_contract import (
    TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE,
)
from ..main.workspace_contract import (
    validate_phase_metadata,
    validate_student_task_json,
)
from ..main.runner import _answers_equal, _run_reference
from .archive import validate_archive_envelope
from .review_comments import validate_review_comments
from .safe_extract import SafeExtractError, safe_extract_archive

BUILT_PACKAGE_NAME = re.compile(
    r"^bigdata_p(?P<phase>[12])_(?P<student_id>[0-9A-Za-z_]{2,32})(?:_v\d+)?\.tar\.gz$"
)


class BuiltPackageError(RuntimeError):
    """总包校验/接收失败。"""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fail(errors: list, code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def _extract_inner_tar(outer_extract_root: Path, inner_name: str, target: Path) -> None:
    import tarfile

    inner = outer_extract_root / inner_name
    with tarfile.open(inner, "r:gz") as bundle:
        bundle.extractall(target, filter="data")


def _is_real_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
        return "[Content_Types].xml" in names and any(n.startswith("word/") for n in names)
    except Exception:  # noqa: BLE001
        return False


def validate_built_package(archive: str | Path) -> dict[str, Any]:
    """Built Package Validation 唯一入口；返回统一 ValidationResult。"""

    archive = Path(archive)
    errors: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "archive": archive.name,
        "status": "PASS",
        "validation_layer": "built_package",
        "error_code": None,
        "errors": errors,
        "metadata": None,
        "phase": None,
        "student_id": None,
    }

    match = BUILT_PACKAGE_NAME.match(archive.name)
    if match is None:
        _fail(errors, "INVALID_BUILT_PACKAGE_NAME",
              f"总包名 {archive.name!r} 不符合 bigdata_p1/p2_<学号>.tar.gz")
        result["status"] = "FAIL"
        result["error_code"] = "INVALID_BUILT_PACKAGE_NAME"
        return result
    phase = f"phase{match.group('phase')}"
    student_id = match.group("student_id")
    result["phase"] = phase
    result["student_id"] = student_id

    try:
        validate_archive_envelope(archive)
    except SafeExtractError as exc:
        _fail(errors, exc.code, exc.detail)
        result.update(status="FAIL", error_code=exc.code)
        return result
    except Exception as exc:  # noqa: BLE001 - 损坏包必须变成结构化 FAIL，不能抛异常
        import tarfile

        code = ("TAR_EXTRACT_ERROR"
                if isinstance(exc, tarfile.TarError) else "ARCHIVE_UNREADABLE")
        _fail(errors, code, f"{archive.name}: {type(exc).__name__}: {exc}")
        result.update(status="FAIL", error_code=code)
        return result

    expected_top = {
        f"Tasks_{student_id}.tar.gz", f"Agent_{student_id}.tar.gz", "metadata.json"
    }
    if phase == "phase2":
        expected_top |= {"review-comments.json", EXPERIMENT_REPORT_FILENAME}

    with tempfile.TemporaryDirectory(prefix="csa_built_pkg_") as tmp:
        extract_root = Path(tmp) / "outer"
        try:
            safe_extract_archive(archive, extract_root)
        except SafeExtractError as exc:
            _fail(errors, exc.code, exc.detail)
            result.update(status="FAIL", error_code=exc.code)
            return result

        top_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
        top_name = f"bigdata_p{match.group('phase')}_{student_id}"
        if len(top_dirs) != 1 or top_dirs[0].name != top_name:
            _fail(errors, "BAD_TOP_LEVEL_DIR",
                  f"解压后顶层目录必须是 {top_name}/，实际 "
                  f"{[p.name for p in extract_root.iterdir()]}")
        else:
            top = top_dirs[0]
            actual_top = sorted(p.name for p in top.iterdir())
            if actual_top != sorted(expected_top):
                _fail(errors, "BAD_TOP_LEVEL_FILES",
                      f"一级文件必须恰好 {sorted(expected_top)}，实际 {actual_top}")
            else:
                package_metadata = _validate_metadata(
                    top / "metadata.json", phase, student_id, errors)
                if package_metadata is not None:
                    result["metadata"] = package_metadata
                tasks_dir = Path(tmp) / "tasks"
                agent_dir = Path(tmp) / "agent"
                _extract_inner_tar(top, f"Tasks_{student_id}.tar.gz", tasks_dir)
                _extract_inner_tar(top, f"Agent_{student_id}.tar.gz", agent_dir)
                _validate_tasks_inner(tasks_dir / f"Tasks_{student_id}", errors)
                _validate_agent_inner(agent_dir / f"Agent_{student_id}", errors)
                if phase == "phase2":
                    review = top / "review-comments.json"
                    if review.is_file():
                        try:
                            review_data = json.loads(
                                review.read_text(encoding="utf-8"))
                            keys = list(review_data.keys()) if isinstance(review_data, dict) else []
                        except Exception as exc:  # noqa: BLE001
                            _fail(errors, "MALFORMED_JSON",
                                  f"review-comments.json 无法解析: {exc}")
                            keys = None
                        if keys is not None:
                            # assignment.json 不随包分发（§22 排除清单），覆盖校验
                            # 在 Workspace Build 阶段完成；这里只做结构/字段级校验。
                            check = validate_review_comments(top, keys or ["__none__"])
                            for error in check.get("errors", []):
                                code = error.get("code", "REVIEW_ERROR")
                                if code in ("MISSING_REVIEW_TASK",):
                                    continue  # assigned 集合不在包内，跳过覆盖类错误
                                _fail(errors, code,
                                      f"{error.get('location')}: {error.get('detail')}")
                    report = top / EXPERIMENT_REPORT_FILENAME
                    if not report.is_file() or report.stat().st_size == 0 \
                            or not _is_real_docx(report):
                        _fail(errors, "INVALID_EXPERIMENT_REPORT",
                              f"{EXPERIMENT_REPORT_FILENAME} 缺失 / 0 字节 / 不是真实 docx")

    if errors:
        result.update(status="FAIL", error_code=errors[0]["code"])
    return result


def _validate_metadata(path: Path, phase: str, student_id: str,
                       errors: list) -> dict | None:
    if not path.is_file():
        _fail(errors, "MISSING_METADATA", "顶层缺少 metadata.json")
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(errors, "MALFORMED_JSON", f"metadata.json 无法解析: {exc}")
        return None
    errors.extend(
        {"code": "BAD_METADATA", "detail": message}
        for message in validate_phase_metadata(metadata, phase)
    )
    if not errors and str(metadata["student_id"]) != student_id:
        _fail(errors, "STUDENT_ID_MISMATCH",
              f"metadata.student_id={metadata['student_id']!r} 与包名学号 {student_id!r} 不一致")
    return None if errors else metadata


def _validate_tasks_inner(tasks_top: Path, errors: list) -> None:
    if not tasks_top.is_dir():
        _fail(errors, "BAD_TASKS_PACKAGE", "Tasks 包缺少 Tasks_<学号>/ 目录")
        return

    # ── 1. Required file names（严格文件名契约，§4）──
    actual = sorted(p.name for p in tasks_top.iterdir())
    if actual != ["data.csv", "reference.py", "task.json"]:
        _fail(errors, "BAD_TASKS_PACKAGE",
              f"Tasks 包内必须恰好 task.json/data.csv/reference.py，实际 {actual}")
        return

    # ── 2. task.json JSON 结构 + 禁止字段（§6/§7）──
    try:
        task = json.loads((tasks_top / "task.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(errors, "INVALID_TASK_JSON", f"task.json 无法解析: {exc}")
        return
    for message in validate_student_task_json(task):
        code = TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE \
            if "hypothesized_failure_mode" in message else "BAD_TASK_JSON"
        _fail(errors, code, message)

    # ── 3. CSV UTF-8 编码（§10/§11）──
    import pandas as pd

    try:
        df = pd.read_csv(tasks_top / "data.csv", encoding="utf-8")
    except UnicodeDecodeError as exc:
        _fail(errors, "INVALID_CSV_ENCODING",
              f"data.csv 不是合法 UTF-8 编码: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        _fail(errors, "INVALID_CSV", f"data.csv 解析失败: {exc}")
        return

    # ── 4. 数据规模（§13/§14）──
    from ..main.validation import _data_limits

    max_bytes, max_rows, max_cols = _data_limits()
    size = (tasks_top / "data.csv").stat().st_size
    if size > max_bytes:
        _fail(errors, "CSV_FILE_TOO_LARGE",
              f"data.csv 大小 {size} B 超过上限 {max_bytes} B")
    rows, cols = df.shape
    if rows >= max_rows:
        _fail(errors, "CSV_TOO_MANY_ROWS",
              f"data.csv 行数 {rows} 达到/超过上限 {max_rows}")
    if cols > max_cols:
        _fail(errors, "CSV_TOO_MANY_COLUMNS",
              f"data.csv 列数 {cols} 超过上限 {max_cols}")

    # ── 5. reference.py Guardrail（语法/签名/禁止 import/禁止 I/O）──
    from ..main.validation import check_reference_source, _reference_limits

    ref_path = tasks_top / "reference.py"
    try:
        source = ref_path.read_text(encoding="utf-8")
        _, max_lines = _reference_limits()
        check_reference_source(source, max_lines)
        from ..main.project import import_module_from_path
        import uuid as _uuid

        ref_mod = import_module_from_path(ref_path, f"built_ref_{_uuid.uuid4().hex[:8]}")

        ref_fn = getattr(ref_mod, "reference", None)
        if not callable(ref_fn):
            _fail(errors, "REFERENCE_ERROR", "reference.py 缺少可调用的 reference(df)")
            return
        from ..main.validation import _param_names

        if _param_names(ref_fn) != ["df"]:
            _fail(errors, "REFERENCE_ERROR",
                  f"reference.py 签名必须恰好是 (df)，当前为 {_param_names(ref_fn)}")
            return
    except Exception as exc:  # noqa: BLE001
        _fail(errors, "REFERENCE_ERROR", f"reference.py 校验失败: {exc}")
        return

    # ── 6. Answer consistency ──
    try:
        computed = _run_reference(ref_path, df.copy(deep=True))
    except Exception as exc:  # noqa: BLE001
        _fail(errors, "REFERENCE_ERROR",
              f"reference.py 执行失败: {type(exc).__name__}: {exc}")
        return
    if not _answers_equal(computed, task["answer"]):
        _fail(errors, "ANSWER_MISMATCH",
              f"task.json.answer={task['answer']!r} != reference 实算 {computed!r}")


def _validate_agent_inner(agent_top: Path, errors: list) -> None:
    if not agent_top.is_dir():
        _fail(errors, "BAD_AGENT_PACKAGE", "Agent 包缺少 Agent_<学号>/ 目录")
        return
    actual = sorted(p.name for p in agent_top.iterdir())
    if actual != ["agent.py"]:
        _fail(errors, "BAD_AGENT_PACKAGE",
              f"Agent 包内必须恰好 agent.py，实际 {actual}")


# ---------------------------------------------------------------------------
# TA Receive（§24/§25）
# ---------------------------------------------------------------------------


def receive_built_package(archive: str | Path, out_root: str | Path) -> dict[str, Any]:
    """助教端接收：安全解压 -> validate -> 映射进 submissions/phase<N>/<学号>/。"""

    archive = Path(archive)
    out_root = Path(out_root)
    result = validate_built_package(archive)
    record: dict[str, Any] = {
        "archive": archive.name,
        "status": result["status"],
        "error_code": result["error_code"],
        "phase": result["phase"],
        "student_id": result["student_id"],
        "errors": result["errors"],
    }
    if result["status"] != "PASS":
        record["exit_code"] = 1
        return record

    phase, sid = result["phase"], result["student_id"]
    sha = _sha256_file(archive)
    received = out_root / "received" / archive.name
    stage_dir = out_root / "submissions" / phase / sid
    marker = out_root / "submissions" / ".received_sha" / f"{phase}_{sid}"

    if stage_dir.exists():
        known = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        if known == sha:
            record.update(status="SKIPPED_IDEMPOTENT", exit_code=0,
                          target=str(stage_dir))
            return record
        record.update(
            status="FAIL", exit_code=1, error_code="DUPLICATE_ACTIVE_SUBMISSION",
            errors=[{"code": "DUPLICATE_ACTIVE_SUBMISSION",
                     "detail": f"{stage_dir} 已存在且内容不同（sha {known[:12]}… != {sha[:12]}…）；"
                               "换档请走显式 supersede 流程"}],
        )
        return record

    expected_top = expected_top_level_files(phase, sid)
    with tempfile.TemporaryDirectory(prefix="csa_receive_") as tmp:
        extract_root = Path(tmp) / "outer"
        safe_extract_archive(archive, extract_root)
        top = extract_root / f"bigdata_p{1 if phase == 'phase1' else 2}_{sid}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        for name in expected_top:
            shutil.copy2(top / name, stage_dir / name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(sha, encoding="utf-8")

    received.parent.mkdir(parents=True, exist_ok=True)
    if not received.exists():
        shutil.copy2(archive, received)
    record.update(
        status="ACCEPTED", exit_code=0, target=str(stage_dir), received=str(received),
        files=sorted(expected_top),
    )
    return record


def expected_top_level_files(phase: str, student_id: str) -> tuple[str, ...]:
    from ..main.build_package import expected_top_level_files as _impl

    return _impl(phase, student_id)
