import asyncio
import os
import logging
import urllib.parse
from datetime import datetime, timedelta
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
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

# =========================================================
# THE "RENDER & PYTHON 3.11+" EVENT LOOP FIX
# =========================================================
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# =========================================================
# LOGGING & CONFIG
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Moviesaibbot")

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
PORT = int(os.environ.get("PORT", 10000))
MONGO_URL = os.environ.get("MONGO_URL", "").strip()
FSUB_CHANNEL_ID = os.environ.get("FSUB_CHANNEL_ID", "").strip()
FSUB_CHANNEL_LINK = os.environ.get("FSUB_CHANNEL_LINK", "").strip()
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip().rstrip("/")

# =========================================================
# INITIALIZE
# =========================================================
app = Client("Moviesaibbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
STREAM_BASE_URL = "https://streamimdb.ru/embed/movie/"
DEFAULT_POSTER = "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1000"
MOVIE_DATA = {}
users_db = None
USER_STATE = {}

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
# SECURE PROXY PLAYER (iOS/iPad Fix + Monetag + Rotation)
# =========================================================
async def watch_movie(request):
    imdb_id = request.match_info.get("imdb_id")
    
    # --- Place your Monetag Ad Tag script below in ad_script once verified ---
    ad_script = "" 
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta name="monetag" content="aef11068ac212ba3c9a82e845215d8a9">
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>AAKASH👑 Player</title>
        {ad_script}
        <style>
            body, html {{ margin: 0; padding: 0; height: 100%; background-color: #000; overflow: hidden; font-family: sans-serif; }}
            .video-container {{ position: relative; width: 100%; height: 100%; }}
            iframe {{ width: 100%; height: 100%; border: none; }}
            #rotate-btn {{
                position: fixed; top: 15px; right: 15px; z-index: 999;
                background: rgba(255,255,255,0.2); color: white; padding: 10px;
                border-radius: 5px; cursor: pointer; border: 1px solid rgba(255,255,255,0.3);
                font-size: 12px; backdrop-filter: blur(5px);
            }}
            .rotated {{ 
                transform: rotate(90deg); transform-origin: bottom left;
                position: absolute; top: -100vw; left: 0;
                height: 100vw; width: 100vh;
            }}
        </style>
    </head>
    <body>
        <div id="rotate-btn" onclick="toggleRotation()">🔄 Rotate</div>
        <div class="video-container" id="player-box">
            <iframe 
                id="video-iframe"
                src="{STREAM_BASE_URL}{imdb_id}" 
                sandbox="allow-forms allow-scripts allow-pointer-lock allow-same-origin allow-top-navigation"
                allow="autoplay; fullscreen; encrypted-media; picture-in-picture" 
                allowfullscreen 
                playsinline>
            </iframe>
        </div>
        <script>
            function toggleRotation() {{
                var element = document.getElementById("player-box");
                element.classList.toggle("rotated");
                var btn = document.getElementById("rotate-btn");
                btn.innerHTML = element.classList.contains("rotated") ? "🔙 Normal" : "🔄 Rotate";
            }}
            document.addEventListener('touchstart', function() {{}}, false);
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

async def keep_alive():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Running!"))
    # FIX: Correctly formatted the route string to single brackets
    server.router.add_get("/watch/{imdb_id}", watch_movie)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"✅ Secure Player running on port {PORT}")

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
    total = await users_db.count_documents({})
    banned = await users_db.count_documents({"is_banned": True})
    await message.reply_text(f"👥 **Stats:** Total {total} | Banned {banned}")

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    if not message.reply_to_message: return await message.reply_text("❌ Reply to a message!")
    msg = await message.reply_text("📢 Broadcasting...")
    s, f = 0, 0
    async for user in users_db.find({"is_banned": {"$ne": True}}):
        try:
            await message.reply_to_message.copy(user["_id"])
            s += 1
            await asyncio.sleep(0.05)
        except Exception: f += 1
    await msg.edit_text(f"✅ Done! Success: {s} | Fail: {f}")

@app.on_message(filters.command(["ban", "unban"]) & filters.private)
async def ban_unban_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    args = message.text.split()
    if len(args) != 2: return await message.reply_text("Usage: `/ban UserID` or `/unban UserID`")
    action = True if "ban" in args[0] and "unban" not in args[0] else False
    await users_db.update_one({"_id": int(args[1])}, {"$set": {"is_banned": action}})
    await message.reply_text(f"✅ User {args[1]} {'banned' if action else 'unbanned'}.")

@app.on_message(filters.command("reply") & filters.private)
async def reply_cmd(client, message):
    if not await is_admin(message.from_user.id): return
    args = message.text.split(" ", 2)
    if len(args) < 3: return
    try:
        await client.send_message(int(args[1]), f"👨‍💻 **Message from Admin:**\n\n{args[2]}")
        await message.reply_text("✅ Sent.")
    except Exception: await message.reply_text("❌ Failed.")

@app.on_message(filters.command("admin") & filters.private)
async def contact_admin_cmd(client, message):
    args = message.text.split(" ", 1)
    if len(args) < 2: return await message.reply_text("❌ Usage: `/admin your_message`")
    adm_text = f"📩 **New Ticket**\n👤 {message.from_user.mention}\n🆔 `{message.from_user.id}`\n\n💬: {args[1]}"
    await client.send_message(OWNER_ID, adm_text)
    await message.reply_text("✅ Sent to Admins.")

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
# START & UNIVERSAL HANDLER
# =========================================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    USER_STATE[user_id] = None
    if await check_banned(user_id): return
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)

    if users_db is not None:
        user = await users_db.find_one({"_id": user_id})
        if not user:
            ref_id = int(message.command[1]) if len(message.command) > 1 and message.command[1].isdigit() else None
            await users_db.insert_one({
                "_id": user_id, "referrals": 0, "credits": 0, "movies_watched": 0, "referred_by": ref_id,
                "join_date": datetime.utcnow(), "is_banned": False, "is_admin": False
            })
            if ref_id and ref_id != user_id:
                await users_db.update_one({"_id": ref_id}, {"$inc": {"referrals": 1, "credits": 10}})
                try: await client.send_message(ref_id, "🎊 **Someone joined! You earned 10 Credits!**")
                except: pass

    kb = [[KeyboardButton("🔍 Search Movie")], [KeyboardButton("📊 My Stats"), KeyboardButton("🎁 Referral Link")], [KeyboardButton("🎧 Support"), KeyboardButton("📢 Updates Channel")]]
    if await is_admin(user_id): kb.append([KeyboardButton("👑 Owner Panel")])
    
    cap = (
        "🎬 **Welcome to Moviesaibbot!**\n\n"
        "✨ *Clean • No ADs • No Buffering • HQ 4K Support*\n"
        "🎁 *Enjoy a 90-Day Unlimited Free Trial!*\n\n"
        "👑 **Bot Owner:** AAKASH👑"
    )
    await message.reply_photo(photo=DEFAULT_POSTER, caption=cap, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

@app.on_message(filters.private & filters.text & ~filters.command(["start", "broadcast", "stats", "ban", "unban", "admin", "reply", "addadmin", "deladmin"]))
async def handle_text(client, message):
    user_id = message.from_user.id
    if await check_banned(user_id): return
    if not await is_subscribed(client, user_id): return await send_fsub_msg(message)
    text = message.text.strip()

    if text.startswith("@admin "):
        msg = text.split("@admin ", 1)[1]
        await client.send_message(OWNER_ID, f"📩 **Ticket from {user_id}**: {msg}")
        return await message.reply_text("✅ Sent.")

    if "Search Movie" in text:
        USER_STATE[user_id] = "SEARCHING"
        return await message.reply_text("🍿 **Type movie name:**", reply_markup=ForceReply(selective=True))
    
    elif "My Stats" in text:
        u = await users_db.find_one({"_id": user_id})
        trial_end = u['join_date'] + timedelta(days=90)
        trial_text = "🟢 Active" if datetime.utcnow() < trial_end else "🔴 Expired"
        await message.reply_text(f"📊 **Stats:**\nTrial: {trial_text}\nCredits: {u['credits']}\nReferrals: {u['referrals']}\nWatched: {u['movies_watched']}")

    elif "Referral Link" in text:
        me = await client.get_me()
        await message.reply_text(f"🎁 **Link:** `https://t.me/{me.username}?start={user_id}`\nEarn 10 Credits per join!")

    elif "Owner Panel" in text and await is_admin(user_id):
        await message.reply_text("👑 **Admin:** `/stats` | `/broadcast` | `/ban ID` | `/unban ID` | `/reply ID msg`\nOwner: AAKASH👑")

    elif USER_STATE.get(user_id) == "SEARCHING" or (message.reply_to_message and "Type movie name" in message.reply_to_message.text):
        USER_STATE[user_id] = None
        status = await message.reply_text("🔍 Searching...")
        query = urllib.parse.quote(text.lower().replace(" ", "_"))
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://v3.sg.media-imdb.com/suggestion/{query[0]}/{query}.json") as resp:
                results = (await resp.json()).get("d", []) if resp.status == 200 else []
        if not results: return await status.edit("❌ No Results.")
        
        keyboard = []
        top_poster = DEFAULT_POSTER
        for item in results[:8]:
            imdb_id = item.get("id")
            if not imdb_id or not imdb_id.startswith("tt"): continue
            if top_poster == DEFAULT_POSTER: top_poster = item.get("i", {}).get("imageUrl", DEFAULT_POSTER)
            MOVIE_DATA[imdb_id] = {"title": item["l"], "poster": item.get("i", {}).get("imageUrl", DEFAULT_POSTER)}
            keyboard.append([InlineKeyboardButton(f"🎬 {item['l']} ({item.get('y','N/A')})", callback_data=f"play_{imdb_id}")])
        
        await message.reply_photo(photo=top_poster, caption=f"✅ Results for: {text}", reply_markup=InlineKeyboardMarkup(keyboard))
        await status.delete()

    else:
        await message.reply_text("👇 Click **🔍 Search Movie** below!")

# =========================================================
# CALLBACK HANDLER
# =========================================================
@app.on_callback_query()
async def cb_handler(client, query):
    data, user_id = query.data, query.from_user.id
    if await check_banned(user_id): return await query.answer("🚫 Banned", show_alert=True)
    if data == "check_fsub":
        if await is_subscribed(client, user_id):
            await query.message.delete()
            return await client.send_message(user_id, "✅ Verified!")
        return await query.answer("❌ Join first!", show_alert=True)

    if data.startswith("play_"):
        u = await users_db.find_one({"_id": user_id})
        if not await is_admin(user_id) and datetime.utcnow() > (u['join_date'] + timedelta(days=90)) and u['credits'] < 1:
            return await query.answer("❌ 0 Credits! Refer friends.", show_alert=True)
        
        imdb_id = data.split("_")[1]
        movie = MOVIE_DATA.get(imdb_id, {"title": "Movie", "poster": DEFAULT_POSTER})
        await users_db.update_one({"_id": user_id}, {"$inc": {"movies_watched": 1, "credits": -1 if datetime.utcnow() > (u['join_date'] + timedelta(days=90)) else 0}})
        
        watch_url = f"{BASE_URL}/watch/{imdb_id}" if BASE_URL else f"{STREAM_BASE_URL}{imdb_id}"
        cap = f"🎥 **{movie['title']}**\n✨ Clean • No ADs • 4K\n🍿 Click below to watch!"
        await query.message.edit_media(media=InputMediaPhoto(movie['poster'], cap), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🍿 Watch Movie", url=watch_url)], [InlineKeyboardButton("🔙 Close", callback_data="close")]]))

    elif data == "close": await query.message.delete()

# =========================================================
# STARTUP
# =========================================================
async def start_bot():
    await keep_alive()
    await init_db()
    async with aiohttp.ClientSession() as s: await s.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    await app.start()
    await app.set_bot_commands([BotCommand("start", "Main Menu")])
    logger.info("✅ Bot is Online!")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
