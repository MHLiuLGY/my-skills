# Docling Gradio API Workflow

Use this reference when adjusting the wrapper script or debugging a live conversion.

## Service Assumption

- Service root: `http://localhost:5001`
- Gradio UI root used by `gradio_client`: `http://localhost:5001/ui/`

## Endpoints Used By This Skill

### Common helper

- `/change_ocr_lang`
  Input: `ocr_engine`
  Return: OCR language string for the selected engine.
  Use this only when `--ocr-lang` is not explicitly supplied.

### URL flow

- `/process_url`
  Inputs:
  - `auth`
  - `input_sources`
  - `to_formats`
  - `image_export_mode`
  - `pipeline`
  - `ocr`
  - `force_ocr`
  - `ocr_engine`
  - `ocr_lang`
  - `pdf_backend`
  - `table_mode`
  - `abort_on_error`
  - `return_as_file`
  - `do_code_enrichment`
  - `do_formula_enrichment`
  - `do_picture_classification`
  - `do_picture_description`
  Return: task id string

- `/wait_task_finish`
  Inputs: `auth`, `task_id`, `return_as_file`
  Return: 9-tuple
  Index `8` is the downloadable file path when `return_as_file=true`.

### File flow

- `/process_file`
  Inputs match `/process_url`, except `files` replaces `input_sources`.
  Return: task id string

- `/wait_task_finish_1`
  Inputs: `auth`, `task_id`, `return_as_file`
  Return: 9-tuple
  Index `8` is the downloadable file path when `return_as_file=true`.

## Defaults Used By The Wrapper

- `to_formats=["md"]`
- `image_export_mode="embedded"`
- `pipeline="standard"`
- `ocr=true`
- `force_ocr=false`
- `ocr_engine="auto"`
- `ocr_lang` resolved by `/change_ocr_lang`, else `en,fr,de,es`
- `pdf_backend="dlparse_v4"`
- `table_mode="accurate"`
- `abort_on_error=false`
- `return_as_file=true`
- all enrichment flags disabled by default

## Output Handling

- The wrapper always requests `return_as_file=true`.
- Treat the returned path as the authoritative artifact.
- Extract ZIP results into the chosen output directory.
- When Markdown contains `data:image/...;base64,...`, post-process those images into a sibling `images/` directory and rewrite the Markdown references.
- When URL conversions still contain `Image not available` placeholders, fetch the original page HTML, collect article `<img>` URLs, download them, and replace placeholders sequentially.
- Copy non-ZIP artifacts into the chosen output directory unchanged.

## Notes

- The API surface contains duplicated helper endpoints with `_1`, `_2`, or `_3` suffixes for different tabs. This skill uses the file pair `/process_file` + `/wait_task_finish_1` and the URL pair `/process_url` + `/wait_task_finish`.
- Keep file and URL jobs separate even if the user provides both kinds of input.
- The local Gradio UI does not expose every low-level Docling image option. For URL jobs, the wrapper may need to repair image placeholders outside the API response.
