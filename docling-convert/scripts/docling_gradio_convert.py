#!/usr/bin/env python3
"""Submit Docling conversion jobs through the Gradio client and extract outputs."""

from __future__ import annotations

import argparse
import base64
import glob
import json
import re
import shutil
import sys
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

DEFAULT_SERVICE_URL = "http://localhost:5001"
DEFAULT_OCR_LANG = "en,fr,de,es"
TO_FORMAT_CHOICES = ("json", "md", "html", "text", "doctags")
IMAGE_EXPORT_CHOICES = ("embedded", "placeholder", "referenced")
PIPELINE_CHOICES = ("legacy", "standard", "vlm", "asr")
OCR_ENGINE_CHOICES = ("auto", "easyocr", "tesseract", "rapidocr")
PDF_BACKEND_CHOICES = ("pypdfium2", "dlparse_v1", "dlparse_v2", "dlparse_v4")
TABLE_MODE_CHOICES = ("fast", "accurate")
EMBEDDED_IMAGE_PATTERN = re.compile(r"!\[(.*?)\]\(data:image/([^;]+);base64,([^)]+)\)")
URL_IMAGE_PLACEHOLDER = "<!-- 🖼️❌ Image not available. Please use `PdfPipelineOptions(generate_picture_images=True)` -->"
FRONT_MATTER_KEY_PATTERN = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
CORE_FRONTMATTER_FIELDS = (
    "url",
    "title",
    "description",
    "author",
    "published",
    "cover_image",
    "language",
    "captured_at",
    "converter",
    "pipeline",
    "ocr",
    "ocr_lang",
)
ARTICLE_TYPES = {
    "Article",
    "NewsArticle",
    "BlogPosting",
    "WebPage",
    "ReportageNewsArticle",
}
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


@dataclass
class FrontMatterEntry:
    raw: str
    value: Any = None


@dataclass
class SourcePage:
    url: str
    html: str
    metadata: OrderedDict[str, Any]


class MetadataHTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None
        self.meta_entries: list[dict[str, str]] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.time_datetimes: list[str] = []
        self.json_ld_scripts: list[str] = []
        self._title_depth = 0
        self._capture_h1 = False
        self._first_h1_closed = False
        self._capture_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        lower_tag = tag.lower()

        if lower_tag == "html" and not self.html_lang:
            self.html_lang = attr_map.get("lang")
        elif lower_tag == "meta":
            self.meta_entries.append(attr_map)
        elif lower_tag == "time":
            value = attr_map.get("datetime")
            if value:
                self.time_datetimes.append(value)
        elif lower_tag == "title":
            self._title_depth += 1
        elif lower_tag == "h1" and not self._first_h1_closed and not self._capture_h1:
            self._capture_h1 = True
        elif lower_tag == "script":
            script_type = attr_map.get("type", "").lower()
            if script_type == "application/ld+json":
                self._capture_json_ld = True
                self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if lower_tag == "title" and self._title_depth > 0:
            self._title_depth -= 1
        elif lower_tag == "h1" and self._capture_h1:
            self._capture_h1 = False
            self._first_h1_closed = True
        elif lower_tag == "script" and self._capture_json_ld:
            script_text = "".join(self._json_ld_parts).strip()
            if script_text:
                self.json_ld_scripts.append(script_text)
            self._capture_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._title_depth > 0:
            self.title_parts.append(data)
        if self._capture_h1:
            self.h1_parts.append(data)
        if self._capture_json_ld:
            self._json_ld_parts.append(data)


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def pick_first(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            normalized = normalize_text(value)
            if normalized:
                return normalized
    return None


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unescape(value).replace("\r", " ").replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def normalize_markdown(markdown: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", markdown.replace("\r\n", "\n").replace("\r", "\n")).strip()


def normalize_language_tag(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    primary = trimmed.split(",", 1)[0].split(";", 1)[0].split()[0]
    normalized = primary.replace("_", "-").strip()
    return normalized or None


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

    if isinstance(resolved, dict):
        resolved = resolved.get("value")

    if isinstance(resolved, str) and resolved.strip():
        return resolved.strip()

    return DEFAULT_OCR_LANG


def extract_artifact_path(value) -> Path:
    if isinstance(value, dict):
        value = value.get("value")

    if not isinstance(value, (str, Path)) or not value:
        raise ValueError(f"Unsupported artifact payload: {value!r}")

    return Path(value)


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
    artifact_path = extract_artifact_path(result[8])
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
    artifact_path = extract_artifact_path(result[8])
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


def import_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    return BeautifulSoup


def extract_embedded_images(markdown_root: Path) -> int:
    extracted_count = 0
    if markdown_root.is_file() and markdown_root.suffix.lower() == ".md":
        markdown_paths = [markdown_root]
    else:
        markdown_paths = sorted(path for path in markdown_root.rglob("*.md") if path.is_file())

    for markdown_path in markdown_paths:
        markdown_text = markdown_path.read_text(encoding="utf-8")
        matches = list(EMBEDDED_IMAGE_PATTERN.finditer(markdown_text))
        if not matches:
            continue

        images_dir = markdown_path.parent / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        def replace_match(match):
            nonlocal extracted_count

            alt_text, image_type, encoded_bytes = match.groups()
            extension = image_type.lower()
            if extension == "jpeg":
                extension = "jpg"

            image_name = f"{markdown_path.stem}-image-{extracted_count + 1:03d}.{extension}"
            image_path = images_dir / image_name
            image_bytes = base64.b64decode(encoded_bytes)
            image_path.write_bytes(image_bytes)
            extracted_count += 1

            relative_path = Path("images") / image_name
            safe_alt_text = alt_text or "Image"
            return f"![{safe_alt_text}]({relative_path.as_posix()})"

        rewritten_text = EMBEDDED_IMAGE_PATTERN.sub(replace_match, markdown_text)
        markdown_path.write_text(rewritten_text, encoding="utf-8")

    return extracted_count


def choose_content_root(soup):
    candidate_selectors = [
        "#cnblogs_post_body",
        "article",
        "main article",
        "[role='main'] article",
        ".post-body",
        ".entry-content",
        ".article-content",
        ".post-content",
        ".markdown-body",
        ".content",
        "main",
    ]

    for selector in candidate_selectors:
        node = soup.select_one(selector)
        if node and node.find("img"):
            return node

    best_node = None
    best_score = -1
    for node in soup.find_all(["article", "main", "section", "div"]):
        images = node.find_all("img")
        if not images:
            continue
        text_length = len(node.get_text(" ", strip=True))
        score = len(images) * 1000 + text_length
        if score > best_score:
            best_node = node
            best_score = score
    return best_node or soup


def fetch_source_html(page_url: str) -> str:
    response = requests.get(page_url, headers=DEFAULT_HEADERS, timeout=60)
    response.raise_for_status()
    return response.text


def get_meta_content(meta_entries: list[dict[str, str]], names: Iterable[str]) -> str | None:
    wanted = {name.lower() for name in names}
    for entry in meta_entries:
        for field_name in ("name", "property", "http-equiv", "itemprop"):
            key = entry.get(field_name, "").lower()
            if key in wanted:
                return normalize_text(entry.get("content"))
    return None


def flatten_json_ld_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items: list[dict[str, Any]] = []
        for item in data:
            items.extend(flatten_json_ld_items(item))
        return items
    if not isinstance(data, dict):
        return []

    items = [data]
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            items.extend(flatten_json_ld_items(item))
    return items


def extract_author_from_json_ld(author_data: Any) -> str | None:
    if isinstance(author_data, str):
        return normalize_text(author_data)
    if isinstance(author_data, list):
        names = [extract_author_from_json_ld(item) for item in author_data]
        joined = ", ".join(name for name in names if name)
        return joined or None
    if isinstance(author_data, dict):
        return normalize_text(author_data.get("name"))
    return None


def extract_primary_json_ld_meta(json_ld_scripts: list[str]) -> dict[str, str]:
    for script_text in json_ld_scripts:
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue

        for item in flatten_json_ld_items(payload):
            item_type = item.get("@type")
            if isinstance(item_type, list):
                item_type = item_type[0] if item_type else None
            if isinstance(item_type, str) and item_type not in ARTICLE_TYPES:
                continue

            image = item.get("image")
            image_value = None
            if isinstance(image, str):
                image_value = image
            elif isinstance(image, list) and image:
                first_image = image[0]
                if isinstance(first_image, str):
                    image_value = first_image
                elif isinstance(first_image, dict):
                    image_value = first_image.get("url")
            elif isinstance(image, dict):
                image_value = image.get("url")

            return {
                "title": pick_first(item.get("headline"), item.get("name")) or "",
                "description": normalize_text(item.get("description")) or "",
                "author": extract_author_from_json_ld(item.get("author")) or "",
                "published": pick_first(item.get("datePublished"), item.get("dateCreated")) or "",
                "cover_image": normalize_text(image_value) or "",
            }
    return {}


def clean_title_value(title: str | None) -> str | None:
    normalized = normalize_text(title)
    if not normalized:
        return None
    cleaned = re.split(r"\s*[-|–—]\s*", normalized, maxsplit=1)[0].strip()
    return cleaned or normalized


def infer_title_from_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    path_segment = parsed.path.rstrip("/").split("/")[-1]
    if not path_segment:
        return parsed.netloc or "Untitled"
    base_segment = re.sub(r"\.[a-zA-Z0-9]+$", "", path_segment)
    candidate = re.sub(r"[-_]+", " ", base_segment).strip()
    return candidate or parsed.netloc or "Untitled"


def build_source_metadata(page_url: str, html: str, args, captured_at: str) -> OrderedDict[str, Any]:
    collector = MetadataHTMLCollector()
    collector.feed(html)
    json_ld_meta = extract_primary_json_ld_meta(collector.json_ld_scripts)

    title = pick_first(
        get_meta_content(collector.meta_entries, ["og:title", "twitter:title"]),
        json_ld_meta.get("title"),
        clean_title_value("".join(collector.title_parts)),
        "".join(collector.h1_parts),
    )
    description = pick_first(
        get_meta_content(collector.meta_entries, ["description", "og:description", "twitter:description"]),
        json_ld_meta.get("description"),
    )
    author = pick_first(
        get_meta_content(collector.meta_entries, ["author", "article:author", "twitter:creator"]),
        json_ld_meta.get("author"),
    )
    published = pick_first(
        collector.time_datetimes[0] if collector.time_datetimes else None,
        get_meta_content(collector.meta_entries, ["article:published_time", "datePublished", "publishdate", "date", "pubdate"]),
        json_ld_meta.get("published"),
    )
    cover_image = pick_first(
        get_meta_content(collector.meta_entries, ["og:image", "twitter:image", "twitter:image:src"]),
        json_ld_meta.get("cover_image"),
    )
    language = pick_first(
        normalize_language_tag(collector.html_lang),
        normalize_language_tag(
            get_meta_content(
                collector.meta_entries,
                ["language", "content-language", "og:locale"],
            )
        ),
    )

    metadata = OrderedDict()
    metadata["url"] = page_url
    metadata["title"] = title or infer_title_from_url(page_url)
    metadata["description"] = description
    metadata["author"] = author
    metadata["published"] = published
    metadata["cover_image"] = cover_image
    metadata["language"] = language
    metadata["captured_at"] = captured_at
    metadata["converter"] = "docling"
    metadata["pipeline"] = args.pipeline
    metadata["ocr"] = args.ocr
    metadata["ocr_lang"] = args.ocr_lang or DEFAULT_OCR_LANG
    return metadata


def fetch_source_page(page_url: str, args) -> SourcePage:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        html = fetch_source_html(page_url)
    except requests.RequestException as exc:
        print(f"Warning: failed to fetch source HTML for {page_url}: {exc}")
        html = ""

    metadata = build_source_metadata(page_url, html, args, captured_at) if html else OrderedDict(
        [
            ("url", page_url),
            ("title", infer_title_from_url(page_url)),
            ("captured_at", captured_at),
            ("converter", "docling"),
            ("pipeline", args.pipeline),
            ("ocr", args.ocr),
            ("ocr_lang", args.ocr_lang or DEFAULT_OCR_LANG),
        ]
    )
    return SourcePage(url=page_url, html=html, metadata=metadata)


def fetch_page_image_urls_from_html(page_url: str, html: str) -> list[str]:
    if not html:
        return []

    BeautifulSoup = import_bs4()
    if BeautifulSoup is None:
        print("Warning: beautifulsoup4 is not installed; skipping URL image backfill.")
        return []

    soup = BeautifulSoup(html, "lxml")
    root = choose_content_root(soup)
    image_urls: list[str] = []
    seen: set[str] = set()

    for image in root.find_all("img"):
        src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or image.get("data-actualsrc")
            or image.get("data-lazy-src")
        )
        if not src:
            continue

        absolute_url = requests.compat.urljoin(page_url, src)
        if absolute_url.startswith("data:") or absolute_url in seen:
            continue

        seen.add(absolute_url)
        image_urls.append(absolute_url)

    return image_urls


def infer_extension(image_url: str, response: requests.Response) -> str:
    path_extension = Path(urlparse(image_url).path).suffix.lower().lstrip(".")
    if path_extension in {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}:
        return "jpg" if path_extension == "jpeg" else path_extension

    content_type = response.headers.get("content-type", "").lower()
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "gif" in content_type:
        return "gif"
    if "webp" in content_type:
        return "webp"
    if "svg" in content_type:
        return "svg"
    return "bin"


def backfill_url_markdown_images(markdown_path: Path, source_page: SourcePage) -> int:
    markdown_text = markdown_path.read_text(encoding="utf-8")
    placeholder_count = markdown_text.count(URL_IMAGE_PLACEHOLDER)
    if placeholder_count == 0:
        return 0

    image_urls = fetch_page_image_urls_from_html(source_page.url, source_page.html)
    if not image_urls:
        print(f"Warning: no source images found for {source_page.url}")
        return 0

    images_dir = markdown_path.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    replacements: list[str] = []
    downloaded_count = 0

    for index, image_url in enumerate(image_urls[:placeholder_count], start=1):
        response = requests.get(image_url, headers=DEFAULT_HEADERS, timeout=60)
        response.raise_for_status()

        extension = infer_extension(image_url, response)
        image_name = f"{markdown_path.stem}-image-{index:03d}.{extension}"
        image_path = images_dir / image_name
        image_path.write_bytes(response.content)
        replacements.append(f"![Image](images/{image_name})")
        downloaded_count += 1

    replacement_iter = iter(replacements)
    rewritten_text = markdown_text
    for _ in replacements:
        rewritten_text = rewritten_text.replace(URL_IMAGE_PLACEHOLDER, next(replacement_iter), 1)

    remaining_placeholders = rewritten_text.count(URL_IMAGE_PLACEHOLDER)
    if remaining_placeholders:
        rewritten_text = rewritten_text.replace(URL_IMAGE_PLACEHOLDER, "")
        print(
            f"Warning: removed {remaining_placeholders} unmatched image placeholders from {markdown_path.name} "
            f"after backfilling {downloaded_count} images."
        )

    markdown_path.write_text(rewritten_text, encoding="utf-8")

    return downloaded_count


def collect_markdown_files(final_path: Path) -> list[Path]:
    if final_path.is_dir():
        return sorted(path for path in final_path.rglob("*.md") if path.is_file())
    if final_path.is_file() and final_path.suffix.lower() == ".md":
        return [final_path]
    return []


def parse_simple_yaml_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        unquoted = value[1:-1]
        unquoted = unquoted.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        return unquoted
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    return value


def parse_front_matter_entries(lines: list[str]) -> OrderedDict[str, FrontMatterEntry]:
    entries: OrderedDict[str, FrontMatterEntry] = OrderedDict()
    raw_counter = 0
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line:
            raw_counter += 1
            entries[f"__raw_{raw_counter}"] = FrontMatterEntry(raw="")
            index += 1
            continue

        if line.startswith((" ", "\t")):
            raw_block = [line]
            index += 1
            while index < len(lines) and not FRONT_MATTER_KEY_PATTERN.match(lines[index]):
                raw_block.append(lines[index])
                index += 1
            raw_counter += 1
            entries[f"__raw_{raw_counter}"] = FrontMatterEntry(raw="\n".join(raw_block))
            continue

        match = FRONT_MATTER_KEY_PATTERN.match(line)
        if not match:
            raw_counter += 1
            entries[f"__raw_{raw_counter}"] = FrontMatterEntry(raw=line)
            index += 1
            continue

        key = match.group(1)
        block_lines = [line]
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if FRONT_MATTER_KEY_PATTERN.match(next_line) and not next_line.startswith((" ", "\t")):
                break
            block_lines.append(next_line)
            index += 1

        value = None
        if len(block_lines) == 1:
            _, raw_value = block_lines[0].split(":", 1)
            value = parse_simple_yaml_scalar(raw_value)
        entries[key] = FrontMatterEntry(raw="\n".join(block_lines), value=value)

    return entries


def split_front_matter(document: str) -> tuple[OrderedDict[str, FrontMatterEntry], str]:
    normalized = document.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return OrderedDict(), normalized

    lines = normalized.split("\n")
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        return OrderedDict(), normalized

    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return parse_front_matter_entries(lines[1:end_index]), body


def format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n")
    return f'"{text}"'


def extract_heading_title(body: str) -> str | None:
    match = re.match(r"^\s*#\s+(.+?)\s*(?:\n|$)", body)
    return normalize_text(match.group(1)) if match else None


def body_has_matching_title(body: str, title: str) -> bool:
    escaped_title = re.escape(title.strip())
    title_regex = re.compile(rf"^\s*#\s+{escaped_title}\s*(?:\n|$)", re.IGNORECASE)
    return bool(title_regex.match(body))


def build_front_matter(metadata: OrderedDict[str, Any], existing_entries: OrderedDict[str, FrontMatterEntry]) -> str:
    lines = ["---"]

    for key in CORE_FRONTMATTER_FIELDS:
        derived_value = metadata.get(key)
        existing_value = existing_entries.get(key).value if key in existing_entries else None
        final_value = derived_value if derived_value not in (None, "") else existing_value
        if final_value in (None, ""):
            continue
        lines.append(f"{key}: {format_yaml_scalar(final_value)}")

    for key, entry in existing_entries.items():
        if key in CORE_FRONTMATTER_FIELDS:
            continue
        if entry.raw:
            lines.extend(entry.raw.split("\n"))
        else:
            lines.append("")

    lines.append("---")
    return "\n".join(lines)


def prepare_markdown_document(markdown_text: str, source_page: SourcePage) -> str:
    existing_entries, body = split_front_matter(markdown_text)
    normalized_body = normalize_markdown(body)

    merged_metadata = OrderedDict(source_page.metadata)
    if not merged_metadata.get("title"):
        merged_metadata["title"] = (
            existing_entries.get("title").value if existing_entries.get("title") else None
        ) or extract_heading_title(normalized_body) or infer_title_from_url(source_page.url)

    if not merged_metadata.get("url"):
        merged_metadata["url"] = source_page.url

    front_matter = build_front_matter(merged_metadata, existing_entries)
    title = merged_metadata.get("title")
    final_body = normalized_body
    if title and final_body and not body_has_matching_title(final_body, title):
        final_body = f"# {title}\n\n{final_body}"

    if final_body:
        return f"{front_matter}\n\n{final_body}\n"
    return f"{front_matter}\n"


def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def save_source_html(output_dir: Path, html: str) -> Path:
    target_path = output_dir / "source.html"
    target_path.write_text(html, encoding="utf-8")
    return target_path


def save_manifest(
    *,
    output_dir: Path,
    source_page: SourcePage,
    args,
    task_id: str,
    service_url: str,
    artifact_path: Path,
    materialized_output: Path,
    extracted_images: int,
    url_images_backfilled: int,
    markdown_files: list[Path],
) -> Path:
    manifest_path = output_dir / "manifest.json"
    payload = OrderedDict(
        [
            ("source_url", source_page.url),
            ("captured_at", source_page.metadata.get("captured_at")),
            ("task_id", task_id),
            ("service_url", service_url),
            ("job_kind", "url"),
            ("to_formats", list(args.to_formats)),
            ("image_export_mode", args.image_export_mode),
            ("pipeline", args.pipeline),
            ("ocr", args.ocr),
            ("force_ocr", args.force_ocr),
            ("ocr_engine", args.ocr_engine),
            ("ocr_lang", args.ocr_lang or DEFAULT_OCR_LANG),
            ("pdf_backend", args.pdf_backend),
            ("table_mode", args.table_mode),
            ("abort_on_error", args.abort_on_error),
            ("do_code_enrichment", args.do_code_enrichment),
            ("do_formula_enrichment", args.do_formula_enrichment),
            ("do_picture_classification", args.do_picture_classification),
            ("do_picture_description", args.do_picture_description),
            ("artifact_path", artifact_path.resolve().as_posix()),
            ("materialized_output", materialized_output.resolve().as_posix()),
            ("embedded_images_extracted", extracted_images),
            ("url_images_backfilled", url_images_backfilled),
            ("markdown_files", [safe_relative_path(path, output_dir) for path in markdown_files]),
        ]
    )
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path


def process_url_outputs(
    *,
    final_path: Path,
    output_dir: Path,
    url_inputs: list[str],
    args,
    task_id: str,
    service_url: str,
    artifact_path: Path,
    extracted_images: int,
) -> int:
    markdown_files = collect_markdown_files(final_path)
    if not markdown_files or not url_inputs:
        return 0

    url_images_backfilled = 0

    if len(url_inputs) == 1:
        source_page = fetch_source_page(url_inputs[0], args)
        for markdown_path in markdown_files:
            url_images_backfilled += backfill_url_markdown_images(markdown_path, source_page)
            prepared = prepare_markdown_document(markdown_path.read_text(encoding="utf-8"), source_page)
            markdown_path.write_text(prepared, encoding="utf-8")

        if args.save_source_html and source_page.html:
            save_source_html(output_dir, source_page.html)
        if args.save_manifest:
            save_manifest(
                output_dir=output_dir,
                source_page=source_page,
                args=args,
                task_id=task_id,
                service_url=service_url,
                artifact_path=artifact_path,
                materialized_output=final_path,
                extracted_images=extracted_images,
                url_images_backfilled=url_images_backfilled,
                markdown_files=markdown_files,
            )
        return url_images_backfilled

    if args.save_source_html or args.save_manifest:
        print("Warning: sidecar files are only supported for single URL jobs in this version; skipping sidecar output.")

    if len(markdown_files) != len(url_inputs):
        print("Warning: URL post-processing skipped because the number of Markdown files does not match the number of URLs.")
        return 0

    for markdown_path, page_url in zip(markdown_files, url_inputs):
        source_page = fetch_source_page(page_url, args)
        url_images_backfilled += backfill_url_markdown_images(markdown_path, source_page)
        prepared = prepare_markdown_document(markdown_path.read_text(encoding="utf-8"), source_page)
        markdown_path.write_text(prepared, encoding="utf-8")

    return url_images_backfilled


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
    print(f"Save source HTML: {args.save_source_html}")
    print(f"Save manifest: {args.save_manifest}")


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
        "--save-source-html",
        action="store_true",
        help="Save fetched source HTML as source.html for single URL jobs.",
    )
    parser.add_argument(
        "--save-manifest",
        action="store_true",
        help="Save manifest.json for single URL jobs.",
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
    args.ocr_lang = ocr_lang

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
        extracted_images = 0
        if args.image_export_mode == "embedded" and (final_path.is_dir() or final_path.suffix.lower() == ".md"):
            extracted_images = extract_embedded_images(final_path)

        url_images_backfilled = 0
        if kind == "url":
            url_images_backfilled = process_url_outputs(
                final_path=final_path,
                output_dir=output_dir,
                url_inputs=job_inputs,
                args=args,
                task_id=task_id,
                service_url=service_url,
                artifact_path=artifact_path,
                extracted_images=extracted_images,
            )

        print(f"Task id: {task_id}")
        print(f"Artifact: {artifact_path}")
        print(f"Materialized output: {final_path}")
        if extracted_images:
            print(f"Extracted embedded images: {extracted_images}")
        if url_images_backfilled:
            print(f"Backfilled URL images: {url_images_backfilled}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
