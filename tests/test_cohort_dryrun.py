"""Phase E — Real-Cohort Dry-Run tests（§73–§78）。

- Real manual inventory：14 文件夹家族、5 Agent 类型、FM 覆盖门（当前缺 4 类）
- Generator 数学：200 学生、类别 14/15、Agent 40/40、交叉均衡、唯一、确定性
- 全链（stub-compliant manual）：official build → batch → ingest → assignment
  → verifier → audit → anonymous preview → leakage scan → DRY_RUN_VERIFIED，
  且无任何 reviewer package / review_package_*.tar.gz。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from scripts.cohort_dryrun_200 import (  # noqa: E402
    DRY_AGENT_TYPES,
    OFFICIAL_FMS,
    allocation_plan,
    checkpoint_a,
    discover_manual,
    generate_cohort_manifest,
    run_dryrun,
)

MANUAL = ROOT / "workspace" / "manual"
STUDENTS = 200
SEED = 20261005


@pytest.fixture(scope="module")
def inventory():
    return discover_manual(MANUAL)


# ---------------------------------------------------------------------------
# §73：Real Manual Inventory
# ---------------------------------------------------------------------------


def test_manual_folder_families_18(inventory):
    # 14 个既有 DA 家族 + 4 个新增 FM 家族（DA10_FM01/DA02_FM03/DA12_FM12/DA09_FM14）
    assert len(inventory["folder_families"]) == 18
    for family in ("DA10_FM01", "DA02_FM03", "DA12_FM12", "DA09_FM14"):
        assert family in inventory["folder_families"]


def test_manual_agent_types_exactly_5(inventory):
    assert inventory["agent_type_count"] == 5
    assert set(inventory["agent_types"]) == set(DRY_AGENT_TYPES)
    shas = {info["agent_sha256"] for info in inventory["agent_types"].values()}
    assert len(shas) == 5  # 5 个真实不同的 agent 算法


def test_manual_covers_all_official_failure_modes(inventory):
    """§44：manual 已补齐，14/14 官方 FM 全覆盖。"""
    assert inventory["manual_fm_count"] == 14
    assert inventory["missing_fm_codes"] == []
    assert set(inventory["manual_fm_codes"]) == set(OFFICIAL_FMS)
    assert inventory["status"] == "OK"
    # §19：分布维度分别报告
    assert len(inventory["failure_mode_distribution"]) == 14
    assert inventory["da_capability_distribution"]


def test_checkpoint_a_passes_on_completed_manual(inventory):
    assert checkpoint_a(inventory)["status"] == "PASS"


def test_manual_read_only(inventory):
    """discover 不改动 manual（两次 hash 一致）。"""
    before = inventory["manual_hash"]
    again = discover_manual(MANUAL)
    assert again["manual_hash"] == before


def test_real_manual_full_chain(tmp_path):
    """§74–§78：真实 manual-derived 200 全链 -> DRY_RUN_VERIFIED。"""
    out = tmp_path / "dryrun_real"
    result = run_dryrun(MANUAL, out, seed=SEED)
    assert result["final_status"] == "DRY_RUN_VERIFIED", result
    assert result["submission_build_pass"] == 200
    assert result["batch_accepted"] == 200
    assert result["canonical_tasks"] == 200
    assert result["canonical_agents"] == 200
    assert result["category_vector_length"] == 200
    assert result["non_empty_categories"] == 14
    assert result["category_min"] == 14
    assert result["category_max"] == 15
    assert result["assignment_method"] == "CATEGORY_AWARE_SWAP"
    assert result["matrix_shape"] == [200, 200]
    assert result["total_assignments"] == 2000
    assert result["verifier"] == "PASS"
    assert result["identity_leaks"] == 0
    assert result["private_content_leaks"] == 0
    assert result["anonymous_previews"] == 200
    assert result["distribution_packages_created"] == 0
    assert result["distribution"]["status"] == "SKIPPED_DRY_RUN"
    assert not (out / "distributions").exists() or not list(
        (out / "distributions").rglob("review_package_*.tar.gz")
    )
    # §28：Template usage 均衡（同一 FM 的 T1/T2 都被使用）
    usage = list(csv.reader((out / "generation" / "template_usage.csv").open()))
    assert usage[0] == ["template_id", "failure_mode", "usage_count"]
    assert len(usage) - 1 >= 14 * 2  # 至少 T1/T2 多模板被使用


# ---------------------------------------------------------------------------
# §73：Generator math（与真实 manual 内容解耦）
# ---------------------------------------------------------------------------


def _plan_checks(plan):
    sizes = [row["size"] for row in plan]
    assert sum(sizes) == STUDENTS
    assert sorted(sizes) == [14] * 10 + [15] * 4
    agent_totals = {ag: 0 for ag in DRY_AGENT_TYPES}
    per_cat = {}
    for row in plan:
        row_agents = dict(row["agent_counts"])
        per_cat[row["category"]] = row_agents
        for ag in DRY_AGENT_TYPES:
            agent_totals[ag] += row_agents[ag]
    assert agent_totals == {ag: 40 for ag in DRY_AGENT_TYPES}
    for row_agents in per_cat.values():
        assert set(row_agents) == set(DRY_AGENT_TYPES)  # 无 Category-Agent 绑定
        assert sorted(row_agents.values()) in ([2, 3, 3, 3, 3], [3, 3, 3, 3, 3])
    return per_cat


def test_allocation_plan_40_per_agent_and_14_15():
    _plan_checks(allocation_plan(SEED))
    # light agent：每个 agent 恰在 2 个 14 类中
    light_counts = {ag: 0 for ag in DRY_AGENT_TYPES}
    for row in allocation_plan(SEED):
        if row["size"] == 14:
            light = [ag for ag, c in row["agent_counts"].items() if c == 2]
            assert len(light) == 1
            light_counts[light[0]] += 1
    assert light_counts == {ag: 2 for ag in DRY_AGENT_TYPES}


def test_allocation_deterministic_and_seed_sensitive():
    a = allocation_plan(SEED)
    b = allocation_plan(SEED)
    assert a == b
    plans = [allocation_plan(s) for s in (1, 2, 3)]
    size_sigs = {tuple(sorted(row["size"] for row in p)) for p in plans}
    assert len(size_sigs) == 1  # 形状不变
    fourteen_order = [tuple(row["category"] for row in p if row["size"] == 14) for p in plans]
    assert len(set(fourteen_order)) == len(fourteen_order)  # 不同 seed 顺序不同


def _stub_inventory() -> dict:
    """合规 stub inventory（测试用）：14 FM × (T1/T2) × 5 agent 模板名。"""
    fm_templates = {}
    for idx, fm in enumerate(OFFICIAL_FMS, start=1):
        fm_templates[fm] = (
            [f"STUB_{idx:02d}_T1_{ag}" for ag in DRY_AGENT_TYPES]
            + [f"STUB_{idx:02d}_T2_{ag}" for ag in DRY_AGENT_TYPES]
        )
    return {
        "manual_fm_count": 14,
        "agent_type_count": 5,
        "fm_templates": fm_templates,
        "missing_fm_codes": [],
    }


def test_generate_manifest_counts_and_uniqueness(tmp_path):
    out = generate_cohort_manifest(
        tmp_path / "cohort_generation_manifest.jsonl", seed=SEED,
        inventory=_stub_inventory(), students=STUDENTS,
    )
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == STUDENTS
    ids = [r["synthetic_student_id"] for r in rows]
    assert len(set(ids)) == STUDENTS and min(ids) == "99000001" and max(ids) == "99000200"
    from collections import Counter

    cat_counts = Counter(r["task_category"] for r in rows)
    assert sorted(cat_counts.values()) == [14] * 10 + [15] * 4
    agent_counts = Counter(r["agent_type"] for r in rows)
    assert agent_counts == {ag: 40 for ag in DRY_AGENT_TYPES}
    cross = Counter((r["task_category"], r["agent_type"]) for r in rows)
    for fm in OFFICIAL_FMS:
        assert set(cross[(fm, ag)] for ag in DRY_AGENT_TYPES) <= {2, 3}
        assert all(cross[(fm, ag)] >= 2 for ag in DRY_AGENT_TYPES)


def test_generate_manifest_template_rotation_balanced(tmp_path):
    """§27/§28：同一 FM 的 T1/T2 模板都被使用且尽量均衡。"""
    out = generate_cohort_manifest(
        tmp_path / "m.jsonl", seed=SEED, inventory=_stub_inventory(),
        usage_path=tmp_path / "usage.csv",
    )
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    from collections import Counter

    for fm in OFFICIAL_FMS:
        for letter in DRY_AGENT_TYPES:
            used = Counter(
                r["source_template_dir"] for r in rows
                if r["task_category"] == fm and r["agent_type"] == letter
            )
            # T1/T2 都被用上（2~3 人轮换 2 个模板）
            prefix = f"STUB_{OFFICIAL_FMS.index(fm) + 1:02d}"
            assert set(used) == {f"{prefix}_T1_{letter}", f"{prefix}_T2_{letter}"}
            assert max(used.values()) - min(used.values()) <= 1


def test_generate_manifest_deterministic(tmp_path):
    a = generate_cohort_manifest(tmp_path / "a.jsonl", seed=SEED, inventory=_stub_inventory())
    b = generate_cohort_manifest(tmp_path / "b.jsonl", seed=SEED, inventory=_stub_inventory())
    assert a.read_bytes() == b.read_bytes()


# ---------------------------------------------------------------------------
# §45–§47：新增 FM 语义 + FM14 安全性
# ---------------------------------------------------------------------------

NEW_FM_FAMILIES = {
    "FM01_REQUIREMENT": "DA10_FM01",
    "FM03_DATA_TYPE": "DA02_FM03",
    "FM12_VERIFICATION": "DA12_FM12",
    "FM14_PROMPT_INJECTION": "DA09_FM14",
}


@pytest.mark.parametrize("fm,family", sorted(NEW_FM_FAMILIES.items()))
@pytest.mark.parametrize("tlevel", ["T1", "T2"])
def test_new_fm_template_semantics(fm, family, tlevel):
    """§45：新增模板 metadata 标签与 FM 一致且可加载。"""
    template = MANUAL / f"{family}_{tlevel}_A"
    assert template.is_dir()
    task = json.loads((template / "task.json").read_text(encoding="utf-8"))
    assert task["hypothesized_failure_mode"] == fm
    assert task["expected_failure_mode"] == fm.split("_")[0]
    assert task["capability"]
    import pandas as pd

    df = pd.read_csv(template / "data.csv")
    ns = {}
    exec((template / "reference.py").read_text(), ns)
    gold = ns["reference"](df)
    assert gold is not None


@pytest.mark.parametrize("fm,family", sorted(NEW_FM_FAMILIES.items()))
@pytest.mark.parametrize("letter", list("ABCDE"))
def test_new_fm_agents_are_canonical_types(fm, family, letter):
    """新增模板复用既有 5 类 Agent（核心算法零改动）。"""
    template = MANUAL / f"{family}_T1_{letter}"
    canonical = MANUAL / f"DA01_T1_{letter}" / "agent.py"
    assert (template / "agent.py").read_bytes() == canonical.read_bytes()


_DANGEROUS = ("http://", "https://", "socket", "subprocess", "os.system", "os.popen",
              "import os", "curl", "wget", "requests", "urllib", "eval(", "open(",
              "shutil", "secret", "password", "token", "api_key", "LLM_API_KEY",
              "read_env", "getenv", "! shell", "rm -rf")


@pytest.mark.parametrize("letter", list("ABCDE"))
@pytest.mark.parametrize("tlevel", ["T1", "T2"])
def test_fm14_prompt_injection_safety(letter, tlevel):
    """§46：FM14 模板无网络 / shell / secret / 破坏性内容。"""
    template = MANUAL / f"DA09_FM14_{tlevel}_{letter}"
    # 注入风险只存在于 FM14 专属内容（task/data/reference）；agent.py 是既有通用
    # harness（含合法 eval，不属于注入载体）。
    for name in ("task.json", "data.csv", "reference.py"):
        text = (template / name).read_text(encoding="utf-8", errors="ignore")
        for token in _DANGEROUS:
            assert token.lower() not in text.lower(), (name, token)
    data = (template / "data.csv").read_text(encoding="utf-8")
    assert ("忽略" in data or "跳过" in data or "ignore" in data.lower()
            or "hack" in data.lower())  # 注入为无害文本
    task = json.loads((template / "task.json").read_text(encoding="utf-8"))
    assert task["hypothesized_failure_mode"] == "FM14_PROMPT_INJECTION"


def _write_stub_workspace(directory: Path, fm: str, letter: str) -> Path:
    directory.mkdir(parents=True)
    (directory / "task.json").write_text(
        json.dumps({"task_id": "STUB", "student_id": "272401",
                    "hypothesized_failure_mode": fm,
                    "task_statement": f"stub task for {fm}"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (directory / "data.csv").write_text("region,amount\neast,10\nwest,20\n", encoding="utf-8")
    (directory / "agent.py").write_text(
        f'def run(task, df, llm):\n    """stub agent {letter}"""\n    return str(int(df["amount"].sum()))\n',
        encoding="utf-8",
    )
    (directory / "reference.py").write_text(
        "def reference(df):\n    return str(int(df['amount'].sum()))\n", encoding="utf-8"
    )
    (directory / ".env").write_text("LLM_BASE_URL=https://example.invalid/v1\nLLM_API_KEY=dummy-key\nLLM_MODEL=dummy-model\n",
                                    encoding="utf-8")
    (directory / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n", encoding="utf-8")
    return directory


@pytest.fixture(scope="module")
def stub_manual(tmp_path_factory):
    base = tmp_path_factory.mktemp("stub_manual")
    for idx, fm in enumerate(OFFICIAL_FMS, start=1):
        for letter in DRY_AGENT_TYPES:
            _write_stub_workspace(base / f"STUB_{idx:02d}_T1_{letter}", fm, letter)
    return base


def test_full_chain_stub_compliant_manual(stub_manual, tmp_path):
    """§74–§78：整条链 -> DRY_RUN_VERIFIED，无 reviewer package / archive。"""
    out = tmp_path / "dryrun_chain"
    result = run_dryrun(stub_manual, out, students=STUDENTS, seed=SEED)
    assert result["final_status"] == "DRY_RUN_VERIFIED", result
    assert result["submission_build_pass"] == 200
    assert result["submission_build_failed"] == 0
    assert result["batch_accepted"] == 200
    assert result["canonical_tasks"] == 200
    assert result["canonical_agents"] == 200
    assert result["category_vector_length"] == 200
    assert result["non_empty_categories"] == 14
    assert result["category_min"] == 14
    assert result["category_max"] == 15
    assert result["assignment_method"] == "CATEGORY_AWARE_SWAP"
    assert result["matrix_shape"] == [200, 200]
    assert result["total_assignments"] == 2000
    assert result["verifier"] == "PASS"
    assert result["identity_leaks"] == 0
    assert result["private_content_leaks"] == 0
    assert result["anonymous_previews"] == 200
    assert result["distribution_packages_created"] == 0
    assert result["distribution"]["status"] == "SKIPPED_DRY_RUN"

    # §77：无 reviewer distribution packages；§78：无 review_package_*.tar.gz
    assert not (out / "distributions").exists() or list(
        (out / "distributions").rglob("review_package_*.tar.gz")
    ) == []
    assert sorted(p.name for p in (out / "submissions").glob("DataC1004_P1_*.tar.gz")) == sorted(
        p.name for p in (out / "submissions").glob("*.tar.gz")
    )
    assert len(list((out / "submissions").glob("*.tar.gz"))) == 200

    # category vector 来自 canonical repository 重新提取
    summary = json.loads((out / "reports" / "cohort_dryrun_summary.json").read_text())
    assert summary["category_vector_length"] == 200
    assert summary["non_empty_categories"] == 14