import asyncio
import os
import logging

# =========================================================
# THE "RENDER & PYTHON 3.11+" EVENT LOOP FIX
# This MUST stay at the very top, before importing Pyrogram
# =========================================================
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Now we can safely import everything else
import urllib.parse
import aiohttp
from aiohttp import web
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    WebAppInfo, 
    InputMediaPhoto, 
    BotCommand
)
from pyrogram.errors import UserNotParticipant, FloodWait
from motor.motor_asyncio import AsyncIOMotorClient

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Moviesaibbot")

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
PORT = int(os.environ.get("PORT", 10000))

MONGO_URL = os.environ.get("MONGO_URL", "").strip()
FSUB_CHANNEL_ID = os.environ.get("FSUB_CHANNEL_ID", "").strip()
FSUB_CHANNEL_LINK = os.environ.get("FSUB_CHANNEL_LINK", "").strip()
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "").strip()

# --- INITIALIZE ---
app = Client("Moviesaibbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
STREAM_BASE_URL = "https://streamimdb.ru/embed/movie/"
DEFAULT_POSTER = "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1000"
MOVIE_DATA = {}
users_db = None

# --- DATABASE ---
async def init_db():
    global users_db
    if MONGO_URL:
        try:
            mongo = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            users_db = mongo.Moviesaibbot.users
            logger.info("✅ MongoDB Connected")
        except Exception as e:
            logger.error(f"❌ DB Error: {e}")

# --- WEB SERVER (For Render Health Check) ---
async def keep_alive():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Running!"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"✅ Port {PORT} bound successfully")

# --- UTILS ---
async def is_subscribed(client, user_id):
    if not FSUB_CHANNEL_ID: return True
    try:
        member = await client.get_chat_member(int(FSUB_CHANNEL_ID), user_id)
        return member.status not in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]
    except UserNotParticipant: return False
    except Exception: return True

async def send_fsub_msg(message):
    btns = [[InlineKeyboardButton("📢 Join Channel", url=FSUB_CHANNEL_LINK)],
            [InlineKeyboardButton("🔄 Try Again", callback_data="check_fsub")]]
    await message.reply_photo(photo=DEFAULT_POSTER, caption="🚨 **Join our channel to use this bot!**", reply_markup=InlineKeyboardMarkup(btns))

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    if users_db is not None:
        try:
            ref_id = int(message.command[1]) if len(message.command) > 1 and message.command[1].isdigit() else None
            user = await users_db.find_one({"_id": user_id})
            if not user:
                await users_db.insert_one({"_id": user_id, "referrals": 0, "movies_watched": 0, "referred_by": ref_id})
                if ref_id and ref_id != user_id:
                    await users_db.update_one({"_id": ref_id}, {"$inc": {"referrals": 1}})
                    try: await client.send_message(ref_id, "🎊 **New user joined via your link!**")
                    except: pass
        except Exception as e: logger.error(f"Start DB Error: {e}")

    kb = [[KeyboardButton("🔍 Search Movie")], [KeyboardButton("📊 My Stats"), KeyboardButton("🎁 Referral Link")]]
    if SUPPORT_LINK: kb.append([KeyboardButton("🎧 Support")])
    
    await message.reply_photo(
        photo=DEFAULT_POSTER,
        caption="🎬 **Welcome to Moviesaibbot!**\n\nSearch any movie and watch instantly.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

@app.on_message(filters.text & filters.private)
async def handle_text(client, message):
    if message.text.startswith("/"): return
    user_id = message.from_user.id
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    if message.text == "🔍 Search Movie":
        return await message.reply_text("🍿 **Send me a movie name!**")
    elif message.text == "📊 My Stats" and users_db:
        u = await users_db.find_one({"_id": user_id})
        return await message.reply_text(f"📊 **Your Stats:**\n\n👥 Referrals: {u.get('referrals',0)}\n🎬 Watched: {u.get('movies_watched',0)}")
    elif message.text == "🎁 Referral Link":
        me = await client.get_me()
        return await message.reply_text(f"🎁 **Your Link:**\n`https://t.me/{me.username}?start={user_id}`")
    elif message.text == "🎧 Support":
        return await message.reply_text(f"📞 Contact: {SUPPORT_LINK}")

    status = await message.reply_text("🔍 Searching...")
    query = urllib.parse.quote(message.text.lower().strip().replace(" ", "_"))
    url = f"https://v3.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json() if resp.status == 200 else {}
            results = data.get("d", [])

    if not results: return await status.edit("❌ No Results Found.")

    keyboard = []
    top_poster = DEFAULT_POSTER
    for item in results[:8]:
        imdb_id = item.get("id")
        if not imdb_id or not imdb_id.startswith("tt"): continue
        title, year, poster = item.get("l", "Unknown"), item.get("y", "N/A"), item.get("i", {}).get("imageUrl", DEFAULT_POSTER)
        if top_poster == DEFAULT_POSTER: top_poster = poster
        MOVIE_DATA[imdb_id] = {"title": title, "poster": poster}
        keyboard.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"play_{imdb_id}")])

    await message.reply_photo(photo=top_poster, caption=f"✅ Results for: `{message.text}`", reply_markup=InlineKeyboardMarkup(keyboard))
    await status.delete()

@app.on_callback_query()
async def cb_handler(client, query):
    data, user_id = query.data, query.from_user.id
    if data == "check_fsub":
        if await is_subscribed(client, user_id):
            await query.message.delete()
            await client.send_message(user_id, "✅ Verified! You can now search.")
        else: await query.answer("❌ Join first!", show_alert=True)

    elif data.startswith("play_"):
        imdb_id = data.split("_")[1]
        movie = MOVIE_DATA.get(imdb_id, {"title": "Unknown", "poster": DEFAULT_POSTER})
        if users_db: await users_db.update_one({"_id": user_id}, {"$inc": {"movies_watched": 1}})
        
        await query.message.edit_media(
            media=InputMediaPhoto(media=movie["poster"], caption=f"🎥 **{movie['title']}**"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍿 Watch In-App", web_app=WebAppInfo(url=f"{STREAM_BASE_URL}{imdb_id}"))],
                [InlineKeyboardButton("🔙 Close", callback_data="close")]
            ])
        )
    elif data == "close": await query.message.delete()

# --- STARTUP ---
async def start_bot():
    await keep_alive()
    await init_db()
    
    # Drop backlog safely
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
        async with aiohttp.ClientSession() as s:
            await s.get(url)
    except:
        pass
    
    logger.info("🚀 Bot Starting...")
    await app.start()
    await app.set_bot_commands([BotCommand("start", "Main Menu")])
    logger.info("✅ Bot is Online!")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
