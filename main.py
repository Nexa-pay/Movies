import asyncio
import os
import logging

# =========================================================
# THE "RENDER & PYTHON 3.11+" EVENT LOOP FIX
# =========================================================
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Moviesaibbot")

# =========================================================
# CONFIG
# =========================================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
PORT = int(os.environ.get("PORT", 10000))

MONGO_URL = os.environ.get("MONGO_URL", "").strip()
FSUB_CHANNEL_ID = os.environ.get("FSUB_CHANNEL_ID", "").strip()
FSUB_CHANNEL_LINK = os.environ.get("FSUB_CHANNEL_LINK", "").strip()
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "").strip()

# =========================================================
# INITIALIZE
# =========================================================
app = Client("Moviesaibbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
STREAM_BASE_URL = "https://streamimdb.ru/embed/movie/"
DEFAULT_POSTER = "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1000"
MOVIE_DATA = {}
users_db = None

# Track what the user is currently doing (Search or Broadcast)
USER_STATE = {} 

# =========================================================
# DATABASE
# =========================================================
async def init_db():
    global users_db
    if MONGO_URL:
        try:
            mongo = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            users_db = mongo.Moviesaibbot.users
            logger.info("✅ MongoDB Connected")
        except Exception as e:
            logger.error(f"❌ DB Error: {e}")

# =========================================================
# WEB SERVER (For Render Health Check)
# =========================================================
async def keep_alive():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Running!"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"✅ Port {PORT} bound successfully")

# =========================================================
# UTILS
# =========================================================
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

# =========================================================
# START COMMAND
# =========================================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    USER_STATE[user_id] = None # Reset any stuck states
    
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    # Database Saving
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

    # Build Bottom Menu
    kb = [[KeyboardButton("🔍 Search Movie")], [KeyboardButton("📊 My Stats"), KeyboardButton("🎁 Referral Link")]]
    
    bottom_row = []
    if SUPPORT_LINK.startswith("http"):
        bottom_row.append(KeyboardButton("🎧 Support"))
    if FSUB_CHANNEL_LINK.startswith("http"):
        bottom_row.append(KeyboardButton("📢 Updates Channel"))
    if bottom_row:
        kb.append(bottom_row)

    # ADD OWNER PANEL IF USER IS ADMIN
    if user_id == OWNER_ID:
        kb.append([KeyboardButton("👑 Owner Panel")])
    
    welcome_text = (
        "🎬 **Welcome to Moviesaibbot!**\n\n"
        "Search any movie and watch instantly.\n\n"
        "👑 **Bot Owner:** AAKASH👑"
    )
    
    await message.reply_photo(
        photo=DEFAULT_POSTER,
        caption=welcome_text,
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

# =========================================================
# UNIVERSAL MESSAGE HANDLER (Menu, Search, Broadcast)
# =========================================================
@app.on_message(filters.private & ~filters.command(["start"]))
async def handle_all_messages(client, message):
    user_id = message.from_user.id
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    text = message.text if message.text else ""

    # -----------------------------------------
    # 1. MENU BUTTONS (These override everything)
    # -----------------------------------------
    if text == "🔍 Search Movie":
        USER_STATE[user_id] = "SEARCH"
        return await message.reply_text("🍿 **Send me the name of the movie you want to watch!**")
        
    elif text == "📊 My Stats":
        if users_db is None:
            return await message.reply_text("❌ Database is currently offline.")
        try:
            u = await users_db.find_one({"_id": user_id})
            refs = u.get('referrals', 0) if u else 0
            watched = u.get('movies_watched', 0) if u else 0
            return await message.reply_text(f"📊 **Your Stats:**\n\n👥 Referrals: {refs}\n🎬 Movies Watched: {watched}")
        except Exception:
            return await message.reply_text("❌ Error fetching stats.")
            
    elif text == "🎁 Referral Link":
        me = await client.get_me()
        return await message.reply_text(f"🎁 **Your Referral Link:**\n`https://t.me/{me.username}?start={user_id}`\n\nInvite your friends to use the bot!")
        
    elif text == "🎧 Support":
        return await message.reply_text(f"📞 Contact Support: {SUPPORT_LINK}")
        
    elif text == "📢 Updates Channel":
        return await message.reply_text(f"Join our official channel: {FSUB_CHANNEL_LINK}")

    elif text == "👑 Owner Panel" and user_id == OWNER_ID:
        USER_STATE[user_id] = None # Cancel any pending actions
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Bot Stats", callback_data="owner_stats"),
             InlineKeyboardButton("📢 Broadcast", callback_data="owner_broadcast")],
            [InlineKeyboardButton("❌ Close", callback_data="close")]
        ])
        return await message.reply_text("👑 **Owner Control Panel**", reply_markup=kb)

    # -----------------------------------------
    # 2. STATE HANDLING (Searching & Broadcasting)
    # -----------------------------------------
    state = USER_STATE.get(user_id)

    if state == "SEARCH":
        if not text:
            return await message.reply_text("❌ Please send a valid movie name in text format.")
            
        USER_STATE[user_id] = None # LOCK SEARCH UNTIL BUTTON IS PRESSED AGAIN
        
        status = await message.reply_text("🔍 Searching IMDb...")
        query = urllib.parse.quote(text.lower().strip().replace(" ", "_"))
        url = f"https://v3.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json() if resp.status == 200 else {}
                results = data.get("d", [])

        if not results: return await status.edit("❌ **No Results Found. Please click '🔍 Search Movie' to try again.**")

        keyboard = []
        top_poster = DEFAULT_POSTER
        for item in results[:8]:
            imdb_id = item.get("id")
            if not imdb_id or not imdb_id.startswith("tt"): continue
            m_title, m_year, m_poster = item.get("l", "Unknown"), item.get("y", "N/A"), item.get("i", {}).get("imageUrl", DEFAULT_POSTER)
            
            if top_poster == DEFAULT_POSTER: top_poster = m_poster
            MOVIE_DATA[imdb_id] = {"title": m_title, "poster": m_poster}
            keyboard.append([InlineKeyboardButton(f"🎬 {m_title} ({m_year})", callback_data=f"play_{imdb_id}")])

        await message.reply_photo(photo=top_poster, caption=f"✅ Results for: `{text}`", reply_markup=InlineKeyboardMarkup(keyboard))
        await status.delete()

    elif state == "BROADCAST" and user_id == OWNER_ID:
        USER_STATE[user_id] = None # Lock broadcast
        
        if users_db is None:
            return await message.reply_text("❌ Database not connected. Cannot broadcast.")
        
        msg = await message.reply_text("📢 **Broadcasting message...**")
        success, failed = 0, 0
        
        async for user in users_db.find():
            try:
                await message.copy(user["_id"])
                success += 1
                await asyncio.sleep(0.05) # Prevent Telegram flood limits
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await message.copy(user["_id"])
                success += 1
            except Exception:
                failed += 1
                
        await msg.edit_text(f"✅ **Broadcast Complete!**\n\n🎯 Success: {success}\n❌ Failed: {failed}")

    else:
        # User typed something without clicking the required button first
        if text:
            await message.reply_text("👇 Please click the **🔍 Search Movie** button below to start a search!")

# =========================================================
# CALLBACK HANDLERS (Inline Buttons)
# =========================================================
@app.on_callback_query()
async def cb_handler(client, query):
    data, user_id = query.data, query.from_user.id
    
    if data == "check_fsub":
        if await is_subscribed(client, user_id):
            await query.message.delete()
            await client.send_message(user_id, "✅ Verified! You can now use the bot.")
        else: 
            await query.answer("❌ You haven't joined the channel yet!", show_alert=True)
        return

    if not await is_subscribed(client, user_id):
        await query.answer("❌ Join the channel first!", show_alert=True)
        return await send_fsub_msg(query.message)

    # --- OWNER PANEL BUTTONS ---
    if data == "owner_stats" and user_id == OWNER_ID:
        total_users = await users_db.count_documents({}) if users_db else 0
        await query.answer(f"👥 Total Database Users: {total_users}", show_alert=True)

    elif data == "owner_broadcast" and user_id == OWNER_ID:
        USER_STATE[user_id] = "BROADCAST"
        await query.answer()
        await query.message.reply_text("📥 **Send the message (Text/Photo/Video) you want to broadcast now:**")

    # --- MOVIE WATCH BUTTONS ---
    elif data.startswith("play_"):
        await query.answer("Fetching Movie...") 
        
        imdb_id = data.split("_")[1]
        movie = MOVIE_DATA.get(imdb_id, {"title": "Unknown", "poster": DEFAULT_POSTER})
        
        if users_db is not None: 
            try:
                await users_db.update_one({"_id": user_id}, {"$inc": {"movies_watched": 1}})
            except Exception: pass
        
        watch_url = f"{STREAM_BASE_URL}{imdb_id}"
        
        try:
            await query.message.edit_media(
                media=InputMediaPhoto(media=movie["poster"], caption=f"🎥 **{movie['title']}**"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍿 Watch In-App", web_app=WebAppInfo(url=watch_url))],
                    [InlineKeyboardButton("🔙 Close Menu", callback_data="close")]
                ])
            )
        except Exception: pass

    elif data == "close": 
        await query.message.delete()

# =========================================================
# STARTUP LOGIC
# =========================================================
async def start_bot():
    await keep_alive()
    await init_db()
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
        async with aiohttp.ClientSession() as s:
            await s.get(url)
    except:
        pass
    
    logger.info("🚀 Pyrogram Client Starting...")
    await app.start()
    
    try:
        await app.set_bot_commands([BotCommand("start", "Show Main Menu")])
    except:
        pass
        
    logger.info("✅ Bot is Online and Ready!")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
