import os

# ── Telegram credentials ─────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", "38498066"))
API_HASH = os.environ.get("API_HASH", "c9696114751feacdeb1b4487f5839a1a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ── Owner / admin ─────────────────────────────────────────────────────────────
OWNER_ID = int(os.environ.get("OWNER_ID", "8909902924"))

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://devms786178_db_user:cEtMdLjmHF5EM2Pf@cluster0.xbqyvnn.mongodb.net/?appName=Cluster0")

# ── Force-Subscribe channel ───────────────────────────────────────────────────
# FORCE_SUB_CHAT can be "@username" (bot must be admin there) or a -100... chat id
FORCE_SUB_CHAT = os.environ.get("FORCE_SUB_CHAT", "@TeamCinderella")
FORCE_SUB_TITLE = os.environ.get("FORCE_SUB_TITLE", "Team Cinderella")
FORCE_SUB_URL = os.environ.get("FORCE_SUB_URL", "https://t.me/TeamCinderella")

# ── Web server (Render port binding) ─────────────────────────────────────────
PORT = int(os.environ.get("PORT", "8000"))

# ── Watermark tuning (safe to leave as-is) ────────────────────────────────────
TOP_TEXT_MAX_LEN = 50          # Type-1 watermark text max length
LINK_TEXT_MAX_LEN = 20         # Type-2/3 redirect text max length
FILENAME_MAX_WORDS = 200       # Max words allowed in final file name
REPEAT_EVERY_N_PAGES = 15      # Type-2 watermark repeats every Nth page
