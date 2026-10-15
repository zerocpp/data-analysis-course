"""v10.1.10 §27/§64：Exposure 不产生任何学生正式 Submission。"""
from __future__ import annotations

from bigdata_v10110_helpers import (
    cli,
    make_exposure_package,
    make_workspace,
)
from codesetarena.exposure.contract import EXPOSURE_DIRNAME
from codesetarena.exposure.importer import import_exposure_package


def test_exposure_import_and_run_produce_no_submission(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    package = make_exposure_package(tmp_path / "BigData_Exposure_R000023.zip")
    import_exposure_package(package, ws)

    code, out = cli(["exposure", "run", "--workspace", str(ws)], cwd=tmp_path)
    assert code == 0, out

    exposure_root = ws / EXPOSURE_DIRNAME
    assert (exposure_root / "state.json").is_file()
    assert (exposure_root / "diagnostics.json").is_file()

    # 结构性红线：workspace 内不允许出现正式提交产物
    zips = [p.name for p in ws.rglob("*.zip")]
    assert zips == ["../BigData_Exposure_R000023.zip"] or all(
        name.startswith("BigData_Exposure_") for name in zips
    )
    assert not (ws / "submission").exists()
    assert not list(ws.rglob("BigData_P1_*.zip"))
    assert not list(ws.rglob("BigData_P2_*.zip"))
