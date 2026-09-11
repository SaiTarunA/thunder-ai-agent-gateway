## Content Extraction part is done for below specified formats. Need to add other parts and summary/Q&A on top of cannonical document

## Currently supported, by backend

|Backend|Formats|
|---|---|
|`PdfExtractionPipeline` (PyMuPDF → Docling fallback)|`.pdf`|
|`DocumentExtractionPipeline` (Docling, `SimplePipeline`)|`.docx`, `.md`, `.html`, `.htm`, `.tex`, `.pptx`, `.odt`, `.epub`|
|`XlsxBackend` (openpyxl, normal mode)|`.xlsx`, `.xlsm`|
|`CsvBackend` (pandas)|`.csv`|
|`JsonBackend` (stdlib `json`)|`.json`|
|`YamlBackend` (PyYAML, safe loader)|`.yaml`, `.yml`|
|`XmlBackend` (defusedxml)|`.xml`|
|`RstBackend` (docutils, hardened)|`.rst`|
|`SourceCodeBackend`|`.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.java`, `.c`, `.cpp`, `.cs`, `.go`, `.php`, `.rb`, `.sh`, `.css`|
|`PlainTextBackend`|`.txt`|

**Not counted as "supported"**: `DoclingBackend` also handles `.png`/`.jpg`/`.jpeg`/`.webp`/`.bmp` directly, but only via OCR — there is no vision fallback yet for images with no extractable text, so these are treated as partially supported and listed separately below rather than in the table above.

## Pending, by category

| Category               | Pending formats                                                                                                     | Status                                                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Documents               | `.eml`, `.msg`                                                                                                       | Next planned — Docling-native (confirmed via backend inspection), needs fixture verification before shipping                       |
| Documents               | `.doc`, `.rtf`                                                                                                       | Deferred — LibreOffice-dependent, needs its own sandboxing/deployment initiative                                                    |
| Spreadsheets & Tabular  | `.xls`                                                                                                               | Deferred — legacy binary format, openpyxl can't read it; LibreOffice-dependent or needs a separate library                          |
| Presentations           | `.ppt`                                                                                                               | Deferred — legacy binary, LibreOffice-dependent                                                                                     |
| Structured / Config     | `.env`                                                                                                               | Blocked — needs an explicit secret-handling policy decision before it enters normal extraction/indexing                             |
| Web / Markup / Styling  | —                                                                                                                     | Complete (`.html`, `.htm`, `.xml`, `.css`, `.rst`, `.tex` all done)                                                                  |
| Source Code             | —                                                                                                                     | Complete (all 13 languages done)                                                                                                     |
| Images                  | `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp` (OCR-only, not "supported"); `.dib`, `.heic`, `.gif` (not even mapped yet)  | Deferred — needs a vision fallback for non-text images before counting as supported; `.heic` also needs a separate decode library   |
| Audio                   | all formats                                                                                                          | Untouched                                                                                                                            |
| Video                   | all formats                                                                                                          | Untouched                                                                                                                            |
| Archives                | `.zip`, `.rar`, `.7z`, `.tar`, `.gz`                                                                                 | Blocked — PRD-vs-Streams-allowlist policy conflict, not a technical gap                                                             |

## Module layout

- `canonical/` — the canonical domain model (`CanonicalDocument`, `DocumentBlock`, `TableData`, etc.) and its enums. This is the shared output shape every backend converts into; it does not depend on `extraction/`.
- `extraction/` — everything that produces a `CanonicalDocument`:
  - `backends/` — one module per format/library (`docling.py`, `pymupdf.py`, `csv_backend.py`, etc.)
  - `pipelines/` — adaptive multi-step extraction strategies (`pdf.py`, `document.py`) that wrap a backend with quality evaluation and an accept/fallback policy
  - `policy.py` / `quality.py` — the accept-vs-fallback decision and the scoring it's based on
  - `registry.py` / `orchestrator.py` / `factory.py` — routes a file to the right backend/pipeline by extension and tries candidates in order
  - `limits.py` — shared resource-limit constants and checks (row/cell/node counts, zip-bomb guards) used across backends
  - `schemas.py` — extraction-*process* types (`ExtractionOptions`, `ExtractionResult`, `ExtractionQuality`, ...), distinct from `canonical/schemas.py`'s document *model* types
- `fixtures/` — sample files used by the test suite, one `simple.<ext>` per supported format
- `benchmarks/` — standalone extraction benchmarking scripts (not part of the test suite)
