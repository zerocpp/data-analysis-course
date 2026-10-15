"""Integration Regression —— 固定 ZIP 驱动的自动化回归（pytest 入口）。

在现有 Unit/Module 测试之上增加的一层：真实 CLI + 永久 Fixture 语料
（CODESETARENA_FIXTURE_ROOT）。语料缺失时先自动生成（作者工具，确定性字节）。

- test_integration_smoke：smoke 子集（~10 case，秒级）
- test_integration_full ：全量 52 case（含 cohort / late，分钟级）

断言完全以真实运行产物（validation_report / server_metadata / registry /
assignment / result.json ...）为准，不重新实现业务规则。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.integration.fixture_runner import default_fixture_root  # noqa: E402


def _assert_corpus_present() -> None:
    """Corpus 缺失 => FAIL（绝不自动重建：auto rebuild = 静默改变测试输入）。

    语料是作者工具产物，回归只消费已冻结的 corpus。
    """

    fixture_root = default_fixture_root()
    index = fixture_root / "case_index.json"
    assert index.is_file(), (
        f"Integration corpus 缺失: {index}；请显式运行 "
        f"`python -m tests.integration.build_fixture_corpus --build`（作者工具）"
    )


def _run_suite(suite: str) -> None:
    _assert_corpus_present()
    env = dict(os.environ)
    cmd = [sys.executable, str(ROOT / "scripts" / "run_integration_cases.py"), "--suite", suite]
    result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        print(result.stdout[-6000:])
        print(result.stderr[-2000:])
    assert result.returncode == 0, f"Integration {suite} suite FAILED"


def test_integration_smoke():
    """固定 ZIP smoke 子集（本地 Build / Server 身份 / 信任边界 / Workspace 快速回归）。"""

    _run_suite("smoke")


def test_integration_full():
    """固定 ZIP 全量回归（Common Base + Trust Boundary + P2 Extension + Workflow +
    Workspace + Cohort/Sampling，全部真实产物驱动）。"""

    _run_suite("full")