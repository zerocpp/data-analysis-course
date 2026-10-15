"""Configuration / Runtime Policy Separation Tests（§31–§37/§42/§43/§45）。

核心不变量（Configuration Upgrade）：

1. ``.env.example`` = Connection Configuration Template，禁止出现任何 Policy 字段；
2. ``.env`` = 本地连接配置 / Secret；**不能覆盖** Runtime Policy
   （policy > environment，§6/§22）；
3. ``policy/runtime_policy.json`` 是 token budget / request timeout /
   wall time / memory / disk 的唯一权威来源；
4. Policy 损坏 = ``SystemConfigurationError``（SYSTEM_CONFIG_ERROR，退出码 2），
   不是学生提交错误；
5. ``result.json`` 记录非敏感 Policy Snapshot（§24/§35），绝不记录 API Key；
6. Submission tar.gz 不含 .env / .env.example / runtime_policy.json（§37）；
7. Secret 端到端零泄漏（§36）。

全部在 tmp_path 内完成：不依赖外部 corpus；Policy 注入通过 CSA_ROOT 指向
临时的 policy/ 副本（``policy_loader.project_root`` 尊重 CSA_ROOT）。
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from codesetarena import policy_loader
from codesetarena.main.build import build_project
from codesetarena.main.project import load_project
from codesetarena.main.runner import run_project

FAKE_SECRET = "TEST_FAKE_SECRET_12345"
ENV_POLICY_FIELDS = (
    "LLM_TIMEOUT", "LLM_TOKEN_BUDGET",
    "MAX_LLM_CALLS", "WALL_TIME_LIMIT", "EXECUTION_TIMEOUT", "MEMORY_LIMIT",
    "MEMORY_MB", "DISK_LIMIT", "DISK_MB", "CPU_LIMIT", "MAX_STEPS",
    "MAX_ITERATIONS", "MAX_OUTPUT_TOKENS", "TOTAL_TOKEN_BUDGET", "RUN_TIMEOUT",
)
DATA = "region,amount\neast,10\nwest,20\neast,5\n"
DIRECT_AGENT = 'def run(task, df, llm):\n    return int(df["amount"].sum())\n'
REFERENCE = 'def reference(df):\n    return int(df["amount"].sum())\n'
ROOT = Path(__file__).resolve().parents[1]
PROTO = "DataC1004_P1_273001.tar.gz"


def make_workspace(tmp_path: Path, *, env_text: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".env").write_text(env_text, encoding="utf-8")
    (ws / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\n", encoding="utf-8")
    (ws / "agent.py").write_text(DIRECT_AGENT, encoding="utf-8")
    (ws / "task.json").write_text(
        json.dumps({"task_id": "T001", "student_id": "273001",
                    "hypothesized_failure_mode": "FM02_SCHEMA",
                    "task_statement": "amount 求和"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (ws / "data.csv").write_text(DATA, encoding="utf-8")
    (ws / "reference.py").write_text(REFERENCE, encoding="utf-8")
    return ws


CONNECTION_ENV = f"LLM_BASE_URL=https://example.invalid/v1\nLLM_API_KEY={FAKE_SECRET}\n"


class _DirectLLM:
    """直接 Agent 不使用 llm；若被调用则立即失败（证明没走网络）。"""

    def complete(self, messages):
        raise AssertionError("direct agent 不应调用 llm")


def _run_workspace(ws: Path) -> dict:
    """执行一次 run，返回完整 result.json 记录（含 runtime_policy 快照）。"""

    run_project(ws, llm=_DirectLLM())
    run_dir = next((ws / "runs").iterdir())
    return json.loads((run_dir / "result.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# §34/§45-A：.env.example 不含 Policy 字段（ENV_EXAMPLE_POLICY_LEAK）
# ---------------------------------------------------------------------------


def test_env_example_has_no_policy_fields():
    """官方 .env.example 模板与《学生手册》不得出现任何 Runtime Policy 字段。"""

    shipped: list[Path] = []
    for base in (ROOT / "src", ROOT / "docs", ROOT / "tests"):
        shipped.extend(sorted(base.rglob(".env.example")))
    root_level = ROOT / ".env.example"
    if root_level.is_file():
        shipped.append(root_level)
    violations: list[str] = []
    for template in shipped:
        text = template.read_text(encoding="utf-8", errors="ignore")
        for field in ENV_POLICY_FIELDS:
            if field + "=" in text:
                violations.append(f"{template.relative_to(ROOT)}: {field}")

    # 学生手册是学生配置 .env 的唯一官方指引：不得出现"在 .env 里写
    # LLM_TIMEOUT / LLM_TOKEN_BUDGET="的配置指令（解释"不生效"的行除外，
    # 那些行以 > 或反引号引导，不是 env 条目）。
    manual = ROOT / "docs" / "学生手册_Phase1_Current.md"
    if manual.is_file():
        for lineno, line in enumerate(manual.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("LLM_TIMEOUT=", "LLM_TOKEN_BUDGET=")):
                violations.append(f"学生手册:{lineno}: {stripped}")
    assert violations == [], f"ENV_EXAMPLE_POLICY_LEAK: {violations}"

    if shipped:
        for template in shipped:
            assert "LLM_TIMEOUT=" not in template.read_text(encoding="utf-8")
            assert "LLM_TOKEN_BUDGET=" not in template.read_text(encoding="utf-8")


def test_matrix_env_example_template_clean(tmp_path):
    """Matrix 生成的 workspace .env.example 只含连接字段。"""

    from codesetarena.main.matrix import prepare_workspace  # noqa: F401 - 构造路径方式

    # 直接检查常量模板（prepare_workspace 依赖输入太多，此处以字符串模板为准）
    import inspect

    source = inspect.getsource(prepare_workspace)
    template = source.split(".env.example", 1)[1].split('"', 2)[2].split('"', 1)[0]
    for field in ENV_POLICY_FIELDS:
        assert field + "=" not in template, f"matrix 模板含 Policy 字段: {field}"


# ---------------------------------------------------------------------------
# §31/§32/§33/§45-B/C/D：Policy 才是 Source of Truth，env 不能覆盖
# ---------------------------------------------------------------------------


def test_env_cannot_override_token_budget_and_timeout(tmp_path):
    """.env 写 LLM_TOKEN_BUDGET=999999 / LLM_TIMEOUT=999999 -> Runtime 仍用 Policy。"""

    import warnings as _warnings

    ws = make_workspace(
        tmp_path,
        env_text=(CONNECTION_ENV + "LLM_MODEL=dummy\n"
                  "LLM_TOKEN_BUDGET=999999\nLLM_TIMEOUT=999999\n"),
    )
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        record = _run_workspace(ws)
    assert record["status"] == "success"
    snapshot = record["runtime_policy"]
    policy = policy_loader.runtime_policy()
    assert snapshot["llm"]["token_budget"] == \
        policy["llm"]["token_budget_per_execution"] == 10000
    assert snapshot["llm"]["request_timeout_seconds"] == \
        policy["llm"]["request_timeout_seconds"] == 75
    assert snapshot["llm"]["token_budget"] != 999999
    assert snapshot["llm"]["request_timeout_seconds"] != 999999
    # v10.1.12：模型不再属于 Policy —— snapshot 记录学生选择
    assert snapshot["llm"]["model_source"] == "student .env (LLM_MODEL)"

    artifact = build_project(load_project(ws))
    assert artifact.config["token_budget"] == 10000
    assert artifact.config["llm_timeout"] == 75
    # v10.1.12：模型属于学生 .env —— LLM_MODEL=dummy 即生效
    assert artifact.config["model"] == "dummy"


def test_env_without_policy_fields_still_runs(tmp_path):
    """.env 没有 LLM_TIMEOUT / LLM_TOKEN_BUDGET（Policy 字段）-> 正常取得 Policy；
    模型用学生配置的 LLM_MODEL。"""

    ws = make_workspace(tmp_path, env_text=CONNECTION_ENV + "LLM_MODEL=dummy-model\n")
    record = _run_workspace(ws)
    assert record["status"] == "success"
    assert record["runtime_policy"]["llm"]["token_budget"] == 10000
    assert record["runtime_policy"]["llm"]["request_timeout_seconds"] == 75

    config_yaml = (ws / "runs").glob("*/config.yaml")
    text = next(config_yaml).read_text(encoding="utf-8")
    assert "llm_timeout: 75" in text
    assert "token_budget: 10000" in text
    assert "model:" in text and "dummy-model" in text
    assert "api_key" not in text.lower()


def test_policy_change_is_source_of_truth(tmp_path, monkeypatch):
    """修改 policy/runtime_policy.json -> Runtime 真正随 Policy 变化（§45-D）。"""

    fake_root = tmp_path / "csa_root"
    (fake_root / "policy").mkdir(parents=True)
    modified = copy.deepcopy(policy_loader.runtime_policy())
    modified["llm"]["token_budget_per_execution"] = 500
    modified["llm"]["request_timeout_seconds"] = 30
    (fake_root / "policy" / "runtime_policy.json").write_text(
        json.dumps(modified, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("CSA_ROOT", str(fake_root))
    policy_loader.reload_policies()
    try:
        ws = make_workspace(tmp_path, env_text=CONNECTION_ENV + "LLM_MODEL=dummy-model\n")
        record = _run_workspace(ws)
    finally:
        policy_loader.reload_policies()  # 恢复真实 Policy 缓存
    assert record["runtime_policy"]["llm"]["token_budget"] == 500
    assert record["runtime_policy"]["llm"]["request_timeout_seconds"] == 30


# ---------------------------------------------------------------------------
# §42/§43：Malformed Policy = System Configuration Error（不是学生错误）
# ---------------------------------------------------------------------------


def test_malformed_policy_is_system_config_error(tmp_path, monkeypatch, capsys):
    from codesetarena.main.__main__ import main

    fake_root = tmp_path / "csa_root_bad"
    (fake_root / "policy").mkdir(parents=True)
    bad = copy.deepcopy(policy_loader.runtime_policy())
    bad["llm"]["token_budget_per_execution"] = -1  # 非法范围
    (fake_root / "policy" / "runtime_policy.json").write_text(
        json.dumps(bad, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("CSA_ROOT", str(fake_root))
    policy_loader.reload_policies()
    try:
        exit_code = main(["run", "--workspace", str(tmp_path)])  # workspace 不存在也无妨
    finally:
        policy_loader.reload_policies()
    assert exit_code == 2
    assert "SYSTEM_CONFIG_ERROR" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# §24/§25/§35：Policy Snapshot 记录；Secret 不泄漏
# ---------------------------------------------------------------------------


def test_run_artifacts_record_policy_snapshot_no_secret(tmp_path):
    ws = make_workspace(tmp_path, env_text=CONNECTION_ENV + "LLM_MODEL=dummy-model\n")
    record = _run_workspace(ws)
    assert record["runtime_policy"]["policy_version"] == \
        policy_loader.runtime_policy_version()
    run_dir = next((ws / "runs").iterdir())
    result_json = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert FAKE_SECRET not in json.dumps(result_json)
    assert "api_key" not in json.dumps(result_json)
    assert result_json["runtime_policy"]["llm"]["token_budget"] == 10000
    assert result_json["runtime_policy"]["llm"]["model"] == "dummy-model"


# ---------------------------------------------------------------------------
# §37/§45-E：Submission 边界 — tar.gz 不含 .env / .env.example / runtime_policy.json
# ---------------------------------------------------------------------------


def test_submission_archive_excludes_env_and_policy(tmp_path):
    from codesetarena.submission.local import build_submission

    ws = make_workspace(tmp_path, env_text=CONNECTION_ENV + "LLM_MODEL=dummy-model\n")
    out = tmp_path / PROTO
    record = build_submission(
        ws, phase="phase1", student_id="273001", output_path=out,
        archive_format="tar.gz",
    )
    assert record["exit_code"] == 0
    with tarfile.open(out, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert ".env" not in names
    assert ".env.example" not in names
    assert "runtime_policy.json" not in names
    assert "manifest.json" in names and "task/task.json" in names


def test_validate_rejects_env_in_archive_but_warns_env_example(tmp_path):
    """.env 手工加入 -> FAIL FORBIDDEN_FILE；.env.example -> WARNING 不阻断。

    （.env.example 为 WARNING 是 legacy 冻结 corpus 包内含模板的兼容策略，
    新 Build 已自动排除。）
    """
    from codesetarena.submission.archive import validate_submission_archive
    from tests.test_submission_conformance import make_submission_tree

    tree = make_submission_tree(tmp_path / "tree")
    tree.joinpath(".env").write_text(f"LLM_API_KEY={FAKE_SECRET}\n", encoding="utf-8")
    tree.joinpath(".env.example").write_text("LLM_BASE_URL=\n", encoding="utf-8")
    tree.joinpath("runtime_policy.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "with_env" / PROTO
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'src'); "
         "from codesetarena.submission import create_submission_archive; "
         "create_submission_archive(sys.argv[1], sys.argv[2])",
         str(tree), str(out)],
        check=True, cwd=ROOT,
    )
    result = validate_submission_archive(out)
    assert result["status"] == "FAIL"
    assert result["error_code"] == "FORBIDDEN_FILE"

    # 仅 .env.example（无 .env / policy）-> PASS + WARNING（DEV_TEMPLATE_FILE）
    tree2 = make_submission_tree(tmp_path / "tree2")
    tree2.joinpath(".env.example").write_text("LLM_BASE_URL=\n", encoding="utf-8")
    out2 = tmp_path / "with_example" / PROTO
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'src'); "
         "from codesetarena.submission import create_submission_archive; "
         "create_submission_archive(sys.argv[1], sys.argv[2])",
         str(tree2), str(out2)],
        check=True, cwd=ROOT,
    )
    result2 = validate_submission_archive(out2)
    assert result2["status"] == "PASS"
    codes = [w.get("code") for w in result2["warnings"]]
    assert "DEV_TEMPLATE_FILE" in codes


# ---------------------------------------------------------------------------
# §36：Secret 端到端零泄漏（run / build / validate / batch / ingest 全链路）
# ---------------------------------------------------------------------------


def test_secret_never_leaks_end_to_end(tmp_path):
    from codesetarena.submission.batch.validator import validate_batch
    from codesetarena.submission.ingest import ingest_submissions
    from codesetarena.submission.local import build_submission
    from codesetarena.submission.reporting import write_validation_report

    ws = make_workspace(tmp_path, env_text=CONNECTION_ENV + "LLM_MODEL=dummy-model\n")
    _run_workspace(ws)  # run：产生 runs/
    out = tmp_path / PROTO
    build_submission(ws, phase="phase1", student_id="273001", output_path=out,
                     archive_format="tar.gz")

    batch_in = tmp_path / "batch_in"
    batch_in.mkdir()
    shutil.copy2(out, batch_in / PROTO)
    batch_report = validate_batch(batch_in)
    write_validation_report(batch_report, tmp_path / "batch_out")
    ingest_submissions(tmp_path / "repo", batch_in)

    scan_roots = [
        tmp_path / "runs",
        tmp_path / "batch_out",
        tmp_path / "repo",
    ]
    hits: list[str] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in (".pyc", ".gz", ".zip"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if FAKE_SECRET in text:
                hits.append(str(path.relative_to(tmp_path)))
    assert hits == [], f"Secret 泄漏: {hits}"
    # archive 字节本身也不含 Secret
    assert FAKE_SECRET.encode() not in out.read_bytes()


def test_build_warns_but_ignores_deprecated_env_policy_fields(tmp_path):
    """.env 残留 LLM_TIMEOUT/LLM_TOKEN_BUDGET -> WARNING，正式值仍来自 Policy。"""

    ws = make_workspace(
        tmp_path,
        env_text=(CONNECTION_ENV + "LLM_MODEL=hacker-model\n"
                  "LLM_TOKEN_BUDGET=1\nLLM_TIMEOUT=1\n"),
    )
    artifact = build_project(load_project(ws))
    assert artifact.config["token_budget"] == 10000  # 忽略 .env 的 1
    assert artifact.config["llm_timeout"] == 75
    # v10.1.12：LLM_MODEL 属于学生 .env —— hacker-model 即生效（不再被忽略）
    assert artifact.config["model"] == "hacker-model"
