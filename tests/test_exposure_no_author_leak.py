"""v10.1.10 §26：匿名 Exposure 包禁止 author identity 泄漏。"""
from __future__ import annotations

import pytest

from bigdata_v10110_helpers import make_exposure_package, make_workspace
from codesetarena.exposure.importer import ExposureImportError, import_exposure_package


@pytest.mark.parametrize("key", ["student_id", "author", "author_name", "author_id",
                                 "owner", "submitted_by"])
def test_identity_keys_rejected(tmp_path, key):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip", leak_keys=[key])
    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    message = str(excinfo.value)
    assert "EXPOSURE_LEAK" in message
    assert key in message
    assert not (ws / "exposure" / "state.json").exists()


def test_clean_package_imports(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp.zip")
    state = import_exposure_package(package, ws)
    assert len(state["tasks"]) == 10
