"""DOCX/PPTX support via headless LibreOffice (ticket #10).

Real end-to-end conversion tests only run when `soffice`/`libreoffice`
is actually on PATH (checked at collection time). Without it, we still
verify: the missing-binary error path, the conversion-failure error
path, and that .docx/.pptx dispatch to the LibreOffice conversion step
rather than falling through to UnsupportedFormatError.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import docolage
from docolage.exceptions import DocumentConversionError, LibreOfficeNotFoundError
from docolage import office_convert
from docolage.formats import office as office_format

SOFFICE_AVAILABLE = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def _config(output_dir: Path) -> docolage.DocolageConfig:
    return docolage.DocolageConfig(output_volume_path=str(output_dir))


# ---------------------------------------------------------------------------
# Missing binary -> actionable error, not a raw FileNotFoundError.
# ---------------------------------------------------------------------------

def test_missing_soffice_binary_raises_actionable_error(monkeypatch, tmp_output_volume):
    def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'soffice'")

    monkeypatch.setattr(office_convert.subprocess, "run", _raise_not_found)

    with pytest.raises(LibreOfficeNotFoundError) as exc_info:
        office_convert.convert_to_pdf(Path("fake.docx"), tmp_output_volume)

    message = str(exc_info.value)
    assert "LibreOffice" in message
    assert "soffice" in message
    assert "install" in message.lower()


def test_missing_soffice_binary_surfaces_through_parse(monkeypatch, tmp_output_volume, fixtures_dir):
    def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'soffice'")

    monkeypatch.setattr(office_convert.subprocess, "run", _raise_not_found)

    with pytest.raises(LibreOfficeNotFoundError):
        docolage.parse(str(fixtures_dir / "office_sample.docx"), _config(tmp_output_volume))


# ---------------------------------------------------------------------------
# Conversion failure for a specific file -> actionable error, not a trace.
# ---------------------------------------------------------------------------

def test_conversion_failure_raises_actionable_error(monkeypatch, tmp_output_volume):
    class _FailedResult:
        returncode = 1
        stdout = ""
        stderr = "soffice: source file could not be loaded"

    monkeypatch.setattr(
        office_convert.subprocess, "run", lambda *a, **k: _FailedResult()
    )

    with pytest.raises(DocumentConversionError) as exc_info:
        office_convert.convert_to_pdf(Path("corrupt.pptx"), tmp_output_volume)

    message = str(exc_info.value)
    assert "corrupt.pptx" in message
    assert "source file could not be loaded" in message


def test_conversion_timeout_raises_actionable_error(monkeypatch, tmp_output_volume):
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=120)

    monkeypatch.setattr(office_convert.subprocess, "run", _raise_timeout)

    with pytest.raises(DocumentConversionError):
        office_convert.convert_to_pdf(Path("slow.docx"), tmp_output_volume)


def test_conversion_success_but_no_output_file_is_actionable(monkeypatch, tmp_output_volume):
    """LibreOffice can exit 0 yet still not produce the expected PDF."""

    class _SilentlyFailedResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        office_convert.subprocess, "run", lambda *a, **k: _SilentlyFailedResult()
    )

    with pytest.raises(DocumentConversionError):
        office_convert.convert_to_pdf(Path("ghost.docx"), tmp_output_volume)


# ---------------------------------------------------------------------------
# Dispatch wiring: .docx/.pptx route to the LibreOffice conversion step.
# ---------------------------------------------------------------------------

def test_docx_extension_dispatches_to_office_handler(monkeypatch, tmp_output_volume, fixtures_dir):
    called = {}

    def _fake_parse(path, config):
        called["path"] = path
        return {"document": {"pages": [], "elements": []}, "error_status": []}

    monkeypatch.setattr(office_format, "parse", _fake_parse)
    monkeypatch.setitem(docolage.parser._HANDLERS, ".docx", _fake_parse)

    result = docolage.parse(str(fixtures_dir / "office_sample.docx"), _config(tmp_output_volume))

    assert called["path"] == Path(str(fixtures_dir / "office_sample.docx"))
    assert result["document"]["pages"] == []


def test_pptx_extension_dispatches_to_office_handler(monkeypatch, tmp_output_volume, fixtures_dir):
    called = {}

    def _fake_parse(path, config):
        called["path"] = path
        return {"document": {"pages": [], "elements": []}, "error_status": []}

    monkeypatch.setitem(docolage.parser._HANDLERS, ".pptx", _fake_parse)

    result = docolage.parse(str(fixtures_dir / "office_sample.pptx"), _config(tmp_output_volume))

    assert called["path"] == Path(str(fixtures_dir / "office_sample.pptx"))
    assert result["document"]["pages"] == []


def test_office_handler_delegates_to_pdf_parse_after_conversion(monkeypatch, tmp_output_volume):
    """office.parse must not duplicate pdf.parse logic: it converts, then
    calls the existing pdf handler unchanged."""
    converted_path = tmp_output_volume / "converted.pdf"
    converted_path.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(office_format, "convert_to_pdf", lambda path, output_dir: converted_path)

    received = {}

    def _fake_pdf_parse(path, config):
        received["path"] = path
        return {"document": {"pages": [], "elements": []}, "error_status": []}

    monkeypatch.setattr(office_format.pdf_format, "parse", _fake_pdf_parse)

    result = office_format.parse(Path("anything.docx"), _config(tmp_output_volume))

    assert received["path"] == converted_path
    assert result["document"]["pages"] == []


# ---------------------------------------------------------------------------
# Real end-to-end conversion (only runs if LibreOffice is actually installed).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not SOFFICE_AVAILABLE, reason="LibreOffice (soffice) is not installed")
def test_docx_converts_and_parses_to_standard_schema(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "office_sample.docx"), _config(tmp_output_volume)
    )

    assert result["error_status"] == []
    pages = result["document"]["pages"]
    assert len(pages) >= 1
    assert all("image_uri" in p for p in pages)

    elements = result["document"]["elements"]
    full_text = " ".join(e["content"] for e in elements if e["type"] == "text")
    assert "Office Fixture" in full_text


@pytest.mark.skipif(not SOFFICE_AVAILABLE, reason="LibreOffice (soffice) is not installed")
def test_pptx_converts_and_parses_to_standard_schema(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "office_sample.pptx"), _config(tmp_output_volume)
    )

    assert result["error_status"] == []
    pages = result["document"]["pages"]
    assert len(pages) >= 1

    elements = result["document"]["elements"]
    full_text = " ".join(e["content"] for e in elements if e["type"] == "text")
    assert "Office Fixture" in full_text


@pytest.mark.skipif(not SOFFICE_AVAILABLE, reason="LibreOffice (soffice) is not installed")
def test_converted_docx_output_matches_pdf_output_structure(fixtures_dir, tmp_output_volume):
    """Structural parity: same element-type set and bbox/page conventions
    as parsing an equivalent PDF directly."""
    docx_result = docolage.parse(
        str(fixtures_dir / "office_sample.docx"), _config(tmp_output_volume)
    )
    pdf_result = docolage.parse(
        str(fixtures_dir / "born_digital_single.pdf"), _config(tmp_output_volume)
    )

    for result in (docx_result, pdf_result):
        for page in result["document"]["pages"]:
            assert set(page.keys()) >= {"id", "image_uri"}
        for element in result["document"]["elements"]:
            assert set(element.keys()) >= {
                "type",
                "content",
                "confidence",
                "bbox",
                "description",
                "image_uri",
            }
            for bbox in element["bbox"]:
                assert set(bbox.keys()) == {"page_id", "coord"}
                assert len(bbox["coord"]) == 4
