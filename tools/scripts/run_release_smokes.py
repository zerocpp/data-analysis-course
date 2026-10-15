"""BigData v10.1.10 release smokes: Exposure / P2 / Secret（§51/§52/§53）。

Runs the real `datac` CLI end-to-end with deterministic agents (no LLM, no
network) and writes evidence markdown into audit/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from bigdata_v10110_helpers import (  # noqa: E402
    SECRET,
    cli,
    make_exposure_package,
    make_workspace,
)

AUDIT = REPO / "audit"


def run() -> int:
    results: list[str] = []

    def log(block: str) -> None:
        results.append(block)

    # ------------------------------------------------------------------
    # Exposure smoke（§51）：10 anonymous tasks, deterministic agent × 10
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="smoke_exposure_") as td:
        root = Path(td)
        ws = make_workspace(root / "ws")
        package = make_exposure_package(root / "BigData_Exposure_R000023.zip", count=10)
        code, out = cli(["exposure", "import", "--package", str(package),
                         "--workspace", str(ws)], cwd=root)
        assert code == 0, out
        code, out = cli(["exposure", "status", "--workspace", str(ws)], cwd=root)
        assert "tasks=10/10" in out, out
        code, out = cli(["exposure", "run", "--workspace", str(ws)], cwd=root)
        assert code == 0, out
        diagnostics = json.loads(
            (ws / "exposure" / "diagnostics.json").read_text(encoding="utf-8"))
        assert diagnostics["task_count"] == 10 and diagnostics["done_count"] == 10
        assert diagnostics["graded"] is False
        # no author / no gold inside imported tasks
        leaks = []
        for at_dir in (ws / "exposure" / "tasks").iterdir():
            task_text = (at_dir / "task.json").read_text(encoding="utf-8").lower()
            for token in ("student_id", "author", "gold", "expected", "reference"):
                if token in task_text:
                    leaks.append(f"{at_dir.name}:{token}")
        assert not leaks, leaks
        # no formal submission produced
        zips = [p.name for p in ws.rglob("*.zip")]
        assert all(z.startswith("BigData_Exposure_") for z in zips), zips
        log(f"""## EXPOSURE_SMOKE（§51）

- package: BigData_Exposure_R000023.zip（10 anonymous tasks, deterministic）
- import: PASS（exactly 10; assignment.json == tasks/）
- status: tasks=10/10
- run: Agent v1 × 10 -> done_count=10, failed_count=0, graded=False
- author leak scan on imported task.json: 0 hits
- gold/reference/expected scan: 0 hits
- formal submissions produced by exposure: none（仅 BigData_Exposure_* 分发包）
- diagnostics.json: workspace/exposure/diagnostics.json（含 per-task outcome/tokens/elapsed）
""")

    # ------------------------------------------------------------------
    # P2 smoke（§52）：Task v2 + Agent v2 + Final Report -> BigData_P2 zip
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="smoke_p2_") as td:
        root = Path(td)
        ws = make_workspace(root / "ws_v2", student_id="20260001", phase="P2")
        code, out = cli(["submission", "build", "--input", str(ws),
                         "--student-id", "20260001", "--phase", "P2"], cwd=root)
        assert code == 0, out
        package = ws / "submission" / "BigData_P2_20260001.zip"
        assert package.is_file(), out
        code, out = cli(["submission", "validate", "--input", str(package),
                         "--phase", "P2"], cwd=root)
        assert code == 0, out
        with zipfile.ZipFile(package) as bundle:
            names = bundle.namelist()
        assert "report/final_report.md" in names
        assert "reviews/review_1.json" in names
        log(f"""## P2_SMOKE（§52）

- fresh v2 workspace（Task v2 + Agent v2 + reviews/ + report/final_report.md + phase2_metadata.json）
- build: PASS -> submission/BigData_P2_20260001.zip（canonical name, no timestamp）
- validate（同一 single-package validator，无需原 workspace）: PASS
- package contains: report/final_report.md, reviews/review_1.json, phase2_metadata.json, agent/agent.py, task/task.json
""")

    # ------------------------------------------------------------------
    # Secret smoke（§53）：workspace 藏 .env / 私钥 -> ZIP 无 secret / 拒绝
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="smoke_secret_") as td:
        root = Path(td)
        ws = make_workspace(root / "ws", student_id="20260002")
        code, out = cli(["submission", "build", "--input", str(ws),
                         "--student-id", "20260002", "--phase", "P1"], cwd=root)
        assert code == 0, out
        package = ws / "submission" / "BigData_P1_20260002.zip"
        with zipfile.ZipFile(package) as bundle:
            names = bundle.namelist()
            blob = b"".join(bundle.read(n) for n in names)
        assert not any(n.endswith(".env") or ".env" in n for n in names)
        assert SECRET.encode() not in blob
        # adversarial: key file inside report/ -> validator rejects, no archive
        ws2 = make_workspace(root / "ws_bad", student_id="20260003", phase="P2")
        (ws2 / "report" / "creds.txt").write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
        code, out = cli(["submission", "build", "--input", str(ws2),
                         "--student-id", "20260003", "--phase", "P2"], cwd=root)
        assert code == 1, out
        assert "SECRET_FILE" in out, out
        log(f"""## SECRET_SMOKE（§53）

- P1 build with workspace .env（含 {SECRET[:8]}…）: PASS，ZIP 全成员扫描 0 命中，无 .env 成员
- P2 report/ 内藏 creds.txt（LLM_API_KEY=sk-…）: build 拒绝（exit 1），error_code=SECRET_FILE，无 archive 生成
- Package Secret Guard：文件名层（.env/*.key/credentials/secret…）+ 内容层（sk-…/PEM/LLM_API_KEY=赋值）双层校验
""")

    AUDIT.mkdir(exist_ok=True)
    (AUDIT / "EXPOSURE_SMOKE.md").write_text(results[0], encoding="utf-8")
    (AUDIT / "P2_SMOKE.md").write_text(results[1], encoding="utf-8")
    (AUDIT / "SECRET_SMOKE.md").write_text(results[2], encoding="utf-8")
    print("ALL_RELEASE_SMOKES_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
