"""DAC Test Gate 自身的负向测试（Test Infrastructure Tests，不是 Corpus 集成 Case）。

验证门禁在以下情况必须 FAIL：

1. Corpus 某 fixture 改变一个字节 -> `corpus_identity_sha256` 不匹配 -> Gate FAIL
2. Baseline Lock 与 Corpus 不一致（case_count 被改）-> Gate FAIL
3. Runner 遇到运行期动态构造（submission_compose / manifest_overrides）-> 拒绝
4. 真实 Corpus 静态审计：dynamic_cases == 0

这些测试全部在临时目录进行，绝不修改真实 DAC_TestCases。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from tests.integration.dac_baseline import (
    build_lock_content,
    compute_corpus_identity,
    verify_lock,
)
from tests.integration import fixture_runner as fr


def _tmp_corpus() -> tuple[Path, Path]:
    """最小临时 corpus：拷贝一个真实 case + index + manifest。"""

    src = fr.default_fixture_root()
    dst = Path(tempfile.mkdtemp(prefix="dac_gate_neg_"))
    case_dir = "common/COMMON_VALID_BASE"
    shutil.copytree(src / case_dir, dst / case_dir)
    index = json.loads((src / "case_index.json").read_text(encoding="utf-8"))
    entry = next(c for c in index["cases"] if c["case_id"] == "COMMON_VALID_BASE")
    index["cases"] = [entry]
    (dst / "case_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((src / "corpus_manifest.json").read_text(encoding="utf-8"))
    manifest["case_count"] = 1
    (dst / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return src, dst


def test_gate_fails_on_fixture_byte_flip():
    src, dst = _tmp_corpus()
    try:
        actual = compute_corpus_identity(dst)
        lock = dst / "lock.json"
        lock.write_text(json.dumps(
            build_lock_content(actual, baseline_version="neg-test"), ensure_ascii=False, indent=2))
        ok, mismatches = verify_lock(lock, dst)
        assert ok and not mismatches, "基线自身必须先一致"

        # 翻转 case fixture 的一个字节（临时副本）
        target = dst / "common/COMMON_VALID_BASE/input/DataC1004_P1_273001.zip"
        data = bytearray(target.read_bytes())
        data[0] = data[0] ^ 0xFF
        target.write_bytes(bytes(data))

        ok2, mismatches2 = verify_lock(lock, dst)
        assert not ok2, "fixture 被修改后 Gate 必须 FAIL"
        assert any(("hash_mismatches" in m) or ("corpus_identity_sha256" in m)
                   for m in mismatches2), mismatches2
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def test_gate_fails_on_lock_mismatch():
    src, dst = _tmp_corpus()
    try:
        actual = compute_corpus_identity(dst)
        lock_content = build_lock_content(actual, baseline_version="neg-test")
        lock_content["case_count"] = lock_content["case_count"] + 1  # 伪造 lock
        lock = dst / "lock.json"
        lock.write_text(json.dumps(lock_content, ensure_ascii=False, indent=2))
        ok, mismatches = verify_lock(lock, dst)
        assert not ok
        assert any("case_count" in m for m in mismatches)
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def test_runner_rejects_dynamic_prepare():
    case_dir = Path(tempfile.mkdtemp(prefix="dac_neg_prep_"))
    case_ws = Path(tempfile.mkdtemp(prefix="dac_neg_ws_"))
    try:
        case = {
            "case_id": "NEG_DYNAMIC",
            "fixtures": [],
            "executions": [
                {"id": "x", "phase": "phase1", "profile": "server",
                 "prepare": {"kind": "submission_compose", "base": "b.zip",
                             "as": "o.zip", "overlays": ["o2.zip"],
                             "manifest_overrides": {"submission_phase": "phase2"}},
                 "command": {"name": "none", "args": {}},
                 "expected": {"exit_code": 0}},
            ],
        }
        result = fr.run_case(case, case_dir, case_ws=case_ws, keep_on_failure=False)
        assert result["status"] == "FAIL"
        assert "禁止动态构造" in result["executions"][0]["failures"][0]
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)
        shutil.rmtree(case_ws, ignore_errors=True)


def test_real_corpus_audit_zero_dynamic():
    """真实 Corpus 静态审计：0 动态 / 0 缺失 / 0 哈希不一致。"""

    actual = compute_corpus_identity(fr.default_fixture_root())
    assert actual["dynamic_cases"] == 0
    assert actual["missing_fixtures"] == 0
    assert actual["hash_mismatches"] == 0
    assert actual["case_count"] == 82
    assert actual["execution_count"] == 186