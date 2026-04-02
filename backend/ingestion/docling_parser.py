"""
PDF and DOCX parser using Docling.

Docling uses the DocLayNet AI model for layout understanding — it recovers
headings, sections, tables, and figures from the document's actual layout,
not from font-size heuristics. Output is clean markdown that feeds directly
into the MarkdownChunker.

For image-heavy PDFs, Docling extracts image crops which are described by a
VLM (qwen3-vl:4b via Ollama) and injected back into the markdown as text so
figure content is searchable. Requires `ollama pull qwen3-vl:4b`.
Controlled by config.enable_image_description; gracefully skipped if the
model is not available or Ollama is unreachable.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat

from config import settings
from ingestion.base import BaseParser, ParseResult


def _build_converter(enable_ocr: bool = False) -> DocumentConverter:
    """
    Build a Docling DocumentConverter.

    PyPdfium2 backend is used by default (fast, accurate for digital PDFs).
    OCR via EasyOCR is activated only when explicitly requested (e.g. scanned PDFs).
    TableFormer in accurate mode extracts complex multi-header tables correctly.
    """
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = enable_ocr
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.mode = "accurate"  # TableFormer accurate mode
    # Must be True for pic.image.pil_image to be populated — without it Docling
    # records that figures exist but never crops the pixel data, so _picture_to_base64
    # always returns None and no VLM descriptions are ever generated.
    pipeline_options.generate_picture_images = True

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


# Module-level converter — instantiated once to avoid reloading AI models on every request
_converter = _build_converter(enable_ocr=False)


class DoclingParser(BaseParser):
    """Handles both PDF and DOCX via a single Docling pipeline."""

    def parse(self, path: Path, file_hash: str, size_bytes: int) -> ParseResult:
        result = _converter.convert(str(path))
        doc = result.document

        # Export to markdown — Docling preserves heading hierarchy, tables, lists
        markdown = doc.export_to_markdown()

        # Extract document-level metadata from Docling's structured output
        extracted: dict = {}
        file_type = path.suffix.lower().lstrip(".")

        if file_type == "pdf":
            # Page count from Docling result
            page_count = len(result.pages) if hasattr(result, "pages") else 0
            # Pull title/author from document metadata if available
            meta = doc.meta if hasattr(doc, "meta") else {}
            extracted = {
                "title": getattr(meta, "title", None) or _extract_first_heading(markdown),
                "author": getattr(meta, "authors", None),
                "page_count": page_count,
                "headings": _extract_headings(markdown)[:50],
            }
        elif file_type == "docx":
            extracted = {
                "title": _extract_first_heading(markdown),
                "headings": _extract_headings(markdown)[:50],
                "section_count": len(_extract_headings(markdown)),
            }

        # Build heading → page number map for PDF/DOCX so the chunker can annotate
        # each chunk with the page of its nearest preceding heading.
        # Docling records page_no on each element via item.prov[0].page_no.
        # We iterate the text items and store only section headers / titles.
        # Wrapped in try/except — gracefully omitted if the Docling API changes.
        if file_type in ("pdf", "docx"):
            extracted["heading_page_map"] = _build_heading_page_map(doc)

        # If image description is enabled, extract compressed figure images and store
        # in metadata. The background embedding task reads these to call the VLM
        # asynchronously — upload stays fast, SQLite chunks keep <!-- image --> as-is.
        if settings.enable_image_description:
            pictures = getattr(doc, "pictures", None) or []
            if pictures:
                extracted["figures_b64"] = [
                    _picture_to_base64(pic, doc) or "" for pic in pictures
                ]

        return ParseResult(
            filename=path.name,
            file_type=file_type,
            size_bytes=size_bytes,
            file_hash=file_hash,
            extracted_metadata=extracted,
            markdown_content=markdown,
        )



def _build_heading_page_map(doc) -> dict[str, int]:
    """
    Walk Docling's element list and return {heading_text: page_no} for all
    section headers and titles.

    This map is stored in extracted_metadata so MarkdownChunker can attribute
    each chunk to a page number by looking up its nearest heading.

    Docling stores page_no on each element's ProvenanceItem (item.prov[0].page_no).
    The heading text stored here matches the text that _build_heading_index extracts
    from the markdown (both strip surrounding whitespace), so the dict key lookup
    in the chunker is reliable.

    Returns an empty dict on any error so callers never crash.
    """
    heading_map: dict[str, int] = {}
    try:
        for item in (getattr(doc, "texts", None) or []):
            prov = getattr(item, "prov", None)
            if not prov:
                continue
            page_no = prov[0].page_no if prov else None
            if page_no is None:
                continue
            text = (getattr(item, "text", "") or "").strip()
            label = str(getattr(item, "label", "") or "")
            # DocItemLabel values for headings: SECTION_HEADER, TITLE
            if text and any(kw in label for kw in ("SECTION_HEADER", "TITLE", "heading", "section")):
                heading_map[text] = page_no
    except Exception:
        pass
    return heading_map


def _pil_to_base64(pil_img, max_size: int = 512, quality: int = 50) -> str:
    """Resize a PIL image to fit within max_size and encode as base64 JPEG."""
    if pil_img.width > max_size or pil_img.height > max_size:
        pil_img = pil_img.copy()
        pil_img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _picture_to_base64(pic, doc) -> str | None:
    """Extract a picture item from Docling as a compressed base64 JPEG string."""
    try:
        # Canonical Docling API: get_image(doc) returns a PIL Image
        pil_img = pic.get_image(doc)
        if pil_img is not None:
            return _pil_to_base64(pil_img)
    except Exception:
        pass
    try:
        # Fallback: picture.image.pil_image (populated when generate_picture_images=True)
        pil_img = pic.image.pil_image
        if pil_img is not None:
            return _pil_to_base64(pil_img)
    except Exception:
        pass
    try:
        # Last resort: raw bytes on picture.image.data
        raw = pic.image.data
        if isinstance(raw, bytes) and raw:
            return base64.b64encode(raw).decode()
    except Exception:
        pass
    return None



def _extract_headings(markdown: str) -> list[str]:
    """Pull all heading texts from markdown (lines starting with #)."""
    headings = []
    for line in markdown.splitlines():
        stripped = line.lstrip("#").strip()
        if line.startswith("#") and stripped:
            headings.append(stripped)
    return headings


def _extract_first_heading(markdown: str) -> str | None:
    """Return the first H1 heading as the document title."""
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
