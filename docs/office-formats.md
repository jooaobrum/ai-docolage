# DOCX/PPTX support: the LibreOffice cluster requirement

`docolage.parse(path)` accepts `.docx` and `.pptx` files the same way it
accepts `.pdf`. Internally, both formats are first normalized to PDF via
headless LibreOffice (`soffice --headless --convert-to pdf`), then flow
through the same rasterize -> Router -> parse path as any other PDF. There
is no second schema mapping and no format-specific branch after
conversion, so DOCX/PPTX output is structurally identical to what parsing
an equivalent PDF would produce.

## LibreOffice is a system dependency, not a Python package

LibreOffice cannot be `pip install`-ed. The `soffice` binary must already
be present on the machine (or Databricks cluster) running `docolage.parse`.
This is the one part of docolage's setup that may require a cluster init
script, despite the project's general "no init script" goal.

- **macOS (local dev/testing)**: `brew install --cask libreoffice`
- **Debian/Ubuntu**: `apt-get install libreoffice`
- **Databricks cluster**: install LibreOffice via a cluster-scoped init
  script (e.g. `apt-get install -y libreoffice` on the init script's
  target image), then confirm `soffice` is on `PATH` for the cluster's
  Python environment.

If `soffice` is missing, `docolage.parse` raises
`docolage.exceptions.LibreOfficeNotFoundError` naming LibreOffice as the
missing dependency and how to install it — it does not surface as a raw
`FileNotFoundError` or subprocess traceback.

If LibreOffice is installed but fails to convert a specific file (corrupt
document, LibreOffice crash, non-zero exit, or a conversion that times
out), `docolage.parse` raises `docolage.exceptions.DocumentConversionError`
with the underlying LibreOffice error message, instead of a raw subprocess
trace.
