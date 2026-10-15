"""Anonymous Distribution：Reviewer Package 构建 / 校验 / 泄露扫描 / 导出。

结构（§34/§35，public tree 只用 R 编号，不使用 student_id）：

    distributions/<AR######>/reviewers/<R######>/
        assignment.json          {assignment_id, distribution_id, phase,
                                  source_phase, distribution_phase,
                                  anonymous_tasks:[AT...]}
        tasks/<AT###>/           匿名 Task View（从 ingest 视图复制，只读）

分发包独立校验（verify_distribution）与匿名泄露扫描（scan_*）是强制步骤：
Assignment 即使 Verifier PASS，Distribution 未通过也绝不能 READY。

纯构建函数（``build_reviewer_package`` / ``build_reviewer_archive``）与目录位置
解耦：正式 ``distribute`` 与 Release Candidate 构建复用同一份实现（§26），
保证「验证过的字节 == 未来发布的字节」。
"""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from codesetarena.assignment.contract import (
    DISTRIBUTION_SCHEMA_VERSION,
    DIST_ARCHIVE_INVALID,
    DIST_EXTRA_TASK,
    DIST_IDENTITY_LEAK,
    DIST_MISSING_TASK,
    DIST_MISMATCH_MATRIX,
    DIST_SELF_LEAK,
    DIST_TASK_COUNT,
    DIST_UNKNOWN_TASK,
    PRIVATE_CONTENT_TOKENS,
    AssignmentError,
)
from codesetarena.submission.archive import create_submission_archive

# Reviewer Package 是「评审活动」的产物：来源阶段是 phase1 submission，
# 分发阶段是 phase2 评审（§37：禁止只用 phase 丢失来源语义）。
SOURCE_PHASE_DEFAULT = "phase1"
DISTRIBUTION_PHASE = "phase2"
ASSIGNMENT_JSON = "assignment.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reviewer_packages_root(repo: Path, run_id: str) -> Path:
    """正式 Reviewer Package 目录（repo 内）。"""
    return Path(repo) / "distributions" / run_id / "reviewers"


def reviewer_package_manifest(run: dict, reviewer_package_id: str,
                              anonymous_task_ids: list) -> dict:
    """学生可见的评审清单（assignment.json）——唯一 schema，RC 与正式分发共用。"""
    run_id = run["assignment_run_id"]
    return {
        "schema_version": DISTRIBUTION_SCHEMA_VERSION,
        "assignment_id": f"{run_id}_{reviewer_package_id}",
        "distribution_id": reviewer_package_id,
        "phase": DISTRIBUTION_PHASE,
        "source_phase": run.get("phase") or SOURCE_PHASE_DEFAULT,
        "distribution_phase": DISTRIBUTION_PHASE,
        "anonymous_tasks": list(anonymous_task_ids),
    }


# ---------------------------------------------------------------------------
# Reviewer Package 构建（纯函数：给定输出目录 + 匿名视图根，不依赖 repo 布局）
# ---------------------------------------------------------------------------


def build_reviewer_package(
    package_dir: str | Path,
    *,
    run: dict,
    reviewer_package_id: str,
    anonymous_task_ids: list,
    anonymous_tasks_root: str | Path,
) -> Path:
    """构建单个 Reviewer Package 目录（assignment.json + tasks/<AT>/）。

    只从匿名视图根复制（``anonymous_tasks_root/<AT###>/``），绝不触碰 canonical
    Task / Agent，也不携带 Task 作者的 agent。正式 distribute 与 Release
    Candidate 调用同一函数。
    """
    package_dir = Path(package_dir)
    tasks_root = Path(anonymous_tasks_root)
    tasks_dir = package_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    manifest = reviewer_package_manifest(run, reviewer_package_id, anonymous_task_ids)
    (package_dir / ASSIGNMENT_JSON).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for anonymous_task_id in anonymous_task_ids:
        source = tasks_root / anonymous_task_id
        if not source.is_dir():
            raise AssignmentError(DIST_UNKNOWN_TASK, f"匿名视图缺失: {anonymous_task_id}")
        target = tasks_dir / anonymous_task_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    return package_dir


def build_reviewer_distributions(repo: Path, run_dir: Path, run: dict, registry) -> Path:
    """为每个 Reviewer 构建匿名分发包（copy 匿名视图，不触碰 canonical）。"""
    repo = Path(repo)
    dist_base = reviewer_packages_root(repo, run["assignment_run_id"])
    tasks_root = repo / "distributions" / "tasks"
    mapping = _read_json(run_dir / "private_mapping.json")
    for reviewer in mapping["reviewers"]:
        build_reviewer_package(
            dist_base / reviewer["reviewer_package_id"],
            run=run,
            reviewer_package_id=reviewer["reviewer_package_id"],
            anonymous_task_ids=reviewer["anonymous_task_ids"],
            anonymous_tasks_root=tasks_root,
        )
    return dist_base.parent  # distributions/<AR######>/


# ---------------------------------------------------------------------------
# Distribution 独立校验（§38/§78：从最终包反向重建 == assignment.npy）
# ---------------------------------------------------------------------------


def rebuild_matrix_from_packages(dist_base: Path, registry, task_index: dict, n: int, r: int):
    """解析全部 reviewer packages；返回 (matrix, package_errors, package_count)。

    供 ``verify_distribution``（正式分发）与 Release Candidate 反向重建共用
    （§48：必须从最终包重建，禁止只数文件）。
    """
    matrix = [[0] * n for _ in range(n)]
    errors: list = []
    package_count = 0
    if not dist_base.is_dir():
        return None, [f"{DIST_MISSING_TASK}: 无 distribution 目录"], 0
    for package_dir in sorted(dist_base.iterdir()):
        if not package_dir.is_dir() or not package_dir.name.startswith("R"):
            continue
        package_count += 1
        name = package_dir.name
        if not name[1:].isdigit():
            errors.append(f"{DIST_UNKNOWN_TASK}: 非法 package 名 {name}")
            continue
        idx = int(name[1:]) - 1
        if not (0 <= idx < n):
            errors.append(f"{DIST_UNKNOWN_TASK}: package 索引越界 {name}")
            continue
        manifest_path = package_dir / "assignment.json"
        if not manifest_path.is_file():
            errors.append(f"{DIST_MISSING_TASK}: {name} 缺 assignment.json")
            continue
        ats = _read_json(manifest_path)["anonymous_tasks"]
        if len(ats) != r:
            errors.append(f"{DIST_TASK_COUNT}: {name} {len(ats)} != {r}")
        if len(set(ats)) != len(ats):
            errors.append(f"{DIST_TASK_COUNT}: {name} 含重复 anonymous task")
        for at in ats:
            if not (package_dir / "tasks" / at).is_dir():
                errors.append(f"{DIST_MISSING_TASK}: {name}/tasks/{at} 缺失")
            canonical = registry.find_canonical_task(at)
            if canonical is None:
                errors.append(f"{DIST_UNKNOWN_TASK}: {at} 无 canonical 回溯")
                continue
            task_id = canonical["task_id"]
            if task_id not in task_index:
                errors.append(f"{DIST_UNKNOWN_TASK}: {at} -> {task_id} 不在本次 assignment")
                continue
            matrix[idx][task_index[task_id]] += 1
    return matrix, errors, package_count


def verify_distribution(repo: Path, run_dir: Path, run: dict, registry) -> dict:
    """从最终 Reviewer Packages 反向重建矩阵并核对 canonical matrix（§78）。

    检查：每包恰好 r 个且唯一 AT；AT 均可回溯 canonical task；task 目录存在；
    与 assignment.npy 完全一致（无 unknown/missing/extra）；全局每 canonical
    task 恰好 r 次；无 self-leak。
    """
    import numpy as np

    r = int(run["r"])
    run_id = run["assignment_run_id"]
    snapshot = _read_json(run_dir / "input_snapshot.json")
    reviewer_order = snapshot["reviewer_order"]
    task_order = snapshot["task_order"]
    n = len(reviewer_order)
    task_index = {tid: j for j, tid in enumerate(task_order)}

    dist_base = repo / "distributions" / run_id / "reviewers"
    rebuilt, errors, package_count = rebuild_matrix_from_packages(
        dist_base, registry, task_index, n, r
    )

    checks = {"packages_built": package_count}

    # 配对序守卫：task_order[i] 必须是 reviewer_order[i] 的 own task，否则 no-self 检查失效
    paired = all(
        task_order[i] == snapshot["tasks"][i]["task_id"] for i in range(n)
    )
    checks["ordering_paired"] = bool(paired)
    if not paired:
        errors.append(f"{DIST_MISMATCH_MATRIX}: task_order 未与 owner 配对")

    if rebuilt is not None and paired:
        expected = np.load(run_dir / "assignment.npy").tolist()
        diff_cells = sum(
            1 for i in range(n) for j in range(n) if rebuilt[i][j] != expected[i][j]
        )
        if diff_cells:
            errors.append(
                f"{DIST_MISMATCH_MATRIX}: {diff_cells} cells 与 assignment.npy 不一致"
            )
        # 每个 canonical task 的全局 exposure == r
        exposure: dict = {}
        for i in range(n):
            for j in range(n):
                if rebuilt[i][j]:
                    exposure[task_order[j]] = exposure.get(task_order[j], 0) + 1
        for tid in task_order:
            if exposure.get(tid, 0) != r:
                errors.append(f"{DIST_TASK_COUNT}: task {tid} exposure={exposure.get(tid, 0)} != {r}")
        # 多分配（同 reviewer 重复 task）会破坏 binary 矩阵
        if any(rebuilt[i][j] > 1 for i in range(n) for j in range(n)):
            errors.append(f"{DIST_EXTRA_TASK}: 存在同 reviewer 重复任务")
        checks["min_exposure"] = min(exposure.values()) if task_order else 0
        checks["max_exposure"] = max(exposure.values()) if task_order else 0
        # no-self：配对序下 reviewer i 的 own task = task_order[i]
        self_leaks = sum(rebuilt[i][i] for i in range(n))
        checks["self_review_leaks"] = int(self_leaks)
        if self_leaks:
            errors.append(f"{DIST_SELF_LEAK}: {self_leaks} 条 reviewer 拿到 own task")

    return {"valid": not errors, "checks": checks, "errors": errors}


# ---------------------------------------------------------------------------
# 泄露扫描（identity §39 + private content §40）
# ---------------------------------------------------------------------------


def distribution_secret_tokens(repo: Path, registry) -> list:
    """扫描 token 集：student / submission / source / canonical / archive / registry 名。

    匿名 ID（AT### / AA###）是学生可见的公开标识，不在此列。
    """
    tokens: list = []
    for row in registry.rows:
        for key in ("student_id", "submission_id", "source_task_id", "task_id",
                    "source_agent_id", "agent_id", "original_archive"):
            value = row.get(key)
            if value:
                tokens.append(str(value))
    for mapping in registry.mappings:
        for key in ("task_id", "agent_id"):
            value = mapping.get(key)
            if value:
                tokens.append(str(value))
    tokens.extend(["submission_registry.jsonl", "anonymous_mapping.jsonl",
                   "assignments.jsonl", "private_mapping.json"])
    return sorted({t for t in tokens if t})


def scan_distribution_leaks(dist_root: Path, tokens: list) -> list:
    """复用 submission.anonymization 的身份泄露扫描（§39）。"""
    from codesetarena.submission.anonymization import scan_for_identity_leaks

    return scan_for_identity_leaks(dist_root, tokens)


def scan_private_content_leaks(dist_root: Path) -> list:
    """内容级私有物扫描（§40）：gold / reference / private evaluator / 内部 metadata。"""
    hits: list = []
    for path in sorted(dist_root.rglob("*")):
        if not path.is_file() or path.suffix in (".pyc", ".gz", ".zip", ".tgz", ".whl"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in PRIVATE_CONTENT_TOKENS:
            if token in text:
                hits.append({"path": str(path.relative_to(dist_root)), "token": token})
    return hits


# ---------------------------------------------------------------------------
# Reviewer Archive 导出 + 校验（§58/§59）
# ---------------------------------------------------------------------------


def build_reviewer_archive(package_dir: str | Path, archive_path: str | Path,
                           *, prefix: str | None = None) -> Path:
    """把一个 Reviewer Package 目录打包为确定性 tar.gz（§42/§59）。

    复用唯一正式 archive builder（``create_submission_archive``）：成员排序、
    固定 mtime/uid/gid、空 uname/gname、gzip 头固定 —— 同一 package 两次构建
    字节一致（§59 verified bytes == released bytes）。
    """
    return create_submission_archive(package_dir, archive_path, prefix=prefix)


def reviewer_archive_name(reviewer_package_id: str) -> str:
    """正式匿名包名规则（§28：不含 student_id / canonical / source id）。"""
    return f"review_package_{reviewer_package_id}.tar.gz"


def scan_exposure_contract_leaks(dist_root) -> list:
    """Exposure semantic leak scan（v10.1.11 Exposure Contract Fix #3）。

    对最终 reviewer distributions 里的每个匿名 task view（canonical nested
    ``tasks/AT*/task/task.json`` 优先，legacy flat 兼容）递归检查
    ``exposure.contract.FORBIDDEN_TASK_KEYS``（expected_failure_mode /
    author_expected / gold 系 / identity 字段）。

    与 identity/token 扫描（scan_distribution_leaks）互不替代：本扫描是
    Assignment READY 的额外 gate —— 任何命中 => DISTRIBUTION_FAILED，
    绝不允许 READY / export。
    """

    from codesetarena.exposure.contract import scan_task_view_leaks

    dist_root = Path(dist_root)
    root_posix = dist_root.as_posix()
    hits: list = []
    for task_json in sorted(dist_root.rglob("task.json")):
        rel = task_json.as_posix()
        if "/tasks/" not in ("/" + rel[len(root_posix):] if rel.startswith(root_posix) else rel):
            continue
        try:
            view = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # 结构错误由 verify_distribution 负责；此处只做语义泄漏扫描
        keys = scan_task_view_leaks(view)
        if keys:
            hits.append({"path": rel, "keys": sorted(set(keys))})
    return hits


def export_reviewer_archives(repo: Path, run_id: str, out_dir=None) -> list:
    """按私有映射导出 per-reviewer 独立 tar.gz（review_package_<R######>.tar.gz）。

    v10.1.11 Exposure Contract Fix #3：仅 READY（distribution_status == "READY"）
    的 assignment run 允许导出 —— READY => export 可发放 => exposure import 必过。
    """
    run_json = Path(repo) / "assignments" / run_id / "run.json"
    run = json.loads(run_json.read_text(encoding="utf-8")) if run_json.is_file() else {}
    if run.get("distribution_status") != "READY":
        raise AssignmentError(
            "EXPOSURE_EXPORT_NOT_READY",
            f"assignment run {run_id} distribution_status="
            f"{run.get('distribution_status')!r}（仅 READY 允许导出 Exposure 包）",
        )
    dist_base = reviewer_packages_root(repo, run_id)
    export_dir = Path(out_dir) if out_dir else (
        Path(repo) / "distributions" / run_id / "exports"
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for package_dir in sorted(dist_base.iterdir()):
        if not package_dir.is_dir() or not package_dir.name.startswith("R"):
            continue
        archive = export_dir / reviewer_archive_name(package_dir.name)
        build_reviewer_archive(package_dir, archive)
        exported.append(str(archive))
    return exported


def member_task_dir(member_name: str) -> str:
    """从 tar member 名提取匿名任务目录（兼容包根 ``tasks/...`` 与带前缀布局）。"""
    marker = "/tasks/"
    if marker in member_name:
        rest = member_name.split(marker, 1)[1]
    elif member_name.startswith("tasks/"):
        rest = member_name[len("tasks/"):]
    else:
        return ""
    return rest.split("/", 1)[0] if "/" in rest else rest


def validate_reviewer_archive(archive_path: Path, r: int, *, secret_tokens=()) -> dict:
    """§59：最终 Reviewer Archive 独立校验。"""
    errors: list = []
    task_count = -1
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            names = [m.name for m in members]
            for name in names:
                if name.startswith("/") or ".." in Path(name).parts:
                    errors.append(f"{DIST_ARCHIVE_INVALID}: 路径安全违规 {name}")
            if not any(n.endswith(ASSIGNMENT_JSON) for n in names):
                errors.append(f"{DIST_MISSING_TASK}: 缺 {ASSIGNMENT_JSON}")
            task_prefixes = {d for d in (member_task_dir(n) for n in names) if d}
            task_count = len(task_prefixes)
            if task_count != r:
                errors.append(f"{DIST_TASK_COUNT}: {task_count} != {r}")
            if task_prefixes and not all(t.startswith("AT") for t in task_prefixes):
                errors.append(f"{DIST_UNKNOWN_TASK}: 出现非匿名 task 目录")
            joined = "\n".join(names)
            for token in secret_tokens:
                if token and token in joined:
                    errors.append(f"{DIST_IDENTITY_LEAK}: {token}")
    except (tarfile.TarError, OSError) as exc:
        errors.append(f"{DIST_ARCHIVE_INVALID}: {exc}")
    return {"valid": not errors, "errors": errors, "task_count": task_count}

# ---------------------------------------------------------------------------
# v10.1.16 桥接：把 READY/VERIFIED run 打包成学生 Exposure Import 契约的 zip
# （BigData_Exposure_R######.zip：assignment.json{student_id, agent_id,
# assigned_tasks} + tasks/<AT>/{匿名视图, reference.py}）
# ---------------------------------------------------------------------------

_EXPOSURE_ZIP_STAMP = (2026, 1, 1, 0, 0, 0)


def export_exposure_packages(repo: Path, run_id: str, out_dir=None) -> list:
    """把已验证的 Assignment Run 导出为每位 Reviewer 一个可导入 Exposure 包。

    - 分配来源：``run_dir/private_mapping.json``（distribute 已生成）；
      缺失时用纯函数从 assignment.npy + snapshot 现算（不落盘、不改状态）；
    - 每 AT 内容 = 匿名视图（task.json + data）+ ``reference.py``
      （来自 canonical ``tasks/<T>/reference.py``，v10.1.16 契约随匿名题分发）；
    - assignment.json 使用冻结 schema ``{student_id, agent_id, assigned_tasks}``，
      student_id/agent_id 是**接收者本人**的公开身份；
    - 产物：``<out_dir>/BigData_Exposure_R######.zip``（确定性字节），
      默认 out_dir = ``repo/distributions/<AR>/exposure/``。
    """

    import tempfile
    import zipfile

    repo = Path(repo)
    run_dir = repo / "assignments" / run_id
    run = _read_json(run_dir / "run.json")
    snapshot = _read_json(run_dir / "input_snapshot.json")
    import numpy as np

    matrix = np.load(run_dir / "assignment.npy").tolist()
    from codesetarena.submission.registry import SubmissionRegistry

    registry = SubmissionRegistry(repo)
    mapping_path = run_dir / "private_mapping.json"
    if mapping_path.is_file():
        private = _read_json(mapping_path)
    else:
        from codesetarena.assignment.mapping import derive_private_assignment_mapping

        _, private = derive_private_assignment_mapping(
            snapshot, matrix, r=int(run["r"]), run_id=run_id,
            phase=run.get("phase", "phase1"), registry=registry,
        )

    destination = Path(out_dir) if out_dir else (
        repo / "distributions" / run_id / "exposure"
    )
    destination.mkdir(parents=True, exist_ok=True)

    exported = []
    for reviewer in private["reviewers"]:
        sid = reviewer["reviewer_student_id"]
        ats = list(reviewer["anonymous_task_ids"])
        staging = Path(tempfile.mkdtemp(prefix="csa_exposure_export_"))
        try:
            root = staging / "pkg"
            (root / "tasks").mkdir(parents=True)
            (root / "assignment.json").write_text(
                json.dumps({
                    "student_id": sid,
                    "agent_id": f"AGENT_{sid}",
                    "assigned_tasks": ats,
                }, ensure_ascii=False, indent=2) + chr(10),
                encoding="utf-8",
            )
            for at in ats:
                view = repo / "distributions" / "tasks" / at
                if not view.is_dir():
                    raise AssignmentError(DIST_UNKNOWN_TASK, f"匿名视图缺失: {at}")
                target = root / "tasks" / at
                shutil.copytree(view, target)
                canonical = registry.find_canonical_task(at)
                reference = repo / "tasks" / canonical["task_id"] / "reference.py"
                if not reference.is_file():
                    raise AssignmentError(
                        DIST_MISSING_TASK, f"{at}: canonical reference.py 缺失"
                    )
                shutil.copy2(reference, target / "reference.py")
            archive = destination / f"BigData_Exposure_{reviewer['reviewer_package_id']}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(p for p in root.rglob("*") if p.is_file()):
                    info = zipfile.ZipInfo(
                        path.relative_to(root).as_posix(), date_time=_EXPOSURE_ZIP_STAMP)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    bundle.writestr(info, path.read_bytes())
            exported.append(str(archive))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return exported
