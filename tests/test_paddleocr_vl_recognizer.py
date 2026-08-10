"""Non-GPU tests for PaddleOCRVLRecognizer (ticket #6).

These exercise only what's verifiable without a GPU, real weights, or the
`paddleocr`/`paddlex`/PaddlePaddle stack installed: construction-time
validation (missing model_path, missing/empty weight subdirectories) and the
missing-dependency error path. They deliberately do not import `paddleocr` —
the whole point is that PaddleOCRVLRecognizer fails clearly *before* trying
to.

The real GPU inference path is covered separately by the
`@pytest.mark.gpu` smoke test in test_paddleocr_vl_gpu_smoke.py, which is
excluded from the default run (see pyproject.toml addopts) and was not run
here.
"""

from __future__ import annotations

import pytest

from docolage.vlm_paddleocr import ModelWeightsNotFoundError, PaddleOCRVLRecognizer


def test_empty_model_path_raises_actionable_error():
    with pytest.raises(ModelWeightsNotFoundError, match="model_path"):
        PaddleOCRVLRecognizer(model_path="")


def test_nonexistent_model_path_raises_actionable_error(tmp_path):
    missing = tmp_path / "does-not-exist"

    with pytest.raises(ModelWeightsNotFoundError, match="does not exist"):
        PaddleOCRVLRecognizer(model_path=str(missing))


def test_model_path_missing_weight_subdirs_raises_actionable_error(tmp_path):
    # model_path exists but has neither 'layout_detection' nor 'vl_rec'.
    model_root = tmp_path / "paddleocr-vl-weights"
    model_root.mkdir()

    with pytest.raises(ModelWeightsNotFoundError, match="layout_detection"):
        PaddleOCRVLRecognizer(model_path=str(model_root))


def test_model_path_with_empty_weight_subdir_raises_actionable_error(tmp_path):
    model_root = tmp_path / "paddleocr-vl-weights"
    (model_root / "layout_detection").mkdir(parents=True)
    (model_root / "vl_rec").mkdir()  # present but empty

    with pytest.raises(ModelWeightsNotFoundError, match="vl_rec"):
        PaddleOCRVLRecognizer(model_path=str(model_root))


def test_valid_weight_dirs_but_missing_paddleocr_package_raises_import_error(tmp_path, monkeypatch):
    # Simulate weights being present on the Volume, but the `paddleocr`
    # package not installed in this environment (the actual state of this
    # sandbox: the real package pulls in `paddlex` + PaddlePaddle-GPU, which
    # are deliberately not part of the default install per ADR-0005).
    model_root = tmp_path / "paddleocr-vl-weights"
    (model_root / "layout_detection" / "dummy.pdiparams").parent.mkdir(parents=True)
    (model_root / "layout_detection" / "dummy.pdiparams").write_bytes(b"")
    (model_root / "vl_rec" / "config.json").parent.mkdir(parents=True)
    (model_root / "vl_rec" / "config.json").write_text("{}")

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "paddleocr":
            raise ImportError("No module named 'paddleocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ImportError, match="paddleocr"):
        PaddleOCRVLRecognizer(model_path=str(model_root))


def test_explicit_weight_dirs_override_model_path_layout(tmp_path, monkeypatch):
    # config can point layout_detection_model_dir / vl_rec_model_dir
    # somewhere other than model_path/{layout_detection,vl_rec} — validation
    # must check the resolved dirs, not the defaults.
    layout_dir = tmp_path / "custom-layout"
    layout_dir.mkdir()
    (layout_dir / "weights.bin").write_bytes(b"x")
    vl_rec_dir = tmp_path / "custom-vlrec"
    vl_rec_dir.mkdir()
    (vl_rec_dir / "weights.bin").write_bytes(b"x")

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "paddleocr":
            raise ImportError("No module named 'paddleocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    # model_path itself points nowhere near the real weights, but the
    # explicit overrides should be what's validated and used.
    model_root = tmp_path / "model-root-not-containing-weights"
    model_root.mkdir()

    with pytest.raises(ImportError):
        PaddleOCRVLRecognizer(
            model_path=str(model_root),
            layout_detection_model_dir=str(layout_dir),
            vl_rec_model_dir=str(vl_rec_dir),
        )


def test_config_wiring_selects_real_recognizer_class(tmp_path, monkeypatch):
    # Selecting the real recognizer via DocolageConfig is just constructing
    # it and assigning it to config.vlm_recognizer (same pattern as
    # FakeVLMRecognizer) — no separate factory/flag exists. This test
    # confirms that wiring path type-checks and fails the same way
    # (missing paddleocr) rather than being blocked by DocolageConfig
    # itself.
    import builtins

    import docolage

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "paddleocr":
            raise ImportError("No module named 'paddleocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    model_root = tmp_path / "paddleocr-vl-weights"
    (model_root / "layout_detection").mkdir(parents=True)
    (model_root / "layout_detection" / "w.bin").write_bytes(b"x")
    (model_root / "vl_rec").mkdir()
    (model_root / "vl_rec" / "w.bin").write_bytes(b"x")

    with pytest.raises(ImportError, match="paddleocr"):
        docolage.DocolageConfig(
            model_path=str(model_root),
            vlm_recognizer=docolage.PaddleOCRVLRecognizer(model_path=str(model_root)),
        )
