#!/usr/bin/env python3
"""Phase 2 发放包构建器（教师端；v10.1.14 两阶段协议）。

课程组用本脚本把「学生 Phase 2 初始工作区」打成一个可直接发放的压缩包。
学生解压后原地修改，不需要任何 scaffold 命令：

    python3 scripts/build_phase2_package.py \
        --output release/phase2_student_sample/BigData_Phase2_R000001.zip

包内结构（顶层自带路径前缀，学生**解压到发行包根目录即可，零拷贝零创建**）::

    PHASE2_解压说明.txt          ← 落在发行包根目录
    workspace/phase2/
    ├── task.json / data.csv / agent.py / reference.py / .env.example
    ├── assignment.json            分配权威（anonymous_tasks）
    ├── tasks/AT*/task.json + data/   10 个已分配匿名 Task
    ├── review-comments.json       唯一审稿记录模板（score/comments 留空待填）
    └── 实验报告模板.docx           学生另存为 实验报告.docx 填写

--assignment-dir 可选：指向 assignment distribute 的产物目录（含 assignment.json +
tasks/）；缺省 demo 模式生成课程示例的 10 个匿名 Task（AT028..AT192）。
输出为确定性字节（固定时间戳），同名重建哈希一致。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ZIP_STAMP = (2026, 10, 8, 0, 0, 0)
ZIP_PREFIX = "workspace/phase2"
"""发放包内固定路径前缀：整包解压到发行包根目录即自动落位（零拷贝）。"""

UNZIP_README_NAME = "PHASE2_解压说明.txt"
UNZIP_README = """Phase 2 发放包（BigData v10.1.14）

用法（只有一步）：
  把本压缩包整体解压到发行包根目录（即 bin/datac 所在的 BigData-v10.1.14 目录），
  文件会自动出现在 workspace/phase2/ 下，无需拷贝、无需手动创建任何文件。

然后：
  1. 在 workspace/phase2/ 里原地修改 task.json / data.csv / agent.py（= Task v2 / Agent v2）
  2. 用 Word 打开 实验报告模板.docx 填写，另存为 实验报告.docx（同目录）
  3. 填写 review-comments.json（每个匿名 Task 一条：task_id / score 1~5 / 非空 comments）
  4. task.json 里的 student_id / task_id 占位符改成自己的（task_id 与 P1 保持一致）
  5. bin/datac run --workspace /workspace/phase2
  6. bin/datac submission build --input /workspace/phase2 --student-id 你的学号
"""

DEMO_ASSIGNED = ["AT028", "AT035", "AT066", "AT086", "AT101",
                 "AT115", "AT130", "AT165", "AT182", "AT192"]

REPORT_SECTIONS = (
    "一、Task v2 设计思路",
    "二、Agent v2 设计思路",
    "三、代表性失败案例",
    "四、根据失败进行的改进",
)

DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _paragraph(text: str, *, bold: bool = False) -> str:
    weight = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p><w:r>{weight}<w:t xml:space=\"preserve\">{_esc(text)}</w:t></w:r></w:p>"


def build_experiment_report_template() -> bytes:
    """生成可被 Word 打开的最小 docx 实验报告模板（含四个必填小节）。"""
    body = [_paragraph("BigData 课程实验报告（Phase 2）", bold=True),
            _paragraph("填写完成后另存为：实验报告.docx（放在工作区根目录，导出校验必查）。")]
    for section in REPORT_SECTIONS:
        body.append(_paragraph(section, bold=True))
        body.append(_paragraph("（在此填写）"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in (("[Content_Types].xml", DOCX_CONTENT_TYPES),
                           ("_rels/.rels", DOCX_RELS),
                           ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    return buf.getvalue()


def collect_assigned_tasks(assignment_dir: Path | None) -> tuple[list[str], dict[str, bytes]]:
    """返回 (assigned_ids, {zip 相对路径: 字节})；nested/flat 布局都规整为 nested。"""
    files: dict[str, bytes] = {}
    if assignment_dir is None:
        demo_task_data = (REPO_ROOT / "docker/template/data.csv").read_bytes()
        for at in DEMO_ASSIGNED:
            task = {
                "task_id": at,
                "description": f"匿名示例任务 {at}（demo；正式发放请用 --assignment-dir）",
                "task_statement": "data.csv 含 region、amount 两列，输出 amount 总和（JSON）。",
            }
            files[f"tasks/{at}/task/task.json"] = (json.dumps(task, ensure_ascii=False,
                                                             indent=2) + "\n").encode("utf-8")
            files[f"tasks/{at}/task/data/data.csv"] = demo_task_data
        return list(DEMO_ASSIGNED), files

    assignment = json.loads((assignment_dir / "assignment.json").read_text(encoding="utf-8"))
    assigned = [str(t) for t in assignment.get("anonymous_tasks") or []]
    if not assigned:
        sys.exit("[phase2-package] assignment.json 缺少 anonymous_tasks")
    tasks_root = assignment_dir / "tasks"
    for at in assigned:
        nested = tasks_root / at / "task" / "task.json"
        flat = tasks_root / at / "task.json"
        task_json = nested if nested.is_file() else flat
        if task_json is None or not task_json.is_file():
            sys.exit(f"[phase2-package] 缺少 {at} 的 task.json")
        data_dir = task_json.parent / "data"
        files[f"tasks/{at}/task/task.json"] = task_json.read_bytes()
        if data_dir.is_dir():
            for data_file in sorted(data_dir.iterdir()):
                if data_file.is_file():
                    files[f"tasks/{at}/task/data/{data_file.name}"] = data_file.read_bytes()
    return assigned, files


def build_phase2_package(output: Path, *, assignment_dir: Path | None,
                         workspace_template: Path, reviewer_id: str) -> Path:
    assigned, task_files = collect_assigned_tasks(assignment_dir)
    if len(set(assigned)) != len(assigned):
        sys.exit("[phase2-package] anonymous_tasks 存在重复 ID")

    payload: dict[str, bytes] = {}
    for name in ("task.json", "data.csv", "agent.py", "reference.py", ".env.example"):
        path = workspace_template / name
        if not path.is_file():
            sys.exit(f"[phase2-package] workspace 模板缺少 {name}")
        payload[name] = path.read_bytes()

    payload["assignment.json"] = (json.dumps({
        "assignment_id": f"AR000001_{reviewer_id}",
        "phase": "phase2",
        "reviewer_package_id": reviewer_id,
        "anonymous_tasks": assigned,
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload["review-comments.json"] = (json.dumps({
        at: {"task_id": at, "score": None, "comments": ""}
        for at in assigned
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload["实验报告模板.docx"] = build_experiment_report_template()
    payload.update(task_files)

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        entries = {UNZIP_README_NAME: UNZIP_README.encode("utf-8")}
        for arc, data in payload.items():
            entries[f"{ZIP_PREFIX}/{arc}"] = data
        for arc in sorted(entries):
            info = zipfile.ZipInfo(arc, date_time=ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, entries[arc])

    unpacked = output.parent / output.stem
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir(parents=True)
    for arc, data in entries.items():
        dest = unpacked / arc
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Phase 2 学生发放包（教师端）")
    parser.add_argument("--output", default="release/phase2_student_sample/BigData_Phase2_R000001.zip",
                        help="输出 zip 路径（同时产出同名解包目录）")
    parser.add_argument("--assignment-dir", default=None,
                        help="assignment distribute 产物目录（assignment.json + tasks/）；缺省 demo 10 题")
    parser.add_argument("--workspace-template", default=str(REPO_ROOT / "docker/template"),
                        help="P1 六件套模板目录（默认 docker/template）")
    parser.add_argument("--reviewer-id", default="R000001", help="评审包编号（默认 R000001）")
    args = parser.parse_args()

    output = Path(args.output)
    assignment_dir = Path(args.assignment_dir) if args.assignment_dir else None
    result = build_phase2_package(output, assignment_dir=assignment_dir,
                                  workspace_template=Path(args.workspace_template),
                                  reviewer_id=args.reviewer_id)
    unpacked = result.parent / result.stem
    print(f"[phase2-package] zip      -> {result} ({result.stat().st_size} bytes)")
    print(f"[phase2-package] unpacked -> {unpacked}")
    print("[phase2-package] 发放给学生：整包解压到发行包根目录（bin/datac 同级）即自动落位 "
          "workspace/phase2/，无需拷贝")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
