"""Telegram entry point.

Run with:
    python -m bot.telegram_bot

Flow per user message:
1. Look up per-chat conversation history and stored location (in-memory).
2. Show a 'typing' indicator + ack message so user gets feedback within ~1s.
3. Inject current location context into the user message if available.
4. Hand enriched message + history off to TravelAgent and await the final answer.
5. Strip [SCREENSHOT:...] markers, send photos, send text chunks.

Current location support:
- User shares live location via Telegram ("Lampirkan → Lokasi").
- Bot reverse-geocodes it via Nominatim and stores it per chat_id.
- /lokasi       — shows stored current location.
- /resetlokasi  — clears stored current location.

Memory is stored in plain dicts — no database required for MVP.
Users can reset conversation history with /reset.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent.agent_core import get_agent
from agent.response_formatter import format_for_telegram
from browser.playwright_manager import shutdown_manager
from config.settings import settings

_SCREENSHOT_RE = re.compile(r"\[SCREENSHOT:([^\]]+)\]")

# Keywords that signal the user is referring to their current position
_LOCATION_HINTS = re.compile(
    r"\b(lokasi(ku|saya|gue|gw)?|posisi(ku|saya)?|sini|dari sini|titik ini|"
    r"current location|my location|koordinat(ku)?)\b",
    re.IGNORECASE,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("travel-bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

MAX_HISTORY_TURNS = 6

_history: dict[int, List[BaseMessage]] = defaultdict(list)


# ---------------------------------------------------------------------------
# Location storage
# ---------------------------------------------------------------------------

@dataclass
class LocationData:
    lat: float
    lon: float
    address: str

    def short_address(self) -> str:
        """Return a compact display string."""
        return self.address

    def coords_str(self) -> str:
        return f"{self.lat:.6f},{self.lon:.6f}"


_locations: dict[int, LocationData] = {}

# ---------------------------------------------------------------------------
# Auth / access control
# ---------------------------------------------------------------------------

# Chat IDs that have successfully authenticated
_authorized: set[int] = set()

_AUTH_REQUIRED_MSG = (
    "Halo! Bot ini dilindungi dengan token akses.\n\n"
    "Gunakan perintah berikut untuk masuk:\n"
    "  /auth <token>"
)

def _is_authorized(chat_id: int) -> bool:
    """Return True if BOT_ACCESS_TOKEN is empty (open) or chat_id has authenticated."""
    if not settings.bot_access_token:
        return True
    return chat_id in _authorized


async def _reverse_geocode(lat: float, lon: float) -> str:
    """Reverse-geocode via Nominatim (no API key required)."""
    def _fetch() -> str:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lon}&format=json&accept-language=id"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TravelAgentBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        addr = data.get("address", {})
        parts: List[str] = []
        for key in ("road", "suburb", "city_district", "city", "county", "state"):
            val = addr.get(key, "")
            if val and val not in parts:
                parts.append(val)
            if len(parts) >= 3:
                break
        return ", ".join(parts) if parts else data.get("display_name", f"{lat:.5f}, {lon:.5f}")

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        logger.debug("Reverse geocode failed: %s", exc)
        return f"{lat:.5f}, {lon:.5f}"


# ---------------------------------------------------------------------------
# Conversation store
# ---------------------------------------------------------------------------

class ConversationStore:
    """Thin wrapper around the per-chat history dict."""

    @staticmethod
    def get(chat_id: int) -> List[BaseMessage]:
        return list(_history[chat_id])

    @staticmethod
    def append(chat_id: int, user_text: str, agent_answer: str) -> None:
        _history[chat_id].append(HumanMessage(content=user_text))
        _history[chat_id].append(AIMessage(content=agent_answer))
        max_messages = MAX_HISTORY_TURNS * 2
        if len(_history[chat_id]) > max_messages:
            _history[chat_id] = _history[chat_id][-max_messages:]

    @staticmethod
    def reset(chat_id: int) -> int:
        count = len(_history[chat_id])
        _history[chat_id] = []
        return count


# ---------------------------------------------------------------------------
# Welcome / help text
# ---------------------------------------------------------------------------

WELCOME = (
    "Halo! Aku AI Travel Agent kamu — BolangTravel.\n\n"
    "Ceritakan rencana perjalananmu, contoh:\n"
    "  - \"Mau ke Gacoan terdekat dari lokasiku\"\n"
    "  - \"Wisata kuliner Surabaya Sabtu ini\"\n"
    "  - \"Rute dari PENS ke Pakuwon Mall\"\n\n"
    "📍 Bagikan lokasi HP-mu: ketuk ikon lampiran → Lokasi.\n\n"
    "Perintah:\n"
    "  /auth <token>  - Masuk dengan token akses\n"
    "  /logout        - Keluar / cabut akses\n"
    "  /reset         - Mulai percakapan baru\n"
    "  /lokasi        - Lihat lokasi saat ini yang tersimpan\n"
    "  /resetlokasi   - Hapus lokasi saat ini\n"
    "  /help          - Tampilkan pesan ini"
)


# ---------------------------------------------------------------------------
# Auth command handlers
# ---------------------------------------------------------------------------

async def cmd_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/auth <token> — authenticate to access the bot."""
    chat_id = update.message.chat_id

    if not settings.bot_access_token:
        await update.message.reply_text("Bot ini tidak memerlukan autentikasi.")
        return

    if chat_id in _authorized:
        await update.message.reply_text("Kamu sudah terautentikasi. Langsung kirim pesanmu!")
        return

    token_input = " ".join(ctx.args).strip() if ctx.args else ""
    if token_input == settings.bot_access_token:
        _authorized.add(chat_id)
        logger.info("chat=%s authenticated successfully", chat_id)
        await update.message.reply_text(
            "Autentikasi berhasil! Selamat datang di BolangTravel.\n\n" + WELCOME
        )
    else:
        logger.warning("chat=%s failed auth attempt", chat_id)
        await update.message.reply_text(
            "Token salah. Coba lagi dengan /auth <token>"
        )


async def cmd_logout(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/logout — revoke access for this chat."""
    chat_id = update.message.chat_id
    if chat_id in _authorized:
        _authorized.discard(chat_id)
        ConversationStore.reset(chat_id)
        logger.info("chat=%s logged out", chat_id)
        await update.message.reply_text(
            "Kamu telah logout. Gunakan /auth <token> untuk masuk kembali."
        )
    else:
        await update.message.reply_text("Kamu belum login.")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    ConversationStore.reset(chat_id)
    await update.message.reply_text(WELCOME)


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    await update.message.reply_text(WELCOME)


async def cmd_reset(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    count = ConversationStore.reset(chat_id)
    if count:
        await update.message.reply_text(
            "Percakapan direset. Kirim pesan baru untuk mulai merencanakan!"
        )
    else:
        await update.message.reply_text(
            "Belum ada riwayat percakapan. Langsung kirim tujuan wisatamu!"
        )


async def cmd_lokasi(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    loc = _locations.get(chat_id)
    if loc:
        await update.message.reply_text(
            f"Lokasi tersimpan:\n{loc.short_address()}\n"
            f"({loc.coords_str()})\n\n"
            "Ketik /resetlokasi untuk menghapusnya."
        )
    else:
        await update.message.reply_text(
            "Belum ada lokasi tersimpan.\n\n"
            "Bagikan lokasimu: ketuk ikon lampiran (paperclip) lalu pilih Lokasi."
        )


async def cmd_reset_lokasi(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    if chat_id in _locations:
        del _locations[chat_id]
        await update.message.reply_text("Lokasi saat ini dihapus.")
    else:
        await update.message.reply_text("Tidak ada lokasi yang perlu dihapus.")


# ---------------------------------------------------------------------------
# Location message handler
# ---------------------------------------------------------------------------

async def handle_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when user shares a live or static location — saves as current position."""
    if not update.message:
        return
    if not _is_authorized(update.message.chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return
    loc_obj = update.message.location or (
        update.message.venue.location if update.message.venue else None
    )
    if loc_obj is None:
        return

    chat_id = update.message.chat_id
    lat, lon = loc_obj.latitude, loc_obj.longitude

    await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    address = await _reverse_geocode(lat, lon)

    _locations[chat_id] = LocationData(lat=lat, lon=lon, address=address)
    logger.info("Location stored for chat=%s: %s (%.5f, %.5f)", chat_id, address, lat, lon)
    await update.message.reply_text(
        f"Lokasi berhasil tercatat!\n{address}\n\n"
        "Sekarang ceritakan tujuanmu — aku akan gunakan lokasi ini sebagai titik awal."
    )


# ---------------------------------------------------------------------------
# Typing indicator + processing helpers
# ---------------------------------------------------------------------------

async def _keep_typing(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


def _extract_screenshots(text: str) -> Tuple[str, List[str]]:
    """Pull [SCREENSHOT:/path] markers from text; return (cleaned_text, [paths])."""
    paths = _SCREENSHOT_RE.findall(text)
    cleaned = _SCREENSHOT_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, paths


async def _send_screenshots(update: Update, paths: List[str]) -> None:
    """Send screenshot file paths as Telegram photos."""
    for path in paths:
        path = path.strip()
        if not os.path.isfile(path):
            logger.warning("Screenshot not found, skipping: %s", path)
            continue
        try:
            with open(path, "rb") as fh:
                await update.message.reply_photo(photo=fh)
        except Exception:
            logger.exception("Failed to send photo: %s", path)


def _build_agent_input(user_text: str, chat_id: int) -> str:
    """Prepend stored current-location context so the agent can use it."""
    loc = _locations.get(chat_id)
    if loc is None:
        return user_text
    context_line = (
        f"[Konteks sistem — lokasi user saat ini: {loc.short_address()} "
        f"| koordinat: {loc.coords_str()}]"
    )
    return f"{context_line}\n{user_text}"


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.message.chat_id

    # Auth gate — any plain message from an unauthenticated user gets blocked
    if not _is_authorized(chat_id):
        await update.message.reply_text(_AUTH_REQUIRED_MSG)
        return

    history = ConversationStore.get(chat_id)
    logger.info(
        "Message from chat=%s (history=%d msgs): %r",
        chat_id, len(history), user_text,
    )

    # Guard: "lokasiku" but no current location stored
    loc = _locations.get(chat_id)
    if _LOCATION_HINTS.search(user_text) and loc is None:
        await update.message.reply_text(
            "Sepertinya kamu ingin menggunakan lokasi saat ini sebagai titik awal.\n\n"
            "Bagikan lokasimu dulu: ketuk ikon lampiran (paperclip) di Telegram "
            "lalu pilih Lokasi. Setelah itu kirim ulang pesanmu ya!"
        )
        return

    ack_msg = await update.message.reply_text("Sedang memproses...")

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(chat_id, ctx, stop_typing))

    agent_input = _build_agent_input(user_text, chat_id)
    try:
        agent = get_agent()
        answer = await agent.run(agent_input, chat_history=history)
    except Exception:
        logger.exception("Unexpected error during agent run")
        answer = "Maaf, ada kendala teknis. Silakan coba lagi sebentar lagi."
    finally:
        stop_typing.set()
        await typing_task

    clean_answer, screenshot_paths = _extract_screenshots(answer)

    # Persist cleaned answer so screenshots paths aren't stored in history
    ConversationStore.append(chat_id, user_text, clean_answer)

    try:
        await ack_msg.delete()
    except Exception:
        pass

    if screenshot_paths:
        await _send_screenshots(update, screenshot_paths)

    chunks = format_for_telegram(clean_answer)
    if not chunks:
        await update.message.reply_text(
            "Maaf, tidak ada hasil yang bisa aku berikan untuk permintaan itu."
        )
        return
    for chunk in chunks:
        await update.message.reply_text(chunk, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Application wiring
# ---------------------------------------------------------------------------

def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. See .env.example.")
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("lokasi", cmd_lokasi))
    app.add_handler(CommandHandler("resetlokasi", cmd_reset_lokasi))
    # Location messages (live location + static pin)
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Plain text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


async def _shutdown(app: Application) -> None:
    logger.info("Stopping bot...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    await shutdown_manager()


def main() -> None:
    app = build_application()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler(*_a):
        logger.info("Signal received, shutting down")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop_event.set())

    async def runner() -> None:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot is running.")
        await stop_event.wait()
        await _shutdown(app)

    loop.run_until_complete(runner())


if __name__ == "__main__":
    main()
