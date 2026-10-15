"""Task Quality Gate Contract Tests（§31 C1–C12 + §33 哨兵 + §37 核心 Case）。

核心验收（§37/§48）：学生 reference 与声明答案**同时写错且彼此一致**时，
Blind Independent Solver 必须发现分歧，Gate 不得 PASS。
全部离线：LLM 用注入 Fake（只记录 prompt / 返回脚本化响应），§38 要求
Fake 只替代网络返回，不绕过 BlindTaskView / prompt builder / 序列化路径。
"""
from __future__ import annotations

import json
from pathlib import Path


from codesetarena.task_quality import (
    GATE_INFRASTRUCTURE_FAILURE,
    GateConfig,
    audit_leaks,
    audit_sandbox_files,
    build_blind_task_view,
    run_task_quality_gate,
)
from codesetarena.task_quality.blind_view import serialize_blind_prompt

DATA_CSV = "region,active,amount\nA,Y,30\nB,Y,40\nA,N,30\n"

ANSWER_SENTINEL = "SECRET_GROUND_TRUTH_7F92"
REF_SENTINEL = "STUDENT_REFERENCE_SENTINEL_91AB"
RESULT_SENTINEL = 4242

GOOD_CODE = (
    "import pandas as pd\n\n"
    "def reference(df):\n"
    "    return int(df.loc[df[\"active\"] == \"Y\", \"amount\"].sum())\n"
)
# 学生 reference 忘记 active 过滤（漏条件）→ 100
WRONG_CODE = (
    "def reference(df):\n"
    "    return int(df[\"amount\"].sum())\n"
)
LOOP_CODE = "def reference(df):\n    while True:\n        pass\n"


class FakeLLM:
    """记录全部 prompt（§32 审计对象=实际发送文本）；按 pass 脚本化返回。"""

    def __init__(self, solver_responses, adjudicator_response=None, error=None):
        self.prompts: list[str] = []
        self._solver = list(solver_responses)
        self._adjudicator = adjudicator_response
        self._error = error

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        if "EVIDENCE_JSON" in prompt:
            assert self._adjudicator is not None, "unexpected adjudication call"
            return self._adjudicator
        assert self._solver, "unexpected extra solver call"
        return self._solver.pop(0)


def solver_json(code, status="SOLVED", confidence=0.93,
                interpretation="sum amount where active == Y"):
    return json.dumps({
        "status": status,
        "interpretation": interpretation,
        "assumptions": [],
        "ambiguities": [],
        "data_sufficiency": "SUFFICIENT",
        "algorithm_summary": "filter active then sum",
        "generated_reference_code": code,
        "confidence": confidence,
    }, ensure_ascii=False)


ADJ_BUG = json.dumps({
    "category": "likely_student_reference_bug", "confidence": 0.88,
    "explanation": "student reference misses the active filter",
    "evidence": ["data has active column", "question requires active == Y"]})


def make_task_ws(root: Path, *, ref_code: str, declared=None, data=DATA_CSV) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": "T_GATE_001",
        "description": "统计 active 为 Y 的行 amount 总和",
        "task_statement": "data.csv 含 region、active、amount 三列。"
                          "请只统计 active == \"Y\" 的行，输出 amount 总和（整数）。",
        "student_id": "20260001",
        "hypothesized_failure_mode": "FM06_AGGREGATION",
        "answer_type": "integer",
    }
    if declared is not None:
        task["declared_answer"] = declared
    (root / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (root / "data.csv").write_text(data, encoding="utf-8")
    (root / "reference.py").write_text(ref_code, encoding="utf-8")
    (root / "agent.py").write_text(
        "import json\n\n\ndef run(task, df, llm):\n"
        "    return json.dumps({\"answer\": int(df[\"amount\"].sum())})\n",
        encoding="utf-8")
    (root / ".env").write_text(
        "LLM_BASE_URL=http://localhost:9\nLLM_API_KEY=sk-dummy\nLLM_MODEL=dummy\n",
        encoding="utf-8")
    (root / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n",
                                       encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# §32/§33 Blindness 直接证明（对象级 + 实际序列化 prompt 级）
# ---------------------------------------------------------------------------


def test_c1_blind_view_and_prompt_contain_no_answer(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=617241)
    task = json.loads((ws / "task.json").read_text(encoding="utf-8"))
    view, _ = build_blind_task_view(task, ws / "data.csv")
    prompt = serialize_blind_prompt(view)
    leaks = audit_leaks(prompt, {"declared_answer": "617241",
                                 "answer_sentinel": ANSWER_SENTINEL})
    assert not any(leaks.values()), leaks
    assert "617241" not in prompt and ANSWER_SENTINEL not in prompt
    assert not hasattr(view, "declared_answer") and "declared_answer" not in view.to_payload()


def test_c2_blind_prompt_contains_no_reference_source(tmp_path):
    ws = make_task_ws(tmp_path / "ws",
                      ref_code=f"# {REF_SENTINEL}\n" + GOOD_CODE.replace("70", "70"))
    task = json.loads((ws / "task.json").read_text(encoding="utf-8"))
    view, _ = build_blind_task_view(task, ws / "data.csv")
    prompt = serialize_blind_prompt(view)
    assert REF_SENTINEL not in prompt
    assert "reference" not in view.to_payload()


def test_c3_blind_prompt_contains_no_student_reference_result(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE.replace(
        "sum())", "sum()) + 0  # result sentinel"))
    task = json.loads((ws / "task.json").read_text(encoding="utf-8"))
    view, _ = build_blind_task_view(task, ws / "data.csv")
    prompt = serialize_blind_prompt(view)
    assert str(RESULT_SENTINEL) not in prompt
    assert "reference result" not in prompt.lower()


def test_sandbox_filesystem_audit(tmp_path):
    from codesetarena.task_quality.sandbox import create_blind_sandbox

    (tmp_path / "data.csv").write_text(DATA_CSV, encoding="utf-8")
    sandbox = create_blind_sandbox(tmp_path, data_file=tmp_path / "data.csv",
                                   code=GOOD_CODE)
    assert audit_sandbox_files(sandbox) == []
    (sandbox / "reference.py").write_text("evil", encoding="utf-8")
    assert audit_sandbox_files(sandbox) == ["reference.py"]


# ---------------------------------------------------------------------------
# C4-C8 对账矩阵
# ---------------------------------------------------------------------------


def test_c5_strong_agreement_passes(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    fake = FakeLLM(solver_responses=[solver_json(GOOD_CODE)])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "PASS", result["report"]["category"]
    assert result["category"] == "STRONG_AGREEMENT"
    assert result["report"]["comparison"] == {
        "student_ref_vs_answer": True, "independent_vs_answer": True,
        "student_ref_vs_independent": True}
    # 证据已落盘（§23）：独立参考代码可审计
    evidence = Path(result["report_path"]).parent
    assert (evidence / "independent_reference.py").is_file()


def test_c6_reference_answer_mismatch_is_deterministic_fail(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=71)
    fake = FakeLLM(solver_responses=[])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "FAIL"
    assert result["category"] == "REFERENCE_ANSWER_MISMATCH"
    assert fake.prompts == [], "确定性 FAIL 不得花费 LLM Token（§5）"


def test_c7_wrong_reference_and_wrong_answer_never_pass(tmp_path):
    """§37 核心验收：reference 与 answer 同时错且一致 → Blind 发现分歧 → Gate != PASS。"""
    ws = make_task_ws(tmp_path / "ws", ref_code=WRONG_CODE, declared=100)
    fake = FakeLLM(solver_responses=[solver_json(GOOD_CODE)],
                   adjudicator_response=ADJ_BUG)
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] != "PASS"
    assert result["final_status"] == "FAIL"
    assert result["category"] == "INDEPENDENT_DISAGREEMENT"
    assert result["report"]["comparison"]["student_ref_vs_independent"] is False
    assert result["report"]["adjudication"]["category"] == "likely_student_reference_bug"
    # 学生反馈：中文一段话、包含两方数字、不暴露内部 prompt
    feedback = result["student_feedback"]
    assert "70" in feedback and "100" in feedback and "reference.py" in feedback
    assert len(fake.prompts) == 2 and "EVIDENCE_JSON" in fake.prompts[1]


def test_c7b_unresolved_disagreement_goes_to_review(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=WRONG_CODE, declared=100)
    fake = FakeLLM(solver_responses=[solver_json(GOOD_CODE)],
                   adjudicator_response=json.dumps({
                       "category": "ambiguous_question", "confidence": 0.6,
                       "explanation": "tie semantics unclear", "evidence": []}))
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "REVIEW"
    assert result["report"]["adjudication"]["category"] == "ambiguous_question"


def test_c8_reference_equals_independent_but_answer_wrong_fails(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=999)
    fake = FakeLLM(solver_responses=[solver_json(GOOD_CODE)])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "FAIL"
    assert result["category"] == "REFERENCE_ANSWER_MISMATCH"


def test_reference_crash_is_hard_fail_without_llm(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code="def reference(df):\n    raise ValueError('boom')\n",
                      declared=70)
    fake = FakeLLM(solver_responses=[])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "FAIL"
    assert result["category"] == "REFERENCE_CRASH"
    assert fake.prompts == []


# ---------------------------------------------------------------------------
# C9-C11 Blind 状态与基础设施
# ---------------------------------------------------------------------------


def test_c9_ambiguous_solver_goes_to_review(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    fake = FakeLLM(solver_responses=[solver_json("", status="AMBIGUOUS",
                                                 interpretation="tie handling undefined")])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "REVIEW"
    assert result["category"] == "AMBIGUOUS"


def test_c10_insufficient_data_goes_to_review(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    fake = FakeLLM(solver_responses=[solver_json("", status="INSUFFICIENT_DATA",
                                                 interpretation="no timestamp column")])
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "REVIEW"
    assert result["category"] == "INSUFFICIENT_DATA"


def test_c11_llm_infrastructure_failure_is_not_student_fail(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    fake = FakeLLM(solver_responses=[], error=TimeoutError("model unavailable"))
    result = run_task_quality_gate(ws, llm=fake)
    assert result["final_status"] == "REVIEW"
    assert result["category"] == GATE_INFRASTRUCTURE_FAILURE
    assert "不计入" in result["student_feedback"]


def test_c12_generated_code_timeout_is_contained(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    fake = FakeLLM(solver_responses=[solver_json(LOOP_CODE), solver_json(LOOP_CODE)])
    result = run_task_quality_gate(ws, llm=fake, config=GateConfig(
        max_generation_attempts=2, reference_timeout_seconds=0.5))
    assert result["final_status"] == "REVIEW"
    assert result["category"] == GATE_INFRASTRUCTURE_FAILURE
    assert "timeout" in json.dumps(result["report"]["independent_reference"])


# ---------------------------------------------------------------------------
# §48 最终验收证据（真实执行链）
# ---------------------------------------------------------------------------


def test_acceptance_evidence_block(tmp_path):
    ws = make_task_ws(tmp_path / "ws", ref_code=WRONG_CODE, declared=100)
    fake = FakeLLM(solver_responses=[solver_json(GOOD_CODE)],
                   adjudicator_response=ADJ_BUG)
    result = run_task_quality_gate(ws, llm=fake)
    report = result["report"]
    blind_audit = report["blind_review"]["leak_audit"]
    assert list(blind_audit.values()).count(True) == 0, blind_audit
    assert report["independent_reference"]["execution_status"] == "ok"
    assert report["independent_reference"]["normalized_result"] == "70"
    assert report["final_status"] != "PASS"
    evidence = Path(result["report_path"]).parent
    code = (evidence / "independent_reference.py").read_text(encoding="utf-8")
    assert "reference(df)" in code
    # 哨兵双保险：answer / reference sentinel 未出现在任何实际发送 prompt
    assert not any(ANSWER_SENTINEL in p or REF_SENTINEL in p for p in fake.prompts)


def test_cli_gate_uses_real_request_path_and_never_fails_student_on_llm(tmp_path):
    """§38：CLI 走真实 request 构建路径（mock:// 只替代网络）；LLM 不可用 ⇒ REVIEW/infra，
    绝不记为学生 FAIL（§28）。"""
    from bigdata_v10110_helpers import cli

    ws = make_task_ws(tmp_path / "ws", ref_code=GOOD_CODE, declared=70)
    (ws / ".env").write_text(
        "LLM_BASE_URL=mock://offline\nLLM_API_KEY=sk-dummy\nLLM_MODEL=dummy\n",
        encoding="utf-8")
    json_out = tmp_path / "gate_report.json"
    code, out = cli(["task-quality-gate", "--workspace", str(ws),
                     "--output", str(tmp_path / "evidence"),
                     "--json-output", str(json_out)], cwd=tmp_path)
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["final_status"] == "REVIEW"
    assert report["category"] == GATE_INFRASTRUCTURE_FAILURE
    assert code == 2
    assert "不计入" in report["student_feedback"]
