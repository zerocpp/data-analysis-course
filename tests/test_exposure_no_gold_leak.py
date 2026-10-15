"""v10.1.10 §26：匿名 Exposure 包禁止 gold / reference / expected 泄漏。"""
from __future__ import annotations

import pytest

from bigdata_v10110_helpers import make_exposure_package, make_workspace
from codesetarena.exposure.importer import ExposureImportError, import_exposure_package


@pytest.mark.parametrize("key", ["declared_answer", "gold_answer", "expected_answer",
                                 "expected_failure_mode", "author_expected",
                                 "reference_solution"])
def test_gold_like_keys_rejected(tmp_path, key):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip", leak_keys=[key])
    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    assert "EXPOSURE_LEAK" in str(excinfo.value)


def test_reference_py_file_rejected(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip", include_reference=True)
    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    message = str(excinfo.value)
    assert "EXPOSURE_LEAK" in message
    assert "reference.py" in message


def test_env_file_rejected(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip", include_env=True)
    with pytest.raises(ExposureImportError):
        import_exposure_package(package, ws)
