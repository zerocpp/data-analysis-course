#!/usr/bin/env python3
"""Mock Repair —— 把遗留学生包批量重建为现行契约的正式提交包（BigData_P1_{sid}.zip）。

原理：不手补 zip 字节，而是"包 → workspace → 官方 build_submission"重建，
因此产物与 bin/datac submission build 的导出**完全同构**（同一 Staging Tree、
同一 Single-Package Validator、同一确定性 packer）。

每个遗留包的重建步骤：
  1. safe_extract（复用接收端安全解压）；
  2. 还原 workspace 六件套：task.json（student_id 归一为包名学号）、
     data.csv（取 task/data 主 CSV）、agent/agent.py、reference.py（缺失则
     生成 pandas 合规 stub）、.env / .env.example（连接+模型模板，非真实凭据）；
  3. build_submission(workspace, phase="phase1", student_id=<sid>) —— build 内部
     先跑 validate_single_package，FAIL 则拒绝出包（绝不产出非法包）；
  4. validate_submission_archive 复验，PASS 才计入成功。

用法::

    .venv/bin/python scripts/mock_repair_submissions.py \
        --input  <遗留包目录> \
        --output <修复输出目录> [--limit N]

输入目录只读；所有产物写在 --output。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codesetarena.submission.archive import validate_submission_archive  # noqa: E402
from codesetarena.submission.identity import parse_archive_name  # noqa: E402
from codesetarena.submission.local import build_submission  # noqa: E402
from codesetarena.submission.safe_extract import safe_extract_archive  # noqa: E402

PANDAS_STUB_REFERENCE = (
    "import pandas as pd\n\n\n"
    "def reference(df):\n"
    "    return '{\"rows\": ' + str(len(df)) + '}'\n"
)

ENV_TEXT = (
    "LLM_BASE_URL=http://localhost:9\n"
    "LLM_API_KEY=MOCK-NOT-A-REAL-KEY\n"
    "LLM_MODEL=mock-repair-model\n"
)
ENV_EXAMPLE_TEXT = "LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _primary_csv(data_dir: Path) -> Path | None:
    preferred = data_dir / "data.csv"
    if preferred.is_file():
        return preferred
    csvs = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in (".csv", ".tsv"))
    return csvs[0] if csvs else None


def repair_one(archive: Path, out_dir: Path, work_root: Path, *, sid_from: str = "", sid_to: str = "") -> dict:
    """重建单个遗留包；返回结果记录（不抛异常，错误进记录）。"""
    record: dict = {"source": archive.name, "status": "FAIL", "target": ""}
    sid_hint = archive.name
    try:
        sid = parse_archive_name(archive.name)["student_id"]
        if sid_from and sid.startswith(sid_from):
            sid = sid_to + sid[len(sid_from):]  # 学号映射（如 9900xxxx -> 2026xxxx）
    except Exception:  # noqa: BLE001 - 非协议名时回退 manifest
        sid = ""
    record["student_id"] = sid or sid_hint

    staging = Path(tempfile.mkdtemp(prefix="mock_repair_", dir=work_root))
    ws = staging / "ws"
    try:
        extracted = staging / "pkg"
        safe_extract_archive(archive, extracted)
        pkg = extracted if (extracted / "manifest.json").is_file() else next(
            (p for p in sorted(extracted.iterdir()) if p.is_dir() and (p / "manifest.json").is_file()),
            extracted)

        # ---- 学号：包名 > manifest > task.json ----
        if not sid:
            manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
            sid = str(manifest.get("student_id") or "").strip()
        record["student_id"] = sid
        if not sid:
            record["error"] = "无法确定 student_id（包名与 manifest 均缺失）"
            return record

        ws.mkdir(parents=True)
        # task.json：保留学生语义，student_id 归一
        task = json.loads((pkg / "task" / "task.json").read_text(encoding="utf-8"))
        task["student_id"] = sid
        (ws / "task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # data.csv
        data = _primary_csv(pkg / "task" / "data")
        if data is None:
            record["error"] = "包内缺少 task/data CSV"
            return record
        shutil.copyfile(data, ws / "data.csv")
        # agent.py
        shutil.copyfile(pkg / "agent" / "agent.py", ws / "agent.py")
        # reference.py（根或 task/；缺失或不可用 -> 合规 stub，由 build 静态校验兜底）
        ref = None
        for cand in (pkg / "reference.py", pkg / "task" / "reference.py"):
            if cand.is_file():
                ref = cand
                break
        if ref is None:
            (ws / "reference.py").write_text(PANDAS_STUB_REFERENCE, encoding="utf-8")
            record["reference_stub"] = True
        else:
            shutil.copyfile(ref, ws / "reference.py")
        # .env / .env.example（mock 连接 + 模型，非真实凭据）
        (ws / ".env").write_text(ENV_TEXT, encoding="utf-8")
        (ws / ".env.example").write_text(ENV_EXAMPLE_TEXT, encoding="utf-8")

        # ---- 官方 build（内部即 Single-Package Validator；FAIL 不出包）----
        result = build_submission(ws, phase="phase1", student_id=sid)
        if result["exit_code"] != 0 or not result.get("package_path"):
            lv = result.get("local_validation") or {}
            record["error"] = f"build 校验拒绝: {lv.get('error_code')} {(lv.get('errors') or [{}])[0].get('detail', '')}"
            return record
        target = out_dir / f"BigData_P1_{sid}.zip"
        shutil.copyfile(result["package_path"], target)

        # ---- 独立复验（TA 同款入口）----
        check = validate_submission_archive(target, phase="phase1")
        if check["status"] != "PASS":
            record["error"] = f"复验失败: {check['error_code']}"
            return record
        record.update(status="PASS", target=str(target))
        return record
    except Exception as exc:  # noqa: BLE001 - 单包失败不阻断批次
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="遗留学生包 -> BigData_P1_{sid}.zip 批量重建")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（0=全部）")
    ap.add_argument("--sid-from", default="", help="学号映射前缀旧值（如 9900）")
    ap.add_argument("--sid-to", default="", help="学号映射前缀新值（如 2026）；包内三处身份同步重写")
    args = ap.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    archives = sorted([*src.glob("*.tar.gz"), *src.glob("*.zip")])
    if args.limit:
        archives = archives[: args.limit]

    work_root = out / "_work"
    work_root.mkdir(exist_ok=True)
    results = [repair_one(a, out, work_root, sid_from=args.sid_from, sid_to=args.sid_to)
               for a in archives]
    shutil.rmtree(work_root, ignore_errors=True)

    passed = [r for r in results if r["status"] == "PASS"]
    failed = [r for r in results if r["status"] != "PASS"]
    report = {
        "schema_version": "bigdata.mock_repair.v1",
        "generated_at": _now_iso(),
        "input": str(src),
        "output": str(out),
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "failures": failed,
    }
    (out / "repair_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[mock-repair] total={len(results)} passed={len(passed)} failed={len(failed)}")
    for f in failed[:10]:
        print(f"[mock-repair] FAIL {f['source']}: {f.get('error')}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
