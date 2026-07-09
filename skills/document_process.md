---
name: document_process
description: "Extract text and tables from PDF/xlsx/docx into markdown/CSV, and generate docx/xlsx files from structured input"
roles_allowed: [worker]
triggers: [pdf, docx, xlsx, spreadsheet, word document, extract tables, document conversion, generate document]
---

## Overview

Use this skill when a goal requires reading office/PDF documents into workable text/data, or producing docx/xlsx deliverables. Tooling is runtime-installed, not a repo dependency: `pip install pypdf python-docx openpyxl` in the run environment if missing (all permissively licensed libraries). Honest-lossiness rule: report what extraction could not preserve rather than papering over it.

## Steps

1. **Identify format and direction** — extraction (document → markdown/CSV) or generation (structured input → docx/xlsx)? Pick the library: pypdf for PDF, python-docx for docx, openpyxl for xlsx. Install at runtime if the import fails.
2. **Inspect before converting** — page count and metadata for PDFs; sheet names, dimensions, and header rows for xlsx; section/heading structure for docx. Decide what subset the goal actually needs.
3. **Extract text to markdown** — preserve heading hierarchy where the format carries it (docx styles → `#` levels). PDFs have no semantic headings: extract page-by-page, keep page markers, and don't invent structure the source doesn't encode.
4. **Extract tables to CSV** — one file per table under output/, first row as verified header. docx tables and xlsx ranges map directly; for PDFs, if text extraction scrambles table layout, reconstruct from line positions and mark the output low-confidence — or report the table as non-extractable.
5. **Verify the extraction** — diff row/column counts against the inspection from step 2; spot-check 5 cells or paragraphs against the source; check the non-empty ratio. A scanned/image-only PDF yields no text — flag it as needing OCR, don't fabricate content.
6. **Generation: structure first** — build the document as plain data (list of sections/rows, dicts for styles) before touching a library, then render with python-docx/openpyxl. Never hand-assemble the underlying XML.
7. **Round-trip generated files** — reopen each generated file with the same library and assert the expected headings/sheets/cell values are present before calling the step done.
8. **Report** — list artifacts written, per-document extraction confidence, and anything lossy or skipped (merged cells, embedded images, formulas evaluated vs stored).

## Quality gates

- Never fabricate content for regions that failed to extract — emit an explicit failure note instead.
- Every generated file must reopen cleanly (step 7) — an unopenable deliverable is a failed step.
- Tables ship with their verification (counts + spot-check result), not bare.
- All artifacts land under output/ with names tying them to the source document.

<!-- route decision 2026-07-09: checked github.com/anthropics/skills — the document skills (skills/docx, pdf, pptx, xlsx) are explicitly "source-available, not open source" per the repo README, so NO content was copied from them. Steps here are original, built on pypdf (BSD) / python-docx (MIT) / openpyxl (MIT) usage patterns as runtime-installed tools — matching the survey's BUILD fallback route (persona-skill-survey.md §4). -->
