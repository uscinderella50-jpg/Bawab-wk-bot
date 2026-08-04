import asyncio
import os
import re
import shutil
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message
from pypdf import PdfReader

from db import save_user
from force_sub import is_subscribed, force_sub_markup
from utils import ProgressTracker
from vars import TOP_TEXT_MAX_LEN, LINK_TEXT_MAX_LEN, FILENAME_MAX_WORDS
from watermark import build_watermarked_pdf, remove_pdf_pages

REMOVE_PAGES_MAX = 10

PROGRESS_STEPS = [
    "Fine,Im attempting thise wait 😁",
    "Wait, I'm working on it 🔍",
    "Alrights, all Good happings 🧐",
    "Working on Watermark 🌊",
    "All Done ✅ wait lil bit.",
]

DOWNLOADS_DIR = "downloads"


def _schedule_delete(msg: Message, delay: int):
    async def _job():
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    asyncio.create_task(_job())


async def _ask_text(bot: Client, chat_id: int, user_id: int, prompt: str, timeout: int = 300):
    """Send prompt, wait for a text reply scoped to this user, delete both, return text."""
    prompt_msg = await bot.send_message(chat_id, prompt)
    _schedule_delete(prompt_msg, 13)
    reply: Message = await bot.listen(chat_id, filters=filters.text & filters.user(user_id), timeout=timeout)
    _schedule_delete(reply, 5)
    return reply.text.strip() if reply.text else ""


def register_wk_handlers(bot: Client):

    @bot.on_message(filters.command(["wk", "Wk", "WK"]) & filters.private)
    async def wk_cmd(client: Client, m: Message):
        user_id = m.from_user.id
        chat_id = m.chat.id
        await save_user(user_id)

        if not await is_subscribed(bot, user_id):
            await m.reply_text(
                "🚫 Please join our channel first, then tap Verify below.",
                reply_markup=force_sub_markup(),
            )
            return

        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        workdir = os.path.join(DOWNLOADS_DIR, uuid.uuid4().hex)
        os.makedirs(workdir, exist_ok=True)
        loop = asyncio.get_event_loop()

        try:
            # ── Step 1: PDF file ──────────────────────────────────────────
            step1 = await client.send_message(chat_id, "Great 😃 \nSend me Your Watermarkless PDF to me Hurry up")
            _schedule_delete(step1, 13)

            try:
                pdf_msg: Message = await bot.listen(
                    chat_id, filters=filters.document & filters.user(user_id), timeout=1800
                )
            except asyncio.TimeoutError:
                await client.send_message(chat_id, "⏰ Timeout! Please send /wk again.")
                return
            _schedule_delete(pdf_msg, 5)

            if not (pdf_msg.document and pdf_msg.document.file_name and pdf_msg.document.file_name.lower().endswith(".pdf")):
                await client.send_message(chat_id, "❌ That's not a PDF file. Please send /wk again.")
                return

            original_name = pdf_msg.document.file_name
            input_pdf_path = os.path.join(workdir, "input.pdf")

            dl_status = await client.send_message(chat_id, "⬇️ Downloading your PDF...")
            try:
                await bot.download_media(
                    pdf_msg,
                    file_name=input_pdf_path,
                    progress=ProgressTracker(dl_status, "⬇️ Downloading your PDF...").__call__,
                )
            except Exception as e:
                await dl_status.edit(f"❌ Download failed:\n`{str(e)[:300]}`")
                return
            await dl_status.delete()

            try:
                total_pages_count = await loop.run_in_executor(
                    None, lambda: len(PdfReader(input_pdf_path).pages)
                )
            except Exception:
                total_pages_count = 0

            # working_pdf_path is what actually feeds the watermark engine —
            # it stays equal to input_pdf_path unless the user removes pages.
            working_pdf_path = input_pdf_path

            # ── Step 2: Type-1 watermark text (every page) ──────────────
            top_text = await _ask_text(
                bot, chat_id, user_id,
                "Alrights 😊 \nNow Send Watermark text(who carries PDF's Every Pages",
            )
            if top_text.strip().lower() == "/skip":
                await client.send_message(chat_id, "❌ This field is required and can't be skipped. Send /wk again.")
                return
            if not top_text or len(top_text) > TOP_TEXT_MAX_LEN:
                await client.send_message(chat_id, f"❌ Text must be 1-{TOP_TEXT_MAX_LEN} characters. Send /wk again.")
                return

            # ── Step 3: Type-2/3 redirect text (skippable) ────────────────
            link_text = await _ask_text(
                bot, chat_id, user_id,
                "Fantastic 😍 \nNow Send Your redirected Text(who carries PDF's Every 25-50..th Pages"
                "\n\n(Send /Skip to skip this — no repeating link watermark will be added)",
            )
            if link_text.strip().lower() == "/skip":
                link_text = None
            elif not link_text or len(link_text) > LINK_TEXT_MAX_LEN:
                await client.send_message(chat_id, f"❌ Text must be 1-{LINK_TEXT_MAX_LEN} characters. Send /wk again.")
                return

            # ── Step 4: Redirect URL (skippable) ───────────────────────────
            link_url = await _ask_text(
                bot, chat_id, user_id,
                "Alrights 😊 \nNow send this Text Redirect Link(must be starts with http or https) "
                "\n\n(Send /Skip to skip this — no clickable link will be added)",
            )
            if link_url.strip().lower() == "/skip":
                link_url = None
            elif not (link_url.startswith("http://") or link_url.startswith("https://")):
                await client.send_message(chat_id, "❌ Invalid link. It must start with http or https. Send /wk again.")
                return

            # ── Extra step: optional page removal ────────────────────────
            rm_prompt = await client.send_message(
                chat_id,
                "Amazing 🤩!\n"
                f"Total pages in this PDF: {total_pages_count} !\n"
                "Do you wanna to remove any kind of pages of this PDF so send me "
                "number(only with connect & for multiple numbers) OR you Can /Skip this Step!",
            )
            _schedule_delete(rm_prompt, 13)
            try:
                rm_msg: Message = await bot.listen(
                    chat_id, filters=filters.text & filters.user(user_id), timeout=300
                )
            except asyncio.TimeoutError:
                await client.send_message(chat_id, "⏰ Timeout! Please send /wk again.")
                return
            _schedule_delete(rm_msg, 5)

            rm_text = (rm_msg.text or "").strip()
            pages_to_remove = set()

            if rm_text.lower() != "/skip":
                # Only digits joined by "&" are valid — anything else
                # (including letters like "AbCD") is silently treated as /Skip.
                if re.fullmatch(r"\d+(&\d+)*", rm_text):
                    ordered = []
                    for n_str in rm_text.split("&"):
                        n = int(n_str)
                        if 1 <= n <= total_pages_count and n not in ordered:
                            ordered.append(n)
                    if len(ordered) > REMOVE_PAGES_MAX:
                        ordered = ordered[:REMOVE_PAGES_MAX]
                    pages_to_remove = set(ordered)

            if pages_to_remove:
                rm_status = await client.send_message(chat_id, "🧹 Removing selected page(s), please wait...")
                trimmed_pdf_path = os.path.join(workdir, "trimmed.pdf")
                try:
                    total_pages_count = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, remove_pdf_pages, input_pdf_path, trimmed_pdf_path, pages_to_remove
                        ),
                        timeout=300,
                    )
                    working_pdf_path = trimmed_pdf_path
                    try:
                        await rm_status.edit(f"✅ Removed {len(pages_to_remove)} page(s). Continuing...")
                    except Exception:
                        pass
                except asyncio.TimeoutError:
                    try:
                        await rm_status.edit("⚠️ Page removal took too long, continuing with the original PDF instead.")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        await rm_status.edit(f"⚠️ Couldn't remove pages, continuing with original PDF.\n`{str(e)[:200]}`")
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await rm_status.delete()
                except Exception:
                    pass

            # ── Step 5: Last page image (skippable) ────────────────────────
            step5 = await client.send_message(
                chat_id,
                "Gajjab 🫣\nNow send me last page image (directly send me image)"
                "\n\n(Send /Skip to skip this — no extra last page will be added)",
            )
            _schedule_delete(step5, 13)
            try:
                img_msg: Message = await bot.listen(
                    chat_id, filters=(filters.photo | filters.text) & filters.user(user_id), timeout=300
                )
            except asyncio.TimeoutError:
                await client.send_message(chat_id, "⏰ Timeout! Please send /wk again.")
                return
            _schedule_delete(img_msg, 5)

            last_img_path = None
            if img_msg.photo:
                last_img_path = os.path.join(workdir, "last_page.jpg")
                await bot.download_media(img_msg, file_name=last_img_path)
            # any non-photo reply (including /Skip, or anything else) simply skips this step

            apply_last_wm = False
            if last_img_path:
                # ── Step 6: Yes / Skip on last page watermark (only asked if an image was given) ──
                step6 = await client.send_message(
                    chat_id,
                    "Fine 😁 \nDo you want to use your redirected txt & url on this page So send /Yes Or you can /Skip it anyways",
                )
                _schedule_delete(step6, 13)
                try:
                    choice_msg: Message = await bot.listen(
                        chat_id, filters=filters.text & filters.user(user_id), timeout=300
                    )
                except asyncio.TimeoutError:
                    await client.send_message(chat_id, "⏰ Timeout! Please send /wk again.")
                    return
                _schedule_delete(choice_msg, 5)
                apply_last_wm = (choice_msg.text or "").strip().lower() == "/yes"

            # ── Processing: apply watermarks with rotating progress text ──
            progress_msg = await client.send_message(chat_id, PROGRESS_STEPS[0])
            output_pdf_path = os.path.join(workdir, "output.pdf")

            wm_task = loop.run_in_executor(
                None,
                build_watermarked_pdf,
                working_pdf_path,
                output_pdf_path,
                top_text,
                link_text,
                link_url,
                last_img_path,
                apply_last_wm,
            )

            step_i = 1
            while not wm_task.done():
                await asyncio.sleep(4.5)
                if step_i < len(PROGRESS_STEPS) - 1:
                    try:
                        await progress_msg.edit(PROGRESS_STEPS[step_i])
                    except Exception:
                        pass
                    step_i += 1

            try:
                await wm_task
            except Exception as e:
                await progress_msg.edit(f"❌ Watermarking failed:\n`{str(e)[:300]}`")
                return

            try:
                await progress_msg.edit(PROGRESS_STEPS[-1])
            except Exception:
                pass
            await asyncio.sleep(1.5)
            try:
                await progress_msg.delete()
            except Exception:
                pass

            # ── Step 7: New file name (skippable — keeps original name) ────
            step7 = await client.send_message(
                chat_id,
                f"Original file name: `{original_name}`\n\n"
                f"All Fine ✅ \nNow Send me new Your PDF file name(without extension)."
                f"\n\n(Send /Skip to keep the original file name)",
            )
            _schedule_delete(step7, 13)
            try:
                name_msg: Message = await bot.listen(
                    chat_id, filters=filters.text & filters.user(user_id), timeout=300
                )
            except asyncio.TimeoutError:
                await client.send_message(chat_id, "⏰ Timeout! Please send /wk again.")
                return
            _schedule_delete(name_msg, 5)

            new_name_raw = (name_msg.text or "").strip()
            if new_name_raw.lower() == "/skip":
                base, _ext = os.path.splitext(original_name)
                new_name_raw = base or "watermarked_output"
            elif not new_name_raw:
                new_name_raw = "watermarked_output"
            words = new_name_raw.split()
            if len(words) > FILENAME_MAX_WORDS:
                new_name_raw = " ".join(words[:FILENAME_MAX_WORDS])
            safe_name = "".join(c for c in new_name_raw if c not in r'\/:*?"<>|')
            final_filename = f"{safe_name}.pdf"

            # ── Send final watermarked PDF (this message is NEVER deleted) ─
            up_status = await client.send_message(chat_id, "📤 Uploading your watermarked PDF...")
            try:
                await client.send_document(
                    chat_id,
                    document=output_pdf_path,
                    file_name=final_filename,
                    caption=f"✅ **{final_filename}**",
                    progress=ProgressTracker(up_status, "📤 Uploading your watermarked PDF...").__call__,
                )
            except Exception as e:
                await up_status.edit(f"❌ Upload failed:\n`{str(e)[:300]}`")
                return
            await up_status.delete()

            await client.send_message(
                chat_id,
                "Thank you For Using me🥰😘\nWanna Need to use me again \nSo just send /Wk to me again.",
            )

        finally:
            shutil.rmtree(workdir, ignore_errors=True)
