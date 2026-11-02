#!/usr/bin/env python3
r"""Prepare oversized trading-book PDFs for AI-assisted semantic distillation.

Output is an INTERMEDIATE source package, not a summary or an AI knowledge base.
- Page-attributed Markdown: searchable text extracted from the PDF.
- Compact visual PDF parts: chart-heavy/scanned pages rendered at lower resolution.
- Manifest mapping original book names and pages to these outputs.

Usage from PowerShell in the repository root:
  py -m pip install pymupdf pillow
  py prepare_trading_books.py .\docs
  py prepare_trading_books.py .\docs --all-visual

Output defaults to a sibling folder outside the Git repository.
"""

from __future__ import annotations
import argparse
import io
import json
import re
from pathlib import Path

import pymupdf as fitz
from PIL import Image

MAX_TEXT_BYTES = 9 * 1024 * 1024
MAX_VISUAL_BYTES = 22 * 1024 * 1024


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def jpeg_render(page: fitz.Page, dpi: int, quality: int) -> bytes:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=quality, optimize=True)
    return b.getvalue()


def visually_important(page: fitz.Page, extracted_text: str) -> bool:
    if len(extracted_text.strip()) < 120:
        return True
    try:
        info = page.get_image_info()
        for img in info:
            bbox = fitz.Rect(img["bbox"])
            if page.rect.get_area() and (bbox & page.rect).get_area() / page.rect.get_area() > 0.025:
                return True
    except Exception:
        pass
    try:
        return len(page.get_drawings()) > 35
    except Exception:
        return False


def append_visual_page(output: fitz.Document, source_page: fitz.Page, jpeg: bytes, pnum: int) -> None:
    r = source_page.rect
    dest = output.new_page(width=r.width, height=r.height + 23)
    dest.insert_image(fitz.Rect(0, 0, r.width, r.height), stream=jpeg, keep_proportion=False)
    dest.insert_text((8, r.height + 15), f"SOURCE PAGE {pnum}", fontsize=9, color=(0.2, 0.2, 0.2))


def process_book(pdf_path: Path, book_index: int, out_dir: Path, all_visual: bool, dpi: int, quality: int):
    book_id = f"book_{book_index:02d}"
    book_dir = out_dir / book_id
    book_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[{book_id}] {pdf_path.name}", flush=True)

    src = fitz.open(pdf_path)
    if src.needs_pass:
        print("  Encrypted PDF: skipped (requires password).")
        return {"book_id": book_id, "source": pdf_path.name, "error": "encrypted"}

    md_index = 1
    md_sections = []
    md_bytes = 0
    text_files = []
    visual_files = []
    page_map = []
    txt_pages = 0
    visual_pages = 0
    visual_doc = fitz.open()
    visual_size = 0
    visual_index = 1
    visual_first = None
    visual_last = None

    def save_text_part():
        nonlocal md_index, md_sections, md_bytes
        if not md_sections:
            return
        name = f"{book_id}_text_{md_index:03d}.md"
        header = (
            f"# AI source extract — {book_id}\n\n"
            f"Original document: {pdf_path.name}\n\n"
            "This is extracted source text, NOT an AI-generated summary. "
            "Page numbers below refer to the original PDF. "
            "Figures are not fully represented in this text.\n\n"
        )
        (book_dir / name).write_text(header + "".join(md_sections), encoding="utf-8")
        text_files.append(name)
        md_index += 1
        md_sections = []
        md_bytes = 0

    def save_visual_part():
        nonlocal visual_doc, visual_size, visual_index, visual_first, visual_last
        if len(visual_doc) == 0:
            return
        name = f"{book_id}_charts_{visual_index:03d}.pdf"
        path = book_dir / name
        visual_doc.save(str(path), garbage=3, deflate=True)
        actual = path.stat().st_size
        if actual > 25 * 1024 * 1024:
            print(f"  NOTE: {name} is {actual / 1024**2:.1f} MiB, consider lower --dpi / --quality.")
        visual_files.append({"name": name, "start_original_page": visual_first, "end_original_page": visual_last, "bytes": actual})
        visual_doc.close()
        visual_doc = fitz.open()
        visual_size = 0
        visual_index += 1
        visual_first = None
        visual_last = None

    for idx in range(len(src)):
        page = src[idx]
        pnum = idx + 1
        text = clean_text(page.get_text("text", sort=True))
        if text:
            txt_pages += 1
            section = f"\n\n---\n\n## ORIGINAL PDF PAGE {pnum}\n\n{text}\n"
            n_bytes = len(section.encode("utf-8"))
            if md_sections and md_bytes + n_bytes > MAX_TEXT_BYTES:
                save_text_part()
            md_sections.append(section)
            md_bytes += n_bytes
        is_visual = all_visual or visually_important(page, text)
        if is_visual:
            jpeg = jpeg_render(page, dpi=dpi, quality=quality)
            # Hard cap for atypically huge single-page images.
            if len(jpeg) > MAX_VISUAL_BYTES - 500_000:
                jpeg = jpeg_render(page, dpi=max(65, dpi // 2), quality=min(quality, 40))
            if len(visual_doc) and visual_size + len(jpeg) > MAX_VISUAL_BYTES:
                save_visual_part()
            if visual_first is None:
                visual_first = pnum
            visual_last = pnum
            append_visual_page(visual_doc, page, jpeg, pnum)
            visual_size += len(jpeg) + 2000
            visual_pages += 1
        page_map.append({"page": pnum, "has_extractable_text": bool(text), "in_visual_pdf": bool(is_visual)})
        if pnum % 100 == 0:
            print(f"  Read {pnum}/{len(src)} pages...", flush=True)
    save_text_part()
    save_visual_part()
    src.close()
    info = {
        "book_id": book_id,
        "source": pdf_path.name,
        "source_bytes": pdf_path.stat().st_size,
        "pages": len(page_map),
        "pages_with_text": txt_pages,
        "pages_with_visual_source": visual_pages,
        "text_files": text_files,
        "visual_files": visual_files,
        "original_page_map": page_map,
    }
    (book_dir / "page_map.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Done: {txt_pages} text pages, {visual_pages} visual pages; "
          f"{len(text_files)} markdown parts, {len(visual_files)} visual PDF parts.", flush=True)
    return info


def main():
    parser = argparse.ArgumentParser(description="Prepare big trading PDFs for AI reading; no model needed")
    parser.add_argument("pdf_dir", nargs="?", default="docs", help="Folder containing source PDF files")
    parser.add_argument("--output", type=Path, default=None, help="Destination (default: sibling of current directory)")
    parser.add_argument("--all-visual", action="store_true", help="Include every source page as a compact visual PDF")
    parser.add_argument("--dpi", type=int, default=110, help="Rendering quality for visual pages (default: 110)")
    parser.add_argument("--quality", type=int, default=57, help="JPEG quality 1-95 (default: 57)")
    args = parser.parse_args()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        parser.error(f"PDF folder not found: {pdf_dir}")
    output = (args.output or (Path.cwd().parent / "TradingKnowledgeUpload")).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(pdf_dir.rglob("*.pdf"), key=lambda p: p.name.lower())
    if not files:
        parser.error(f"No PDFs found in {pdf_dir}")
    results = []
    for i, f in enumerate(files, 1):
        try:
            results.append(process_book(f, i, output, args.all_visual, args.dpi, args.quality))
        except Exception as e:
            print(f"  ERROR: {f.name}: {type(e).__name__}: {e}", flush=True)
            results.append({"book_id": f"book_{i:02d}", "source": f.name, "error": str(e)})
    (output / "manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Trading Books — AI Reading Package\n",
        "These are intermediate source files, NOT an AI-produced knowledge summary.\n",
        "Upload the Markdown parts and the necessary chart PDFs to an AI assistant to have it analyze, deduplicate, and create a unified knowledge base.\n",
        "Original page numbers are maintained in headings and in visual-PDF footers.\n",
        "Visual PDFs carry essential price-chart information that text extraction cannot capture.\n",
        "Do not commit source books or their derivatives to a public Git repository unless you have the rights to do so.\n",
        "\n## Sources\n",
    ]
    for item in results:
        lines.append(f"- **{item['book_id']}** — {item['source']} "
                     f"({item.get('pages', '?')} pages; {len(item.get('text_files', []))} text parts; "
                     f"{len(item.get('visual_files', []))} visual parts)\n")
    (output / "START_HERE.md").write_text("".join(lines), encoding="utf-8")
    print(f"\nAll done. Output (outside Git repository): {output}")
    print("This is source preparation only; semantic distillation still needs an AI to read the material.")


if __name__ == "__main__":
    main()
