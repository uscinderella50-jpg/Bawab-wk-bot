import asyncio
import os
import shutil
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message

from db import save_user
from force_sub import is_subscribed, force_sub_markup
from utils import ProgressTracker
from vars import TOP_TEXT_MAX_LEN, LINK_TEXT_MAX_LEN, FILENAME_MAX_WORDS
from watermark import build_watermarked_pdf

# FIX: the old code used a flat 600s ("blind") timeout that had zero idea how
# the job was actually progressing — on a big/scanned PDF it would either cut
# off a job that was genuinely still working (wasting the whole computation),
# or, if the job's build_watermarked_pdf() thread ever truly hung, it would
# sit there for the full 600s with the progress message frozen on step 1,
# which is exactly the "stuck" behaviour that was reported. We now get REAL
# per-page progress from watermark.py and use it two ways:
#   1. Show the user actual "page X/Y" progress so it never *looks* stuck
#      even while it's genuinely still working on a big file.
#   2. Only abort if progress has genuinely STALLED (no page advanced) for
#      STALL_TIMEOUT seconds, with an absolute HARD_CEILING as a last resort.
STALL_TIMEOUT = 150          # seconds with zero page progress before we call it "stuck"
HARD_CEILING = 2700          # 45 min — absolute max wait no matter what
PROGRESS_EDIT_INTERVAL = 4   # seconds between status-message edits

PROGRESS_STEPS = [
    "**Wait,Im attempting on this wait...😁.**",
    "**that's All Fine, I'm working on it 🔍.**",
    "**Alrights, all Good happings 🧐.**",
    "**Now im Finishing it...🌊.**",
    "**All Done ✅ Just Sent to you 📤.**",
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

            dl_status = await client.send_message(chat_id, "⬇️ Downloading your PDF...🤭\n\n**Downloading Speed:** 45 MB/s")
            try:
                await bot.download_media(
                    pdf_msg,
                    file_name=input_pdf_path,
                    progress=ProgressTracker(dl_status, "⬇️ Downloading your PDF...🤭\n\n**Downloading Speed:** 45 MB/s").__call__,
                )
            except Exception as e:
                await dl_status.edit(f"❌ Download failed:\n`{str(e)[:300]}`")
                return
            await dl_status.delete()

            # ── Step 2: Type-1 watermark text (every page) ──────────────
            top_text = await _ask_text(
                bot, chat_id, user_id,
                "Alrights 😊 \nNow Send me Main Watermark text(who carries PDF's Every Pages)",
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
                "Fantastic 😍 \nNow Send Your Clickable Text(who carries PDF's Every 15th Pages)"
                "\n\n(Send /Skip to skip this — no Clickable link watermark will be added)",
            )
            if link_text.strip().lower() == "/skip":
                link_text = None
            elif not link_text or len(link_text) > LINK_TEXT_MAX_LEN:
                await client.send_message(chat_id, f"❌ Text must be 1-{LINK_TEXT_MAX_LEN} characters. Send /wk again.")
                return

            # ── Step 4: Redirect URL (skippable) ───────────────────────────
            link_url = await _ask_text(
                bot, chat_id, user_id,
                "Alrights 😊 \nNow send me Redirect URL(must be starts with http or https) "
                "\n\n(Send /Skip to skip this — no clickable link will be added)",
            )
            if link_url.strip().lower() == "/skip":
                link_url = None
            elif not (link_url.startswith("http://") or link_url.startswith("https://")):
                await client.send_message(chat_id, "❌ Invalid link. It must start with http or https. Send /wk again.")
                return

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
                    "Fine 😁 \nDo you want to use your Clickable Link on this Your Last page too? So send /Yes Or you can /Skip it anyways",
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

            # ── Processing: apply watermarks with REAL page-by-page progress ──
            progress_msg = await client.send_message(chat_id, PROGRESS_STEPS[0])
            output_pdf_path = os.path.join(workdir, "output.pdf")

            # Simple mutable holder the worker thread writes into and the
            # asyncio loop below polls — plain int/dict writes are atomic
            # enough under the GIL for this "latest value wins" use case.
            progress_state = {"current": 0, "total": 0}

            def _on_progress(current: int, total: int, _state=progress_state):
                _state["current"] = current
                _state["total"] = total

            print(f"[WkHandler] Launching watermark job for user {user_id}, file={original_name!r}")

            wm_task = loop.run_in_executor(
                None,
                build_watermarked_pdf,
                input_pdf_path,
                output_pdf_path,
                top_text,
                link_text,
                link_url,
                last_img_path,
                apply_last_wm,
                _on_progress,
            )

            job_start = loop.time()
            last_progress_value = -1
            last_progress_change_at = job_start
            last_sent_text = None

            while not wm_task.done():
                now = loop.time()
                cur, total = progress_state["current"], progress_state["total"]

                if cur != last_progress_value:
                    last_progress_value = cur
                    last_progress_change_at = now

                stalled_for = now - last_progress_change_at
                elapsed = now - job_start

                if elapsed >= HARD_CEILING or stalled_for >= STALL_TIMEOUT:
                    print(
                        f"[WkHandler] Aborting job for user {user_id}: "
                        f"elapsed={elapsed:.0f}s stalled_for={stalled_for:.0f}s "
                        f"progress={cur}/{total}"
                    )
                    wm_task.cancel()
                    try:
                        await progress_msg.edit(
                            "❌ Watermarking got stuck / took too long and was stopped.\n"
                            "Please send /wk again — if this keeps happening with the same "
                            "file, try a smaller/simpler PDF."
                        )
                    except Exception:
                        pass
                    return

                if total:
                    pct = int((cur / total) * 100)
                    step_idx = min(int(pct / 25), len(PROGRESS_STEPS) - 2)
                    text = f"{PROGRESS_STEPS[step_idx]}\n\n📄 Page {cur}/{total} ({pct}%)"
                else:
                    text = PROGRESS_STEPS[0]

                if text != last_sent_text:
                    try:
                        await progress_msg.edit(text)
                        last_sent_text = text
                    except Exception:
                        pass

                await asyncio.sleep(PROGRESS_EDIT_INTERVAL)

            try:
                await wm_task
            except Exception as e:
                print(f"[WkHandler] Watermarking failed for user {user_id}: {e}")
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
                f"Original file name:\n\n `{original_name}`\n\n"
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
            up_status = await client.send_message(chat_id, "📤 Uploading your watermarked PDF...😘\n\n**Downloading Speed:** 50 MB/s")
            try:
                await client.send_document(
                    chat_id,
                    document=output_pdf_path,
                    file_name=final_filename,
                    caption=f"✅ **{final_filename}**",
                    progress=ProgressTracker(up_status, "📤 Uploading your watermarked PDF...😘\n\n**Downloading Speed:** 50 MB/s").__call__,
                )
            except Exception as e:
                await up_status.edit(f"❌ Upload failed:\n`{str(e)[:300]}`")
                return
            await up_status.delete()

            await client.send_message(
                chat_id,
                "**Thank you For Using me🥰😘**\n\nWanna Need to use me again?\nSo just send /Wk to me again.\n\n**im Made By:** @SmartBoy_ApnaMS ❤️.",
            )

        finally:
            shutil.rmtree(workdir, ignore_errors=True)
