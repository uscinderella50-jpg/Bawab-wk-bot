"""
PDF watermark engine — Nawaab Wk Bot

Three watermark types, all built with reportlab (draw layer) + pypdf (merge):

  TYPE 1 (top_text)
    - Drawn on EVERY page of the original PDF EXCEPT the last original page.
    - Top-right corner, 45° rotation, 30% opacity, plain text (no link).

  TYPE 2 (link_text / link_url)
    - Drawn in ADDITION to type 1, on every page whose page number is a
      multiple of 25 (25th, 50th, 75th ...), except the last original page.
    - Bottom-right corner, no rotation, 80% opacity, clickable link.

  TYPE 3 (optional, same link_text / link_url as type 2)
    - The image supplied in step 5 becomes a brand-new page appended
      AFTER the original PDF's last page (the true final page of the output).
    - This page never gets type 1 or type 2.
    - It only gets a watermark if the user opts in (/Yes) — same text,
      link and bottom-right position as type 2. If the user sends /Skip,
      this page is added with no watermark at all.

The last ORIGINAL page of the source PDF is intentionally left completely
clean (no type 1, no type 2) since the appended image page takes over as
the document's real "last page".
"""

import io
import os

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from vars import REPEAT_EVERY_N_PAGES

TOP_RIGHT = dict(x_frac=0.80, y_frac=0.85, opacity=0.30, rotation=45, anchor="center")
DOWN_RIGHT = dict(x_frac=0.96, y_frac=0.04, opacity=0.80, rotation=0, anchor="right")


def _page_is_image_based(page) -> bool:
    """Detect scanned/slide-style pages (mostly image, little/no text) so we
    can boost watermark opacity there for visibility."""
    try:
        pw = float(page.mediabox.width)
        ph = float(page.mediabox.height)
        page_area = pw * ph

        resources = page.get("/Resources", {})
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjs = resources.get("/XObject", {}) if resources else {}
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()

        total_img_area = 0
        for key in xobjs:
            obj = xobjs[key]
            if hasattr(obj, "get_object"):
                obj = obj.get_object()
            if obj.get("/Subtype") == "/Image":
                w = int(obj.get("/Width", 0))
                h = int(obj.get("/Height", 0))
                img_area = w * h
                if img_area > page_area * 0.40:
                    return True
                total_img_area += img_area

        if total_img_area > page_area * 0.60:
            return True

        try:
            extracted = page.extract_text() or ""
            if len(extracted.strip()) < 20 and len(list(xobjs.keys())) > 0:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _watermark_layer(page_width: float, page_height: float, configs: list, boost: bool):
    """Build a single reportlab canvas page carrying all requested watermark
    texts/links for this page, returned as a pypdf page object."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_width, page_height))

    for cfg in configs:
        text = cfg["text"]
        url = cfg.get("url")
        x_frac = cfg["x_frac"]
        y_frac = cfg["y_frac"]
        opacity = cfg["opacity"]
        rotation = cfg["rotation"]
        anchor = cfg["anchor"]

        if boost:
            opacity = min(1.0, opacity + 0.35)

        font_size = max(9, int(page_width / 28))
        x_pos = page_width * x_frac
        y_pos = page_height * y_frac

        c.saveState()
        c.setFillColor(Color(0, 0, 1, alpha=opacity))
        c.setFont("Helvetica-Bold", font_size)
        c.translate(x_pos, y_pos)
        if rotation:
            c.rotate(rotation)

        if anchor == "left":
            c.drawString(0, 0, text)
        elif anchor == "right":
            c.drawRightString(0, 0, text)
        else:
            c.drawCentredString(0, 0, text)

        if url:
            try:
                text_width = c.stringWidth(text, "Helvetica-Bold", font_size)
                if anchor == "center":
                    lx = x_pos - text_width / 2
                elif anchor == "right":
                    lx = x_pos - text_width
                else:
                    lx = x_pos
                ly = y_pos - font_size * 0.3
                c.linkURL(url, (lx, ly, lx + text_width, ly + font_size * 1.2), relative=0)
            except Exception as e:
                print(f"[Watermark] link annotation error: {e}")

        c.restoreState()

    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]


def _image_to_page(image_path: str, page_w: float, page_h: float):
    """Render the last-page image centered/fit onto a page of the given size."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_w, page_h))
    try:
        img = Image.open(image_path)
        img = img.convert("RGB")
        img_w, img_h = img.size
        scale = min(page_w / img_w, page_h / img_h)
        draw_w, draw_h = img_w * scale, img_h * scale
        x = (page_w - draw_w) / 2
        y = (page_h - draw_h) / 2
        c.drawImage(image_path, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
    except Exception as e:
        print(f"[Watermark] last-page image draw error: {e}")
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]


def remove_pdf_pages(input_pdf: str, output_pdf: str, pages_to_remove: set) -> int:
    """
    Writes a new PDF with the given 1-based page numbers removed.
    Remaining pages shift up naturally, so e.g. removing page 15 makes the
    old page 16 become the new page 15. Removing the PDF's own last page
    (the most common case) is fully supported.

    Uses strict=False so real-world PDFs with slightly malformed xref/trailer
    data (very common in large scanned/course PDFs) still parse correctly
    instead of failing or hanging, and uses pypdf's native bulk page-index
    clone (PdfWriter.append with an explicit page list) instead of copying
    pages one-by-one, which is both faster and far less prone to producing a
    corrupted output file.
    """
    reader = PdfReader(input_pdf, strict=False)
    total = len(reader.pages)

    # 0-based indices to KEEP, in original order.
    keep_indices = [i for i in range(total) if (i + 1) not in pages_to_remove]

    writer = PdfWriter()
    if keep_indices:
        writer.append(reader, pages=keep_indices)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    return len(keep_indices)


def build_watermarked_pdf(
    input_pdf: str,
    output_pdf: str,
    top_text: str,
    link_text: str,
    link_url: str,
    last_page_image: str | None,
    apply_last_page_watermark: bool,
) -> int:
    """
    Synchronous — run this inside a thread/executor from async code for big PDFs.
    Returns total number of pages in the produced PDF.
    """
    reader = PdfReader(input_pdf)
    writer = PdfWriter()
    total_pages = len(reader.pages)

    for idx, page in enumerate(reader.pages, start=1):
        pw = float(page.mediabox.width)
        ph = float(page.mediabox.height)
        is_last_original_page = idx == total_pages
        boost = _page_is_image_based(page)

        configs = []
        if not is_last_original_page:
            configs.append({"text": top_text, "url": None, **TOP_RIGHT})
            if idx % REPEAT_EVERY_N_PAGES == 0:
                configs.append({"text": link_text, "url": link_url, **DOWN_RIGHT})

        if configs:
            wm_layer = _watermark_layer(pw, ph, configs, boost)
            page.merge_page(wm_layer)  # watermark drawn on top of page content

        writer.add_page(page)

    if last_page_image and os.path.exists(last_page_image):
        last_pw = float(reader.pages[-1].mediabox.width)
        last_ph = float(reader.pages[-1].mediabox.height)
        img_page = _image_to_page(last_page_image, last_pw, last_ph)

        if apply_last_page_watermark:
            wm_layer = _watermark_layer(
                last_pw, last_ph, [{"text": link_text, "url": link_url, **DOWN_RIGHT}], boost=True
            )
            img_page.merge_page(wm_layer)

        writer.add_page(img_page)
        total_pages += 1

    with open(output_pdf, "wb") as f:
        writer.write(f)

    return total_pages
