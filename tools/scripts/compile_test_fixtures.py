#!/usr/bin/env python3
"""Fixture Precompilation / Migration Tool（Immutable Fixture Upgrade）。

Builder 可以聪明，Runner 必须笨。本工具负责把 corpus 编译/审计为静态形态：

    --scan    列出仍依赖运行期动态输入构造的 Case（目标：0）
    --compile 执行预编译（base+overlay+override 合成最终 ZIP；重命名落 input/；
              删除运行期 mutation 字段；移除纯 infra 语义旧 case）
    --verify  校验：静态性 + input/fixture SHA256 + 无运行期构造残留

用法::

    .venv/bin/python scripts/compile_test_fixtures.py --scan
    .venv/bin/python scripts/compile_test_fixtures.py --compile
    .venv/bin/python scripts/compile_test_fixtures.py --verify [--detail]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src"), str(ROOT / "tests")):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.integration.fixture_compile import scan_dynamic, verify_static  # noqa: E402
from tests.integration.fixture_runner import default_fixture_root  # noqa: E402

# 运行期动态标记（允许出现在 builder/迁移工具，不允许出现在 Runner 依赖的 case.json）
SEARCH_TOKENS = ("base.zip", "overlay.zip", "build_time_mutation", "runtime_mutation",
                 "manifest_override", "manifest_overrides", "submission_compose",
                 "apply_mutation", "patch_manifest", "compose_submission")


def _all_cases(fixture_root: Path) -> list[dict]:
    index_path = fixture_root / "case_index.json"
    if not index_path.is_file():
        print(f"case_index 缺失: {index_path}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(index_path.read_text(encoding="utf-8")).get("cases") or []


def cmd_scan(fixture_root: Path) -> int:
    dynamic: list[tuple[str, dict]] = []
    for entry in _all_cases(fixture_root):
        case_dir = fixture_root / entry["dir"]
        case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        flags = scan_dynamic(case)
        if flags:
            dynamic.append((entry["case_id"], flags))
    print(f"TOTAL_CASES={len(_all_cases(fixture_root))}")
    print(f"REMAINING_DYNAMIC_CASES={len(dynamic)}")
    for cid, flags in sorted(dynamic):
        print(f"  DYNAMIC {cid}: {flags}")
    return 1 if dynamic else 0


def cmd_compile(fixture_root: Path) -> int:
    """执行预编译（等价 build_all 的收尾 pass，可独立运行于已构建 corpus）。"""

    from tests.integration.fixture_compile import precompile_corpus

    index = precompile_corpus(fixture_root, _all_cases(fixture_root))
    # 重写 index（移除 legacy infra case）
    (fixture_root / "case_index.json").write_text(
        json.dumps({"schema_version": 1, "corpus_version": "1.0.0", "cases": index},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"compiled {len(index)} cases (static form)")
    return 0


def cmd_verify(fixture_root: Path, *, detail: bool) -> int:
    errors: list[str] = []
    total = 0
    for entry in _all_cases(fixture_root):
        case_dir = fixture_root / entry["dir"]
        case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        total += 1
        problems = verify_static(case, case_dir)
        flags = scan_dynamic(case)
        if flags:
            problems.append(f"动态标记残留: {flags}")
        if problems:
            errors.append(f"{entry['case_id']}: {problems}")
            if detail:
                for p in problems:
                    print(f"  FAIL {entry['case_id']}: {p}")
        elif detail:
            print(f"  ok   {entry['case_id']}")
    print(f"TOTAL_CASES={total}  VERIFY_FAILS={len(errors)}")
    for e in errors[:30]:
        print(f"  - {e}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compile_test_fixtures")
    parser.add_argument("--scan", action="store_true", help="列出运行期动态构造 case")
    parser.add_argument("--compile", action="store_true", help="执行 fixture 预编译")
    parser.add_argument("--verify", action="store_true", help="校验静态化 + SHA256")
    parser.add_argument("--detail", action="store_true", help="verify 逐 case 输出")
    parser.add_argument("--fixture-root", default=None)
    args = parser.parse_args(argv)

    fixture_root = Path(args.fixture_root).resolve() if args.fixture_root else default_fixture_root()
    if args.scan:
        return cmd_scan(fixture_root)
    if args.compile:
        return cmd_compile(fixture_root)
    if args.verify:
        return cmd_verify(fixture_root, detail=args.detail)
    print("请指定 --scan / --compile / --verify", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())