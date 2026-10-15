"""TestLab Regression Runner —— 对固定 Fixture 跑指定版本镜像的 datac validate。

用法：python3 run_regression.py --image bigdata-student:10.1.19 --label v10.1.19
结果：results/history/<label>_<run_id>/{summary.json, cases/*.json}
"""
import argparse
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
FIXTURES = LAB / "fixtures" / "task_packages"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()

    manifest = json.loads((LAB / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    out = LAB / "results" / "history" / f"{args.label}_{run_id}"
    (out / "cases").mkdir(parents=True, exist_ok=True)

    results = {}
    for name, meta in sorted(manifest.items()):
        pkg = LAB / meta["file"]
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            mount = Path(tmp)
            os.chmod(mount, 0o755)
            dst = mount / "bigdata_p1_12561005.tar.gz"
            dst.write_bytes(pkg.read_bytes())
            proc = subprocess.run(
                ["docker", "run", "--rm", "-v", f"{mount}:/lab",
                 args.image, "datac", "submission", "validate",
                 "--input", "/lab/bigdata_p1_12561005.tar.gz"],
                capture_output=True, text=True, timeout=300)
        actual_fail = proc.returncode != 0
        stderr = proc.stderr
        stdout = proc.stdout
        actual_code = ""
        for line in (stderr + chr(10) + stdout).splitlines():
            text = line.strip()
            if text.startswith("[submission]") and ": " in text:
                code = text[len("[submission]"):].split(":")[0].strip()
                if code and code not in ("validate", "validate(built)", "ingest",
                                         "build", "batch-validate"):
                    actual_code = code
                    break
        matched = ((meta["expected"] == "FAIL" and actual_fail) or
                   (meta["expected"] == "PASS" and not actual_fail))
        stage_ok = (not meta["code"]) or (meta["code"] in stderr) or \
                   (meta["expected"] == "PASS")
        status = "PASS" if (matched and stage_ok) else "FAIL"
        results[name] = {
            "expected": meta["expected"], "expected_code": meta["code"],
            "actual_fail": actual_fail, "actual_code": actual_code,
            "stage_ok": stage_ok, "status": status,
            "returncode": proc.returncode,
        }
        (out / "cases" / f"{name}.json").write_text(json.dumps({
            **results[name], "stderr_tail": stderr[-1500:]},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[{status}] {name} expected={meta['expected']}/{meta['code']} "
              f"actual={'FAIL' if actual_fail else 'PASS'}/{actual_code}")

    passed = sum(1 for r in results.values() if r["status"] == "PASS")
    summary = {
        "label": args.label, "image": args.image,
        "run_id": run_id, "finished_at": datetime.now(
            timezone.utc).isoformat(timespec="seconds"),
        "total": len(results), "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SUMMARY {args.label}: {passed}/{len(results)} PASS -> {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
