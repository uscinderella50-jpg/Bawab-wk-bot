from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from vars import FORCE_SUB_CHAT, FORCE_SUB_TITLE, FORCE_SUB_URL


async def is_subscribed(bot: Client, user_id: int) -> bool:
    """Returns True if user has joined FORCE_SUB_CHAT (or if no channel is configured)."""
    if not FORCE_SUB_CHAT:
        return True
    try:
        member = await bot.get_chat_member(FORCE_SUB_CHAT, user_id)
        return member.status not in ("left", "kicked", "banned")
    except UserNotParticipant:
        return False
    except Exception as e:
        # Channel misconfigured / bot not admin there — fail-open so the bot
        # doesn't get stuck for every user because of an admin mistake.
        print(f"[ForceSub] check error: {e}")
        return True


def force_sub_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"📢 Join {FORCE_SUB_TITLE}", url=FORCE_SUB_URL)],
            [InlineKeyboardButton("✅ Verify", callback_data="verify_fsub")],
        ]
    )
