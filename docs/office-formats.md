# DOCX/PPTX support: the LibreOffice host requirement

`docolage.parse(path)` accepts `.docx` and `.pptx` files the same way it
accepts `.pdf`. Internally, both formats are first normalized to PDF via
headless LibreOffice (`soffice --headless --convert-to pdf`), then flow
through the same rasterize -> recognize path as any other PDF (docling's
standard pipeline decides per page whether OCR is needed — see ADR-0010;
there is no separate born-digital routing step). There is no second schema
mapping and no format-specific branch after conversion, so DOCX/PPTX
output is structurally identical to what parsing an equivalent PDF would
produce.

## LibreOffice is a system dependency, not a Python package

LibreOffice cannot be `pip install`-ed. The `soffice` binary must already
be present on the host running `docolage.parse` — a plain machine or a
Service Worker container (see `Dockerfile.worker-gpu`).

- **macOS (local dev/testing)**: `brew install --cask libreoffice`
- **Debian/Ubuntu**: `apt-get install libreoffice`
- **Worker container**: add `apt-get install -y libreoffice` to
  `Dockerfile.worker-gpu` and confirm `soffice` is on `PATH` inside the
  container — it is not installed there today, so DOCX/PPTX jobs fail with
  `LibreOfficeNotFoundError` until it is added.

If `soffice` is missing, `docolage.parse` raises
`docolage.exceptions.LibreOfficeNotFoundError` naming LibreOffice as the
missing dependency and how to install it — it does not surface as a raw
`FileNotFoundError` or subprocess traceback.

If LibreOffice is installed but fails to convert a specific file (corrupt
document, LibreOffice crash, non-zero exit, or a conversion that times
out), `docolage.parse` raises `docolage.exceptions.DocumentConversionError`
with the underlying LibreOffice error message, instead of a raw subprocess
trace.
