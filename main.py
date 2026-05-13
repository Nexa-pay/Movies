import asyncio
import os
import logging
from datetime import datetime, timedelta

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
    InputMediaPhoto, 
    BotCommand,
    ForceReply
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
USER_STATE = {} # Tracks if user clicked search

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
# UTILS & PERMISSIONS
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

async def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if users_db is not None:
        user = await users_db.find_one({"_id": user_id})
        return user.get("is_admin", False) if user else False
    return False

async def check_banned(user_id):
    if users_db is not None:
        user = await users_db.find_one({"_id": user_id})
        return user.get("is_banned", False) if user else False
    return False

# =========================================================
# ADMIN COMMANDS
# =========================================================
@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    if users_db is None: return await message.reply_text("❌ Database not connected.")
    total_users = await users_db.count_documents({})
    banned_users = await users_db.count_documents({"is_banned": True})
    await message.reply_text(f"👥 **Bot Statistics**\n\nTotal Users: {total_users}\nBanned Users: {banned_users}")

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    if not message.reply_to_message:
        return await message.reply_text("❌ Please reply to a message with `/broadcast`.")
    if users_db is None: return await message.reply_text("❌ Database not connected.")
    
    msg = await message.reply_text("📢 **Broadcasting message...**")
    success, failed = 0, 0
    
    async for user in users_db.find({"is_banned": {"$ne": True}}):
        try:
            await message.reply_to_message.copy(user["_id"])
            success += 1
            await asyncio.sleep(0.05) 
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_to_message.copy(user["_id"])
            success += 1
        except Exception:
            failed += 1
            
    await msg.edit_text(f"✅ **Broadcast Complete!**\n\n🎯 Success: {success}\n❌ Failed: {failed}")

@app.on_message(filters.command("ban") & filters.private)
async def ban_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    args = message.text.split()
    if len(args) != 2: return await message.reply_text("Usage: `/ban UserID`")
    if users_db is not None:
        await users_db.update_one({"_id": int(args[1])}, {"$set": {"is_banned": True}})
        await message.reply_text(f"✅ User {args[1]} has been banned.")

@app.on_message(filters.command("unban") & filters.private)
async def unban_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    args = message.text.split()
    if len(args) != 2: return await message.reply_text("Usage: `/unban UserID`")
    if users_db is not None:
        await users_db.update_one({"_id": int(args[1])}, {"$set": {"is_banned": False}})
        await message.reply_text(f"✅ User {args[1]} has been unbanned.")

@app.on_message(filters.command("reply") & filters.private)
async def reply_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    args = message.text.split(" ", 2)
    if len(args) < 3: return await message.reply_text("Usage: `/reply UserID Message`")
    try:
        await client.send_message(int(args[1]), f"👨‍💻 **Message from Admin:**\n\n{args[2]}")
        await message.reply_text("✅ Message sent to user.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to send: {e}")

# --- OWNER ONLY ---
@app.on_message(filters.command("addadmin") & filters.private & filters.user(OWNER_ID))
async def addadmin_cmd(client, message):
    args = message.text.split()
    if len(args) != 2: return await message.reply_text("Usage: `/addadmin UserID`")
    if users_db is not None:
        await users_db.update_one({"_id": int(args[1])}, {"$set": {"is_admin": True}})
        await message.reply_text(f"✅ User {args[1]} is now an Admin.")

@app.on_message(filters.command("deladmin") & filters.private & filters.user(OWNER_ID))
async def deladmin_cmd(client, message):
    args = message.text.split()
    if len(args) != 2: return await message.reply_text("Usage: `/deladmin UserID`")
    if users_db is not None:
        await users_db.update_one({"_id": int(args[1])}, {"$set": {"is_admin": False}})
        await message.reply_text(f"✅ User {args[1]} is no longer an Admin.")

# =========================================================
# CONTACT ADMIN SYSTEM
# =========================================================
@app.on_message(filters.command("admin") & filters.private)
async def contact_admin(client, message):
    if await check_banned(message.from_user.id): return
    args = message.text.split(" ", 1)
    if len(args) < 2:
        return await message.reply_text("❌ Please include your message. Example:\n`/admin Please help me with...`")
    
    admin_msg = f"📩 **New Support Ticket**\n👤 **User:** {message.from_user.mention}\n🆔 **ID:** `{message.from_user.id}`\n\n💬 **Message:** {args[1]}\n\n*Reply with: `/reply {message.from_user.id} YourMessage`*"
    await client.send_message(OWNER_ID, admin_msg)
    await message.reply_text("✅ Your message has been sent to the admins. We will respond soon.")

# =========================================================
# START COMMAND & DB INITIALIZATION
# =========================================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    USER_STATE[user_id] = None # Reset state
    
    if await check_banned(user_id): return await message.reply_text("🚫 You are banned from using this bot.")
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    if users_db is not None:
        try:
            ref_id = int(message.command[1]) if len(message.command) > 1 and message.command[1].isdigit() else None
            user = await users_db.find_one({"_id": user_id})
            if not user:
                # Give new users 90 days free and 0 initial credits
                await users_db.insert_one({
                    "_id": user_id, 
                    "referrals": 0, 
                    "credits": 0,
                    "movies_watched": 0, 
                    "referred_by": ref_id,
                    "join_date": datetime.utcnow(),
                    "is_banned": False,
                    "is_admin": False
                })
                # Reward referrer with 10 credits
                if ref_id and ref_id != user_id:
                    await users_db.update_one({"_id": ref_id}, {"$inc": {"referrals": 1, "credits": 10}})
                    try: await client.send_message(ref_id, "🎊 **Someone joined using your link! You earned 10 Credits!**")
                    except: pass
        except Exception as e: logger.error(f"Start DB Error: {e}")

    kb = [[KeyboardButton("🔍 Search Movie")], [KeyboardButton("📊 My Stats"), KeyboardButton("🎁 Referral Link")]]
    bottom_row = []
    if SUPPORT_LINK.startswith("http"): bottom_row.append(KeyboardButton("🎧 Support"))
    if FSUB_CHANNEL_LINK.startswith("http"): bottom_row.append(KeyboardButton("📢 Updates Channel"))
    if bottom_row: kb.append(bottom_row)

    if await is_admin(user_id): kb.append([KeyboardButton("👑 Owner Panel")])
    
    welcome_text = (
        "🎬 **Welcome to Moviesaibbot!**\n\n"
        "Press the search button below to find and stream movies instantly.\n"
        "✨ *Clean • No ADs • No Buffering • HQ 4K Support*\n\n"
        "🎁 *Enjoy a 90-Day Unlimited Free Trial!*\n\n"
        "👑 **Bot Owner:** AAKASH👑"
    )
    
    await message.reply_photo(photo=DEFAULT_POSTER, caption=welcome_text, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

# =========================================================
# UNIVERSAL TEXT HANDLER
# =========================================================
@app.on_message(filters.private & filters.text & ~filters.command(["start", "broadcast", "stats", "ban", "unban", "admin", "reply", "addadmin", "deladmin"]))
async def handle_text(client, message):
    user_id = message.from_user.id
    if await check_banned(user_id): return
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    text = message.text.strip()

    # --- CONTACT ADMIN SHORTCUT ---
    if text.startswith("@admin "):
        msg_content = text.split("@admin ", 1)[1]
        admin_msg = f"📩 **New Support Ticket**\n👤 **User:** {message.from_user.mention}\n🆔 **ID:** `{user_id}`\n\n💬 **Message:** {msg_content}\n\n*Reply with: `/reply {user_id} YourMessage`*"
        await client.send_message(OWNER_ID, admin_msg)
        return await message.reply_text("✅ Your message has been sent to the admins. We will respond soon.")

    # --- MENU BUTTONS (USING 'in' TO FIX EMOJI MISMATCH BUG) ---
    if "Search Movie" in text:
        USER_STATE[user_id] = "SEARCHING"
        return await message.reply_text("🍿 **Please type the movie name and send it to me:**", reply_markup=ForceReply(selective=True))
        
    elif "My Stats" in text:
        USER_STATE[user_id] = None
        if users_db is None: return await message.reply_text("❌ Database is offline.")
        u = await users_db.find_one({"_id": user_id})
        if u:
            refs = u.get('referrals', 0)
            credits = u.get('credits', 0)
            watched = u.get('movies_watched', 0)
            join_date = u.get('join_date', datetime.utcnow())
            
            trial_end = join_date + timedelta(days=90)
            now = datetime.utcnow()
            
            if now < trial_end:
                days_left = (trial_end - now).days
                trial_text = f"🟢 Active ({days_left} days remaining)"
            else:
                trial_text = "🔴 Expired"

            stats_msg = (
                f"📊 **Your Account Stats:**\n\n"
                f"⏱ **Free Trial:** {trial_text}\n"
                f"💰 **Credits:** {credits}\n"
                f"👥 **Referrals:** {refs}\n"
                f"🎬 **Movies Watched:** {watched}\n\n"
                f"*(Note: Watching movies costs 1 credit only AFTER your 90-day trial expires. Invite friends to earn 10 credits each!)*"
            )
            return await message.reply_text(stats_msg)
            
    elif "Referral Link" in text:
        USER_STATE[user_id] = None
        me = await client.get_me()
        return await message.reply_text(f"🎁 **Your Referral Link:**\n`https://t.me/{me.username}?start={user_id}`\n\nInvite your friends and earn **10 Credits** for each join!")
        
    elif "Support" in text:
        USER_STATE[user_id] = None
        return await message.reply_text(f"📞 You can contact support via {SUPPORT_LINK} or by sending a message starting with `@admin ` or `/admin `.")
        
    elif "Updates Channel" in text:
        USER_STATE[user_id] = None
        return await message.reply_text(f"Join our official channel: {FSUB_CHANNEL_LINK}")

    elif "Owner Panel" in text and await is_admin(user_id):
        USER_STATE[user_id] = None
        admin_text = (
            "👑 **Owner & Admin Panel**\n"
            "Welcome back, **AAKASH👑**!\n\n"
            "📊 `/stats` - View bot stats\n"
            "📢 `/broadcast` - Reply to a message to send to all\n"
            "🚫 `/ban UserID` - Ban a user\n"
            "✅ `/unban UserID` - Unban a user\n"
            "🗣 `/reply UserID Message` - Reply to support tickets\n\n"
            "*(Owner Only)*\n"
            "➕ `/addadmin UserID`\n"
            "➖ `/deladmin UserID`"
        )
        return await message.reply_text(admin_text)

    # --- SEARCH HANDLING (Triggered by state OR ForceReply) ---
    elif USER_STATE.get(user_id) == "SEARCHING" or (message.reply_to_message and message.reply_to_message.text and "Please type the movie name" in message.reply_to_message.text):
        USER_STATE[user_id] = None # Reset state immediately
        movie_name = text
        status = await message.reply_text("🔍 Searching IMDb...")
        query = urllib.parse.quote(movie_name.lower().replace(" ", "_"))
        url = f"https://v3.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json() if resp.status == 200 else {}
                results = data.get("d", [])

        if not results: 
            return await status.edit("❌ **No Results Found.**\n\nPress **🔍 Search Movie** below to try again.")

        keyboard = []
        top_poster = DEFAULT_POSTER
        for item in results[:8]:
            imdb_id = item.get("id")
            if not imdb_id or not imdb_id.startswith("tt"): continue
            m_title, m_year, m_poster = item.get("l", "Unknown"), item.get("y", "N/A"), item.get("i", {}).get("imageUrl", DEFAULT_POSTER)
            if top_poster == DEFAULT_POSTER: top_poster = m_poster
            MOVIE_DATA[imdb_id] = {"title": m_title, "poster": m_poster}
            keyboard.append([InlineKeyboardButton(f"🎬 {m_title} ({m_year})", callback_data=f"play_{imdb_id}")])

        if not keyboard: return await status.edit("❌ **No valid streamable movies found.**")

        await message.reply_photo(photo=top_poster, caption=f"✅ Results for: `{movie_name}`", reply_markup=InlineKeyboardMarkup(keyboard))
        await status.delete()

    else:
        # Fallback for completely random text
        await message.reply_text("👇 Please click the **🔍 Search Movie** button below to search!")

# =========================================================
# CALLBACK HANDLERS (Movie Streaming & Credit Checks)
# =========================================================
@app.on_callback_query()
async def cb_handler(client, query):
    data, user_id = query.data, query.from_user.id
    if await check_banned(user_id): return await query.answer("🚫 Banned", show_alert=True)
    
    if data == "check_fsub":
        if await is_subscribed(client, user_id):
            await query.message.delete()
            return await client.send_message(user_id, "✅ Verified! You can now use the bot.")
        return await query.answer("❌ You haven't joined the channel yet!", show_alert=True)

    if not await is_subscribed(client, user_id): return await send_fsub_msg(query.message)

    if data.startswith("play_"):
        # --- CREDIT & TRIAL VERIFICATION ---
        if users_db is not None and not await is_admin(user_id):
            user = await users_db.find_one({"_id": user_id})
            if user:
                join_date = user.get("join_date", datetime.utcnow())
                credits = user.get("credits", 0)
                trial_end = join_date + timedelta(days=90)
                
                # If trial is expired, check and deduct credits
                if datetime.utcnow() > trial_end:
                    if credits < 1:
                        return await query.answer("❌ Your 90-Day Free Trial has expired and you have 0 Credits. Invite friends to earn more!", show_alert=True)
                    else:
                        await users_db.update_one({"_id": user_id}, {"$inc": {"credits": -1, "movies_watched": 1}})
                else:
                    await users_db.update_one({"_id": user_id}, {"$inc": {"movies_watched": 1}})
        
        await query.answer("Fetching Movie...") 
        imdb_id = data.split("_")[1]
        movie = MOVIE_DATA.get(imdb_id, {"title": "Unknown", "poster": DEFAULT_POSTER})
        watch_url = f"{STREAM_BASE_URL}{imdb_id}"
        
        try:
            caption = (
                f"🎥 **{movie['title']}**\n\n"
                f"✨ **Clean • No ADs • No Buffering • HQ 4K Support**\n\n"
                f"🍿 Click the button below to start streaming!"
            )
            await query.message.edit_media(
                media=InputMediaPhoto(media=movie["poster"], caption=caption),
                reply_markup=InlineKeyboardMarkup([
                    # Using a standard URL instead of web_app to ensure maximum compatibility!
                    [InlineKeyboardButton("🍿 Watch Movie", url=watch_url)],
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
        async with aiohttp.ClientSession() as s: await s.get(url)
    except: pass
    
    logger.info("🚀 Pyrogram Client Starting...")
    await app.start()
    try: await app.set_bot_commands([BotCommand("start", "Show Main Menu")])
    except: pass
        
    logger.info("✅ Bot is Online and Ready!")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
