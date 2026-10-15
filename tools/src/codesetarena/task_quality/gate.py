"""TaskQualityGate Orchestrator（§40）：加载 → 确定性校验 → 学生 reference 执行 →
Blind 独立求解（代码级隔离 + 实际 prompt 泄漏审计）→ 沙箱真实执行 → 三方对账 →
分歧 adjudication → 最终 PASS/FAIL/REVIEW → 证据落盘 → 学生反馈。"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..main.project import load_project, parse_env_file
from ..main.runner import _reference_timeout, _require_jsonable, _run_reference
from ..main.validation import check_reference_source, validate_project
from ..policy_loader import runtime_policy
from .blind_view import audit_leaks, audit_sandbox_files, build_blind_task_view, serialize_blind_prompt
from .comparator import AdjudicationError, answers_equal, run_adjudication
from .feedback import build_student_feedback
from .models import (
    GATE_INFRASTRUCTURE_FAILURE,
    SOLVER_SOLVED,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_REVIEW,
    GateConfig,
)
from .sandbox import create_blind_sandbox, run_reference_in_sandbox
from .solver import BlindSolverError, repair_prompt, run_blind_solver


class TaskQualityGateInternalError(RuntimeError):
    """Gate 基础设施故障（§28）：不计为学生任务 FAIL。"""


def _default_llm(project):
    """生产默认适配器：workspace .env → RuntimeConfig → model_client 网关（复用，不建第二套）。"""

    from ..config import RuntimeConfig
    from ..model_client import managed_completion

    env = parse_env_file(project.file(".env"))
    base_url = env.get("LLM_BASE_URL", "").strip()
    api_key = env.get("LLM_API_KEY", "").strip()
    model = env.get("LLM_MODEL", "").strip()
    if not base_url or not model:
        raise TaskQualityGateInternalError(
            "workspace .env 缺少 LLM_BASE_URL / LLM_MODEL，Blind Solver 无法运行")
    config = RuntimeConfig(base_url=base_url, api_key=api_key,
                           models=[model], env_file=project.file(".env"))

    def llm(prompt: str) -> str:
        result = managed_completion(config=config, model=model, prompt=prompt)
        return result.content

    return llm, model


def _norm(value: Any) -> str:
    try:
        _require_jsonable(value, "result")
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def run_task_quality_gate(
    workspace: str | Path,
    *,
    llm: Callable[[str], str] | None = None,
    config: GateConfig | None = None,
    evidence_dir: str | Path | None = None,
) -> dict:
    """对单个任务 workspace 执行 Task Quality Gate。

    返回 {"final_status", "category", "student_feedback", "report", "report_path"}。
    final_status ∈ PASS / FAIL / REVIEW；基础设施故障归 REVIEW + GATE_INFRASTRUCTURE_FAILURE，
    绝不计为学生任务 FAIL（§28）。
    """

    cfg = config or GateConfig()
    workspace = Path(workspace)
    project = load_project(workspace)

    report: dict[str, Any] = {
        "gate_version": cfg.gate_version,
        "prompt_version": cfg.prompt_version,
        "adjudicator_prompt_version": cfg.adjudicator_prompt_version,
        "task_id": "",
        "submission_id": workspace.name,
        "deterministic_checks": {},
        "student_reference": {},
        "blind_review": {},
        "independent_reference": {},
        "comparison": {},
        "adjudication": {},
        "agent_admission": {},
        "final_status": STATUS_REVIEW,
        "student_feedback": "",
    }

    state: dict[str, Any] = {
        "declared_answer": None, "student_reference_result": None,
        "independent_result": None, "independent_code": "", "independent_exec": {},
    }

    def finish(status: str, category: str, feedback_detail: str = "",
               extra: dict | None = None) -> dict:
        report["final_status"] = status
        report["category"] = category
        report["student_feedback"] = build_student_feedback(
            status=status, category=category,
            declared_answer=_norm(state["declared_answer"]) if state["declared_answer"] is not None else "",
            student_reference_result=_norm(state["student_reference_result"]) if state["student_reference_result"] is not None else "",
            independent_result=_norm(state["independent_result"]) if state["independent_result"] is not None else "",
            detail=feedback_detail or category,
        )
        report.update(extra or {})
        path = _write_evidence(workspace, evidence_dir, report,
                               state["independent_code"], state["independent_exec"])
        report["report_path"] = str(path)
        return {"final_status": status, "category": category,
                "student_feedback": report["student_feedback"],
                "report": report, "report_path": str(path)}

    def infra_fail(detail: str) -> dict:
        return finish(STATUS_REVIEW, GATE_INFRASTRUCTURE_FAILURE, feedback_detail=detail)

    # ---- §5 第一阶段：确定性 Machine Checks（复用现有 Validator，含 agent admission） ----
    errors = validate_project(project)
    report["deterministic_checks"] = {"validate_project_errors": errors}
    report["agent_admission"] = {
        "checked_by": "validate_project (import + run(task, df, llm) signature)",
        "pass": not any("agent.py" in e for e in errors),
    }
    if errors:
        report["task_id"] = _load_task_id(project)
        agent_fail = any("agent.py" in e for e in errors)
        return finish(STATUS_FAIL,
                      "AGENT_ADMISSION_FAILED" if agent_fail else "WORKSPACE_VALIDATION_FAILED",
                      feedback_detail="; ".join(errors[:3]))

    task = json.loads(project.file("task.json").read_text(encoding="utf-8"))
    report["task_id"] = str(task.get("task_id") or "")
    state["declared_answer"] = task.get("declared_answer")  # 可选字段；存在才参与三角对账

    # ---- 学生 reference 真实执行（复用生产 reference runner + policy 超时） ----
    timeout = cfg.reference_timeout_seconds or _reference_timeout()
    # 沙箱子进程双层超时：业务时限（子进程内 alarm，导入后计时）+ 冷启动兼底
    # （policy ``main.boot_grace_seconds``，与 ``main/runner.py::_execution_budget`` 同源）。
    boot_grace = float((runtime_policy().get("main") or {}).get("boot_grace_seconds") or 0)
    try:
        df = pd.read_csv(project.file("data.csv"))
        state["student_reference_result"] = _run_reference(project.file("reference.py"), df)
    except Exception as exc:
        return finish(STATUS_FAIL, "REFERENCE_CRASH",
                      feedback_detail=f"{type(exc).__name__}: {exc}")

    # ---- Case B/D：声明答案 vs 学生 reference 结果（确定性硬证据） ----
    if state["declared_answer"] is not None:
        if not answers_equal(state["student_reference_result"], state["declared_answer"]):
            return finish(STATUS_FAIL, "REFERENCE_ANSWER_MISMATCH")

    # ---- §3 Blind Boundary：构造视图 + 序列化 + **实际 prompt** 泄漏审计 ----
    reference_source = project.file("reference.py").read_text(encoding="utf-8")
    view, _ = build_blind_task_view(task, project.file("data.csv"))
    prompt = serialize_blind_prompt(view)
    leaks = audit_leaks(prompt, {
        "declared_answer": repr(state["declared_answer"]) if state["declared_answer"] is not None else "",
        "reference_source": reference_source,
        "student_reference_result": _norm(state["student_reference_result"]),
    })
    report["blind_review"] = {
        "model": "injected-adapter",
        "leak_audit": leaks,
        "prompt_chars": len(prompt),
        "status": None, "ambiguities": [], "assumptions": [],
        "data_sufficiency": "", "interpretation": "", "confidence": None,
    }
    if any(leaks.values()):
        return infra_fail(f"blind prompt leak audit failed: {[k for k, v in leaks.items() if v]}")

    try:
        if llm is None:
            llm, model_id = _default_llm(project)
            report["blind_review"]["model"] = model_id
    except TaskQualityGateInternalError as exc:
        return infra_fail(str(exc))

    # ---- §6-§12 Blind 独立求解 + 生成代码真实执行（§29 有限修复，总尝试 ≤ max） ----
    solver_cfg = GateConfig(**{**asdict(cfg), "max_generation_attempts": 1})
    generation_log: list[dict] = []
    current_prompt = prompt
    last_problem = ""
    for attempt in range(1, max(cfg.max_generation_attempts, 1) + 1):
        try:
            verdict, solver_log = run_blind_solver(current_prompt, llm, config=solver_cfg)
        except TaskQualityGateInternalError as exc:
            return infra_fail(str(exc))
        except (BlindSolverError, Exception) as exc:
            return infra_fail(f"{type(exc).__name__}: {exc}")
        generation_log.extend(solver_log)
        report["blind_review"].update({
            "status": verdict["status"],
            "ambiguities": verdict["ambiguities"],
            "assumptions": verdict["assumptions"],
            "data_sufficiency": verdict["data_sufficiency"],
            "interpretation": verdict["interpretation"],
            "confidence": verdict["confidence"],
            "algorithm_summary": verdict["algorithm_summary"],
            "generation_attempts": attempt,
            "generation_log": generation_log,
        })
        if verdict["status"] != SOLVER_SOLVED:
            detail = (verdict["interpretation"] or "; ".join(verdict["ambiguities"]))[:200]
            return finish(STATUS_REVIEW, verdict["status"], feedback_detail=detail)

        state["independent_code"] = verdict["generated_reference_code"]
        report["independent_reference"] = {
            "source_sha256": hashlib.sha256(
                state["independent_code"].encode("utf-8")).hexdigest()}

        # 生成代码必须通过与学生 reference 相同的 guardrail（复用，非第二套规则）
        try:
            check_reference_source(state["independent_code"], cfg.max_reference_source_lines)
        except Exception as exc:
            last_problem = f"generated code guardrail rejected: {exc}"
            if attempt < cfg.max_generation_attempts:
                current_prompt = repair_prompt(prompt, "", last_problem)
                continue
            return infra_fail(last_problem)

        # 受控沙箱真实执行（隔离目录 / 超时 / 无密钥环境 / 文件系统审计）
        sandbox_root = Path(tempfile.mkdtemp(prefix="task_quality_gate_"))
        try:
            sandbox = create_blind_sandbox(sandbox_root, data_file=project.file("data.csv"),
                                           code=state["independent_code"])
            violations = audit_sandbox_files(sandbox)
            report["independent_reference"]["sandbox_audit"] = {"forbidden_files": violations}
            if violations:
                return infra_fail(f"blind sandbox contains forbidden files: {violations}")
            state["independent_exec"] = exec_result = run_reference_in_sandbox(
                sandbox, timeout_seconds=timeout, boot_grace_seconds=boot_grace)
        finally:
            shutil.rmtree(sandbox_root, ignore_errors=True)

        report["independent_reference"]["execution_status"] = exec_result.get("execution_status")
        if exec_result.get("execution_status") == "ok":
            state["independent_result"] = exec_result["result"]
            break
        last_problem = (f"generated reference execution "
                        f"{exec_result.get('execution_status')}: "
                        f"{exec_result.get('error') or exec_result.get('stderr', '')[:200]}")
        if attempt < cfg.max_generation_attempts:
            current_prompt = repair_prompt(prompt, "", last_problem)
    else:
        return infra_fail(last_problem)

    report["student_reference"] = {
        "execution_status": "ok",
        "normalized_result": _norm(state["student_reference_result"]),
    }
    report["independent_reference"]["normalized_result"] = _norm(state["independent_result"])

    # ---- §13-§15 三方对账（复用生产 comparator） ----
    report["comparison"] = {
        "student_ref_vs_answer": (
            None if state["declared_answer"] is None
            else answers_equal(state["student_reference_result"], state["declared_answer"])),
        "independent_vs_answer": (
            None if state["declared_answer"] is None
            else answers_equal(state["independent_result"], state["declared_answer"])),
        "student_ref_vs_independent": answers_equal(
            state["student_reference_result"], state["independent_result"]),
    }

    if report["comparison"]["student_ref_vs_independent"]:
        return finish(STATUS_PASS, "STRONG_AGREEMENT")

    # ---- §16/§17 第二阶段 Adjudication（Blind 已结束，此时才可见双方材料） ----
    try:
        adjudication = run_adjudication(
            question=str(task.get("task_statement") or task.get("description") or ""),
            data_summary=json.dumps(view.to_payload()["data_files"], ensure_ascii=False)[:2000],
            declared_answer=(_norm(state["declared_answer"])
                             if state["declared_answer"] is not None else "ABSENT"),
            student_reference_source=reference_source,
            student_reference_result=_norm(state["student_reference_result"]),
            independent_reference_source=state["independent_code"],
            independent_reference_result=_norm(state["independent_result"]),
            blind_interpretation=report["blind_review"].get("interpretation", ""),
            llm=llm, config=cfg)
    except TaskQualityGateInternalError as exc:
        adjudication = {"status": "ERROR", "category": "unresolved", "is_fail_category": False,
                        "explanation": f"gate internal: {exc}", "confidence": 0.0}
    except (AdjudicationError, Exception) as exc:
        adjudication = {"status": "ERROR", "category": "unresolved", "is_fail_category": False,
                        "explanation": f"{type(exc).__name__}: {exc}", "confidence": 0.0}
    report["adjudication"] = adjudication

    if adjudication.get("is_fail_category"):
        return finish(STATUS_FAIL, "INDEPENDENT_DISAGREEMENT",
                      feedback_detail=adjudication.get("category", ""))
    return finish(STATUS_REVIEW, "INDEPENDENT_DISAGREEMENT",
                  feedback_detail=adjudication.get("category", "unresolved"))


def _load_task_id(project) -> str:
    try:
        task = json.loads(project.file("task.json").read_text(encoding="utf-8"))
        return str(task.get("task_id") or "") if isinstance(task, dict) else ""
    except Exception:
        return ""


def _write_evidence(workspace: Path, evidence_dir, report: dict,
                    independent_code: str, independent_exec: dict) -> Path:
    """§22/§23：结构化报告 + 可审计证据（生成代码 / stdout / stderr / hash / 版本）。
    不写入 prompt 原文与任何 secret。"""

    root = Path(evidence_dir) if evidence_dir else workspace / "quality_gate"
    root.mkdir(parents=True, exist_ok=True)
    (root / "task_quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8")
    if independent_code:
        (root / "independent_reference.py").write_text(independent_code, encoding="utf-8")
        (root / "independent.stdout.txt").write_text(
            str(independent_exec.get("stdout", "")), encoding="utf-8")
        (root / "independent.stderr.txt").write_text(
            str(independent_exec.get("stderr", "")), encoding="utf-8")
    return root / "task_quality_report.json"
