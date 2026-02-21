#!/usr/bin/env python3
"""
Document Image Extractor
Extract images from Word (.docx) and PDF (.pdf) documents.
Supports raster images (PNG, JPG, JPEG, GIF, BMP, TIFF) and vector graphics.
"""

import argparse
import os
import sys
from pathlib import Path

# Disable Pillow decompression bomb check for large images
from PIL import Image
Image.MAX_IMAGE_PIXELS = None


def convert_emf_to_svg_and_png(emf_data: bytes, output_path: Path, base_filename: str, dpi: int = 150, keep_emf: bool = False, convert_svg: bool = False) -> None:
    """Convert EMF/WMF vector image to PNG (and optionally SVG/EMF).

    Args:
        emf_data: Raw EMF/WMF data
        output_path: Output directory
        base_filename: Base filename without extension
        dpi: DPI for PNG conversion
        keep_emf: Keep original EMF file
        convert_svg: Convert to SVG (with embedded PNG)
    """
    import io
    try:
        from PIL import Image
    except ImportError:
        return

    # Calculate scale factor based on DPI (default 96 DPI -> scale to target DPI)
    scale = dpi / 96.0
    width, height = 100, 100  # Default dimensions
    png_data = None

    # Save original EMF file only if requested
    if keep_emf:
        emf_filepath = output_path / f"{base_filename}.emf"
        with open(emf_filepath, "wb") as f:
            f.write(emf_data)
        print(f"    Saved: {emf_filepath.name}")

    # Try to convert EMF to PNG using Pillow with higher resolution
    try:
        with Image.open(io.BytesIO(emf_data)) as img:
            # Get original dimensions
            orig_width, orig_height = img.size

            # Scale up the image for higher DPI
            new_width = int(orig_width * scale)
            new_height = int(orig_height * scale)

            # Resize if scale > 1
            if scale > 1:
                img = img.resize((new_width, new_height), Image.LANCZOS)

            # Convert to RGBA if needed
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

            width, height = img.size
            # Save as PNG (always output PNG)
            png_filepath = output_path / f"{base_filename}.png"
            # Save to buffer first to get PNG data
            png_buffer = io.BytesIO()
            img.save(png_buffer, "PNG", quality=95)
            png_data = png_buffer.getvalue()
            with open(png_filepath, "wb") as f:
                f.write(png_data)
            print(f"    Converted: {png_filepath.name} ({width}x{height} @ {dpi} DPI)")
    except Exception as e:
        print(f"    Warning: Could not convert EMF to PNG: {e}")

    # Convert to SVG only if requested
    if convert_svg and png_data:
        try:
            svg_content = convert_emf_to_svg(emf_data, png_data, width, height)
            if svg_content:
                svg_filepath = output_path / f"{base_filename}.svg"
                with open(svg_filepath, "w", encoding="utf-8") as f:
                    f.write(svg_content)
                print(f"    Converted: {svg_filepath.name}")
        except Exception as e:
            print(f"    Warning: Could not convert EMF to SVG: {e}")


def convert_emf_to_svg(emf_data: bytes, png_data: bytes, width: int, height: int) -> str:
    """Convert EMF to SVG by embedding the PNG rasterization."""
    import base64
    w, h = width, height

    try:
        from PIL import Image
        import io
        # Get actual dimensions from the image
        with Image.open(io.BytesIO(emf_data)) as img:
            w, h = img.size
    except Exception:
        pass

    # Encode PNG as base64 for embedding in SVG
    png_b64 = base64.b64encode(png_data).decode('utf-8')

    # Create SVG with embedded PNG image
    svg_template = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <title>EMF Vector Image</title>
  <desc>EMF image rasterized as PNG and embedded in SVG</desc>
  <image width="{w}" height="{h}" image-rendering="pixelated" xlink:href="data:image/png;base64,{png_b64}"/>
</svg>'''
    return svg_template


def extract_from_docx(docx_path: str, output_dir: str, dpi: int = 150, keep_emf: bool = False, convert_svg: bool = False) -> int:
    """Extract images from Word document.

    Args:
        docx_path: Path to Word document
        output_dir: Output directory
        dpi: DPI for PNG conversion (default 150)
        keep_emf: Keep original EMF/WMF files
        convert_svg: Convert EMF/WMF to SVG
    """
    try:
        from docx import Document
    except ImportError:
        print("Error: python-docx is required. Install with: pip install python-docx")
        return 0

    doc = Document(docx_path)
    image_count = 0
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Extract images from document relationships
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            image_part = rel.target_part
            image_count += 1
            base_filename = f"image_{image_count:03d}"

            # Determine image type
            content_type = image_part.content_type
            is_vector = False

            if "png" in content_type:
                ext = ".png"
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "gif" in content_type:
                ext = ".gif"
            elif "bmp" in content_type:
                ext = ".bmp"
            elif "tiff" in content_type or "tif" in content_type:
                ext = ".tiff"
            elif "webp" in content_type:
                ext = ".webp"
            elif "svg" in content_type:
                ext = ".svg"
            elif "emf" in content_type or "x-emf" in content_type:
                ext = ".emf"
                is_vector = True
            elif "wmf" in content_type:
                ext = ".wmf"
                is_vector = True
            else:
                ext = ".png"

            # For vector images (EMF/WMF), convert based on options
            if is_vector:
                print(f"  Extracting: {base_filename} (EMF/WMF vector)")
                convert_emf_to_svg_and_png(
                    image_part.blob, output_path, base_filename, dpi,
                    keep_emf=keep_emf, convert_svg=convert_svg
                )
            else:
                # Save raster image
                filename = f"{base_filename}{ext}"
                filepath = output_path / filename
                with open(filepath, "wb") as f:
                    f.write(image_part.blob)
                print(f"  Extracted: {filename}")

    return image_count


def extract_from_pdf(pdf_path: str, output_dir: str) -> int:
    """Extract images from PDF document using PyMuPDF (fitz).

    Note: Vector graphics extraction is not supported because PDF stores vector
    paths as drawing instructions, not as separate image objects. PyMuPDF cannot
    extract individual vector graphics as SVG files.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("Error: PyMuPDF is required. Install with: pip install pymupdf")
        return 0

    doc = fitz.open(pdf_path)
    image_count = 0
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for page_num, page in enumerate(doc, 1):
        # Extract all images from the page
        image_list = page.get_images(full=True)

        for img in image_list:
            xref = img[0]
            base_image = doc.extract_image(xref)

            image_count += 1
            ext = base_image["ext"]
            image_data = base_image["image"]

            # Determine filename with page info
            filename = f"image_{image_count:03d}.{ext}"
            filepath = output_path / filename

            with open(filepath, "wb") as f:
                f.write(image_data)

            print(f"  Extracted: {filename} (page {page_num})")

    doc.close()
    return image_count


def main():
    parser = argparse.ArgumentParser(
        description="Extract images from Word (.docx) and PDF (.pdf) documents"
    )
    parser.add_argument("input", help="Path to the document file (.docx or .pdf)")
    parser.add_argument(
        "-o", "--output",
        help="Output directory (default: <input_file>_images)"
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="DPI for PNG conversion (default: 150, higher = better quality)"
    )
    parser.add_argument(
        "--keep-emf",
        action="store_true",
        help="Keep original EMF/WMF files (Word only)"
    )
    parser.add_argument(
        "--convert-svg",
        action="store_true",
        help="Convert EMF/WMF to SVG (Word only)"
    )

    args = parser.parse_args()

    # Get DPI setting
    dpi = args.dpi

    # Word-specific options
    keep_emf = args.keep_emf
    convert_svg = args.convert_svg

    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        output_dir = f"{input_path.parent / input_path.stem}_images"

    print(f"Extracting images from: {input_path}")
    print(f"Output directory: {output_dir}")
    print(f"PNG DPI: {dpi}")

    # Extract based on file type
    ext = input_path.suffix.lower()

    if ext == ".docx":
        image_count = extract_from_docx(str(input_path), output_dir, dpi=dpi, keep_emf=keep_emf, convert_svg=convert_svg)
    elif ext == ".pdf":
        image_count = extract_from_pdf(str(input_path), output_dir)
    else:
        print(f"Error: Unsupported file type '{ext}'. Only .docx and .pdf are supported.")
        sys.exit(1)

    print(f"\nTotal images extracted: {image_count}")
    print(f"Images saved to: {output_dir}")

    if image_count == 0:
        print("Note: No images found in the document.")
        sys.exit(0)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
