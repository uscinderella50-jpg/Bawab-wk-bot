"""
Minimal MongoDB user tracking (motor / async).
Not used for saving watermark inputs — per spec, every /wk run is fresh
and nothing about the watermark job itself is persisted.
"""

import motor.motor_asyncio
from vars import MONGO_URL

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL) if MONGO_URL else None
_db = _client["nawaab_wk_bot"] if _client is not None else None
_users = _db["users"] if _db is not None else None


async def save_user(user_id: int):
    if _users is None:
        return
    try:
        await _users.update_one(
            {"_id": user_id},
            {"$setOnInsert": {"_id": user_id}},
            upsert=True,
        )
    except Exception as e:
        print(f"[DB] save_user error: {e}")


async def total_users() -> int:
    if _users is None:
        return 0
    try:
        return await _users.count_documents({})
    except Exception as e:
        print(f"[DB] total_users error: {e}")
        return 0
