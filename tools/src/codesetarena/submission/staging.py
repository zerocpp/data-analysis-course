"""Staging —— BUILD 的第一步：Workspace -> 最终 Submission Staging Tree（v2 §4/§39）。

``prepare_submission_staging`` 负责：

- workspace 网关校验（与 run/export 同一 ``validate_project``，尽早止损）；
- 固化 task_id（已有保留 / 缺失生成 ``T_<uuid4hex>``，Export 语义）；
- 重新生成所有 locally / generated metadata（忽略学生手填值，绝不读）；
- 组装 Base Contract 树（Student-authored 内容逐字节保留；.env / runs/ 永不进入）。
  Phase 2 额外复制：assignment.json / review-comments.json / 实验报告.docx
  （v10.1.14：旧 reviews/ report/ final_report.md phase2_metadata.json 已删除，
  实验报告模板.docx 是课程材料，不进入提交包）。

返回 ``(tree, task_id, course)``。**不校验、不打包**：

- 校验发生在 ``validate_submission_tree()``（build 与 validate 共用同一份）；
- 打包发生在 ``create_submission_archive()``（build 的 pack 一步）。

硬规则：学生实际校验的 Staging Tree == 实际提交的 Archive（先 validate 同一个
tree，再原样打包；绝无"先校验 A 再打包装 B"的 TOCTOU）。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from ..main.build import sha256_file
from ..main.export import ensure_task_identity
from ..main.project import load_project
from ..main.validation import validate_project
from . import contract as C


class StagingError(RuntimeError):
    """Staging 失败（workspace 网关校验未通过等；CLI 映射为退出码 2）。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _copy_bytes(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _workspace_data_files(workspace: Path) -> list[Path]:
    """workspace 根目录的数据文件（当前课程契约为 data.csv；按名排序）。"""

    return sorted(p for p in workspace.iterdir() if p.is_file() and p.suffix in (".csv", ".tsv"))


def _is_secret_name(name: str) -> bool:
    """Package Secret Guard（v10.1.10 §29）的 Build 端匹配（大小写不敏感）。"""

    import fnmatch

    lowered = name.lower()
    if lowered == C.ENV_EXAMPLE:
        return False
    return any(fnmatch.fnmatch(lowered, pat.lower()) for pat in C.SECRET_GUARD_PATTERNS)


def prepare_submission_staging(
    workspace: str | Path,
    *,
    phase: str,
    student_id: str,
    course: str | None = None,
) -> tuple[Path, str, str]:
    """Workspace -> 最终 Submission Staging Tree。

    返回 ``(tree, task_id, course_code)``。tree 在临时目录内，调用方负责在
    打包 / 校验完成后清理（``TemporaryDirectory`` 或 ``rmtree``）。
    任何校验失败抛 ``StagingError``（不产生、不返回任何部分 Staging Tree）。
    """

    workspace = Path(workspace)
    if phase not in C.VALID_PHASES:
        raise StagingError(f"非法阶段 {phase!r}（只能取 phase1/phase2）")
    if not C.STUDENT_ID_PATTERN.match(student_id):
        raise StagingError(f"非法 student_id {student_id!r}")

    project = load_project(workspace)
    errors = validate_project(project)  # 本地网关（advisory 但尽早止损）
    if errors:
        raise StagingError("workspace 校验失败: " + "; ".join(errors))

    ensure_task_identity(project.file("task.json"))  # task_id：已有保留，缺失生成
    task = json.loads(project.file("task.json").read_text(encoding="utf-8"))
    task_id = str(task.get("task_id") or "").strip()

    staging = Path(tempfile.mkdtemp(prefix="csa_staging_"))
    tree = staging / "submission"
    try:
        (tree / C.TASK_DIR).mkdir(parents=True)
        (tree / C.AGENT_DIR).mkdir(parents=True)
        (tree / C.TASK_DATA_DIR).mkdir(parents=True)

        # ---- Student-authored：逐字节保留 ----
        _copy_bytes(project.file("task.json"), tree / C.TASK_JSON)
        for data_file in _workspace_data_files(workspace):
            if _is_secret_name(data_file.name):
                continue  # Package Secret Guard（§29）：Build 端显式排除
            _copy_bytes(data_file, tree / C.TASK_DATA_DIR / data_file.name)
        _copy_bytes(project.file("agent.py"), tree / C.AGENT_ENTRYPOINT)
        # 注意：.env / .env.example 属于开发环境（连接配置 / Secret），
        # 永不进入 Submission Artifact（Configuration / Policy Separation §17）。
        if project.file("reference.py").is_file():
            _copy_bytes(project.file("reference.py"), tree / "reference.py")
        # Phase 2 专属交付物（v10.1.14 两阶段协议）：分配权威 + 唯一审稿记录 +
        # 实验报告。旧 reviews/ report/ final_report.md phase2_metadata.json 已删除；
        # 实验报告模板.docx 是课程材料，永不进入提交包。
        for rel in (C.PHASE2_ASSIGNMENT, C.PHASE2_REVIEW_COMMENTS,
                    C.PHASE2_EXPERIMENT_REPORT):
            src = workspace / rel
            if src.is_file():
                _copy_bytes(src, tree / rel)

        # ---- Generated metadata：recompute + overwrite（绝不读学生手填值）----
        files_sha: dict[str, str] = {}
        for rel in sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()):
            files_sha[rel] = sha256_file(tree / rel)

        resolved_course = course or C.DEFAULT_COURSE_CODE
        manifest = {
            "schema_version": C.SCHEMA_VERSION,
            "course": resolved_course,
            "submission_phase": phase,
            "student_id": student_id,
            "task_id": task_id,
            "built_at": _now_iso(),
            "build_status": "passed",  # 由本次构建重新推导
            "files": files_sha,
        }
        (tree / C.MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tree / C.AGENT_MANIFEST).write_text(
            json.dumps(
                {
                    "schema_version": C.SCHEMA_VERSION,
                    "student_id": student_id,
                    "task_id": task_id,
                    "entrypoint": "agent.py",
                    "built_at": _now_iso(),
                    "files": {"agent/agent.py": files_sha.get("agent/agent.py", "")},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return tree, task_id, resolved_course