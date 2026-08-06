"""
PDF watermark engine — Nawaab Wk Bot

Three watermark types, all built with reportlab (draw layer) + pypdf (merge):

  TYPE 1 (top_text)
    - Drawn on EVERY page of the original PDF EXCEPT the last original page.
    - Top-right corner (as the page is actually DISPLAYED), 45° extra tilt,
      low opacity, plain text (no link).

  TYPE 2 (link_text / link_url)
    - Drawn in ADDITION to type 1, on every page whose page number is a
      multiple of REPEAT_EVERY_N_PAGES, except the last original page.
    - Bottom-left corner (as displayed), no extra tilt, full opacity,
      clickable link.

  TYPE 3 (optional, same link_text / link_url as type 2)
    - The image supplied in step 5 becomes a brand-new page appended
      AFTER the original PDF's last page (the true final page of the output).
    - This page never gets type 1 or type 2.
    - It only gets a watermark if the user opts in (/Yes) — same text,
      link and position as type 2. If the user sends /Skip, this page is
      added with no watermark at all.

The last ORIGINAL page of the source PDF is intentionally left completely
clean (no type 1, no type 2) since the appended image page takes over as
the document's real "last page".

--------------------------------------------------------------------------
FIX NOTES (read this if you're debugging "stuck" / "watermark not applied"
issues again later):

1. The previous version called pypdf's page.extract_text() (wrapped with an
   8s-timeout thread pool) on every page that wasn't obviously image-heavy,
   to decide whether to boost watermark opacity. extract_text() is known to
   hang or run extremely slowly on certain real-world / edited PDFs, and
   with no true way to kill a native thread mid-call, a handful of "bad"
   pages could burn minutes each — this was the actual cause of jobs
   appearing to freeze forever with the progress message stuck on the very
   first step. That call has been removed completely. The "is this page
   mostly a scanned image" check is now 100% structural (looks only at
   embedded XObject image sizes vs page area) — no text extraction, so it
   can never hang.

2. Pages can be rotated in the PDF itself (/Rotate = 90/180/270 — very
   common in scanned textbook PDFs, including landscape diagrams rotated
   to fit a portrait book). The old code always drew the watermark in the
   *raw* (unrotated) MediaBox coordinate system, so on a rotated page the
   watermark could end up sideways, upside-down, or in the wrong corner
   once the PDF viewer applied its own rotation. This version computes
   each page's actual /Rotate value and maps our desired *visible* corner
   position back into raw MediaBox space (see `_view_to_raw`), so the
   watermark always ends up upright and in the correct corner exactly as
   the reader will actually see the page, for every rotation.

3. A single malformed/corrupt page used to be able to abort the entire
   batch. Each page is now processed in its own try/except — if a page's
   watermark genuinely can't be drawn, that one page is kept as-is
   (unwatermarked) and a clear error is printed to the logs, while the
   rest of the document still finishes normally.

4. `build_watermarked_pdf` now accepts an optional `progress_callback`
   so the bot can show real "page X of Y" progress instead of a canned
   message that never updates — this is what actually fixes the "looks
   stuck" symptom from the user's side, independent of raw speed.

5. Verbose, timestamped logging throughout (prefixed `[Watermark]`) so
   Render logs make it obvious exactly which page / step broke or was
   slow if something ever goes wrong again.
--------------------------------------------------------------------------
"""

import io
import os
import time
import traceback

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from vars import REPEAT_EVERY_N_PAGES

# Visible-corner watermark configs. x_frac/y_frac are fractions of the page
# as the reader actually SEES it (i.e. already accounting for /Rotate) —
# _watermark_layer() takes care of mapping these into raw MediaBox space.

DOWN_RIGHTS = dict(x_frac=0.97, y_frac=0.02, opacity=0.20, rotation=0, anchor="right")
TOP_RIGHT = DOWN_RIGHTS
DOWN_LEFT = dict(x_frac=0.03, y_frac=0.02, opacity=1.00, rotation=0, anchor="left")
DOWN_RIGHT = DOWN_LEFT


def _log(msg: str) -> None:
    print(f"[Watermark] {msg}", flush=True)


def _log_exc(context: str, exc: Exception) -> None:
    print(f"[Watermark] ERROR in {context}: {exc}", flush=True)
    print(traceback.format_exc(), flush=True)


# ── Rotation-aware placement helpers ────────────────────────────────────────

def _page_geometry(page):
    """Return (raw_w, raw_h, offset_x, offset_y, rotate, vis_w, vis_h) for a
    pypdf page — raw_w/raw_h are the MediaBox's own size, offset_x/offset_y
    handle MediaBoxes that don't start at (0,0) (rare, but some scanners /
    editors produce these), rotate is the normalized /Rotate value, and
    vis_w/vis_h are the size of the page as it's actually DISPLAYED."""
    try:
        mb = page.mediabox
        raw_w = float(mb.width)
        raw_h = float(mb.height)
        offset_x = float(mb.left)
        offset_y = float(mb.bottom)
    except Exception as e:
        _log_exc("_page_geometry (mediabox)", e)
        raw_w, raw_h, offset_x, offset_y = 595.0, 842.0, 0.0, 0.0  # A4 fallback

    try:
        rotate = int(page.get("/Rotate", 0)) % 360
    except Exception:
        rotate = 0
    if rotate not in (0, 90, 180, 270):
        rotate = 0

    vis_w, vis_h = (raw_h, raw_w) if rotate in (90, 270) else (raw_w, raw_h)
    return raw_w, raw_h, offset_x, offset_y, rotate, vis_w, vis_h


def _view_to_raw(rotate: int, raw_w: float, raw_h: float, vx: float, vy: float):
    """Map a point (vx, vy) given in the page's *visible/displayed*
    coordinate system into the underlying raw (unrotated) MediaBox
    coordinate system, and return the extra local rotation (degrees) the
    drawn content needs so that — once the PDF viewer applies /Rotate for
    display — it ends up upright at (vx, vy). Verified empirically against
    actual rendered output for all four rotation values."""
    if rotate == 90:
        return raw_w - vy, vx, 90
    if rotate == 180:
        return raw_w - vx, raw_h - vy, 180
    if rotate == 270:
        return vy, raw_h - vx, -90
    return vx, vy, 0


def _page_is_image_based(page) -> bool:
    """Cheap, purely structural scanned/slide-page detector — used only to
    slightly boost watermark opacity on image-heavy pages so it stays
    visible. Deliberately does NOT call page.extract_text() (see FIX NOTES
    above): that call is what used to freeze jobs indefinitely."""
    try:
        pw = float(page.mediabox.width)
        ph = float(page.mediabox.height)
        page_area = pw * ph
        if page_area <= 0:
            return False

        resources = page.get("/Resources", {})
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjs = resources.get("/XObject", {}) if resources else {}
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()

        total_img_area = 0
        for key in xobjs:
            try:
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
            except Exception:
                # A single unreadable XObject entry shouldn't break detection.
                continue

        return total_img_area > page_area * 0.60
    except Exception as e:
        _log_exc("_page_is_image_based", e)
        return False


def _watermark_layer(raw_w: float, raw_h: float, offset_x: float, offset_y: float,
                      rotate: int, configs: list, boost: bool):
    """Build a single reportlab canvas page carrying all requested watermark
    texts/links for this page (in raw MediaBox space, rotation already
    accounted for), returned as a pypdf page object."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(raw_w, raw_h))
    vis_w, vis_h = (raw_h, raw_w) if rotate in (90, 270) else (raw_w, raw_h)

    for cfg in configs:
        text = cfg["text"]
        url = cfg.get("url")
        x_frac = cfg["x_frac"]
        y_frac = cfg["y_frac"]
        opacity = cfg["opacity"]
        base_rotation = cfg["rotation"]
        anchor = cfg["anchor"]

        if boost:
            opacity = min(1.0, opacity + 0.11)

        font_name = "Helvetica-Bold" if url else "Helvetica"
        font_size = max(9, int(vis_w / 35))

        vx = vis_w * x_frac
        vy = vis_h * y_frac
        X, Y, extra_rot = _view_to_raw(rotate, raw_w, raw_h, vx, vy)
        X += offset_x
        Y += offset_y
        total_rotation = base_rotation + extra_rot

        c.saveState()
        try:
            c.setFillColor(Color(0, 0, 1, alpha=opacity))
            c.setFont(font_name, font_size)
            c.translate(X, Y)
            if total_rotation:
                c.rotate(total_rotation)

            text_width = c.stringWidth(text, font_name, font_size)
            if anchor == "left":
                c.drawString(0, 0, text)
                lx = 0.0
            elif anchor == "right":
                c.drawRightString(0, 0, text)
                lx = -text_width
            else:
                c.drawCentredString(0, 0, text)
                lx = -text_width / 2.0

            if url:
                ly = -font_size * 0.3
                try:
                    # relative=1 -> rect is in the CURRENT (translated +
                    # rotated) coordinate system, so the clickable area
                    # tracks the visible text correctly on rotated pages too.
                    c.linkURL(
                        url,
                        (lx, ly, lx + text_width, ly + font_size * 1.2),
                        relative=1,
                    )
                except Exception as e:
                    _log_exc("linkURL", e)
        except Exception as e:
            _log_exc("_watermark_layer (draw text)", e)
        finally:
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
        if img.mode in ("RGBA", "LA", "P"):
            # Composite onto white first so transparent areas don't turn
            # black when we flatten to RGB.
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img_rgba = img.convert("RGBA")
            bg.paste(img_rgba, mask=img_rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")

        img_w, img_h = img.size
        scale = min(page_w / img_w, page_h / img_h)
        draw_w, draw_h = img_w * scale, img_h * scale
        x = (page_w - draw_w) / 2
        y = (page_h - draw_h) / 2
        c.drawImage(
            ImageReader(img), x, y, width=draw_w, height=draw_h,
            preserveAspectRatio=True, mask="auto",
        )
    except Exception as e:
        _log_exc("_image_to_page", e)
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]


def build_watermarked_pdf(
    input_pdf: str,
    output_pdf: str,
    top_text: str,
    link_text: str | None,
    link_url: str | None,
    last_page_image: str | None,
    apply_last_page_watermark: bool,
    progress_callback=None,
) -> int:
    """
    Synchronous — run this inside a thread/executor from async code for big PDFs.
    Returns total number of pages in the produced PDF.

    link_text / link_url may be None (user sent /Skip on those steps) — in
    that case Type-2 (repeating link watermark) and the optional Type-3 last
    page watermark are simply not applied, everything else stays unchanged.

    progress_callback, if given, is called as progress_callback(current_page,
    total_pages) after every page is processed, from this same (worker)
    thread — the caller is responsible for reading it safely (a simple
    attribute/dict write + read is fine; don't do anything blocking in it).
    """
    t_start = time.time()
    _log(f"Starting job: input={input_pdf!r} -> output={output_pdf!r}")

    try:
        in_size = os.path.getsize(input_pdf)
        _log(f"Input file size: {in_size / (1024 * 1024):.2f} MB")
    except OSError:
        pass

    try:
        reader = PdfReader(input_pdf)
    except (PdfReadError, Exception) as e:
        _log_exc("PdfReader(input_pdf)", e)
        raise RuntimeError(
            "This PDF file appears to be corrupted or unreadable. Please try a different file."
        ) from e

    if reader.is_encrypted:
        _log("Input PDF is encrypted — attempting to open with an empty password...")
        try:
            reader.decrypt("")
        except Exception as e:
            _log_exc("reader.decrypt", e)
        if reader.is_encrypted:
            raise RuntimeError(
                "This PDF is password-protected. Please remove the password and send it again."
            )

    writer = PdfWriter()
    total_pages = len(reader.pages)
    _log(f"Total pages in source PDF: {total_pages}")

    failed_pages = 0

    for idx, page in enumerate(reader.pages, start=1):
        page_t0 = time.time()
        try:
            raw_w, raw_h, off_x, off_y, rotate, vis_w, vis_h = _page_geometry(page)
            is_last_original_page = idx == total_pages
            boost = _page_is_image_based(page)

            configs = []
            if not is_last_original_page:
                configs.append({"text": top_text, "url": None, **TOP_RIGHT})
                if idx % REPEAT_EVERY_N_PAGES == 0 and link_text:
                    configs.append({"text": link_text, "url": link_url, **DOWN_RIGHT})

            if configs:
                wm_layer = _watermark_layer(raw_w, raw_h, off_x, off_y, rotate, configs, boost)
                page.merge_page(wm_layer)  # watermark drawn on top of page content

            writer.add_page(page)
        except Exception as e:
            failed_pages += 1
            _log_exc(f"page {idx}/{total_pages} (rotate={locals().get('rotate', '?')})", e)
            # Degrade gracefully: keep the original page content even if the
            # watermark itself failed, instead of aborting the whole job.
            try:
                writer.add_page(page)
            except Exception as e2:
                _log_exc(f"page {idx}/{total_pages} fallback add_page", e2)

        if idx % 20 == 0 or idx == total_pages:
            _log(f"Processed page {idx}/{total_pages} (last page took {time.time() - page_t0:.2f}s)")

        if progress_callback:
            try:
                progress_callback(idx, total_pages)
            except Exception as e:
                _log_exc("progress_callback", e)

    if failed_pages:
        _log(f"WARNING: {failed_pages} page(s) could not be watermarked and were kept as-is.")

    if last_page_image and os.path.exists(last_page_image):
        _log("Adding custom last page image...")
        try:
            last_pw = float(reader.pages[-1].mediabox.width)
            last_ph = float(reader.pages[-1].mediabox.height)
            img_page = _image_to_page(last_page_image, last_pw, last_ph)

            if apply_last_page_watermark and link_text:
                wm_layer = _watermark_layer(
                    last_pw, last_ph, 0.0, 0.0, 0,
                    [{"text": link_text, "url": link_url, **DOWN_RIGHT}],
                    boost=True,
                )
                img_page.merge_page(wm_layer)

            writer.add_page(img_page)
            total_pages += 1
        except Exception as e:
            _log_exc("last page image handling", e)

    _log("Writing output PDF to disk...")
    try:
        with open(output_pdf, "wb") as f:
            writer.write(f)
    except Exception as e:
        _log_exc("writer.write(output_pdf)", e)
        raise RuntimeError("Failed to save the watermarked PDF. Please try again.") from e

    elapsed = time.time() - t_start
    _log(f"Job complete: {total_pages} total pages, {failed_pages} page(s) had watermark issues, took {elapsed:.2f}s")

    if progress_callback:
        try:
            progress_callback(total_pages, total_pages)
        except Exception:
            pass

    return total_pages
