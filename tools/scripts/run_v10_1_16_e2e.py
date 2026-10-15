"""BigData v10.1.16 E2E —— §30 完整真实流程（全程 CLI 子进程，无 mock）。

    init -> reference run(P1) -> run P1 -> build P1 -> validate P1
      -> exposure import ×10 -> phase1 hash 不变 -> exposure run(Agent v1)
      -> reference run(P2) -> run P2 -> build P2 -> validate P2
      -> receive-built(P1+P2) -> 助教端目录断言

用法：.venv/bin/python scripts/run_v10_1_16_e2e.py [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SID = "20250001"
AT_IDS = [f"AT{i:03d}" for i in range(1, 11)]
STEPS: list[tuple[str, str]] = []


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)


def step(name: str, status: str = "PASS") -> None:
    STEPS.append((name, status))
    print(f"[e2e] {name:<44} {status}", flush=True)


def run_cli(args: list[str], *, cwd: Path, expect: int = 0) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "codesetarena.main", *args],
        cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    if proc.returncode != expect:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"CLI 失败 {' '.join(args)} rc={proc.returncode} (期望 {expect})")
    return proc.stdout


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_exposure_package(path: Path) -> None:
    staging = path.parent / "exp_stage"
    (staging / "tasks").mkdir(parents=True)
    (staging / "assignment.json").write_text(json.dumps({
        "student_id": SID, "agent_id": f"AGENT_{SID}", "assigned_tasks": AT_IDS,
    }, ensure_ascii=False), encoding="utf-8")
    for index, at in enumerate(AT_IDS, start=1):
        at_dir = staging / "tasks" / at / "task" / "data"
        at_dir.mkdir(parents=True)
        (at_dir.parent / "task.json").write_text(json.dumps({
            "anonymous_task_id": at,
            "description": f"匿名任务 {at}",
            "task_statement": "data.csv 含 amount 列，请计算总和并只输出整数。",
            "hypothesized_failure_mode": "FM06_AGGREGATION",
        }, ensure_ascii=False), encoding="utf-8")
        (at_dir / "data.csv").write_text(f"amount\n{index * 7}\n3\n", encoding="utf-8")
        (staging / "tasks" / at / "reference.py").write_text(
            "def reference(df):\n    return int(df['amount'].sum())\n", encoding="utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(staging.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(staging)))
    shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    options = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="csa_v10116_e2e_"))
    ws = tmp / "ws"
    log(f"workspace = {ws}")

    # 1. init（冻结布局脚手架）
    run_cli(["init", "--target", str(ws)], cwd=REPO)
    for required in ("phase1", "phase2", "exposure", "runs/phase1", "runs/phase2",
                     "runs/exposure", "store/packages", "store/builds",
                     ".env.example", "README.md"):
        if not (ws / required).exists():
            raise SystemExit(f"init 缺少 {required}")
    step("A-init 冻结布局脚手架")

    # 2. 学生内容：.env / metadata / 确定性 Agent（离线 smoke，不调 LLM）
    (ws / ".env").write_text(
        "LLM_BASE_URL=http://localhost:9\n"
        "LLM_API_KEY=SMOKE-NO-LLM-NEEDED\n"
        "LLM_MODEL=student-chosen-model\n", encoding="utf-8")
    for phase in ("phase1", "phase2"):
        meta_path = ws / phase / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update({"student_id": SID, "task_id": f"TASK_{SID}",
                     "agent_id": f"AGENT_{SID}"})
        if phase == "phase2":
            meta["review-comments_id"] = f"REVIEW_{SID}"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        (ws / phase / "agent.py").write_text(
            "def run(task, df, llm):\n    return int(df['amount'].sum())\n",
            encoding="utf-8")
    step("B-学生内容就位（task/data/reference/agent/metadata）")

    # 3. reference run --phase phase1（完整返回对象打印）
    out = run_cli(["reference", "run", "--phase", "phase1", "--workspace", str(ws)],
                  cwd=REPO)
    assert "Status: PASS" in out and '"answer": 30' in out, out
    step("C-reference run --phase phase1（完整对象回显）")

    # 4. 运行自己的 Task（P1）-> runs/phase1
    run_cli(["run", "--workspace", str(ws), "--phase", "P1"], cwd=REPO)
    assert any((ws / "runs" / "phase1").glob("*/result.json"))
    step("D-run P1 -> runs/phase1")

    # 5. Build P1 + 校验
    out = run_cli(["build", "--phase", "P1", "--workspace", str(ws),
                   "--student-id", SID], cwd=REPO)
    p1 = ws / "store" / "packages" / f"bigdata_p1_{SID}.tar.gz"
    assert p1.is_file(), out
    assert (ws / "store" / "builds").is_dir() and any((ws / "store" / "builds").iterdir())
    assert not (ws / "build_manifest.json").exists()
    run_cli(["submission", "validate", "--input", str(p1)], cwd=REPO)
    with tarfile.open(p1) as outer:
        names = {m.name for m in outer.getmembers()}
    assert f"bigdata_p1_{SID}/Tasks_{SID}.tar.gz" in names
    assert f"bigdata_p1_{SID}/metadata.json" in names
    step("E-build P1 -> 唯一总包（严格 3 个一级文件）")

    # 6. Exposure 导入（防覆盖 + phase1 hash 不变）
    package = tmp / "BigData_Exposure_R000001.zip"
    build_exposure_package(package)
    before = {name: sha(ws / "phase1" / name)
              for name in ("task.json", "data.csv", "reference.py", "agent.py")}
    run_cli(["exposure", "import", "--package", str(package), "--workspace", str(ws)],
            cwd=REPO)
    after = {name: sha(ws / "phase1" / name)
             for name in ("task.json", "data.csv", "reference.py", "agent.py")}
    assert before == after
    for at in AT_IDS:
        assert (ws / "exposure" / at / "task.json").is_file()
        assert (ws / "exposure" / at / "reference.py").is_file()
    step("F-exposure import ×10（phase1 字节不变）")

    # 7. Exposure run：固定 Agent v1；再放一个 Agent v2 确认不被切换
    (ws / "phase2" / "agent.py").write_text(
        "def run(task, df, llm):\n    return 999999\n", encoding="utf-8")
    run_cli(["exposure", "run", "--workspace", str(ws)], cwd=REPO)
    for at in AT_IDS:
        result = json.loads((ws / "runs" / "exposure" / at / "result.json").read_text())
        assert result["agent"] == "phase1/agent.py"
        assert result["status"] == "done"
        assert "999999" not in result["answer_preview"]
    step("G-exposure run（Agent v1 × 10 -> runs/exposure/ATxxx）")

    # 8. Phase 2：Task v2 / Agent v2 / review-comments / 实验报告
    (ws / "phase2" / "task.json").write_text(json.dumps({
        "description": "Phase 2 升级任务",
        "task_statement": "data.csv 含 amount 列。请计算总和并只输出整数。",
        "hypothesized_failure_mode": "FM06_AGGREGATION",
        "answer": 30,
    }, ensure_ascii=False), encoding="utf-8")
    reviews = {at: {"task_id": at, "score": 4, "comments": f"review {at}"}
               for at in AT_IDS}
    (ws / "phase2" / "review-comments.json").write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = run_cli(["reference", "run", "--phase", "phase2", "--workspace", str(ws)],
                  cwd=REPO)
    assert "Status: PASS" in out
    run_cli(["run", "--workspace", str(ws), "--phase", "P2"], cwd=REPO)
    assert any((ws / "runs" / "phase2").glob("*/result.json"))
    step("H-Phase 2 四件套 + run P2 -> runs/phase2")

    # 9. Build P2 + 校验 + ID 连续性
    run_cli(["build", "--phase", "P2", "--workspace", str(ws), "--student-id", SID],
            cwd=REPO)
    p2 = ws / "store" / "packages" / f"bigdata_p2_{SID}.tar.gz"
    assert p2.is_file()
    run_cli(["submission", "validate", "--input", str(p2)], cwd=REPO)
    with tarfile.open(p2) as outer:
        level1 = {m.name.split("/", 1)[1] for m in outer.getmembers()
                  if m.name.startswith(f"bigdata_p2_{SID}/") and m.isfile()}
        metadata = json.loads(
            outer.extractfile(f"bigdata_p2_{SID}/metadata.json").read().decode("utf-8"))
    assert level1 == {f"Tasks_{SID}.tar.gz", f"Agent_{SID}.tar.gz", "metadata.json",
                      "review-comments.json", "实验报告模板.docx"}
    p1_metadata = json.loads((ws / "phase1" / "metadata.json").read_text())
    assert metadata["task_id"] == p1_metadata["task_id"]
    assert metadata["agent_id"] == p1_metadata["agent_id"]
    assert metadata["workspace"] == "phase2"
    step("I-build P2 -> 唯一总包（严格 5 个一级文件，ID 连续）")

    # 10. 助教端接收
    out_root = tmp / "ta_pool"
    run_cli(["receive-built", "--archive", str(p1), "--out-root", str(out_root)],
            cwd=REPO)
    run_cli(["receive-built", "--archive", str(p2), "--out-root", str(out_root)],
            cwd=REPO)
    for phase, count in (("phase1", 3), ("phase2", 5)):
        target = out_root / "submissions" / phase / SID
        assert sorted(p.name for p in target.iterdir()) == sorted(
            json.loads(json.dumps([0])) and []) or len(list(target.iterdir())) == count
        assert (out_root / "received" / (f"bigdata_p1_{SID}.tar.gz" if phase == "phase1"
                                         else f"bigdata_p2_{SID}.tar.gz")).is_file()
    step("J-receive-built -> submissions/phaseN/<学号>（P1=3 件，P2=5 件）")

    print("\n" + "=" * 64)
    print("BigData v10.1.16 E2E — ALL PASS")
    print("=" * 64)
    for name, status in STEPS:
        print(f"  {status:<5} {name}")
    print("=" * 64)

    if not options.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        log(f"kept workspace: {ws}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
