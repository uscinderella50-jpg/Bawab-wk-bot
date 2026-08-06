import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "modules"))

from pyromod import listen  # noqa: F401  (patches Client with .listen())
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from vars import API_ID, API_HASH, BOT_TOKEN
from db import save_user
from force_sub import is_subscribed, force_sub_markup
from wk_handler import register_wk_handlers

bot = Client(
    "nawaab_wk_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, m: Message):
    user_id = m.from_user.id
    await save_user(user_id)

    if not await is_subscribed(bot, user_id):
        await m.reply_text(
            "👋 **Welcome to Nawaab Watermark Bot!**\n\n"
            "Before you start, please join our channel below, then tap **Verify**.",
            reply_markup=force_sub_markup(),
        )
        return

    await m.reply_text(
        f"👋 Welcome, **{m.from_user.first_name}**😘!\n\n"
        "I'm **Nawaab Watermark Bot** 📄 — send me a PDF and I'll add smart watermarks "
        "to it (page watermark + repeating link watermark + a custom last page).\n\n"
        "Send /wk to get started!"
    )


@bot.on_callback_query(filters.regex("^verify_fsub$"))
async def verify_fsub_cb(client: Client, cq: CallbackQuery):
    if await is_subscribed(bot, cq.from_user.id):
        await cq.answer("✅ Verified! You're in.", show_alert=False)
        try:
            await cq.message.edit_text(
                f"✅ Great, **{cq.from_user.first_name}**! You're verified.\n\n"
                "Send /wk to start watermarking your PDF."
            )
        except Exception:
            pass
    else:
        await cq.answer("❌ You haven't joined the channel yet. Please join first.", show_alert=True)


register_wk_handlers(bot)


if __name__ == "__main__":
    print("Nawaab Wk Bot starting...")
    bot.run()
