"""Run / Export 双入口 MVP 验收测试 — 全离线（LLM 一律注入 FakeLLM）。

覆盖：Agent（单步/多轮/错误恢复/非法 JSON / 非法 action/危险代码、
解析失败自修复）、Validate 与 Build 的 Run/Export 复用、Run 编排（校验失败
即停、df 隔离、run artifacts）、Export（Evaluation 门禁、Package）、LLM
client、CLI、agent.py 契约可替换（v1/v2）。Budget 专项在 tests/test_budget.py。
旧 Runtime 套件 tests/test_runtime_mvp.py 保留不动。
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from codesetarena.main import build as build_module
from codesetarena.main import export as export_module
from codesetarena.main import runner as runner_module
from codesetarena.main.budget import BudgetExceeded
from codesetarena.main.build import build_project
from codesetarena.main.export import export_project
from codesetarena.main.llm import LLMClient, make_llm
from codesetarena.main.project import load_project
from codesetarena.main.runner import run_project
from codesetarena.main.validation import validate_project

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "src/codesetarena/main/agent_template.py"

DATA = "region,amount\neast,10\nwest,20\neast,5\n"
REFERENCE = 'def reference(df):\n    return int(df["amount"].sum())\n'
ENV_TEXT = (
    "LLM_API_KEY=dummy-key\n"
    "LLM_BASE_URL=https://example.invalid/v1\n"
    "LLM_MODEL=dummy-model\n"
    "LLM_TIMEOUT=60\n"
    "LLM_TOKEN_BUDGET=10000\n"
)


def load_agent_module():
    spec = importlib.util.spec_from_file_location("agent_under_test", TEMPLATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLLM:
    """按脚本回放 assistant 文本；记录 calls 与最近一次 messages。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.last_messages: list[dict] = []

    def complete(self, messages):
        self.calls += 1
        self.last_messages = messages
        return self._responses.pop(0)


class BudgetFakeLLM(FakeLLM):
    """模块顶层可 pickle；BudgetExceeded 属工程级 Budget 终止，不是 Observation。"""

    def complete(self, messages):
        raise BudgetExceeded("token budget 10000 exhausted")


def pandas_action(code: str) -> str:
    return json.dumps({"action": "execute_pandas", "code": code}, ensure_ascii=False)


def final_action(answer) -> str:
    return json.dumps({"action": "final_answer", "answer": answer}, ensure_ascii=False)


def make_workspace(
    tmp_path: Path,
    *,
    task: dict | None = None,
    data: str = DATA,
    reference: str = REFERENCE,
    env: str = ENV_TEXT,
    omit: tuple[str, ...] = (),
) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    files = {
        ".env": env,
        ".env.example": "LLM_API_KEY=\nLLM_BASE_URL=\nLLM_MODEL=\n",
        "agent.py": TEMPLATE_PATH.read_text(encoding="utf-8"),
        "task.json": json.dumps(
            task or {"task_id": "T001", "description": "销售额求和"}, ensure_ascii=False
        ),
        "data.csv": data,
        "reference.py": reference,
    }
    for name, content in files.items():
        if name not in omit:
            (ws / name).write_text(content, encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# Phase 1：Agent（mock LLM）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_mod():
    return load_agent_module()


def test_agent_single_step_final_answer(agent_mod):
    import pandas as pd

    llm = FakeLLM([final_action("A")])
    assert agent_mod.run({"task_id": "T"}, pd.DataFrame({"x": [1]}), llm) == "A"
    assert llm.calls == 1


def test_agent_execute_then_final(agent_mod):
    import pandas as pd

    df = pd.DataFrame({"region": ["east", "west"], "amount": [1, 2]})
    llm = FakeLLM([pandas_action("print(df.head())"), final_action("west")])
    assert agent_mod.run({"task_id": "T"}, df, llm) == "west"
    assert llm.last_messages[-2]["role"] == "assistant"
    observation = llm.last_messages[-1]["content"]
    assert observation.startswith("Observation:")
    assert "west" in observation


def test_agent_expression_result_in_observation(agent_mod):
    import pandas as pd

    df = pd.DataFrame({"x": [1, 2, 3]})
    observation = agent_mod.execute_pandas("df['x'].sum()", df)
    assert observation["ok"] is True
    assert "6" in observation["output"]  # pandas 3.x repr 为 np.int64(6)


def test_agent_multi_round_pandas(agent_mod):
    import pandas as pd

    df = pd.DataFrame({"region": ["east", "west", "east"], "amount": [10, 20, 5]})
    llm = FakeLLM(
        [
            pandas_action("print(df.columns)"),
            pandas_action("print(df.groupby('region')['amount'].sum())"),
            pandas_action("print(df[df['amount'] > 5])"),
            final_action("west"),
        ]
    )
    assert agent_mod.run({"task_id": "T"}, df, llm) == "west"
    assert llm.calls == 4


def test_agent_pandas_error_recovery(agent_mod):
    import pandas as pd

    df = pd.DataFrame({"region": ["east"], "amount": [10]})
    llm = FakeLLM(
        [
            pandas_action("print(df['wrong_column'].sum())"),
            pandas_action("print(df['amount'].sum())"),
            final_action("10"),
        ]
    )
    assert agent_mod.run({"task_id": "T"}, df, llm) == "10"
    joined = "\n".join(str(m["content"]) for m in llm.last_messages)
    assert "Observation (error):" in joined
    assert "KeyError" in joined


def test_agent_invalid_json_observation(agent_mod):
    result = agent_mod.parse_action("this is not json at all")
    assert result["ok"] is False
    assert "Invalid JSON Action" in result["error"]


def test_agent_markdown_fence_rejected_without_cleanup(agent_mod):
    """严格 JSON：不再做 Markdown fence 清洗 / 正则猜测，fence 输入判为解析失败。"""

    raw = "```json\n" + json.dumps({"action": "final_answer", "answer": "ok"}) + "\n```"
    result = agent_mod.parse_action(raw)
    assert result["ok"] is False
    assert "Invalid JSON Action" in result["error"]


def test_agent_invalid_action_observation(agent_mod):
    result = agent_mod.parse_action('{"action": "delete_file", "path": ".env"}')
    assert result["ok"] is False
    assert "Invalid action" in result["error"]
    result = agent_mod.parse_action('{"action": "execute_pandas"}')
    assert result["ok"] is False
    assert "Missing or invalid 'code'" in result["error"]
    result = agent_mod.parse_action('{"action": "final_answer"}')
    assert result["ok"] is False
    assert "Missing or invalid 'answer'" in result["error"]


def test_agent_parse_error_recovery(agent_mod):
    """解析失败不抛异常：原样回显 assistant 并以 Observation 反馈后继续。"""

    import pandas as pd

    df = pd.DataFrame({"x": [1]})
    llm = FakeLLM(["garbage { not json", final_action("A")])
    assert agent_mod.run({"task_id": "T"}, df, llm) == "A"
    assert llm.calls == 2
    assert llm.last_messages[-2]["role"] == "assistant"
    assert llm.last_messages[-2]["content"] == "garbage { not json"
    assert llm.last_messages[-1]["role"] == "user"
    assert "Observation (error):" in llm.last_messages[-1]["content"]


def test_agent_dangerous_code_rejected(agent_mod):
    import pandas as pd

    df = pd.DataFrame({"x": [1]})
    for code in [
        'open(".env").read()',
        "import os",
        "from subprocess import run",
        "eval('1+1')",
        "exec('x=1')",
    ]:
        observation = agent_mod.execute_pandas(code, df)
        assert observation["ok"] is False, code
        assert "ForbiddenCode" in observation["error"], code


def test_agent_signature_has_no_resource_budget(agent_mod):
    """Case 4：agent.py 签名恰好是 (task, df, llm)，无任何步数/资源预算。"""

    import inspect

    assert list(inspect.signature(agent_mod.run).parameters) == ["task", "df", "llm"]
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    for banned in (
        "max_steps",
        "DEFAULT_MAX_STEPS",
        "AgentMaxStepsExceeded",
        "InvalidAgentAction",
        "InvalidAgentJSON",
        "ForbiddenCodeError",
        "_arm_exec_timeout",
        "_disarm_exec_timeout",
        "_EXEC_TIMEOUT_SECONDS",
        "import signal",
    ):
        assert banned not in source, banned
    assert not hasattr(agent_mod, "InvalidAgentJSON")
    assert not hasattr(agent_mod, "InvalidAgentAction")
    assert not hasattr(agent_mod, "ForbiddenCodeError")


# ---------------------------------------------------------------------------
# Validate / Build：Run 与 Export 的公共能力（Case 9 / Case 10）
# ---------------------------------------------------------------------------


def test_run_and_export_share_validate_and_build():
    assert runner_module.validate_project is export_module.validate_project is validate_project
    assert runner_module.build_project is export_module.build_project is build_project
    assert "validate_for_run" not in dir(build_module)
    assert "build_for_export" not in dir(build_module)


def test_validate_pass(tmp_path: Path):
    assert validate_project(load_project(make_workspace(tmp_path))) == []


@pytest.mark.parametrize(
    "missing", [".env", ".env.example", "agent.py", "task.json", "data.csv", "reference.py"]
)
def test_validate_fails_on_missing_file(tmp_path: Path, missing: str):
    ws = make_workspace(tmp_path, omit=(missing,))
    errors = validate_project(load_project(ws))
    assert any(missing in e for e in errors)


def test_validate_fails_on_env_missing_key(tmp_path: Path):
    ws = make_workspace(tmp_path, env=ENV_TEXT.replace("LLM_API_KEY=dummy-key\n", ""))
    errors = validate_project(load_project(ws))
    assert any("LLM_API_KEY" in e for e in errors)


def test_validate_fails_on_bad_task_json(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "task.json").write_text("[1, 2, 3]", encoding="utf-8")
    errors = validate_project(load_project(ws))
    assert any("JSON 对象" in e for e in errors)


def test_validate_fails_on_agent_without_run(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "agent.py").write_text("x = 1\n", encoding="utf-8")
    errors = validate_project(load_project(ws))
    assert any("run(task, df, llm)" in e for e in errors)


def test_validate_rejects_data_rows_at_limit(tmp_path: Path):
    """data.csv 行数 >= 10000 拒绝（与旧 input file '<10,000 lines' 一致）。"""

    ws = make_workspace(tmp_path)
    (ws / "data.csv").write_text(
        "value\n" + "".join(f"{i}\n" for i in range(1, 10001)), encoding="utf-8"
    )
    errors = validate_project(load_project(ws))
    assert any("行数" in e and "10000" in e for e in errors)


def test_validate_accepts_data_rows_just_below_limit(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "data.csv").write_text(
        "value\n" + "".join(f"{i}\n" for i in range(1, 9999)), encoding="utf-8"
    )
    assert validate_project(load_project(ws)) == []


def test_validate_rejects_data_columns_above_limit(tmp_path: Path):
    """data.csv 列数 > 100 拒绝（对齐 acceptance 最宽 100 列）。"""

    ws = make_workspace(tmp_path)
    header = ",".join(f"c{i}" for i in range(101))
    (ws / "data.csv").write_text(header + "\n" + ",".join(["1"] * 101) + "\n",
                                 encoding="utf-8")
    errors = validate_project(load_project(ws))
    assert any("列数" in e and "100" in e for e in errors)


def test_validate_accepts_data_columns_at_limit(tmp_path: Path):
    ws = make_workspace(tmp_path)
    header = ",".join(f"c{i}" for i in range(100))
    (ws / "data.csv").write_text(header + "\n" + ",".join(["1"] * 100) + "\n",
                                 encoding="utf-8")
    assert validate_project(load_project(ws)) == []


def test_validate_rejects_data_size_above_limit(tmp_path: Path):
    """data.csv 文件大小 > 1 MB 拒绝。"""

    ws = make_workspace(tmp_path)
    row = ",".join(["1"] * 100) + "\n"
    payload = "a,b\n" + (row * 8000)  # ~1.6MB
    (ws / "data.csv").write_text(payload, encoding="utf-8")
    assert (ws / "data.csv").stat().st_size > 1024 * 1024
    errors = validate_project(load_project(ws))
    assert any("大小" in e and "1048576" in e for e in errors)


def test_validate_rejects_step_budget_parameter(tmp_path: Path):
    """资源预算参数（max_steps 等）不允许回到 agent.run 签名。"""

    ws = make_workspace(tmp_path)
    (ws / "agent.py").write_text(
        "def run(task, df, llm, max_steps=10):\n    return None\n", encoding="utf-8"
    )
    errors = validate_project(load_project(ws))
    assert any("恰好是 (task, df, llm)" in e for e in errors)


def test_validate_fails_on_reference_import(tmp_path: Path):
    ws = make_workspace(tmp_path, reference="import os\n\ndef reference(df):\n    return 0\n")
    errors = validate_project(load_project(ws))
    assert any("只允许 import pandas" in e for e in errors)


def test_build_produces_artifact_and_workspace_files(tmp_path: Path):
    from codesetarena import policy_loader

    ws = make_workspace(tmp_path)
    artifact = build_project(load_project(ws))
    # Configuration / Policy Separation：timeout / token budget 来自
    # policy/runtime_policy.json（.env 即使写了 60 也一律忽略）；
    # model 自 v10.1.13 起由学生 .env 的 LLM_MODEL 提供（课程 policy 无默认模型）。
    policy = policy_loader.runtime_policy()
    assert artifact.config == {
        "model": "dummy-model",
        "base_url": "https://example.invalid/v1",
        "llm_timeout": policy["llm"]["request_timeout_seconds"],
        "token_budget": policy["llm"]["token_budget_per_execution"],
    }
    assert artifact.task["task_id"] == "T001"
    assert set(artifact.files) == {
        ".env", ".env.example", "agent.py", "task.json", "data.csv", "reference.py"
    }
    serialized = json.dumps(artifact.config) + json.dumps(artifact.task) + json.dumps(artifact.files)
    assert "dummy-key" not in serialized
    assert (ws / "build_manifest.json").is_file()
    metadata = json.loads((ws / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["task_id"] == "T001"
    assert metadata["build_status"] == "passed"


# ---------------------------------------------------------------------------
# Run 编排：Load → Validate → Build → Execute（Case 5 / Case 6）
# ---------------------------------------------------------------------------


def test_run_validate_failure_stops_before_build_and_execute(tmp_path: Path):
    ws = make_workspace(tmp_path, omit=("reference.py",))
    with pytest.raises(Exception) as excinfo:
        run_project(ws, llm=FakeLLM([final_action(35)]))
    assert "校验失败" in str(excinfo.value)
    assert not (ws / "runs").is_dir()
    assert not (ws / "build_manifest.json").is_file()
    assert not (ws / "metadata.json").is_file()


def test_run_does_not_require_prebuilt_gate(tmp_path: Path):
    """每次 Run 都重新 Validate + Build，不依赖任何历史构建结果。"""

    ws = make_workspace(tmp_path)
    record = run_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["status"] == "success"


def test_replace_agent_py_without_framework_change(tmp_path: Path):
    """替换 workspace/agent.py（同为 run(task, df, llm) 契约），外围零修改。"""

    ws = make_workspace(tmp_path)
    (ws / "agent.py").write_text('def run(task, df, llm):\n    return "v1"\n', encoding="utf-8")
    assert run_project(ws, llm=FakeLLM([]))["result"]["result"] == "v1"
    (ws / "agent.py").write_text('def run(task, df, llm):\n    return "v2"\n', encoding="utf-8")
    assert run_project(ws, llm=FakeLLM([]))["result"]["result"] == "v2"


def test_run_success_and_artifacts(tmp_path: Path):
    ws = make_workspace(tmp_path)
    record = run_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["status"] == "success"
    result = record["result"]
    assert result["result"] == 35  # Agent 执行结果定义为 result
    assert result["declared_gold_answer"] == 35  # reference 执行结果
    assert result["correct"] is True
    run_dir = Path(record["run_dir"])
    assert (run_dir / "config.yaml").is_file()
    assert (run_dir / "result.json").is_file()
    assert not (run_dir / "metadata.json").is_file()  # metadata.json 属于 workspace，不属于 Run
    assert json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["run_id"]
    config_text = (run_dir / "config.yaml").read_text(encoding="utf-8")
    assert "model:" in config_text and "token_budget: 10000" in config_text
    assert "max_steps" not in config_text
    artifacts = "\n".join(p.read_text(encoding="utf-8") for p in run_dir.iterdir())
    assert "dummy-key" not in artifacts and "api_key" not in artifacts


def test_agent_df_mutation_does_not_affect_gold(tmp_path: Path):
    ws = make_workspace(tmp_path)
    llm = FakeLLM([pandas_action("df['amount'] = 0"), final_action(0)])
    record = run_project(ws, llm=llm)
    assert record["result"]["result"] == 0
    assert record["result"]["declared_gold_answer"] == 35
    assert record["result"]["correct"] is False


def test_consecutive_runs_do_not_overwrite(tmp_path: Path):
    ws = make_workspace(tmp_path)
    first = run_project(ws, llm=FakeLLM([final_action(35)]))
    second = run_project(ws, llm=FakeLLM([final_action(35)]))
    assert first["run_id"] != second["run_id"]
    assert Path(first["run_dir"]).is_dir() and Path(second["run_dir"]).is_dir()


def test_agent_failure_maps_to_record(tmp_path: Path):
    ws = make_workspace(tmp_path)
    record = run_project(ws, llm=BudgetFakeLLM([]))
    assert record["status"] == "internal_error"
    assert record["outcome_type"] == "internal_error"
    assert record["internal_error_category"] == "token_budget"
    assert record["correct"] is None
    assert record["failure_stage"] == "agent"
    assert "token_budget_exceeded" in record["error"]
    assert record["result"]["result"] is None
    assert record["result"]["declared_gold_answer"] is None


def test_answer_comparison_accepts_scalar_representation_diff(tmp_path: Path):
    """Agent int 103 与 reference str "103" 语义一致，判 correct。"""

    ws = make_workspace(
        tmp_path,
        reference='def reference(df):\n    return str(int(df["amount"].sum()))\n',
    )
    record = run_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["result"]["result"] == 35
    assert record["result"]["declared_gold_answer"] == "35"
    assert record["result"]["correct"] is True


def test_answer_comparison_rejects_different_values(tmp_path: Path):
    ws = make_workspace(
        tmp_path,
        reference='def reference(df):\n    return str(int(df["amount"].sum()))\n',
    )
    record = run_project(ws, llm=FakeLLM([final_action(36)]))
    assert record["result"]["correct"] is False


def test_reference_timeout_maps_to_failure(tmp_path: Path):
    ws = make_workspace(
        tmp_path, reference="def reference(df):\n    while True:\n        pass\n"
    )
    record = run_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["status"] == "internal_error"
    assert record["failure_stage"] == "reference"
    assert record["internal_error_category"] == "reference_error"
    assert record["termination_source"] == "runner"
    assert "Timeout" in record["error"]


def test_reference_uses_independent_original_df(tmp_path: Path):
    ws = make_workspace(
        tmp_path,
        reference='def reference(df):\n    return str(df.groupby("region")["amount"].sum().idxmax())\n',
    )
    llm = FakeLLM([pandas_action("df['amount'] = 0"), final_action("0")])
    record = run_project(ws, llm=llm)
    assert record["result"]["result"] == "0"
    assert record["result"]["declared_gold_answer"] == "west"


def test_system_profile_in_workspace_metadata(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "元数据.json").write_text(
        json.dumps({"system_number": "4725200597", "system_name": "炘盛"}, ensure_ascii=False),
        encoding="utf-8",
    )
    run_project(ws, llm=FakeLLM([final_action(35)]))
    metadata = json.loads((ws / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["system"]["system_number"] == "4725200597"


# ---------------------------------------------------------------------------
# Export 编排：Load → Validate → Build → Evaluate → Package（Case 7 / Case 8）
# ---------------------------------------------------------------------------


def test_export_validate_failure_stops_before_build_and_package(tmp_path: Path):
    ws = make_workspace(tmp_path, omit=("agent.py",))
    with pytest.raises(Exception) as excinfo:
        export_project(ws, llm=FakeLLM([final_action(35)]))
    assert "校验失败" in str(excinfo.value)
    assert not (ws / "export").is_dir()
    assert not (ws / "build_manifest.json").is_file()


def test_export_success_packages_without_run_dir(tmp_path: Path):
    ws = make_workspace(tmp_path)
    record = export_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["status"] == "exported"
    package_path = Path(record["package_path"])
    assert package_path.is_file()
    with zipfile.ZipFile(package_path) as bundle:
        names = set(bundle.namelist())
    assert {".env.example", "agent.py", "task.json", "data.csv", "reference.py", "metadata.json",
            "export_manifest.json"} <= names
    assert ".env" not in names  # 机密文件永不打包
    assert "runs" not in names
    with zipfile.ZipFile(package_path) as bundle:
        manifest = json.loads(bundle.read("export_manifest.json").decode("utf-8"))
    assert manifest["evaluation"]["correct"] is True
    assert not (ws / "runs").is_dir()  # Export 不产生普通 Run 目录


def test_export_evaluation_failure_blocks_package(tmp_path: Path):
    ws = make_workspace(tmp_path)
    record = export_project(ws, llm=FakeLLM([final_action(999)]))
    assert record["status"] == "evaluation_failed"
    assert record["package_path"] is None
    assert not (ws / "export").is_dir()


def test_export_agent_failure_blocks_package(tmp_path: Path):
    ws = make_workspace(tmp_path)
    record = export_project(ws, llm=BudgetFakeLLM([]))
    assert record["status"] == "evaluation_failed"
    assert record["package_path"] is None


def test_export_uses_same_execution_as_run(tmp_path: Path):
    """Export 的 Evaluation 与普通 Run 调用同一个 execute_project 实现。"""

    assert export_module.execute_project is runner_module.execute_project


def test_export_generates_and_persists_task_id(tmp_path: Path):
    """Export 固化 Task_ID：缺失时生成 T_<uuid4hex> 写回 task.json，重复 Export 不更换。"""

    ws = make_workspace(tmp_path, task={"student_id": "272401", "description": "求和"})
    task_path = ws / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert "task_id" not in task  # 前置：无 task_id

    record = export_project(ws, llm=FakeLLM([final_action(35)]))
    assert record["status"] == "exported"
    first_id = json.loads(task_path.read_text(encoding="utf-8"))["task_id"]
    assert first_id.startswith("T_") and len(first_id) == 2 + 32

    # 幂等：第二次 Export 保留同一 ID（rebuild 不换 ID）
    export_project(ws, llm=FakeLLM([final_action(35)]))
    second_id = json.loads(task_path.read_text(encoding="utf-8"))["task_id"]
    assert second_id == first_id

    # 已有 ID 一律保留（不覆盖学生手写 ID）
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["task_id"] = "STU001"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    export_project(ws, llm=FakeLLM([final_action(35)]))
    assert json.loads(task_path.read_text(encoding="utf-8"))["task_id"] == "STU001"


def test_validate_rejects_illegal_hypothesized_failure_mode(tmp_path: Path):
    """hypothesized_failure_mode 若出现必须为正式 FM 全 key（只读校验，14 类全集）。"""

    ws = make_workspace(
        tmp_path,
        task={
            "task_id": "T001",
            "student_id": "272401",
            "hypothesized_failure_mode": "FM99_NOPE",
            "task_statement": "x",
        },
    )
    errors = validate_project(load_project(ws))
    assert any("hypothesized_failure_mode" in e for e in errors)

    # FM10/FM13 属于正式 14 类 taxonomy（Phase 1 正式枚举包含它们）
    ws2 = make_workspace(
        tmp_path / "ws2",
        task={
            "task_id": "T002",
            "student_id": "272402",
            "hypothesized_failure_mode": "FM13_NUMERICAL_PRECISION_ROUNDING",
            "task_statement": "x",
        },
    )
    assert validate_project(load_project(ws2)) == []


# ---------------------------------------------------------------------------
# Phase 2：LLM client（超时 / 错误映射 / 真实 usage 暴露；不打真实网络）
# ---------------------------------------------------------------------------


def test_llm_captures_provider_usage(monkeypatch):
    client = LLMClient(
        base_url="https://example.invalid/v1",
        api_key="k",
        model="m",
        timeout=5,
    )
    monkeypatch.setattr(
        client,
        "_post",
        lambda messages: ("ok", {"prompt_tokens": 12, "completion_tokens": 30}),
    )
    assert client.complete([{"role": "user", "content": "hello"}]) == "ok"
    assert client.last_usage == {"prompt_tokens": 12, "completion_tokens": 30}
    assert client.prompt_tokens == 12 and client.completion_tokens == 30
    assert client.calls == 1


def test_make_llm_builds_client_from_spec_or_returns_injected():
    spec = {
        "base_url": "https://example.invalid/v1",
        "api_key": "k",
        "model": "m",
        "timeout": 5,
    }
    client = make_llm(spec)
    assert isinstance(client, LLMClient)
    assert client.base_url == "https://example.invalid/v1"
    marker = object()
    assert make_llm({"llm": marker}) is marker


def test_llm_timeout_deadline(monkeypatch):
    import time

    from codesetarena.main.llm import LLMTimeout

    client = LLMClient(
        base_url="http://127.0.0.1:9/v1",
        api_key="k",
        model="m",
        timeout=1,
    )

    def slow_post(messages):
        time.sleep(3)

    monkeypatch.setattr(client, "_post", slow_post)
    with pytest.raises(LLMTimeout):
        client.complete([{"role": "user", "content": "hello"}])


def test_llm_refused_endpoint_maps_to_llm_error():
    from codesetarena.main.llm import LLMError

    client = LLMClient(
        base_url="http://127.0.0.1:9/v1",
        api_key="k",
        model="m",
        timeout=5,
    )
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hello"}])


# ---------------------------------------------------------------------------
# CLI（run + export）
# ---------------------------------------------------------------------------


def test_cli_run(tmp_path: Path, capsys):
    from codesetarena.main.__main__ import main

    bad_ws = make_workspace(tmp_path / "bad", omit=("reference.py",))
    assert main(["run", "--workspace", str(bad_ws)]) == 2
    assert "校验失败" in capsys.readouterr().err

    # run：不可路由端点 → internal_error(llm_api)，流程与 artifacts 完整
    ws = make_workspace(
        tmp_path / "run",
        env=ENV_TEXT.replace("https://example.invalid/v1", "http://127.0.0.1:9/v1").replace(
            "LLM_TIMEOUT=60", "LLM_TIMEOUT=1"
        ),
    )
    assert main(["run", "--workspace", str(ws)]) == 1
    captured = capsys.readouterr().out
    assert "status=internal_error" in captured
    assert "internal_error=llm_api" in captured
    assert "artifacts ->" in captured


def test_cli_export(tmp_path: Path, capsys):
    from codesetarena.main.__main__ import main

    bad_ws = make_workspace(tmp_path / "bad", omit=("reference.py",))
    assert main(["export", "--workspace", str(bad_ws)]) == 2
    assert "校验失败" in capsys.readouterr().err

    # export：不可路由端点 → Evaluation 失败，禁止打包
    ws = make_workspace(
        tmp_path / "exp",
        env=ENV_TEXT.replace("https://example.invalid/v1", "http://127.0.0.1:9/v1").replace(
            "LLM_TIMEOUT=60", "LLM_TIMEOUT=1"
        ),
    )
    assert main(["export", "--workspace", str(ws)]) == 1
    captured = capsys.readouterr().out
    assert "status=evaluation_failed" in captured
