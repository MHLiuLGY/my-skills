---
name: docling-convert
description: Convert local files or URLs with a locally deployed Docling Gradio service into Markdown, JSON, HTML, text, or DocTags, with OCR and image export support. Use when handling `.docx`, `.pdf`, `.pptx`, `.xlsx`, `.html`, images, or web pages for document-to-Markdown conversion, batch conversion, image extraction, or Docling-based parsing through `http://localhost:5001`.
---

# Docling Convert

Use this skill to run document conversion through a local Docling service instead of ad-hoc parsing.

## Quick Start

- Assume the Docling service is already deployed locally and reachable at `http://localhost:5001`.
- Prefer `scripts/docling_gradio_convert.py` for repeatable work. It wraps the documented Gradio API and handles submission, waiting, and archive extraction.
- Install the required client before running the script:

```bash
pip install gradio_client
```

- Read `references/gradio-api-workflow.md` only when changing endpoints, tuning advanced options, or debugging output layouts.

## Workflow

1. Classify the inputs.
   Use the file flow for local paths and the URL flow for web pages. Do not mix files and URLs in one API request; if the user gives both, run two jobs.

2. Choose the outputs.
   Default to `md`.
   Add `json` when the user also needs structured output.
   Add `html`, `text`, or `doctags` only when the task explicitly needs them.

3. Choose the processing options.
   Keep `pipeline=standard`, `ocr=true`, `force_ocr=false`, `pdf_backend=dlparse_v4`, and `table_mode=accurate` unless the task calls for a change.
   Keep `image_export_mode=embedded` when the goal is to preserve extracted images inside the returned package.
   Turn on enrichment flags only when the user explicitly wants code, formulas, picture classification, or picture descriptions.

4. Run the wrapper script.

```bash
# Single file
python scripts/docling_gradio_convert.py report.pdf

# Batch files with Markdown + JSON
python scripts/docling_gradio_convert.py "*.pdf" --to-format md --to-format json

# Single URL
python scripts/docling_gradio_convert.py https://example.com/article --output-dir ./article

# Alternate service URL
python scripts/docling_gradio_convert.py slides.pptx --service-url http://localhost:5001
```

5. Verify the extracted results.
   The script always requests `return_as_file=true`, downloads the returned artifact, and extracts it into the chosen output directory.
   Inspect the produced Markdown plus any extracted image assets before presenting the result to the user.

## Output Conventions

- Prefer the script defaults unless the user asks for a different layout.
- For a single local file, extract into a sibling directory named after the input stem.
- For a single URL, extract into `docling-<slug>` under the current working directory.
- For multiple inputs, extract into `docling-files-batch` or `docling-urls-batch` under the current working directory, unless `--output-dir` is supplied.
- If the user supplies `--output-dir` and both file and URL jobs are needed, the script creates `files/` and `urls/` subdirectories to keep the results separate.

## Script Notes

- Use `scripts/docling_gradio_convert.py --dry-run ...` to verify grouping, endpoint selection, and destination paths without contacting the service.
- Let the script infer the Gradio UI URL from the service root. `http://localhost:5001` becomes `http://localhost:5001/ui/`.
- Let the script ask `/change_ocr_lang` for the default OCR language set when `--ocr-lang` is not provided. Fall back to `en,fr,de,es` if the endpoint is unavailable.
- Treat a missing `gradio_client` installation as an environment issue and fix it with `pip install gradio_client` instead of rewriting the workflow.

## Resources

### `scripts/docling_gradio_convert.py`

Use this wrapper for deterministic Docling conversions. It supports:

- local files, URLs, and wildcard expansion
- batch conversion
- OCR and enrichment flags
- archive download and extraction
- output directory planning
- dry-run validation

### `references/gradio-api-workflow.md`

Read this reference when you need:

- the endpoint mapping for file versus URL jobs
- the argument names expected by the Gradio client
- the `wait_task_finish` tuple layout
- the defaults adopted by this skill
