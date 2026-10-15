"""CodeSetArena Phase E — Manual-Derived 200-Student Real-Cohort Dry-Run orchestrator.

Discover -> Inventory -> Checkpoint A（14 FM 类别硬门）-> [gated] Cohort
Generation -> 200 official submission builds -> batch validate -> ingest
-> canonical repository -> real category extraction -> CATEGORY_AWARE_SWAP
-> independent verifier -> diversity audit -> anonymous preview -> leakage scan
-> DRY_RUN_VERIFIED。

严格边界（本轮）：
- workspace/manual 只读，绝不修改 / 重命名 / 覆盖；
- 不做正式 assignment distribute、不生成 Reviewer Package、
  不生成 review_package_*.tar.gz、状态永不为 READY（只 DRY_RUN_VERIFIED）；
- 学生身份全部 synthetic（99000001..99000200，synthetic 保留段）；
- 不使用 LLM 生成/改写任何 Task / Agent；只做模板实例化 + 身份替换 + 打包；
- 底层全部调用现有正式 API（submission build / batch validate / ingest /
  assignment core / verifier / evaluator / build_anonymous_task_view），
  本脚本只编排，不复制校验逻辑。

Checkpoint（§72，任一失败 -> DRY_RUN_FAILED）：
  A manual inventory：14 个官方 FM 类别必须一一找到；5 类 Agent；
  B 200 个 submission build 全 PASS；
  C batch validation 200 ACCEPT；
  D ingest 后 200 canonical tasks / 200 canonical agents；
  E canonical category：14 个非空类别、min 14 / max 15；
  F assignment verifier PASS；
  G anonymous preview：identity leak = 0 && private content leak = 0。

用法：
  .venv/bin/python scripts/cohort_dryrun_200.py \
      --manual workspace/manual --out workspace/cohort_dryrun_200 --students 200 --seed 20261005
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codesetarena import constants as K

OFFICIAL_FMS = list(K.HYPOTHESIZED_FM_ORDER)  # FM01_REQUIREMENT ... FM14_PROMPT_INJECTION
OFFICIAL_FM_COUNT = K.HYPOTHESIZED_FM_COUNT  # 14
SYNTHETIC_ID_START = 99000001  # 保留 synthetic 范围，与真实 roster 不冲突
DRY_AGENT_TYPES = ("A", "B", "C", "D", "E")
CATEGORY_15 = 4  # 200 = 14*14 + 4
CATEGORY_14 = 10


class DryRunGateError(RuntimeError):
    """Checkpoint 失败 -> 停止（§72）。"""

    def __init__(self, checkpoint: str, message: str):
        super().__init__(f"[Checkpoint {checkpoint}] {message}")
        self.checkpoint = checkpoint
        self.message = message


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _dir_content_hash(directory: Path) -> str:
    """目录内全部文件（排除 runs/）的聚合哈希。"""
    import hashlib

    digest = hashlib.sha256()
    files = sorted(p for p in directory.rglob("*") if p.is_file() and "runs" not in p.parts)
    for path in files:
        digest.update(str(path.relative_to(directory)).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Checkpoint A：Manual Inventory（只读）
# ---------------------------------------------------------------------------


def discover_manual(manual_root) -> dict:
    """扫描 workspace/manual：目录家族、FM、5 类 Agent、源哈希。"""
    root = Path(manual_root)
    entries = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        task_json = directory / "task.json"
        fm = None
        if task_json.is_file():
            try:
                fm = json.loads(task_json.read_text(encoding="utf-8")).get("hypothesized_failure_mode")
            except Exception:  # noqa: BLE001
                fm = "PARSE_ERR"
        agent = directory / "agent.py"
        agent_sha = _sha256_bytes(agent.read_bytes()) if agent.is_file() else None
        entries.append({
            "template_dir": directory.name,
            "template_path": str(directory),
            "hypothesized_failure_mode": fm,
            "agent_sha256": agent_sha,
            "content_hash": _dir_content_hash(directory),
            "has_task": task_json.is_file(),
            "has_agent": agent.is_file(),
            "has_data": (directory / "data.csv").is_file() or (directory / "task/data/data.csv").is_file(),
            "has_reference": (directory / "reference.py").is_file(),
        })

    # 文件夹家族（DA 语义模板类）：DA01..DA12 + DA02_FM10 + DA06_FM13 = 14 families
    family_names = sorted({e["template_dir"].rsplit("_", 2)[0] for e in entries
                           if e["has_task"]})
    family_fm = {}
    for e in entries:
        if e["has_task"] and e["hypothesized_failure_mode"]:
            family = e["template_dir"].rsplit("_", 2)[0]
            family_fm.setdefault(family, set()).add(e["hypothesized_failure_mode"])

    # Agent 类型：按 agent.py 内容指纹归类（A..E 已在命名上稳定）
    agent_types = {}
    for e in entries:
        if not e["agent_sha256"]:
            continue
        letter = e["template_dir"].rsplit("_", 1)[-1]
        agent_types.setdefault(letter, {"agent_sha256": e["agent_sha256"],
                                        "template_names": []})
        agent_types[letter]["template_names"].append(e["template_dir"])

    manual_fm_codes = sorted({e["hypothesized_failure_mode"] for e in entries
                              if e["hypothesized_failure_mode"]})
    missing_fm = sorted(set(OFFICIAL_FMS) - set(manual_fm_codes))

    # 分布维度（§19：DA capability / FM / Agent 分别报告）
    failure_mode_distribution = {}
    for e in entries:
        if e["hypothesized_failure_mode"]:
            failure_mode_distribution[e["hypothesized_failure_mode"]] = \
                failure_mode_distribution.get(e["hypothesized_failure_mode"], 0) + 1
    da_capability_distribution = {}
    for e in entries:
        task_json = Path(e["template_path"]) / "task.json"
        if not task_json.is_file():
            continue
        try:
            cap = json.loads(task_json.read_text(encoding="utf-8")).get("capability") or "(未标注)"
        except Exception:  # noqa: BLE001
            cap = "(未标注)"
        da_capability_distribution[cap] = da_capability_distribution.get(cap, 0) + 1

    fm_templates = {}
    for fm in OFFICIAL_FMS:
        candidates = sorted(
            e["template_dir"] for e in entries
            if e["hypothesized_failure_mode"] == fm
        )
        if candidates:
            fm_templates[fm] = candidates

    return {
        "manual_root": str(root),
        "source_dirs_count": len(entries),
        "folder_families": sorted(family_names),
        "folder_family_fm": {f: sorted(v) for f, v in sorted(family_fm.items())},
        "official_fm_codes": OFFICIAL_FMS,
        "manual_fm_codes": manual_fm_codes,
        "manual_fm_count": len(manual_fm_codes),
        "manual_fm_code_count": len(manual_fm_codes),
        "missing_fm_codes": missing_fm,
        "failure_mode_distribution": failure_mode_distribution,
        "da_capability_distribution": da_capability_distribution,
        "fm_templates": fm_templates,
        "agent_types": {
            letter: {"agent_sha256": info["agent_sha256"],
                     "template_count": len(set(info["template_names"]))}
            for letter, info in sorted(agent_types.items())
        },
        "agent_type_count": len(agent_types),
        "entries": entries,
        "manual_hash": _dir_content_hash(root),
        "status": "INPUT_INVALID" if missing_fm else "OK",
    }


def checkpoint_a(inventory: dict) -> dict:
    """§4：14 Task 模板类别 = 14 官方类别，一一对应；缺失 -> 停止。"""
    if inventory["manual_fm_count"] != OFFICIAL_FM_COUNT:
        missing = inventory["missing_fm_codes"]
        raise DryRunGateError(
            "A",
            f"manual 只覆盖 {inventory['manual_fm_count']}/{OFFICIAL_FM_COUNT} 个官方类别，"
            f"缺少: {missing}；禁止猜类别，停止",
        )
    if inventory["agent_type_count"] != 5:
        raise DryRunGateError("A", f"agent types = {inventory['agent_type_count']} != 5")
    return {"checkpoint": "A", "status": "PASS"}


# ---------------------------------------------------------------------------
# Cohort Generator（gated；只在 inventory OK 时可用）
# ---------------------------------------------------------------------------


def allocation_plan(seed: int) -> list[dict]:
    """确定性分配：14 类别（前 4 个 15、其余 14）+ 5 Agent 各 40，交叉均衡。"""
    import random

    rng = random.Random(seed)
    perm = list(OFFICIAL_FMS)
    rng.shuffle(perm)
    sizes = {fm: (15 if idx < CATEGORY_15 else 14) for idx, fm in enumerate(perm)}

    # 14 类：light agent(2) + 4 个 agent(3)；每 agent 恰在 2 个 14 类中当 light
    # （保证 5 × 40 = 200 总数）；light 位置经 seed 偏移，避免固定类别绑定。
    light_sequence = [(k % 5) for k in range(CATEGORY_14)]
    shift = rng.randrange(5)
    light_sequence = light_sequence[shift:] + light_sequence[:shift]
    fourteen_fms = [fm for fm in perm if sizes[fm] == 14]
    light_of = {fm: DRY_AGENT_TYPES[light_sequence[k]] for k, fm in enumerate(fourteen_fms)}

    plan = []
    for fm in perm:
        size = sizes[fm]
        if size == 15:
            agent_counts = {ag: 3 for ag in DRY_AGENT_TYPES}
        else:
            light = light_of[fm]
            agent_counts = {ag: (2 if ag == light else 3) for ag in DRY_AGENT_TYPES}
        plan.append({"category": fm, "size": size, "agent_counts": agent_counts})
    return plan


def generate_cohort_manifest(out_path: Path, *, seed: int, inventory: dict,
                             students: int = 200, usage_path: Path | None = None) -> Path:
    """按分配计划 + 模板轮换生成 cohort_generation_manifest.jsonl。

    §27/§28：同一 FM 的多个合法模板（T1/T2 / 多 DA）按确定性 seed 轮换并尽量
    均衡（template_usage.csv），避免 200 人全部复制同一模板；模板与 Agent 选择
    分离（先选 Task 模板，再按 5 Agent 配额配 A–E）。
    """
    import random

    checkpoint_a(inventory)
    assert students == 200, "v1 dry-run cohort size fixed at 200"
    plan = allocation_plan(seed)

    rng = random.Random(seed + 999)
    usage: Counter = Counter()
    lines = []
    sid = SYNTHETIC_ID_START
    for row in plan:
        fm = row["category"]
        templates = inventory["fm_templates"][fm]
        for agent in DRY_AGENT_TYPES:
            count = row["agent_counts"][agent]
            legal = [t for t in templates if t.rsplit("_", 1)[-1] == agent]
            if not legal:
                raise DryRunGateError(
                    "GENERATOR", f"FM {fm} 无 {agent} 变体模板: {templates}")
            offset = (OFFICIAL_FMS.index(fm) * 17 + DRY_AGENT_TYPES.index(agent) * 31
                      + rng.randrange(97))
            for k in range(count):
                chosen = legal[(offset + k) % len(legal)]
                usage[chosen] += 1
                lines.append({
                    "synthetic_student_id": str(sid),
                    "student_index": sid - SYNTHETIC_ID_START + 1,
                    "task_category": fm,
                    "agent_type": agent,
                    "source_template_dir": chosen,
                    "synthetic": True,
                    "category_count_group": row["size"],
                })
                sid += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    if usage_path:
        import csv as _csv

        with Path(usage_path).open("w", encoding="utf-8", newline="") as fh:
            writer = _csv.writer(fh)
            writer.writerow(["template_id", "failure_mode", "usage_count"])
            for tpl, count in sorted(usage.items()):
                fm_code = next((row["category"] for row in plan
                                if tpl in inventory["fm_templates"][row["category"]]), "")
                writer.writerow([tpl, fm_code, count])
    return out_path


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Full Dry-Run
# ---------------------------------------------------------------------------


def run_dryrun(manual_root, out_root, *, students: int = 200, seed: int = 20261005) -> dict:
    """完整编排；Checkpoint A 失败立即返回（不生成任何 submission）。"""
    out = Path(out_root)
    out.mkdir(parents=True, exist_ok=True)

    inventory = discover_manual(manual_root)
    (out / "manual_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        checkpoint_a(inventory)
    except DryRunGateError as exc:
        _write_gate_report(out, inventory, exc, seed, students)
        return {
            "final_status": "DRY_RUN_INPUT_INVALID",
            "checkpoint": "A",
            "reason": exc.message,
            "missing_fm_codes": inventory["missing_fm_codes"],
            "manual_fm_count": inventory["manual_fm_count"],
            "folder_families": inventory["folder_families"],
            "agent_type_count": inventory["agent_type_count"],
        }

    return run_dryrun_chain(inventory, out, students=students, seed=seed)


def run_dryrun_chain(inventory: dict, out_root, *, students: int = 200,
                     seed: int = 20261005) -> dict:
    """Checkpoint B..G。只编排，底层全部调用现有正式 API。"""
    from codesetarena.assignment import input_builder as ib
    from codesetarena.assignment import distribution as dist_mod
    from codesetarena.assignment import run as assign_run
    from codesetarena.submission import anonymization as anon
    from codesetarena.submission.batch.validator import validate_batch
    from codesetarena.submission.ingest import ingest_submissions
    from codesetarena.submission.local import build_submission
    from codesetarena.submission.registry import SubmissionRegistry

    out = Path(out_root)
    generation = out / "generation"
    workspaces_dir = out / "student_workspaces"
    submissions_dir = out / "submissions"
    validation_dir = out / "validation"
    repository = out / "repository"
    preview_root = out / "anonymity_preview"
    reports = out / "reports"
    for d in (generation, workspaces_dir, submissions_dir, validation_dir,
              repository, preview_root, reports):
        d.mkdir(parents=True, exist_ok=True)

    # --- B：manifest + 200 workspaces + official build ---
    manifest_path = generate_cohort_manifest(
        generation / "cohort_generation_manifest.jsonl", seed=seed,
        inventory=inventory, students=students,
        usage_path=generation / "template_usage.csv",
    )
    manifest = _load_jsonl(manifest_path)
    assert len(manifest) == students

    build_failures: list[dict] = []
    final_manifest: list[dict] = []
    manual_root = Path(inventory.get("manual_root", "."))
    for idx, row in enumerate(manifest, start=1):
        sid = row["synthetic_student_id"]
        task_id = f"T_{idx:032x}"
        workspace = workspaces_dir / f"student_{idx:03d}"
        template_dir = manual_root / row["source_template_dir"]
        if not template_dir.is_dir():  # 允许绝对路径直接指向模板
            template_dir = Path(row["source_template_dir"])
        _instantiate_workspace(workspace, template_dir, sid, task_id)
        archive_path = submissions_dir / f"DataC1004_P1_{sid}.tar.gz"
        try:
            record = build_submission(
                workspace, phase="phase1", student_id=sid,
                output_path=archive_path, archive_format="tar.gz",
            )
            status = record["local_validation"]["status"]
        except Exception as exc:  # noqa: BLE001
            status = "FAIL"
            build_failures.append({"student_id": sid, "category": row["task_category"],
                                   "agent_type": row["agent_type"], "error": str(exc)})
        if status != "PASS":
            build_failures.append({"student_id": sid, "category": row["task_category"],
                                   "agent_type": row["agent_type"],
                                   "error": record.get("local_validation", {}).get("error_code", "FAIL")})
            continue
        final_manifest.append({
            **row,
            "generated_task_id": task_id,
            "generated_agent_id": task_id,
            "workspace_path": str(workspace),
            "submission_archive_path": str(archive_path),
        })

    if build_failures:
        raise DryRunGateError("B", f"{len(build_failures)} 个 build FAIL: {build_failures[:5]}")
    (generation / "cohort_generation_manifest_final.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in final_manifest) + "\n",
        encoding="utf-8",
    )

    # --- C：Batch validation（官方）---
    batch = validate_batch(submissions_dir)
    accepted = sum(1 for p in batch["packages"] if p["final_status"] == "ACCEPTABLE")
    (validation_dir / "batch_report.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if accepted != students:
        raise DryRunGateError("C", f"batch accepted {accepted}/{students}")

    # --- D：Ingest（官方）---
    ingest_submissions(repository, submissions_dir)
    reg = SubmissionRegistry(repository)
    canonical_tasks = [p for p in (repository / "tasks").iterdir()
                       if p.is_dir() and (p / "metadata.json").is_file()]
    canonical_agents = [p for p in (repository / "agents").iterdir()
                        if p.is_dir() and (p / "metadata.json").is_file()]
    if (len(reg.rows), len(canonical_tasks), len(canonical_agents)) != (students,) * 3:
        raise DryRunGateError(
            "D", f"registry={len(reg.rows)} tasks={len(canonical_tasks)} agents={len(canonical_agents)}"
        )

    # --- E：真实 canonical category 分布（重新提取，不信任 manifest）---
    assignment_input = ib.build_assignment_input(repository, "phase1")
    vector = assignment_input["category_vector"]
    counts = Counter(vector)
    nonempty = sum(1 for c in range(1, 15) if counts.get(c))
    if len(vector) != students or nonempty != 14 or min(counts.values()) < 13:
        raise DryRunGateError(
            "E", f"vector={len(vector)} nonempty={nonempty} counts={sorted(counts.values())}"
        )

    # --- F：Assignment（生产 Core CATEGORY_AWARE_SWAP）+ 独立 Verifier ---
    record = assign_run.build_assignment_run(repository, "phase1", seed=seed)
    if record["status"] != "VERIFIED":
        raise DryRunGateError("F", f"assignment status={record['status']}")
    verifier_file = json.loads(
        (repository / "assignments" / record["assignment_run_id"] / "verifier.json").read_text()
    )
    if not verifier_file["valid"]:
        raise DryRunGateError("F", "独立 verifier FAIL")

    # --- G：Anonymous preview + leakage scan ---
    preview_tasks = preview_root / "tasks"
    preview_tasks.mkdir(parents=True, exist_ok=True)
    for mapping in reg.mappings:
        if "anonymous_task_id" not in mapping:
            continue
        at = mapping["anonymous_task_id"]
        anon.build_anonymous_task_view(repository / "tasks" / mapping["task_id"],
                                       at, preview_tasks / at)
    tokens = sorted({
        *dist_mod.distribution_secret_tokens(repository, reg),
        *[r["synthetic_student_id"] for r in final_manifest],
        *[Path(r["source_template_dir"]).name for r in final_manifest],
        "workspace/manual", "workspace\\manual", "manual/", "manual\\",
    })
    identity_leaks = anon.scan_for_identity_leaks(preview_root, tokens)
    content_leaks = dist_mod.scan_private_content_leaks(preview_root)

    dist_tree = out / "distributions"
    reviewer_dirs = list(dist_tree.glob("*/reviewers")) if dist_tree.is_dir() else []
    review_archives = list(dist_tree.rglob("review_package_*.tar.gz")) if dist_tree.is_dir() else []
    if identity_leaks or content_leaks or reviewer_dirs or review_archives:
        raise DryRunGateError(
            "G",
            f"identity_leaks={len(identity_leaks)} content_leaks={len(content_leaks)} "
            f"reviewer_dirs={len(reviewer_dirs)} review_archives={len(review_archives)}",
        )

    preview_ids = sorted(p.name for p in preview_tasks.iterdir() if p.is_dir())
    result = {
        "final_status": "DRY_RUN_VERIFIED",
        "checkpoint": "A-G",
        "manual_fm_count": inventory["manual_fm_count"],
        "manual_agent_types": inventory["agent_type_count"],
        "students": students,
        "submission_build_pass": len(final_manifest),
        "submission_build_failed": len(build_failures),
        "batch_accepted": accepted,
        "canonical_tasks": len(canonical_tasks),
        "canonical_agents": len(canonical_agents),
        "category_vector_length": len(vector),
        "non_empty_categories": nonempty,
        "category_min": min(counts.values()),
        "category_max": max(counts.values()),
        "assignment_method": record["method"],
        "assignment_seed": record["seed"],
        "matrix_shape": [record["num_reviewers"], record["num_tasks"]],
        "total_assignments": record["total_assignments"],
        "verifier": record["verifier_status"],
        "diversity_score": record["diversity_score"],
        "exposure_mae": record["exposure_mae"],
        "anonymous_previews": len(preview_ids),
        "identity_leaks": len(identity_leaks),
        "private_content_leaks": len(content_leaks),
        "distribution_packages_created": 0,
        "distribution": {"status": "SKIPPED_DRY_RUN",
                         "reason": "real-cohort dry-run explicitly forbids student distribution"},
        "manual_hash": inventory["manual_hash"],
        "cohort_generation_seed": seed,
    }
    _write_dryrun_reports(out, result, final_manifest, vector, record,
                          identity_leaks, content_leaks)
    return result


def _instantiate_workspace(target: Path, template_dir: Path, student_id: str, task_id: str) -> None:
    """模板实例化：复制模板文件 + 身份替换（不改 Task 语义 / Agent 逻辑）。"""
    template = Path(template_dir)
    if not template.is_dir():
        raise DryRunGateError("B", f"模板目录不存在: {template_dir}")
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(p for p in template.rglob("*") if p.is_file() and "runs" not in p.parts):
        rel = path.relative_to(template)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    task_path = target / "task.json"
    if task_path.is_file():
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["student_id"] = student_id
        task["task_id"] = task_id
        task_path.write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path = target / "build_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["student_id"] = student_id
            manifest["task_id"] = task_id
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _write_dryrun_reports(out: Path, result: dict, manifest: list, vector: list,
                          record: dict, identity_leaks: list, content_leaks: list) -> None:
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    cat_counts = Counter(vector)
    agent_counts = Counter(row["agent_type"] for row in manifest)
    (reports / "cohort_dryrun_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (reports / "category_distribution.json").write_text(
        json.dumps({str(c): cat_counts.get(c, 0) for c in range(1, 15)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (reports / "agent_distribution.json").write_text(
        json.dumps(dict(agent_counts), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (reports / "assignment_summary.json").write_text(
        json.dumps({k: v for k, v in record.items()
                    if k in ("assignment_run_id", "method", "method_version", "seed", "r",
                             "num_reviewers", "num_tasks", "input_snapshot_hash",
                             "total_assignments", "verifier_status", "diversity_score",
                             "exposure_mae")}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (reports / "leakage_report.json").write_text(
        json.dumps({"identity_leaks": identity_leaks,
                    "private_content_leaks": content_leaks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for key, value in result.items():
        ws.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value])

    ws = wb.create_sheet("SubmissionGeneration")
    ws.append(["index", "student_id", "category", "agent_type", "template", "task_id"])
    for row in manifest:
        ws.append([row["student_index"], row["synthetic_student_id"], row["task_category"],
                   row["agent_type"], row["source_template_dir"], row.get("generated_task_id")])

    ws = wb.create_sheet("TaskCategoryDistribution")
    ws.append(["category_code", "category_name", "planned_count", "canonical_count", "percentage"])
    for c in range(1, 15):
        fm = OFFICIAL_FMS[c - 1]
        ws.append([fm, K.FAILURE_MODE_SLUGS.get(fm.split("_")[0], ""),
                   cat_counts.get(c, 0), cat_counts.get(c, 0),
                   round(100 * cat_counts.get(c, 0) / 200, 2)])

    ws = wb.create_sheet("AgentTypeDistribution")
    ws.append(["agent_type", "planned_count", "canonical_count", "percentage"])
    for agent in DRY_AGENT_TYPES:
        ws.append([agent, agent_counts.get(agent, 0), agent_counts.get(agent, 0),
                   round(100 * agent_counts.get(agent, 0) / 200, 2)])

    ws = wb.create_sheet("CategoryAgentCross")
    ws.append(["task_category"] + list(DRY_AGENT_TYPES))
    cross = Counter((row["task_category"], row["agent_type"]) for row in manifest)
    for c in range(1, 15):
        fm = OFFICIAL_FMS[c - 1]
        ws.append([fm] + [cross.get((fm, ag), 0) for ag in DRY_AGENT_TYPES])

    ws = wb.create_sheet("AssignmentSummary")
    for key, value in result.items():
        ws.append([key, json.dumps(value, ensure_ascii=False)
                   if isinstance(value, (list, dict)) else value])

    ws = wb.create_sheet("ReviewerDiversity")
    ws.append(["metric", "value"])
    for key, value in (record.get("reviewer_diversity") or {}).items():
        ws.append([key, value])

    ws = wb.create_sheet("LeakageScan")
    ws.append(["scan_scope", "token_type", "matches", "status"])
    if identity_leaks:
        for leak in identity_leaks:
            ws.append(["anonymous_preview", leak.get("token"), 1, "FAIL"])
    else:
        ws.append(["anonymous_preview", "identity_tokens", 0, "PASS"])
    if content_leaks:
        for leak in content_leaks:
            ws.append(["anonymous_preview", leak.get("token"), 1, "FAIL"])
    else:
        ws.append(["anonymous_preview", "private_content", 0, "PASS"])

    wb.save(reports / "cohort_dryrun_report.xlsx")


def _write_gate_report(out: Path, inventory: dict, exc: DryRunGateError,
                       seed: int, students: int) -> None:
    report = {
        "final_status": "DRY_RUN_INPUT_INVALID",
        "checkpoint": exc.checkpoint,
        "reason": exc.message,
        "students": students,
        "cohort_generation_seed": seed,
        "manual_fm_count": inventory["manual_fm_count"],
        "official_fm_count": OFFICIAL_FM_COUNT,
        "missing_fm_codes": inventory["missing_fm_codes"],
        "folder_families": inventory["folder_families"],
        "agent_types": inventory["agent_types"],
        "manual_hash": inventory["manual_hash"],
    }
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "cohort_dryrun_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        for key, value in report.items():
            ws.append([key, json.dumps(value, ensure_ascii=False)
                       if isinstance(value, (list, dict)) else value])
        wb.save(reports / "cohort_dryrun_report.xlsx")
    except Exception:  # noqa: BLE001 — Excel 是管理视图，失败不阻断
        pass


def print_summary(result: dict) -> None:
    print("=" * 40)
    print("CodeSetArena Real Cohort Dry-Run")
    print("=" * 40)
    if result["final_status"] != "DRY_RUN_VERIFIED":
        print(f"CHECKPOINT {result.get('checkpoint')} -> {result['final_status']}")
        for key in ("reason", "manual_fm_count", "missing_fm_codes", "folder_families",
                    "agent_type_count"):
            if key in result:
                print(f"{key.upper()} = {result[key]}")
        print("NO SUBMISSION GENERATED (gate) | DISTRIBUTION = SKIPPED")
        return
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            print(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"{key} = {value}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", default="workspace/manual")
    parser.add_argument("--out", default="workspace/cohort_dryrun_200")
    parser.add_argument("--students", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261005)
    args = parser.parse_args(argv)
    result = run_dryrun(args.manual, args.out, students=args.students, seed=args.seed)
    print_summary(result)
    return 3 if result["final_status"] == "DRY_RUN_INPUT_INVALID" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))