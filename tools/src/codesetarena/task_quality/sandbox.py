"""Blind Independent Solver 的沙箱执行（§10/§11/§12）。

复用策略：隔离目录 + 独立子进程 + policy 超时 + 无密钥环境变量。
生成代码与学生 reference 是两套互不可见的执行环境（不共享工作目录，
不互相 import，不读彼此文件）；沙箱内只有题面元数据 + 数据文件副本 + 生成代码。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .models import SANDBOX_FORBIDDEN_FILES

_CHILD_RUNNER = r'''
import importlib.util
import json
import signal
import sys

import pandas as pd

data_path, module_path, business_timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
df = pd.read_csv(data_path)
spec = importlib.util.spec_from_file_location("independent_reference", module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _boom(signum, frame):
    raise TimeoutError(f"business timeout exceeded {business_timeout:g}s")


# 业务时限在导入完成后才启动（与生产 reference runner 同语义）：
# 解释器/pandas 冷启动由外层 boot 兑底，不侵占业务时间。
signal.signal(signal.SIGALRM, _boom)
signal.setitimer(signal.ITIMER_REAL, business_timeout)
try:
    result = mod.reference(df)
    signal.setitimer(signal.ITIMER_REAL, 0)
except TimeoutError:
    sys.stderr.write(f"business timeout exceeded {business_timeout:g}s")
    sys.exit(3)
sys.stdout.write("CSA_RESULT_JSON:" + json.dumps(result, ensure_ascii=False, default=str))
'''


def create_blind_sandbox(root: Path, *, data_file: Path, code: str) -> Path:
    """建立只含 数据副本 + 独立参考代码 + 元数据 的隔离沙箱（§11）。"""

    sandbox = Path(tempfile.mkdtemp(prefix="task_quality_blind_", dir=str(root)))
    (sandbox / "data.csv").write_bytes(data_file.read_bytes())
    (sandbox / "independent_reference.py").write_text(code, encoding="utf-8")
    (sandbox / "blind_meta.json").write_text(
        json.dumps({"note": "blind sandbox: no student answer/reference present"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return sandbox


def run_reference_in_sandbox(
    sandbox: Path,
    *,
    timeout_seconds: float,
    boot_grace_seconds: float = 0.0,
    data_name: str = "data.csv",
    module_name: str = "independent_reference.py",
) -> dict:
    """真实执行沙箱内代码并捕获实际输出（§9：结果必须来自真实 Python Execution）。

    ``timeout_seconds`` 是业务时限（子进程内 alarm，导入完成后才计时）；
    ``boot_grace_seconds`` 解释器/pandas 冷启动由外层硬超时兼底
    （policy ``main.boot_grace_seconds``，与生产 agent 子进程同源）。
    """

    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD_RUNNER,
             str(sandbox / data_name), str(sandbox / module_name),
             str(max(float(timeout_seconds), 0.05))],
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=max(float(timeout_seconds) + float(boot_grace_seconds) + 10.0, 5.0),
            env=clean_env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "execution_status": "boot_timeout",
            "result": None,
            "stdout": (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": f"sandbox hard timeout after {timeout_seconds:g}s business "
                      f"+ {boot_grace_seconds:g}s boot grace (terminated)",
            "exit_code": None,
        }
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode == 3 and "business timeout" in stderr:
        return {
            "execution_status": "timeout",
            "result": None,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
        }
    marker = "CSA_RESULT_JSON:"
    if marker in stdout and proc.returncode == 0:
        payload = stdout.split(marker, 1)[1].strip()
        try:
            return {"execution_status": "ok", "result": json.loads(payload),
                    "stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}
        except json.JSONDecodeError:
            pass
    detail = (stderr or stdout or "").strip().splitlines()
    reason = detail[-1] if detail else f"exit code {proc.returncode}"
    return {
        "execution_status": "crash" if proc.returncode != 0 else "invalid_output",
        "result": None,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": proc.returncode,
        "error": reason,
    }


def sandbox_forbidden_files(sandbox: Path) -> list[str]:
    forbidden = {n.lower() for n in SANDBOX_FORBIDDEN_FILES}
    return [
        str(p.relative_to(sandbox))
        for p in sorted(sandbox.rglob("*"))
        if p.is_file() and p.name.lower() in forbidden_names_guard(forbidden)
    ]


def forbidden_names_guard(forbidden):
    return forbidden
