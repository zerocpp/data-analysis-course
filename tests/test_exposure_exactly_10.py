"""v10.1.10 §25：Exposure 恰好 10 个匿名 Task —— 正式 Contract 数量。"""
from __future__ import annotations

import pytest

from bigdata_v10110_helpers import make_exposure_package, make_workspace
from codesetarena.exposure.contract import EXPOSURE_TASK_COUNT
from codesetarena.exposure.importer import ExposureImportError, import_exposure_package
from codesetarena.exposure.runner import exposure_status


def test_contract_count_is_exactly_10():
    assert EXPOSURE_TASK_COUNT == 10


def test_import_exactly_10_passes(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip", count=10)
    state = import_exposure_package(package, ws)
    assert state["tasks"] == [f"AT{i:03d}" for i in range(1, 11)]
    status = exposure_status(ws)
    assert status["task_count"] == 10
    assert status["expected_task_count"] == 10


def test_import_9_rejected(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp9.zip", count=9)
    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    assert "EXPOSURE_COUNT_ERROR" in str(excinfo.value)
    assert not (ws / "exposure" / "tasks").exists()


def test_import_11_rejected(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp11.zip", count=11)
    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    assert "EXPOSURE_COUNT_ERROR" in str(excinfo.value)


def test_cli_import_rejects_wrong_count(tmp_path):
    from bigdata_v10110_helpers import cli

    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp9.zip", count=9)
    code, out = cli(["exposure", "import", "--package", str(package),
                     "--workspace", str(ws)], cwd=tmp_path)
    assert code == 1
    assert "EXPOSURE_COUNT_ERROR" in out
