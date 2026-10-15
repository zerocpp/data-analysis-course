"""INGEST —— 校验通过的 Submission 正式拆分 / 规范化 / 入库（v2 §18–§36）。

INEST（§18/§34/§52）＝ 输入目录 batch-validate -> 只取 Single+Batch 都 PASS 的
Accepted Submission -> 拆成独立 canonical Task / Agent -> 私有 Registry ->
匿名映射 -> 匿名分发视图。

硬规则：

- 只处理 ``final_status == ACCEPTABLE`` 的 Submission（§19）；Single FAIL ->
  ``quarantine/single_validation_failed/``；Batch Conflict ->
  ``quarantine/cross_package_conflict/``；
- 原始 *.tar.gz 永久保留在 ``received/``（§21，审计原件，绝不修改）；
- Task / Agent 拆成独立 canonical object（§22），不维护
  ``students/<sid>/task`` 式身份主目录；
- canonical Task / Agent **不保存 student_id**（§23/§24）——身份只存在于
  Private Registry；
- canonical ID（T000001 / A000001 / S000001）与匿名 ID（AT001 / AA001）分层
  （§25/§29），可逆追溯（匿名 -> canonical -> submission -> student，§30）；
- 幂等（§35）：同一 ``archive_sha256`` 已在 registry -> SKIP，绝不重复生成
  Task / Agent；
- 原子性（§34）：Task / Agent / 视图全部落盘且 registry commit 成功才算完成；
  中途任何异常回滚本次新建内容，不产生半套 Registry。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..main.build import sha256_file
from . import contract as C
from .anonymization import (
    TASK_CANONICAL_STRIP_FIELDS,
    build_anonymous_agent_view,
    build_anonymous_task_view,
)
from .batch.validator import validate_archive, validate_cross_package
from .built_package import BUILT_PACKAGE_NAME, validate_built_package
from .registry import SubmissionRegistry, ensure_repo, now_iso
from .reporting import write_json
from .safe_extract import SafeExtractError, safe_extract_archive


class IngestError(RuntimeError):
    """Ingest 流程失败（任何一步异常；CLI 映射为退出码 2，不产生半套 Registry）。"""


def _canonical_task(
    submission_root: Path,
    *,
    canonical_task_id: str,
    phase: str,
    out_dir: Path,
) -> dict[str, Any]:
    """materialize_task（§34）：submission -> tasks/<T000001>/。"""

    out_dir.mkdir(parents=True, exist_ok=True)
    task_path = submission_root / C.TASK_JSON
    task = json.loads(task_path.read_text(encoding="utf-8"))
    source_task_id = str(task.get("task_id") or "")
    canonical = {k: v for k, v in task.items() if k not in TASK_CANONICAL_STRIP_FIELDS}
    canonical["canonical_task_id"] = canonical_task_id
    task_json_out = out_dir / C.TASK_JSON
    task_json_out.parent.mkdir(parents=True, exist_ok=True)
    task_json_out.write_text(
        json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    data_dir = submission_root / C.TASK_DATA_DIR
    if data_dir.is_dir():
        target_data = out_dir / C.TASK_DATA_DIR
        target_data.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in data_dir.iterdir() if p.is_file()):
            shutil.copy2(path, target_data / path.name)

    reference = submission_root / C.REFERENCE
    if reference.is_file():
        shutil.copy2(reference, out_dir / "reference.py")  # canonical-private gold

    files_sha = {
        str(p.relative_to(out_dir)): sha256_file(p)
        for p in sorted(x for x in out_dir.rglob("*") if x.is_file())
    }
    metadata = {
        "canonical_task_id": canonical_task_id,
        "source_task_id": source_task_id,
        "submission_phase": phase,
        "failure_mode": str(task.get("hypothesized_failure_mode") or ""),
        "ingested_at": now_iso(),
        "files": files_sha,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"canonical_task_id": canonical_task_id, "source_task_id": source_task_id,
            "failure_mode": metadata["failure_mode"]}


def _canonical_agent(
    submission_root: Path,
    *,
    canonical_agent_id: str,
    source_agent_id: str,
    out_dir: Path,
) -> dict[str, Any]:
    """materialize_agent（§34）：submission -> agents/<A000001>/。"""

    out_dir.mkdir(parents=True, exist_ok=True)
    agent_dir = submission_root / C.AGENT_DIR
    if agent_dir.is_dir():
        for path in sorted(p for p in agent_dir.iterdir() if p.is_file()):
            if path.name in ("agent_manifest.json", "metadata.json"):
                continue  # 身份 / 私有 manifest 不进 canonical
            shutil.copy2(path, out_dir / path.name)
    files_sha = {
        str(p.relative_to(out_dir)): sha256_file(p)
        for p in sorted(x for x in out_dir.rglob("*") if x.is_file())
    }
    metadata = {
        "canonical_agent_id": canonical_agent_id,
        "source_agent_id": source_agent_id,
        "entrypoint": "agent.py",
        "ingested_at": now_iso(),
        "files": files_sha,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"canonical_agent_id": canonical_agent_id, "source_agent_id": source_agent_id}


def _ingest_one(
    archive: Path,
    repo_root: Path,
    registry: SubmissionRegistry,
    created: list[Path],
    *,
    phase: str,
    student_id: str,
) -> dict[str, Any]:
    """把一个 Accepted Submission 拆成 Task + Agent + Registry + 匿名视图。"""

    archive_sha = sha256_file(archive)
    existing = registry.lookup_archive_sha256(archive_sha)
    if existing is not None:
        return {
            "archive": archive.name,
            "ingest_status": "SKIPPED",
            "reason": C.INGEST_ALREADY_KNOWN,
            "submission_id": existing.get("submission_id", ""),
            "task_id": existing.get("task_id", ""),
            "agent_id": existing.get("agent_id", ""),
        }

    received = repo_root / "received" / archive.name
    if not received.exists():
        shutil.copy2(archive, received)
        created.append(received)
    else:
        created.append(received)  # 防回滚删除已存在原件

    staging = Path(tempfile.mkdtemp(prefix="csa_ingest_extract_"))
    try:
        try:
            safe_extract_archive(archive, staging)
        except SafeExtractError as exc:
            raise IngestError(f"ingest 解压失败（已通过校验不应发生）: {exc}") from exc

        submission_id = registry.next_submission_id()
        task_id = registry.next_task_id()
        agent_id = registry.next_agent_id()

        task_dir = repo_root / "tasks" / task_id
        agent_dir = repo_root / "agents" / agent_id
        task_info = _canonical_task(
            staging, canonical_task_id=task_id, phase=phase, out_dir=task_dir,
        )
        agent_info = _canonical_agent(
            staging, canonical_agent_id=agent_id,
            source_agent_id=task_info["source_task_id"], out_dir=agent_dir,
        )
        created.extend([task_dir, agent_dir])

        anonymous_task_id = registry.next_anonymous_task_id()
        anonymous_agent_id = registry.next_anonymous_agent_id()
        task_view = repo_root / "distributions" / "tasks" / anonymous_task_id
        agent_view = repo_root / "distributions" / "agents" / anonymous_agent_id
        build_anonymous_task_view(task_dir, anonymous_task_id, task_view)
        build_anonymous_agent_view(agent_dir, anonymous_agent_id, agent_view)
        created.extend([task_view, agent_view])

        registry.add_submission({
            "schema_version": C.SCHEMA_VERSION,
            "submission_id": submission_id,
            "student_id": student_id,
            "original_archive": archive.name,
            "archive_sha256": archive_sha,
            "source_task_id": task_info["source_task_id"],
            "task_id": task_id,
            "source_agent_id": agent_info["source_agent_id"],
            "agent_id": agent_id,
            "phase": phase,
            "version": 1,
            "validation_status": "PASS",
            "ingest_status": "INGESTED",
            "ingested_at": now_iso(),
        })
        registry.add_mapping({"task_id": task_id, "anonymous_task_id": anonymous_task_id})
        registry.add_mapping({"agent_id": agent_id, "anonymous_agent_id": anonymous_agent_id})

        return {
            "archive": archive.name,
            "ingest_status": "INGESTED",
            "submission_id": submission_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "anonymous_task_id": anonymous_task_id,
            "anonymous_agent_id": anonymous_agent_id,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ingest_submissions(
    repo_root: str | Path,
    input_dir: str | Path,
) -> dict[str, Any]:
    """Ingest 顶层（§34/§52）：Accepted Submission -> canonical Task/Agent + Registry。

    1. 对输入目录执行 batch-validate（复用 validate_batch，不重写 Base 规则）；
    2. 只 ingest ``final_status == ACCEPTABLE``；其余进 quarantine；
    3. 每个 accepted：幂等 -> received/ -> 拆 Task/Agent -> 匿名映射/视图；
    4. 全部成功后 atomic commit registry；失败回滚本次创建的全部路径。
    """

    from .registry import now_iso

    repo_root = ensure_repo(repo_root)
    input_dir = Path(input_dir)
    registry = SubmissionRegistry(repo_root)

    # v10.1.16：目录里可混合两类包 —— Built 总包（bigdata_pN_<学号>.tar.gz）
    # 与 legacy 包。Built 包走 validate_built_package + 结构转换后复用同一
    # ingest 管线；legacy 包维持原 batch-validate 流程。两路结果合并成同一
    # package_results 语义，互不复制校验规则。
    all_archives = sorted(Path(input_dir).iterdir(), key=lambda p: p.name)
    built_archives = [a for a in all_archives
                      if a.is_file() and BUILT_PACKAGE_NAME.match(a.name)]
    legacy_archives = [a for a in all_archives
                       if a.is_file() and a.suffix in (".zip", ".gz", ".tar")
                       and a not in built_archives]

    legacy_results = [validate_archive(a) for a in legacy_archives]
    legacy_candidates = [r for r in legacy_results if r["single_package_status"] == "PASS"]
    cross = (validate_cross_package(legacy_candidates) if legacy_candidates
             else {"status": "PASS", "error_count": 0, "errors": [],
                   "error_code": None})
    conflicts_by_archive: dict[str, list] = {}
    for conflict in cross.get("errors", []):
        for archive in conflict.get("packages", []):
            conflicts_by_archive.setdefault(archive, []).append(conflict)

    built_results: dict[str, dict[str, Any]] = {}
    for archive in built_archives:
        result = validate_built_package(archive)
        built_results[archive.name] = result

    created: list[Path] = []
    entries: list[dict[str, Any]] = []
    package_results: list[dict[str, Any]] = []
    summary = {
        "discovered": len(legacy_results) + len(built_archives),
        "accepted": 0,
        "single_fail": 0,
        "batch_conflict": 0,
        "ingested": 0,
        "skipped": 0,
    }

    try:
        for package in sorted(legacy_results, key=lambda p: p["archive"]):
            archive_path = input_dir / package["archive"]
            package["batch_status"] = (
                "NOT_RUN" if package["single_package_status"] != "PASS" else
                ("FAIL" if package["archive"] in conflicts_by_archive else "PASS")
            )
            final = ("ACCEPTABLE" if package["batch_status"] == "PASS"
                     else "REJECT")
            if package["batch_status"] == "FAIL":
                package["error_code"] = conflicts_by_archive[package["archive"]][0]["code"]
            package["final_status"] = final
            package_results.append(package)
            if final != "ACCEPTABLE":
                if package.get("single_package_status") != "PASS":
                    summary["single_fail"] += 1
                    quarantine_dir = repo_root / "quarantine" / "single_validation_failed"
                else:
                    summary["batch_conflict"] += 1
                    quarantine_dir = repo_root / "quarantine" / "cross_package_conflict"
                target = quarantine_dir / package["archive"]
                if not target.exists():
                    shutil.copy2(archive_path, target)
                    created.append(target)
                entries.append({
                    "archive": package["archive"],
                    "ingest_status": "REJECTED",
                    "reason": package.get("error_code") or "REJECTED",
                    "quarantine": str(target.relative_to(repo_root)),
                })
                continue

            summary["accepted"] += 1
            result = _ingest_one(
                archive_path, repo_root, registry, created,
                phase=package.get("submission_phase") or C.PHASE1,
                student_id=package.get("student_id") or "",
            )
            entries.append(result)
            if result["ingest_status"] == "INGESTED":
                summary["ingested"] += 1
            else:
                summary["skipped"] += 1

        # ---- v10.1.16 Built 总包支路（bigdata_pN_<学号>.tar.gz）----
        for archive in built_archives:
            result = built_results[archive.name]
            row = {
                "archive": archive.name,
                "student_id": result.get("student_id") or "",
                "task_id": "",
                "submission_phase": result.get("phase") or "",
                "single_package_status": result["status"],
                "batch_status": "PASS" if result["status"] == "PASS" else "NOT_RUN",
                "final_status": "ACCEPTABLE" if result["status"] == "PASS" else "REJECT",
                "error_code": result.get("error_code"),
                "error_detail": (result.get("errors") or [{}])[0].get("detail", ""),
                "validation_layer": result.get("validation_layer", ""),
            }
            package_results.append(row)
            if result["status"] != "PASS":
                summary["single_fail"] += 1
                target = repo_root / "quarantine" / "single_validation_failed" / archive.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archive, target)
                created.append(target)
                entries.append({"archive": archive.name, "ingest_status": "REJECTED",
                                "reason": result.get("error_code") or "REJECTED",
                                "quarantine": str(target.relative_to(repo_root))})
                continue
            summary["accepted"] += 1
            converted = _convert_built_package_to_legacy(
                archive, result["metadata"], Path(tempfile.mkdtemp(prefix="csa_ingest_conv_")))
            try:
                result_one = _ingest_one(
                    converted, repo_root, registry, created,
                    phase=result.get("phase") or "phase1",
                    student_id=result.get("student_id") or "",
                )
            finally:
                shutil.rmtree(converted.parent, ignore_errors=True)
            entries.append(result_one)
            package_results[-1]["ingest_status"] = result_one["ingest_status"]
            if result_one["ingest_status"] == "INGESTED":
                summary["ingested"] += 1
            else:
                summary["skipped"] += 1

        if registry.changed:
            registry.commit()
    except Exception as exc:
        # §34：任何一步失败 -> 回滚本次新建内容，不产生半套 Registry
        for path in sorted({Path(p) for p in created}, key=lambda p: len(p.parts), reverse=True):
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        raise IngestError(f"ingest 失败，已回滚本次创建内容: {exc}") from exc

    report = {
        "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
        "validation_layer": C.LAYER_INGEST,
        "status": "PASS",
        "ingested_at": now_iso(),
        "repo_root": str(repo_root),
        "input_dir": str(input_dir),
        "summary": summary,
        "entries": entries,
        "packages": package_results,
        "repositories": {
            "received": str(repo_root / "received"),
            "tasks": str(repo_root / "tasks"),
            "agents": str(repo_root / "agents"),
            "registry": str(repo_root / "registry"),
            "distributions": str(repo_root / "distributions"),
        },
    }
    write_json(repo_root / "validation" / "ingest_report.json", report)
    return report




def _convert_built_package_to_legacy(
    archive: Path, metadata: dict[str, Any], workdir: Path,
) -> Path:
    """Built 总包 -> legacy Submission 结构（ingest 管线唯一桥接，§24）。

    产出 staging tar.gz（manifest.json + task/{task.json,data.csv,reference.py}
    + agent/{agent.py,agent_manifest.json}），身份来自总包 metadata.json。
    """
    import tarfile

    student_id = str(metadata["student_id"])
    task_id = str(metadata["task_id"])
    phase = str(metadata.get("workspace") or "phase1")
    extract = workdir / "outer"
    safe_extract_archive(archive, extract)
    top = extract / f"bigdata_p{1 if phase == 'phase1' else 2}_{student_id}"

    tree = workdir / "staging"
    (tree / "task" / "data").mkdir(parents=True)
    (tree / "agent").mkdir(parents=True)

    with tarfile.open(top / f"Tasks_{student_id}.tar.gz", "r:gz") as bundle:
        bundle.extractall(workdir / "tasks", filter="data")
    tasks_top = workdir / "tasks" / f"Tasks_{student_id}"
    task_content = json.loads((tasks_top / "task.json").read_text(encoding="utf-8"))
    # v10.1.18：hypothesized_failure_mode 从包外层 metadata 注入 legacy staging
    # （TA 端内部保留链路：canonical metadata.failure_mode 供 Task Quality
    # Review / assignment 使用；学生匿名包绝不携带）。
    task_content["hypothesized_failure_mode"] = str(
        metadata.get("hypothesized_failure_mode") or "")
    (tree / "task" / "task.json").write_text(
        json.dumps(task_content, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8")
    shutil.copy2(tasks_top / "data.csv", tree / "task" / "data" / "data.csv")
    if (tasks_top / "reference.py").is_file():
        shutil.copy2(tasks_top / "reference.py", tree / "task" / "reference.py")

    with tarfile.open(top / f"Agent_{student_id}.tar.gz", "r:gz") as bundle:
        bundle.extractall(workdir / "agents", filter="data")
    agent_top = workdir / "agents" / f"Agent_{student_id}"
    shutil.copy2(agent_top / "agent.py", tree / "agent" / "agent.py")

    from ..main.build import sha256_file

    files_sha = {str(p.relative_to(tree)): sha256_file(p)
                 for p in sorted(tree.rglob("*")) if p.is_file()}
    (tree / "manifest.json").write_text(json.dumps({
        "schema_version": C.SCHEMA_VERSION,
        "course": "BigData",
        "submission_phase": phase,
        "student_id": student_id,
        "task_id": task_id,
        "built_at": now_iso(),
        "build_status": "passed",
        "files": files_sha,
    }, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    (tree / "agent" / "agent_manifest.json").write_text(json.dumps({
        "schema_version": C.SCHEMA_VERSION,
        "student_id": student_id,
        "task_id": task_id,
        "entrypoint": "agent.py",
        "built_at": now_iso(),
        "files": {"agent/agent.py": files_sha.get("agent/agent.py", "")},
    }, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")

    from .archive import create_submission_archive

    legacy_archive = workdir / f"legacy_{archive.stem}.tar.gz"
    create_submission_archive(tree, legacy_archive)
    return legacy_archive


__all__ = ["IngestError", "ingest_submissions"]