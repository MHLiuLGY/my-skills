#!/usr/bin/env python3
"""Submit Docling conversion jobs through the Gradio client and extract outputs."""

from __future__ import annotations

import argparse
import glob
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

DEFAULT_SERVICE_URL = "http://localhost:5001"
DEFAULT_OCR_LANG = "en,fr,de,es"
TO_FORMAT_CHOICES = ("json", "md", "html", "text", "doctags")
IMAGE_EXPORT_CHOICES = ("embedded", "placeholder", "referenced")
PIPELINE_CHOICES = ("legacy", "standard", "vlm", "asr")
OCR_ENGINE_CHOICES = ("auto", "easyocr", "tesseract", "rapidocr")
PDF_BACKEND_CHOICES = ("pypdfium2", "dlparse_v1", "dlparse_v2", "dlparse_v4")
TABLE_MODE_CHOICES = ("fast", "accurate")


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def slugify(value: str, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    if not slug:
        slug = "docling-job"
    return slug[:max_length].rstrip("-") or "docling-job"


def normalize_service_url(service_url: str) -> str:
    root = service_url.rstrip("/")
    if root.endswith("/ui"):
        return f"{root}/"
    return f"{root}/ui/"


def expand_inputs(raw_inputs: Iterable[str]) -> tuple[list[str], list[str]]:
    files: list[str] = []
    urls: list[str] = []

    for raw_input in raw_inputs:
        if is_url(raw_input):
            urls.append(raw_input)
            continue

        matches = sorted(glob.glob(raw_input))
        candidates = matches or [raw_input]

        for candidate in candidates:
            candidate_path = Path(candidate).expanduser()
            if candidate_path.exists():
                files.append(str(candidate_path.resolve()))
            else:
                raise FileNotFoundError(f"Input file not found: {candidate}")

    return files, urls


def resolve_output_dir(
    *,
    raw_inputs: list[str],
    kind: str,
    explicit_output_dir: str | None,
    current_workdir: Path,
    multiple_job_kinds: bool,
) -> Path:
    if explicit_output_dir:
        base_dir = Path(explicit_output_dir).expanduser().resolve()
        if multiple_job_kinds:
            return base_dir / ("files" if kind == "file" else "urls")
        return base_dir

    if kind == "file" and len(raw_inputs) == 1:
        source = Path(raw_inputs[0]).resolve()
        return source.parent / source.stem

    if kind == "url" and len(raw_inputs) == 1:
        return current_workdir / f"docling-{slugify(raw_inputs[0])}"

    suffix = "files-batch" if kind == "file" else "urls-batch"
    return current_workdir / f"docling-{suffix}"


def import_gradio():
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: install gradio_client first with `pip install gradio_client`."
        ) from exc

    return Client, handle_file


def get_ocr_lang(client, ocr_engine: str, explicit_ocr_lang: str | None) -> str:
    if explicit_ocr_lang:
        return explicit_ocr_lang

    try:
        resolved = client.predict(ocr_engine=ocr_engine, api_name="/change_ocr_lang")
    except Exception:
        resolved = None

    if isinstance(resolved, str) and resolved.strip():
        return resolved.strip()

    return DEFAULT_OCR_LANG


def submit_file_job(client, handle_file, args, file_inputs: list[str], ocr_lang: str) -> tuple[str, Path]:
    task_id = client.predict(
        auth=args.auth,
        files=[handle_file(file_path) for file_path in file_inputs],
        to_formats=args.to_formats,
        image_export_mode=args.image_export_mode,
        pipeline=args.pipeline,
        ocr=args.ocr,
        force_ocr=args.force_ocr,
        ocr_engine=args.ocr_engine,
        ocr_lang=ocr_lang,
        pdf_backend=args.pdf_backend,
        table_mode=args.table_mode,
        abort_on_error=args.abort_on_error,
        return_as_file=True,
        do_code_enrichment=args.do_code_enrichment,
        do_formula_enrichment=args.do_formula_enrichment,
        do_picture_classification=args.do_picture_classification,
        do_picture_description=args.do_picture_description,
        api_name="/process_file",
    )

    result = client.predict(
        auth=args.auth,
        task_id=task_id,
        return_as_file=True,
        api_name="/wait_task_finish_1",
    )
    artifact_path = Path(result[8])
    return task_id, artifact_path


def submit_url_job(client, args, url_inputs: list[str], ocr_lang: str) -> tuple[str, Path]:
    task_id = client.predict(
        auth=args.auth,
        input_sources="\n".join(url_inputs),
        to_formats=args.to_formats,
        image_export_mode=args.image_export_mode,
        pipeline=args.pipeline,
        ocr=args.ocr,
        force_ocr=args.force_ocr,
        ocr_engine=args.ocr_engine,
        ocr_lang=ocr_lang,
        pdf_backend=args.pdf_backend,
        table_mode=args.table_mode,
        abort_on_error=args.abort_on_error,
        return_as_file=True,
        do_code_enrichment=args.do_code_enrichment,
        do_formula_enrichment=args.do_formula_enrichment,
        do_picture_classification=args.do_picture_classification,
        do_picture_description=args.do_picture_description,
        api_name="/process_url",
    )

    result = client.predict(
        auth=args.auth,
        task_id=task_id,
        return_as_file=True,
        api_name="/wait_task_finish",
    )
    artifact_path = Path(result[8])
    return task_id, artifact_path


def materialize_artifact(artifact_path: Path, output_dir: Path) -> Path:
    if not artifact_path.exists():
        raise FileNotFoundError(f"Returned artifact does not exist: {artifact_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(artifact_path):
        with zipfile.ZipFile(artifact_path) as archive:
            archive.extractall(output_dir)
        return output_dir

    target_path = output_dir / artifact_path.name
    if artifact_path.resolve() != target_path.resolve():
        shutil.copy2(artifact_path, target_path)
    return target_path


def print_job_plan(kind: str, inputs: list[str], output_dir: Path, service_url: str, args, ocr_lang: str) -> None:
    print(f"Job kind: {kind}")
    print(f"Inputs ({len(inputs)}):")
    for item in inputs:
        print(f"  - {item}")
    print(f"Service URL: {service_url}")
    print(f"Output dir: {output_dir}")
    print(f"Formats: {', '.join(args.to_formats)}")
    print(f"Image export mode: {args.image_export_mode}")
    print(f"Pipeline: {args.pipeline}")
    print(f"OCR: {args.ocr}")
    print(f"Force OCR: {args.force_ocr}")
    print(f"OCR engine: {args.ocr_engine}")
    print(f"OCR language: {ocr_lang}")
    print(f"PDF backend: {args.pdf_backend}")
    print(f"Table mode: {args.table_mode}")
    print(f"Abort on error: {args.abort_on_error}")
    print(f"Code enrichment: {args.do_code_enrichment}")
    print(f"Formula enrichment: {args.do_formula_enrichment}")
    print(f"Picture classification: {args.do_picture_classification}")
    print(f"Picture description: {args.do_picture_description}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert local files or URLs with a local Docling Gradio service.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Local file paths, glob patterns, or HTTP/HTTPS URLs.",
    )
    parser.add_argument(
        "--service-url",
        default=DEFAULT_SERVICE_URL,
        help="Docling service root. Default: http://localhost:5001",
    )
    parser.add_argument(
        "--auth",
        default="",
        help="Authentication value passed to the Docling UI API.",
    )
    parser.add_argument(
        "--output-dir",
        help="Destination directory for extracted results.",
    )
    parser.add_argument(
        "--to-format",
        dest="to_formats",
        action="append",
        choices=TO_FORMAT_CHOICES,
        help="Repeat to request multiple output formats. Default: md",
    )
    parser.add_argument(
        "--image-export-mode",
        default="embedded",
        choices=IMAGE_EXPORT_CHOICES,
        help="Image export mode passed to Docling.",
    )
    parser.add_argument(
        "--pipeline",
        default="standard",
        choices=PIPELINE_CHOICES,
        help="Pipeline passed to Docling.",
    )
    parser.add_argument(
        "--no-ocr",
        dest="ocr",
        action="store_false",
        help="Disable OCR.",
    )
    parser.set_defaults(ocr=True)
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR even when text is already present.",
    )
    parser.add_argument(
        "--ocr-engine",
        default="auto",
        choices=OCR_ENGINE_CHOICES,
        help="OCR engine passed to Docling.",
    )
    parser.add_argument(
        "--ocr-lang",
        help="Explicit OCR language list, for example en,fr,de,es.",
    )
    parser.add_argument(
        "--pdf-backend",
        default="dlparse_v4",
        choices=PDF_BACKEND_CHOICES,
        help="PDF backend passed to Docling.",
    )
    parser.add_argument(
        "--table-mode",
        default="accurate",
        choices=TABLE_MODE_CHOICES,
        help="Table extraction mode.",
    )
    parser.add_argument(
        "--abort-on-error",
        action="store_true",
        help="Abort the job when Docling hits an error.",
    )
    parser.add_argument(
        "--do-code-enrichment",
        action="store_true",
        help="Enable code enrichment.",
    )
    parser.add_argument(
        "--do-formula-enrichment",
        action="store_true",
        help="Enable formula enrichment.",
    )
    parser.add_argument(
        "--do-picture-classification",
        action="store_true",
        help="Enable picture classification.",
    )
    parser.add_argument(
        "--do-picture-description",
        action="store_true",
        help="Enable picture description.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved plan without contacting the Docling service.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.to_formats = args.to_formats or ["md"]

    try:
        file_inputs, url_inputs = expand_inputs(args.inputs)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    jobs: list[tuple[str, list[str]]] = []
    if file_inputs:
        jobs.append(("file", file_inputs))
    if url_inputs:
        jobs.append(("url", url_inputs))

    if not jobs:
        print("No valid inputs were resolved.", file=sys.stderr)
        return 1

    current_workdir = Path.cwd().resolve()
    service_url = normalize_service_url(args.service_url)
    ocr_lang = args.ocr_lang or DEFAULT_OCR_LANG

    if args.dry_run:
        for kind, job_inputs in jobs:
            output_dir = resolve_output_dir(
                raw_inputs=job_inputs,
                kind=kind,
                explicit_output_dir=args.output_dir,
                current_workdir=current_workdir,
                multiple_job_kinds=len(jobs) > 1,
            )
            print_job_plan(kind, job_inputs, output_dir, service_url, args, ocr_lang)
        return 0

    Client, handle_file = import_gradio()
    client = Client(service_url)
    ocr_lang = get_ocr_lang(client, args.ocr_engine, args.ocr_lang)

    for kind, job_inputs in jobs:
        output_dir = resolve_output_dir(
            raw_inputs=job_inputs,
            kind=kind,
            explicit_output_dir=args.output_dir,
            current_workdir=current_workdir,
            multiple_job_kinds=len(jobs) > 1,
        )
        print_job_plan(kind, job_inputs, output_dir, service_url, args, ocr_lang)

        if kind == "file":
            task_id, artifact_path = submit_file_job(client, handle_file, args, job_inputs, ocr_lang)
        else:
            task_id, artifact_path = submit_url_job(client, args, job_inputs, ocr_lang)

        final_path = materialize_artifact(artifact_path, output_dir)
        print(f"Task id: {task_id}")
        print(f"Artifact: {artifact_path}")
        print(f"Materialized output: {final_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
