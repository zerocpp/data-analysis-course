"""TestLab Fixture Builder —— 一次性真实生成全部 Task Package 压缩包（永久保存）。

运行：python3 build_fixtures.py   （生成后不再依赖本脚本；包内容确定性）
"""
import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

LAB = Path(__file__).resolve().parent
# DrvFS 写入有可见性延迟 → 先在 /tmp（ext4）生成，结束后 sync 回 TestLab
import tempfile as _tempfile
import os as _os
GEN_ROOT = Path(_tempfile.mkdtemp(prefix="csa_fixtures_gen_"))
OUT = GEN_ROOT / "fixtures" / "task_packages"
WS_OUT = GEN_ROOT / "fixtures" / "workspace"
OUT.mkdir(parents=True, exist_ok=True)
WS_OUT.mkdir(parents=True, exist_ok=True)

SID = "12561005"
TOP = f"bigdata_p1_{SID}"

NL = chr(10)
REF_OK = NL.join([
    "def reference(df):",
    "    return int(df['value'].sum())", "",
])
FAST_AGENT = NL.join([
    "def run(task, df, llm):",
    "    return int(df['value'].sum())", "",
])

TASK_OK = {
    "description": "计算 value 列总和。",
    "task_statement": "计算 value 列总和并只输出整数。",
    "answer": 60,
}


def csv_value_rows(n, last=60):
    lines = ["value"] + ["0"] * (n - 1) + [str(last)]
    return NL.join(lines) + NL


def csv_cols(n):
    cols = ["value"] + [f"p{i}" for i in range(1, n)]
    lines = [",".join(cols), "10", "20", "30"]
    lines += ["0"] * max(0, n - 3)
    return NL.join(lines[: n + 1]) + NL


def csv_sized_exact(target):
    def build(pad_len):
        return NL.join([
            "value,pad", "10,", "20,",
            '30,"' + "x" * pad_len + '"',
        ]) + NL

    lo, hi = 0, target
    while lo < hi:
        mid = (lo + hi) // 2
        if len(build(mid)) < target:
            lo = mid + 1
        else:
            hi = mid
    data = build(lo)
    assert len(data) == target, (len(data), target)
    return data


def _inner_tar_bytes(members, symlinks=None):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for arcname in sorted(members):
            info = tarfile.TarInfo(arcname)
            data = members[arcname]
            info.size = len(data)
            info.mtime = 1767225600
            tar.addfile(info, io.BytesIO(data))
        for link_name, target in (symlinks or []):
            info = tarfile.TarInfo(link_name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return raw.getvalue()


def write_package(name, tasks_members, links=None, *, inner_symlinks=None):
    """按真实双层契约产出完整 bigdata_p1 外层总包（确定性字节）。"""

    tasks_tar = _inner_tar_bytes(tasks_members, links or inner_symlinks)
    agent_members = {f"Agent_{SID}/agent.py": FAST_AGENT.encode()}
    agent_tar = _inner_tar_bytes(agent_members)
    metadata = {
        "workspace": "phase1", "student_id": SID,
        "task_id": f"TASK_{SID}", "agent_id": f"AGENT_{SID}",
        "hypothesized_failure_mode": "FM_AGGREGATION",
    }

    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{TOP}.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for arcname, data in (
            (f"{TOP}/Tasks_{SID}.tar.gz", tasks_tar),
            (f"{TOP}/Agent_{SID}.tar.gz", agent_tar),
            (f"{TOP}/metadata.json",
             (json.dumps(metadata, ensure_ascii=False, indent=2) + NL).encode("utf-8")),
        ):
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mtime = 1767225600
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(raw.getvalue())
    return path


def inner(*, task_json=None, data_csv=None, reference=None, drop=(), extra=None,
          inner_symlinks=None):
    if task_json is None:
        task_json = dict(TASK_OK)
    members = {
        "task.json": (json.dumps(task_json, ensure_ascii=False, indent=2) + NL
                      ).encode("utf-8"),
        "data.csv": (data_csv if isinstance(data_csv, bytes)
                             else (data_csv if data_csv is not None
                                   else "value\n10\n20\n30\n").encode()),
        "reference.py": (reference if reference is not None else REF_OK).encode(),
    }
    for key in drop:
        members.pop(key, None)
    for key, value in (extra or {}).items():
        members[key] = value.encode() if isinstance(value, str) else value
    return members, inner_symlinks


def base_task(**overrides):
    task = dict(TASK_OK)
    task.update(overrides)
    return task


made = {}

# ── Existing-20 ──
members, links = inner()
made["TASK_VALID_001"] = (write_package("TASK_VALID_001", members, links), "PASS", "")

members, links = inner(task_json=base_task(hypothesized_failure_mode="FM_AGGREGATION"))
made["TASK_LEGACY_FAILURE_MODE_IN_TASK_JSON_001"] = (
    write_package("TASK_LEGACY_FAILURE_MODE_IN_TASK_JSON_001", members, links),
    "FAIL", "TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE")

members, links = inner(drop=("task.json",))
made["TASK_MISSING_TASK_JSON_001"] = (
    write_package("TASK_MISSING_TASK_JSON_001", members, links),
    "FAIL", "BAD_TASKS_PACKAGE")

members, links = inner(drop=("data.csv",))
made["TASK_MISSING_DATA_CSV_001"] = (
    write_package("TASK_MISSING_DATA_CSV_001", members, links),
    "FAIL", "BAD_TASKS_PACKAGE")

members, links = inner(drop=("reference.py",))
made["TASK_MISSING_REFERENCE_001"] = (
    write_package("TASK_MISSING_REFERENCE_001", members, links),
    "FAIL", "BAD_TASKS_PACKAGE")

members, links = inner(extra={"extra.txt": "surprise"})
made["TASK_EXTRA_FILE_001"] = (
    write_package("TASK_EXTRA_FILE_001", members, links),
    "FAIL", "BAD_TASKS_PACKAGE")

members, links = inner(task_json="{not json", drop=("task.json",),
                       extra={"task.json": b"{not json"})
made["TASK_INVALID_JSON_001"] = (
    write_package("TASK_INVALID_JSON_001", members, links),
    "FAIL", "INVALID_TASK_JSON")

members, links = inner(task_json=base_task(drop=("description",)))
made["TASK_MISSING_DESCRIPTION_001"] = (
    write_package("TASK_MISSING_DESCRIPTION_001", members, links),
    "FAIL", "BAD_TASK_JSON")

members, links = inner(task_json=base_task(drop=("task_statement",)))
made["TASK_MISSING_TASK_STATEMENT_001"] = (
    write_package("TASK_MISSING_TASK_STATEMENT_001", members, links),
    "FAIL", "BAD_TASK_JSON")

members, links = inner(task_json=base_task(drop=("answer",)))
made["TASK_MISSING_ANSWER_001"] = (
    write_package("TASK_MISSING_ANSWER_001", members, links),
    "FAIL", "BAD_TASK_JSON")

members, links = inner(task_json=base_task(answer=61))
made["TASK_ANSWER_MISMATCH_001"] = (
    write_package("TASK_ANSWER_MISMATCH_001", members, links),
    "FAIL", "ANSWER_MISMATCH")

members, links = inner(reference="def reference(df):" + NL + "    return ((:")
made["TASK_REFERENCE_SYNTAX_ERROR_001"] = (
    write_package("TASK_REFERENCE_SYNTAX_ERROR_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(reference="def not_reference(df):" + NL + "    return 60")
made["TASK_REFERENCE_MISSING_FUNCTION_001"] = (
    write_package("TASK_REFERENCE_MISSING_FUNCTION_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(
    reference="def reference(data_frame):" + NL +
              "    return int(data_frame['value'].sum())")
made["TASK_REFERENCE_WRONG_SIGNATURE_001"] = (
    write_package("TASK_REFERENCE_WRONG_SIGNATURE_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(
    reference="import os" + NL + NL + "def reference(df):" + NL +
              "    return int(df['value'].sum())")
made["TASK_REFERENCE_FORBIDDEN_IMPORT_001"] = (
    write_package("TASK_REFERENCE_FORBIDDEN_IMPORT_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(
    reference="def reference(df):" + NL +
              "    return int(open('x').read() or 60)")
made["TASK_REFERENCE_FORBIDDEN_FILE_IO_001"] = (
    write_package("TASK_REFERENCE_FORBIDDEN_FILE_IO_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(reference="def reference(df):" + NL + "    return 60")
made["TASK_REFERENCE_HARDCODED_ANSWER_001"] = (
    write_package("TASK_REFERENCE_HARDCODED_ANSWER_001", members, links),
    "FAIL", "REFERENCE_HARDCODED_ANSWER")

members, links = inner(data_csv='value\n"未闭合' + chr(0) + "\n")
made["TASK_DATA_BAD_CSV_001"] = (
    write_package("TASK_DATA_BAD_CSV_001", members, links),
    "FAIL", "INVALID_CSV")

members, links = inner(data_csv="region,amount" + NL + "east,10" + NL)
made["TASK_DATA_MISSING_REQUIRED_COLUMN_001"] = (
    write_package("TASK_DATA_MISSING_REQUIRED_COLUMN_001", members, links),
    "FAIL", "REFERENCE_ERROR")

members, links = inner(
    reference="def reference(df):" + NL + "    raise ValueError('boom')")
made["TASK_REFERENCE_RUNTIME_ERROR_001"] = (
    write_package("TASK_REFERENCE_RUNTIME_ERROR_001", members, links),
    "FAIL", "REFERENCE_ERROR")

# ── 新增 12（v10.1.19 Input Contract Hardening）──
members, links = inner(drop=("data.csv",), extra={"data1.csv": "value\n60\n"})
made["TASK_DATA_WRONG_FILENAME_001"] = (
    write_package("TASK_DATA_WRONG_FILENAME_001", members, links),
    "FAIL", "BAD_TASKS_PACKAGE")

members, links = inner(
    task_json=base_task(answer=30),
    data_csv="region,amount" + NL + "华东,10" + NL + "华西,20" + NL,
    reference="def reference(df):" + NL + "    return int(df['amount'].sum())")
made["TASK_UTF8_CHINESE_VALID_001"] = (
    write_package("TASK_UTF8_CHINESE_VALID_001", members, links),
    "PASS", "")

gbk_cn = ("region,amount" + NL + "华东,10" + NL + "华西,20" + NL).encode("gbk")
members, links = inner(data_csv=gbk_cn.decode("latin-1"))
made["TASK_CSV_GBK_ENCODING_001"] = (
    write_package("TASK_CSV_GBK_ENCODING_001", members, links),
    "FAIL", "INVALID_CSV_ENCODING")

members, links = inner(data_csv=b"region,amount" + b"\n" + b"\xff\xfe\xbaday,10\n")
made["TASK_CSV_INVALID_UTF8_001"] = (
    write_package("TASK_CSV_INVALID_UTF8_001", members, links),
    "FAIL", "INVALID_CSV_ENCODING")

members, links = inner(data_csv=csv_value_rows(9999))
made["TASK_ROWS_AT_LIMIT_001"] = (
    write_package("TASK_ROWS_AT_LIMIT_001", members, links),
    "PASS", "")

members, links = inner(data_csv=csv_value_rows(10000))
made["TASK_ROWS_OVER_LIMIT_001"] = (
    write_package("TASK_ROWS_OVER_LIMIT_001", members, links),
    "FAIL", "CSV_TOO_MANY_ROWS")

members, links = inner(data_csv=csv_cols(100))
made["TASK_COLUMNS_AT_LIMIT_001"] = (
    write_package("TASK_COLUMNS_AT_LIMIT_001", members, links),
    "PASS", "")

members, links = inner(data_csv=csv_cols(101))
made["TASK_COLUMNS_OVER_LIMIT_001"] = (
    write_package("TASK_COLUMNS_OVER_LIMIT_001", members, links),
    "FAIL", "CSV_TOO_MANY_COLUMNS")

members, links = inner(data_csv=csv_sized_exact(1024 * 1024))
made["TASK_FILE_SIZE_AT_LIMIT_001"] = (
    write_package("TASK_FILE_SIZE_AT_LIMIT_001", members, links),
    "PASS", "")

members, links = inner(data_csv=csv_sized_exact(1024 * 1024 + 1024))
made["TASK_FILE_SIZE_OVER_LIMIT_001"] = (
    write_package("TASK_FILE_SIZE_OVER_LIMIT_001", members, links),
    "FAIL", "CSV_FILE_TOO_LARGE")

members, links = inner(extra={"../escape.txt": "evil"})
made["TASK_ARCHIVE_PATH_TRAVERSAL_001"] = (
    write_package("TASK_ARCHIVE_PATH_TRAVERSAL_001", members, links),
    "FAIL", "TAR_PATH_TRAVERSAL")

members, links = inner(extra={"/tmp/escape_abs.txt": "evil"})
made["TASK_ARCHIVE_ABSOLUTE_PATH_001"] = (
    write_package("TASK_ARCHIVE_ABSOLUTE_PATH_001", members, links),
    "FAIL", "TAR_PATH_TRAVERSAL")

members, links = inner(inner_symlinks=[("escape_link", "../../outside_escape.txt")])
made["TASK_ARCHIVE_SYMLINK_ESCAPE_001"] = (
    write_package("TASK_ARCHIVE_SYMLINK_ESCAPE_001", members, links),
    "FAIL", "TAR_UNSAFE_LINK")

# ── workspace fixture（UTF-8 中文合法 P1）──
ws = WS_OUT / "WORKSPACE_UTF8_CHINESE_VALID_001"
if ws.exists():
    shutil.rmtree(ws)
(ws / "phase1").mkdir(parents=True, exist_ok=True)
(ws / ".env").write_text(
    "LLM_BASE_URL=http://x" + NL + "LLM_API_KEY=k" + NL + "LLM_MODEL=m" + NL,
    encoding="utf-8")
(ws / "README.md").write_text("# demo workspace" + NL, encoding="utf-8")
(ws / "phase1" / "metadata.json").write_text(json.dumps({
    "workspace": "phase1", "student_id": "12561005",
    "task_id": "TASK_12561005", "agent_id": "AGENT_12561005",
    "hypothesized_failure_mode": "FM_AGGREGATION"},
    ensure_ascii=False, indent=2) + NL, encoding="utf-8")
(ws / "phase1" / "task.json").write_text(json.dumps({
    "description": "华东/华西聚合。", "task_statement": "按 region 求 amount 总和并只输出整数。",
    "answer": 30}, ensure_ascii=False, indent=2) + NL, encoding="utf-8")
(ws / "phase1" / "data.csv").write_text(
    "region,amount" + NL + "华东,10" + NL + "华西,20" + NL, encoding="utf-8")
(ws / "phase1" / "reference.py").write_text(
    "def reference(df):" + NL + "    return int(df['amount'].sum())" + NL,
    encoding="utf-8")
(ws / "phase1" / "agent.py").write_text(
    "def run(task, df, llm):" + NL + "    return int(df['amount'].sum())" + NL,
    encoding="utf-8")

# ── 清单 ──
manifest = {}
for name, (path, expected, code) in sorted(made.items()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest[name] = {
        "file": str(path.relative_to(path.parents[3])),
        "sha256": digest, "expected": expected, "code": code,
    }
(LAB / "fixtures" / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + NL, encoding="utf-8")
print(f"fixtures: {len(manifest)}")

# ── 同步回 TestLab（先 sync 保证 /mnt/d 可见）──
dest_root = LAB / "fixtures"
dest_root.mkdir(parents=True, exist_ok=True)
if (dest_root / "task_packages").exists():
    shutil.rmtree(dest_root / "task_packages")
shutil.copytree(OUT, dest_root / "task_packages")
if (WS_OUT / "WORKSPACE_UTF8_CHINESE_VALID_001").exists():
    ws_dest = dest_root / "workspace" / "WORKSPACE_UTF8_CHINESE_VALID_001"
    if ws_dest.exists():
        shutil.rmtree(ws_dest)
    shutil.copytree(WS_OUT / "WORKSPACE_UTF8_CHINESE_VALID_001", ws_dest)
_os.sync()
print(f"synced -> {dest_root}")
