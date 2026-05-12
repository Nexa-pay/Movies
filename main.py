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
# BROADCAST COMMAND (ADMIN ONLY)
# =========================================================
@app.on_message(filters.command("broadcast") & filters.private & filters.user(OWNER_ID))
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply_text("❌ Please reply to the message (text/photo/video) you want to broadcast with the command `/broadcast`.")
    
    if users_db is None:
        return await message.reply_text("❌ Database not connected. Cannot broadcast.")
    
    msg = await message.reply_text("📢 **Broadcasting message...**")
    success, failed = 0, 0
    
    async for user in users_db.find():
        try:
            await message.reply_to_message.copy(user["_id"])
            success += 1
            await asyncio.sleep(0.05) # Prevent Telegram from blocking the bot
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_to_message.copy(user["_id"])
            success += 1
        except Exception:
            failed += 1
            
    await msg.edit_text(f"✅ **Broadcast Complete!**\n\n🎯 Success: {success}\n❌ Failed: {failed}")

# =========================================================
# START COMMAND
# =========================================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
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
    
    await message.reply_photo(
        photo=DEFAULT_POSTER,
        caption="🎬 **Welcome to Moviesaibbot!**\n\nSearch any movie and watch instantly.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

# =========================================================
# MENU & TEXT SEARCH HANDLER
# =========================================================
@app.on_message(filters.text & filters.private)
async def handle_text(client, message):
    if message.text.startswith("/"): return
    user_id = message.from_user.id
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    # --- MENU BUTTON RESPONSES ---
    if message.text == "🔍 Search Movie":
        return await message.reply_text("🍿 **Send me a movie name!**")
        
    elif message.text == "📊 My Stats":
        if users_db is None:
            return await message.reply_text("❌ Database is currently offline. Please try again later.")
        try:
            u = await users_db.find_one({"_id": user_id})
            if u:
                return await message.reply_text(f"📊 **Your Stats:**\n\n👥 Referrals: {u.get('referrals',0)}\n🎬 Movies Watched: {u.get('movies_watched',0)}")
            return await message.reply_text("📊 **Your Stats:**\n\n👥 Referrals: 0\n🎬 Movies Watched: 0")
        except Exception:
            return await message.reply_text("❌ Error fetching stats.")
            
    elif message.text == "🎁 Referral Link":
        me = await client.get_me()
        return await message.reply_text(f"🎁 **Your Referral Link:**\n`https://t.me/{me.username}?start={user_id}`\n\nInvite your friends to use the bot!")
        
    elif message.text == "🎧 Support":
        return await message.reply_text(f"📞 Contact Support: {SUPPORT_LINK}")
        
    elif message.text == "📢 Updates Channel":
        return await message.reply_text(f"Join our official channel: {FSUB_CHANNEL_LINK}")

    elif message.text == "👑 Owner Panel" and user_id == OWNER_ID:
        total_users = 0
        if users_db is not None:
            try:
                total_users = await users_db.count_documents({})
            except: pass
        
        admin_text = (
            f"👑 **Admin Control Panel**\n\n"
            f"👥 **Total Database Users:** {total_users}\n\n"
            f"**How to Broadcast:**\n"
            f"To send a message to all users, simply reply to any message, photo, or video with the command: `/broadcast`"
        )
        return await message.reply_text(admin_text)

    # --- MOVIE SEARCH LOGIC ---
    status = await message.reply_text("🔍 Searching IMDb...")
    query = urllib.parse.quote(message.text.lower().strip().replace(" ", "_"))
    url = f"https://v3.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json() if resp.status == 200 else {}
            results = data.get("d", [])

    if not results: return await status.edit("❌ **No Results Found. Please check the spelling.**")

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

    # Check FSUB for all other buttons
    if not await is_subscribed(client, user_id):
        await query.answer("❌ Join the channel first!", show_alert=True)
        return await send_fsub_msg(query.message)

    if data.startswith("play_"):
        # Acknowledge button click instantly so it doesn't get stuck loading
        await query.answer("Fetching Movie...") 
        
        imdb_id = data.split("_")[1]
        movie = MOVIE_DATA.get(imdb_id, {"title": "Unknown", "poster": DEFAULT_POSTER})
        
        # Safely update DB without crashing the bot if it fails
        if users_db is not None: 
            try:
                await users_db.update_one({"_id": user_id}, {"$inc": {"movies_watched": 1}})
            except Exception as e:
                logger.warning(f"Failed to update movie count: {e}")
        
        watch_url = f"{STREAM_BASE_URL}{imdb_id}"
        
        try:
            await query.message.edit_media(
                media=InputMediaPhoto(media=movie["poster"], caption=f"🎥 **{movie['title']}**"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍿 Watch In-App", web_app=WebAppInfo(url=watch_url))],
                    [InlineKeyboardButton("🔙 Close Menu", callback_data="close")]
                ])
            )
        except Exception as e:
            logger.error(f"Edit Media Error: {e}")

    elif data == "close": 
        await query.message.delete()

# =========================================================
# STARTUP LOGIC
# =========================================================
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
