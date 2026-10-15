"""Build a synthetic student-submission repository through the REAL chain.

workspace tree -> formal tar.gz -> batch-validate -> ingest
-> canonical tasks/agents + private registry + anonymous views.

Then the assignment pipeline can be driven on it via:

    .venv/bin/codesetarena assignment --mode prepare --repository <repo> --phase phase1 --seed <S>

Synthetic only（TEST_STUDENT_*），无真实学生数据。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codesetarena import constants as K
from codesetarena.submission import contract as C
from codesetarena.submission.archive import create_submission_archive
from codesetarena.submission.ingest import ingest_submissions

FMS = list(K.HYPOTHESIZED_FM_ORDER)


def _sid(i: int) -> str:
    return f"TEST_STUDENT_{i:03d}"


def _tid(i: int) -> str:
    return "T_" + f"{i:030d}"


def _make_tree(root: Path, sid: str, task_id: str, fm: str) -> Path:
    (root / "task/data").mkdir(parents=True)
    (root / "agent").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": C.SCHEMA_VERSION, "course": "DataC1004",
                    "submission_phase": "phase1", "student_id": sid, "task_id": task_id,
                    "build_status": "passed", "files": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/task.json").write_text(
        json.dumps({"task_id": task_id, "student_id": sid,
                    "hypothesized_failure_mode": fm,
                    "task_statement": "synthetic task statement (no identity)"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/data/data.csv").write_text("region,amount\neast,10\nwest,20\n", encoding="utf-8")
    (root / "agent/agent.py").write_text(
        "def run(task, df, llm):\n    return str(int(df['amount'].sum()))\n", encoding="utf-8"
    )
    (root / "agent/agent_manifest.json").write_text(
        json.dumps({"schema_version": C.SCHEMA_VERSION, "student_id": sid,
                    "task_id": task_id, "entrypoint": "agent.py"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/reference.py").write_text(
        "def reference(df):\n    return str(int(df['amount'].sum()))\n", encoding="utf-8"
    )
    (root / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\n", encoding="utf-8")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="course_repository 输出根目录")
    parser.add_argument("--students", type=int, default=200)
    parser.add_argument("--scratch", default=None, help="中间目录（缺省 = 临时目录）")
    args = parser.parse_args()

    repo = Path(args.repo)
    if repo.exists():
        shutil.rmtree(repo)
    scratch = Path(args.scratch) if args.scratch else (repo.parent / f"{repo.name}_scratch")
    batch_in = scratch / "batch"
    trees = scratch / "trees"
    batch_in.mkdir(parents=True)
    trees.mkdir(parents=True)

    for i in range(1, args.students + 1):
        sid = _sid(i)
        tree = _make_tree(trees / sid, sid, _tid(i), FMS[(i - 1) % 14])
        create_submission_archive(tree, batch_in / f"DataC1004_P1_{sid}.tar.gz")

    report = ingest_submissions(repo, batch_in)
    if report["status"] != "PASS":
        print(f"ingest FAILED: {report['status']}", file=sys.stderr)
        return 1
    s = report["summary"]
    print(f"ingest status={report['status']} discovered={s['discovered']} "
          f"accepted={s['accepted']} ingested={s['ingested']}")
    print(f"repository -> {repo}")
    print()
    print("next steps (assignment pipeline):")
    print(f"  .venv/bin/codesetarena assignment --mode prepare --repository {repo} "
          f"--phase phase1 --seed 20261005")
    print(f"  .venv/bin/codesetarena assignment --mode report --repository {repo} "
          f"--assignment-run AR000001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())