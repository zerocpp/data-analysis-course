"""Runtime MVP mandate tests (Scheduler + Runner + MainAgent chain).

Covers the mandated scenarios: Task privacy, file authorization, file
escape, MainAgent contract, JSON answer, gold leakage, SQLite registry,
LLM secret isolation, LLM timeout wiring, run timeout and recovery
detection.  All tests are OFFLINE (mock LLM); only the timeout-wiring test
monkeypatches the HTTP transport.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
import textwrap
import time
from pathlib import Path

import pytest

from codesetarena.agent_contract import (
    AGENTTASKINPUT_FORBIDDEN_FIELDS,
    DECLARED_GOAL_ANSWER_FIELD,
    RUN_STATUS_SUCCESS,
    RUN_STATUS_TIMEOUT,
)
from codesetarena.agent_interface import (
    AgentFileAccessError,
    AgentTaskInput,
    build_public_task_input,
)
from codesetarena.runtime.runner import ExecutionRequest, LocalRunner
from codesetarena.runtime.scheduler import Scheduler, load_private_env
from codesetarena.task.authoring import (
    AuthoringTaskError,
    build_authoring_task,
    freeze_task_package,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_data_dir(tmp_path: Path, *, extra: bool = False) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "file1.csv").write_text("id,value\n1,10\n2,20\n", encoding="ascii")
    (data_dir / "file2.csv").write_text("id,tag\n1,a\n", encoding="ascii")
    if extra:
        (data_dir / "file3.csv").write_text("id\n1\n", encoding="ascii")
    return data_dir


def freeze_sample_task(
    tmp_path: Path,
    *,
    declared: str = '{"sum":30}',
    files: tuple[str, ...] = ("file1.csv",),
    extra_tar_file: str | None = None,
    data_dir: Path | None = None,
) -> Path:
    data_dir = data_dir or make_data_dir(tmp_path)
    authoring = build_authoring_task(
        {
            "task_id": "TASK001",
            "description": "Sum values from file1.csv.",
            "input_files": list(files),
            "output_type": "string",
            "output_format": 'Return a JSON object {"sum": number}.',
            DECLARED_GOAL_ANSWER_FIELD: declared,
        }
    )
    task_file = tmp_path / "tasks" / "TASK-001.tar.gz"
    freeze_task_package(authoring, data_dir=data_dir, output_path=task_file)
    if extra_tar_file is not None:
        _append_tar_member(task_file, extra_tar_file, "secret,contents\n")
    return task_file


def _append_tar_member(task_file: Path, name: str, content: str) -> None:
    import io

    with tarfile.open(task_file, "r:gz") as archive:
        members = [(m, archive.extractfile(m).read() if m.isfile() else None) for m in archive.getmembers()]
    with tarfile.open(task_file, "w:gz") as archive:
        for member, data in members:
            if data is not None:
                archive.addfile(member, io.BytesIO(data))
        raw = content.encode("utf-8")
        info = tarfile.TarInfo(name)
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))


SIMPLE_AGENT = """
import json


class MainAgent:
    def solve(self, task, context):
        text = context.read_file("file1.csv")
        rows = [line.split(",") for line in text.strip().splitlines()[1:]]
        total = sum(int(r[1]) for r in rows)
        return json.dumps({"sum": total})
"""


def make_agent(tmp_path: Path, source: str = SIMPLE_AGENT, name: str = "AGENT-001.py") -> Path:
    agent_file = tmp_path / "agents" / name
    agent_file.parent.mkdir(exist_ok=True)
    agent_file.write_text(textwrap.dedent(source), encoding="utf-8")
    return agent_file


def make_scheduler(tmp_path: Path, **kwargs) -> Scheduler:
    kwargs.setdefault("policy", {"agent": {"wall_clock_seconds": 10}, "llm": {"request_timeout_seconds": 75}})
    return Scheduler(tmp_path / "ws", **kwargs)


def run_one(tmp_path: Path, *, agent_source: str = SIMPLE_AGENT, **kwargs) -> dict:
    task_file = kwargs.pop("task_file", None) or freeze_sample_task(tmp_path)
    agent_file = kwargs.pop("agent_file", None) or make_agent(tmp_path, agent_source)
    scheduler = make_scheduler(tmp_path, **kwargs)
    summary = scheduler.submit(
        ExecutionRequest(
            task_file=str(task_file),
            agent_file=str(agent_file),
            task_id="TASK001",
            agent_id="AGENT001",
            student_id="20241031027",
        ),
        wait=True,
    )
    return dict(summary)


# ---------------------------------------------------------------------------
# 1. Task privacy: AuthoringTaskInput has it, AgentTaskInput does not
# ---------------------------------------------------------------------------


def test_task_privacy_authoring_vs_agent(tmp_path: Path) -> None:
    data_dir = make_data_dir(tmp_path)
    authoring = build_authoring_task(
        {
            "description": "Use file1.csv.",
            "input_files": ["file1.csv"],
            "output_format": 'Return a JSON object {"sum": number}.',
            DECLARED_GOAL_ANSWER_FIELD: '{"sum":30}',
        }
    )
    assert authoring.declared_goal_answer == '{"sum":30}'
    assert DECLARED_GOAL_ANSWER_FIELD not in authoring.public_projection()
    public = authoring.public_projection()
    task_input = build_public_task_input(public)
    assert DECLARED_GOAL_ANSWER_FIELD not in public
    for name in AGENTTASKINPUT_FORBIDDEN_FIELDS:
        assert not hasattr(task_input, name)
    with pytest.raises(ValueError, match="declared_goal_answer"):
        build_public_task_input({**public, DECLARED_GOAL_ANSWER_FIELD: '{"sum":30}'})


def test_freeze_package_never_contains_declared_goal_answer(tmp_path: Path) -> None:
    task_file = freeze_sample_task(tmp_path)
    with tarfile.open(task_file, "r:gz") as archive:
        task_json = archive.extractfile("task.json").read().decode("utf-8")
        members = archive.getnames()
    assert DECLARED_GOAL_ANSWER_FIELD not in task_json
    assert all("goal" not in name for name in members)


def test_normalize_rejects_declared_goal_answer() -> None:
    from codesetarena.task.task_contract import normalize_task_dict

    with pytest.raises(Exception, match="declared_goal_answer"):
        normalize_task_dict(
            {
                "description": "d",
                "input_files": [],
                "output_type": "string",
                "output_format": "Return JSON.",
                DECLARED_GOAL_ANSWER_FIELD: "1",
            }
        )


# ---------------------------------------------------------------------------
# 2. File authorization + escape (Runner extracts ONLY declared files)
# ---------------------------------------------------------------------------


FILE_AUTH_AGENT = """
import json
from codesetarena.agent_interface import AgentFileAccessError


class MainAgent:
    def solve(self, task, context):
        allowed = context.read_file("file1.csv")
        blocked = []
        for name in ("file2.csv", "file3.csv", "secret.txt"):
            try:
                context.read_file(name)
            except AgentFileAccessError:
                blocked.append(name)
        escapes = []
        for path in ("../escape.csv", "/etc/passwd", "~/.ssh", "C:\\\\tmp"):
            try:
                context.read_file(path)
            except AgentFileAccessError:
                escapes.append(path)
        return json.dumps({"allowed_head": allowed[:2], "blocked": blocked, "escapes": escapes})
"""


def test_file_authorization_and_escape(tmp_path: Path) -> None:
    data_dir = make_data_dir(tmp_path, extra=True)
    (data_dir / "file3.csv").write_text("id\n1\n", encoding="ascii")
    task_file = freeze_sample_task(
        tmp_path, files=("file1.csv",), extra_tar_file="secret.txt", data_dir=data_dir
    )
    summary = run_one(tmp_path, agent_source=FILE_AUTH_AGENT, task_file=task_file)
    assert summary["status"] == "SUCCESS", summary
    payload = json.loads(summary["answer"])
    assert payload["allowed_head"] == "id"
    assert payload["blocked"] == ["file2.csv", "file3.csv", "secret.txt"]
    assert payload["escapes"] == ["../escape.csv", "/etc/passwd", "~/.ssh", "C:\\tmp"]
    input_files = sorted(p.name for p in (Path(summary["result_path"]).parent.parent / "input").iterdir())
    assert input_files == ["file1.csv"]


# ---------------------------------------------------------------------------
# 3. MainAgent contract: MainAgent PASS, StudentAgent FAIL
# ---------------------------------------------------------------------------


def test_mainagent_contract_enforced(tmp_path: Path) -> None:
    summary = run_one(tmp_path)
    assert summary["status"] == "SUCCESS"

    legacy = textwrap.dedent(
        """
        import json


        class StudentAgent:
            def solve(self, task, context):
                return json.dumps({"sum": 30})
        """
    )
    summary_legacy = run_one(tmp_path / "legacy", agent_source=legacy)
    assert summary_legacy["status"] == "FAILED_EXECUTION"


# ---------------------------------------------------------------------------
# 4. JSON answer contract
# ---------------------------------------------------------------------------


def test_json_answer_valid_and_invalid(tmp_path: Path) -> None:
    good = run_one(tmp_path / "good")
    assert good["status"] == "SUCCESS"
    assert good["answer_json_valid"] is True
    json.loads(good["answer"])

    bad_agent = """
    class MainAgent:
        def solve(self, task, context):
            return "valid=10"
    """
    bad = run_one(tmp_path / "bad", agent_source=bad_agent)
    assert bad["status"] == "FAILED_EXECUTION"
    result = json.loads(Path(bad["result_path"]).read_text(encoding="utf-8"))
    assert result["answer"] == ""


# ---------------------------------------------------------------------------
# 5. Gold leakage: nothing in Runner input / AgentTaskInput / result.json
# ---------------------------------------------------------------------------


def test_gold_leakage_full_scan(tmp_path: Path) -> None:
    data_dir = make_data_dir(tmp_path)
    task_file = freeze_sample_task(tmp_path, data_dir=data_dir)
    agent_source = """
    import json
    from codesetarena.agent_interface import AgentTaskInput


    class MainAgent:
        def solve(self, task, context):
            seen = [name for name in ("declared_goal_answer", "gold_answer", "expected_answer") if hasattr(task, name)]
            return json.dumps({"gold_attrs": seen})
    """
    summary = run_one(tmp_path, task_file=task_file, agent_source=agent_source)
    assert summary["status"] == "SUCCESS"
    assert json.loads(summary["answer"])["gold_attrs"] == []
    run_dir = Path(summary["result_path"]).parent.parent
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert DECLARED_GOAL_ANSWER_FIELD not in path.read_text(
                encoding="utf-8", errors="ignore"
            ), path


# ---------------------------------------------------------------------------
# 6. SQLite registry + CSV export
# ---------------------------------------------------------------------------


def test_sqlite_registry_and_csv(tmp_path: Path) -> None:
    scheduler = make_scheduler(tmp_path)
    task_file = freeze_sample_task(tmp_path)
    agent_file = make_agent(tmp_path)
    summary = scheduler.submit(
        ExecutionRequest(
            task_file=str(task_file),
            agent_file=str(agent_file),
            task_id="TASK001",
            agent_id="AGENT001",
            student_id="20241031027",
        ),
        wait=True,
    )
    assert summary["status"] == "SUCCESS"
    with sqlite3.connect(scheduler.db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, status, duration_seconds, result_path FROM runs"
            " WHERE task_id='TASK001' AND agent_id='AGENT001'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "SUCCESS"
    assert rows[0][2] >= 0.0
    csv_path = scheduler.export_runs_csv()
    content = csv_path.read_text(encoding="utf-8")
    assert "run_id,task_id,agent_id" in content
    assert "TASK001" in content


# ---------------------------------------------------------------------------
# 7. LLM secret isolation
# ---------------------------------------------------------------------------


SECRET_AGENT = """
import json
from codesetarena.agent_interface import AgentFileAccessError


class MainAgent:
    def solve(self, task, context):
        capability_blocked = False
        try:
            context.read_file(".env")
        except AgentFileAccessError:
            capability_blocked = True
        return json.dumps({"capability_blocked": capability_blocked})
"""


def test_llm_secret_never_reaches_agent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    private_env = ws / "runtime-private" / ".env"
    private_env.parent.mkdir(parents=True, exist_ok=True)
    private_env.write_text(
        "CODESETARENA_LLM_BASE_URL=mock://local\nCODESETARENA_LLM_API_KEY=sk-test-secret\n",
        encoding="utf-8",
    )
    private_values = load_private_env(private_env)
    assert private_values["CODESETARENA_LLM_API_KEY"] == "sk-test-secret"

    task_file = freeze_sample_task(tmp_path)
    agent_file = make_agent(tmp_path, SECRET_AGENT)
    scheduler = Scheduler(ws, private_env_file=private_env)
    summary = scheduler.submit(
        ExecutionRequest(task_file=str(task_file), agent_file=str(agent_file)),
        wait=True,
    )
    assert summary["status"] == "SUCCESS"
    payload = json.loads(summary["answer"])
    assert payload["capability_blocked"] is True
    import os

    assert not os.environ.get("CODESETARENA_LLM_API_KEY")
    run_dir = Path(summary["result_path"]).parent.parent
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert "sk-test-secret" not in path.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# 8. LLM timeout wiring: policy value MUST reach the HTTP transport
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self._payload


@pytest.mark.parametrize("policy_seconds", [75, 43])
def test_llm_timeout_policy_reaches_http_transport(
    tmp_path: Path, monkeypatch, policy_seconds: int
) -> None:
    import urllib.request

    from codesetarena.config import RuntimeConfig
    from codesetarena.runtime.llm_proxy import SchedulerLLMProxy

    monkeypatch.setattr(
        "codesetarena.policy_loader.runtime_policy",
        lambda: {
            "policy_version": "test",
            "agent": {"wall_clock_seconds": 90},
            "llm": {"request_timeout_seconds": policy_seconds},
        },
    )
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["timeout"] = timeout
        payload = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        ).encode("utf-8")
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    config = RuntimeConfig(
        base_url="http://127.0.0.1:9/v1", api_key="x", models=["m"], env_file=None
    )
    proxy = SchedulerLLMProxy(config=config)
    assert proxy.request_timeout_seconds == policy_seconds
    result = proxy.complete(run_id="RUN-TEST", prompt="hello")
    assert captured["timeout"] == policy_seconds
    assert result.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 9. Run timeout: wall clock enforced by the Runner
# ---------------------------------------------------------------------------


def test_run_timeout_enforced(tmp_path: Path) -> None:
    sleepy = textwrap.dedent(
        """
        import time


        class MainAgent:
            def solve(self, task, context):
                time.sleep(30)
                return "10"
        """
    )
    started = time.monotonic()
    summary = run_one(
        tmp_path,
        agent_source=sleepy,
        policy={"agent": {"wall_clock_seconds": 1}, "llm": {"request_timeout_seconds": 75}},
    )
    elapsed = time.monotonic() - started
    assert summary["status"] == RUN_STATUS_TIMEOUT
    assert elapsed < 15


# ---------------------------------------------------------------------------
# 10. Recovery detection: stale RUNNING rows get re-executed by the Runner
# ---------------------------------------------------------------------------


def test_recovery_detection_and_resubmit(tmp_path: Path) -> None:
    scheduler = make_scheduler(
        tmp_path,
        policy={"agent": {"wall_clock_seconds": 10}, "llm": {"request_timeout_seconds": 75}},
    )
    task_file = freeze_sample_task(tmp_path)
    agent_file = make_agent(tmp_path)
    stale_run_id = scheduler.runner.prepare_run(
        ExecutionRequest(task_file=str(task_file), agent_file=str(agent_file))
    )
    scheduler.registry.insert_run(
        run_id=stale_run_id,
        task_id="TASK001",
        agent_id="AGENT001",
        student_id="20241031027",
        status="RUNNING",
    )
    scheduler.registry.save_request(
        run_id=stale_run_id,
        task_file=str(task_file),
        agent_file=str(agent_file),
        student_id="20241031027",
        request_json=ExecutionRequest(
            task_file=str(task_file), agent_file=str(agent_file)
        ).to_request_json(),
    )
    scheduler.registry.mark_runner_started(stale_run_id)
    scheduler.registry.set_runner_started_at(
        stale_run_id, "2000-01-01T00:00:00+00:00"
    )
    assert scheduler.status(stale_run_id)["runner_finished_at"] is None

    candidates = scheduler.detect_recoveries()
    assert [row["run_id"] for row in candidates] == [stale_run_id]

    summary = scheduler.recover_run(stale_run_id, wait=True)
    assert summary["run_id"] == stale_run_id
    assert summary["status"] == RUN_STATUS_SUCCESS
    assert scheduler.status(stale_run_id)["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# 11. Concurrency: bounded slots inside the Scheduler (no Worker classes)
# ---------------------------------------------------------------------------


CONCURRENCY_AGENT = """
import json
import time


class MainAgent:
    def solve(self, task, context):
        time.sleep(0.4)
        return json.dumps({"sum": 30})
"""


def test_concurrency_slots_respected(tmp_path: Path) -> None:
    scheduler = make_scheduler(
        tmp_path,
        policy={"agent": {"wall_clock_seconds": 10}, "llm": {"request_timeout_seconds": 75}},
        max_parallel_runs=2,
    )
    task_file = freeze_sample_task(tmp_path)
    agent_file = make_agent(tmp_path, CONCURRENCY_AGENT)
    request = ExecutionRequest(task_file=str(task_file), agent_file=str(agent_file))
    run_ids = [scheduler.submit(request, wait=False) for _ in range(4)]
    max_observed = 0
    while any(scheduler.status(run_id)["status"] in ("CREATED", "RUNNING") for run_id in run_ids):
        active = scheduler.registry.list_runs()
        running = sum(1 for row in active if row["status"] == "RUNNING")
        max_observed = max(max_observed, running)
        time.sleep(0.05)
    assert max_observed <= 2
    statuses = [scheduler.status(run_id)["status"] for run_id in run_ids]
    assert statuses == ["SUCCESS"] * 4


# ---------------------------------------------------------------------------
# 12. Run directory layout + execution.xml + result.json schema
# ---------------------------------------------------------------------------


def test_run_artifacts_layout(tmp_path: Path) -> None:
    summary = run_one(tmp_path)
    assert summary["status"] == RUN_STATUS_SUCCESS
    run_dir = Path(summary["result_path"]).parent.parent
    assert (run_dir / "input").is_dir()
    assert (run_dir / "output" / "result.json").is_file()
    assert (run_dir / "logs" / "run.log").is_file()
    execution_xml = (run_dir / "execution.xml").read_text(encoding="utf-8")
    for fragment in (
        "<execution>",
        f"<run_id>{summary['run_id']}</run_id>",
        "<task_id>TASK001</task_id>",
        "<agent_id>AGENT001</agent_id>",
        "<runner>local</runner>",
        "<resource_policy>",
        "<request_timeout_seconds>75</request_timeout_seconds>",
    ):
        assert fragment in execution_xml, fragment
    payload = json.loads((run_dir / "output" / "result.json").read_text(encoding="utf-8"))
    assert set(payload) == {"run_id", "task_id", "agent_id", "status", "answer"}
    assert payload["answer"] == json.dumps({"sum": 30})
    assert json.loads(payload["answer"]) == {"sum": 30}


# ---------------------------------------------------------------------------
# 13. Scheduler never executes Agent code (structural guard)
# ---------------------------------------------------------------------------


def test_scheduler_has_no_solve_and_no_worker_classes() -> None:
    import inspect

    from codesetarena.runtime import runner as runner_module
    from codesetarena.runtime import scheduler as scheduler_module

    for name in ("Worker", "Master", "RuntimeManager", "RunManager"):
        assert not hasattr(scheduler_module, name)
        assert not hasattr(runner_module, name)
    assert not hasattr(Scheduler, "solve")
    source = inspect.getsource(scheduler_module)
    assert ".solve(" not in source
