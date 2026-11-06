import asyncio, io, logging, os, sqlite3, time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web, ClientSession
from telethon import TelegramClient, events, Button, errors
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest, UpdateStatusRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("userbot")

# ====== Sozlamalar (Render Environment bo'lsa, o'sha ustun turadi) ======
API_ID = int(os.getenv("API_ID", "39137299"))
API_HASH = os.getenv("API_HASH", "e67d098704ca5ce1f4a33a2b33e97f89")
BOT_TOKEN = os.getenv("BOT_TOKEN", "9268503477:AAEgumOj9dlPTlYW3-azhNEe3MGTa1vg2aE")
OWNER_ID = int(os.getenv("OWNER_ID", "8426582765"))
SESSION_STRING = os.getenv("SESSION_STRING", "1ApWapzMBu0I2SiR7AljkqQcJSvbsRmgv5FkagyN-MEy_fvFlKYwBn6mF1gSjvpnf1pvny99QQnaJ_ao938iKAngagnIxYQBoXLimSiApDSAW6updQedIOV_u5J9etunFhEQP20bUctZ18m6ybKC1u_bHBZVhoIs6NRgTU5N3aFemFnM-QJr1AYONvAYerErMsAMEnJ64uIkf3Vk9qMpsgvc3L-jZMEuDv71SsJEpTTTdUrW4WyuJJu_dXXIdGMXZNtL-j-aCYTfb3tIQzUD0RRIn7Lt3sorFypcsYv5U-AEAvCPx-OT3tYLtL8uOeY-bSryDbr9wM-INnRF0DtjBvif9YtiszCU=")
DATA_DIR = os.getenv("DATA_DIR", ".")
PORT = int(os.getenv("PORT", "10000"))
SELF_URL = os.getenv("RENDER_EXTERNAL_URL", "")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
DEVICE = "Samsung Galaxy S26"
# ================================================

os.makedirs(DATA_DIR, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA_DIR, "data.db"), check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS msgs(id INTEGER, chat INTEGER, sender INTEGER,
  name TEXT, out INTEGER, text TEXT, date TEXT, deleted INTEGER DEFAULT 0,
  PRIMARY KEY(id, chat));
CREATE TABLE IF NOT EXISTS greeted(uid INTEGER PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS skip(uid INTEGER PRIMARY KEY, name TEXT);
""")
DEFAULTS = {"greet": "1", "bio": "1", "edits": "1", "deletes": "1",
            "archive": "1", "hide_online": "1", "biotext": "da ham meni hayolimdasan", "session": ""}
LABELS = {"greet": "Salomlashish", "bio": "Bio soat", "edits": "Tahrir kuzatuv",
          "deletes": "O'chirish kuzatuv", "archive": "Arxiv (txt)",
          "hide_online": "Online yashirish"}


def get(k):
    r = db.execute("SELECT v FROM cfg WHERE k=?", (k,)).fetchone()
    return r[0] if r else DEFAULTS[k]


def put(k, v):
    db.execute("INSERT OR REPLACE INTO cfg VALUES(?,?)", (k, v))
    db.commit()


def on(k):
    return get(k) == "1"


user = TelegramClient(StringSession(SESSION_STRING or get("session")), API_ID, API_HASH,
                      device_model=DEVICE, system_version="Android 16",
                      app_version="12.0.0", connection_retries=None,
                      retry_delay=5, auto_reconnect=True)
bot = TelegramClient(StringSession(), API_ID, API_HASH,
                     connection_retries=None, retry_delay=5, auto_reconnect=True)


def safe(fn):
    async def w(e, *a):
        try:
            await fn(e, *a)
        except Exception:
            log.exception("handler xato: %s", fn.__name__)
    return w


def panel():
    return [[Button.inline(f"{'✅' if on(k) else '❌'} {n}", f"t:{k}".encode())]
            for k, n in LABELS.items()]


# ---------- Arxivlash ----------
@user.on(events.NewMessage(func=lambda e: e.is_private))
@safe
async def save(e):
    if not on("archive"):
        return
    s = await e.get_sender()
    name = " ".join(filter(None, [getattr(s, "first_name", ""), getattr(s, "last_name", "")])) or "?"
    text = e.raw_text or ("[media]" if e.media else "")
    db.execute("INSERT OR REPLACE INTO msgs(id,chat,sender,name,out,text,date) VALUES(?,?,?,?,?,?,?)",
               (e.id, e.chat_id, e.sender_id, name, int(e.out), text,
                e.date.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()


# ---------- Salomlashish (faqat odam yozsa, kuniga 1 marta) ----------
GREET_STEPS = [
    "안녕하세요",
    "Привет, здравствуйте!",
    "Ассалом Алейкум, онлайн болсам алокага чикаман).",
]


def greeted_today(uid):
    r = db.execute("SELECT ts FROM greeted WHERE uid=?", (uid,)).fetchone()
    return bool(r) and datetime.fromtimestamp(r[0], TZ).date() == datetime.now(TZ).date()


def mark_greeted(uid):
    db.execute("INSERT OR REPLACE INTO greeted VALUES(?,?)", (uid, time.time()))
    db.commit()


def is_skipped(uid):
    return db.execute("SELECT 1 FROM skip WHERE uid=?", (uid,)).fetchone() is not None


async def go_offline():
    if on("hide_online"):
        try:
            await user(UpdateStatusRequest(offline=True))
        except Exception:
            pass


# Men birinchi yozsam, o'sha kuni u menga javob yozganda salom bermaydi
@user.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
@safe
async def mine(e):
    mark_greeted(e.chat_id)


@user.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
@safe
async def greet(e):
    if not on("greet"):
        return
    s = await e.get_sender()
    if not s or getattr(s, "bot", False) or e.sender_id in (777000, OWNER_ID):
        return
    if is_skipped(e.sender_id) or greeted_today(e.sender_id):
        return
    mark_greeted(e.sender_id)
    try:
        await user(SendReactionRequest(peer=e.chat_id, msg_id=e.id,
                                       reaction=[ReactionEmoji(emoticon="👍")]))
    except Exception as ex:
        log.warning("reaksiya xato: %s", ex)
    m = await e.respond("Hello")
    for t in GREET_STEPS:
        await asyncio.sleep(1.5)
        await m.edit(t)
    await go_offline()


# ---------- Tahrirlash ----------
@user.on(events.MessageEdited(func=lambda e: e.is_private))
@safe
async def edited(e):
    if e.out or not on("edits"):
        return
    r = db.execute("SELECT text,name FROM msgs WHERE id=? AND chat=?", (e.id, e.chat_id)).fetchone()
    new = e.raw_text or "[media]"
    if r and r[0] != new:
        await bot.send_message(OWNER_ID,
            f"✏️ {r[1]} ({e.chat_id}) xabarni tahrirladi\n\n"
            f"Asl matn:\n{r[0]}\n\nTahrirlangan:\n{new}")
    db.execute("UPDATE msgs SET text=? WHERE id=? AND chat=?", (new, e.id, e.chat_id))
    db.commit()


# ---------- O'chirish ----------
@user.on(events.MessageDeleted())
@safe
async def deleted(e):
    if not on("deletes"):
        return
    for mid in e.deleted_ids:
        q, args = "SELECT text,name,chat,out FROM msgs WHERE id=?", [mid]
        if e.chat_id:
            q += " AND chat=?"
            args.append(e.chat_id)
        r = db.execute(q, args).fetchone()
        if r and not r[3]:
            await bot.send_message(OWNER_ID, f"🗑 {r[1]} ({r[2]}) xabarni o'chirdi:\n\n{r[0]}")
            db.execute("UPDATE msgs SET deleted=1 WHERE id=?", (mid,))
    db.commit()


# ---------- Bio soat ----------
async def bio_loop():
    last = None
    while True:
        try:
            if on("bio") and await user.is_user_authorized():
                text = f"{datetime.now(TZ).strftime('%H:%M')} {get('biotext')}"[:70]
                if text != last:
                    await user(UpdateProfileRequest(about=text))
                    last = text
        except errors.FloodWaitError as f:
            await asyncio.sleep(f.seconds + 1)
        except Exception as ex:
            log.warning("bio xato: %s", ex)
        await asyncio.sleep(60 - time.time() % 60 + 0.3)


# ---------- Boshqaruv boti ----------
state = {}


def owner(e):
    return e.sender_id == OWNER_ID


@bot.on(events.NewMessage(pattern="/start|/panel", func=owner))
@safe
async def start(e):
    ok = await user.is_user_authorized()
    await e.respond(f"Boshqaruv paneli\nAkkaunt: {'ulangan ✅' if ok else 'ulanmagan ❌ (/login +998...)'}\n\nSalomlashishni cheklash: kontakt yuboring. Ro'yxat: /skiplist",
                    buttons=panel())


@bot.on(events.CallbackQuery(pattern=b"t:", func=lambda e: e.sender_id == OWNER_ID))
@safe
async def toggle(e):
    k = e.data.decode()[2:]
    put(k, "0" if on(k) else "1")
    await e.edit(buttons=panel())


def skip_buttons():
    rows = db.execute("SELECT uid,name FROM skip").fetchall()
    return [[Button.inline(f"❌ {n}", f"u:{u}".encode())] for u, n in rows]


@bot.on(events.NewMessage(func=lambda e: e.sender_id == OWNER_ID and bool(e.message.contact)))
@safe
async def skip_contact(e):
    c = e.message.contact
    uid = c.user_id
    if not uid:
        return await e.respond("Bu kontakt Telegramda topilmadi ❌")
    name = " ".join(filter(None, [c.first_name, c.last_name])) or str(uid)
    if is_skipped(uid):
        db.execute("DELETE FROM skip WHERE uid=?", (uid,))
        db.commit()
        await e.respond(f"✅ {name} cheklovdan chiqarildi (yana salomlashadi)")
    else:
        db.execute("INSERT OR REPLACE INTO skip VALUES(?,?)", (uid, name))
        db.commit()
        await e.respond(f"🚫 {name} ga salomlashish cheklandi.\nQayta yuborsangiz cheklov olib tashlanadi.")


@bot.on(events.NewMessage(pattern="/skiplist", func=owner))
@safe
async def skiplist(e):
    b = skip_buttons()
    await e.respond("Cheklanganlar (bosib o'chiring):" if b else "Cheklanganlar ro'yxati bo'sh",
                    buttons=b or None)


@bot.on(events.CallbackQuery(pattern=b"u:", func=lambda e: e.sender_id == OWNER_ID))
@safe
async def unskip(e):
    db.execute("DELETE FROM skip WHERE uid=?", (int(e.data.decode()[2:]),))
    db.commit()
    b = skip_buttons()
    await e.edit("Cheklanganlar (bosib o'chiring):" if b else "Cheklanganlar ro'yxati bo'sh",
                 buttons=b or None)


@bot.on(events.NewMessage(pattern=r"/biotext (.+)", func=owner))
@safe
async def biotext(e):
    put("biotext", e.pattern_match.group(1).strip())
    await e.respond("Bio matni o'zgardi ✅")


@bot.on(events.NewMessage(pattern=r"/export ?(-?\d+)?", func=owner))
@safe
async def export(e):
    cid = e.pattern_match.group(1)
    base = "SELECT date,name,sender,out,text,deleted FROM msgs"
    rows = (db.execute(base + " WHERE chat=? ORDER BY date", (int(cid),)).fetchall() if cid
            else db.execute(base + " ORDER BY chat,date").fetchall())
    if not rows:
        return await e.respond("Arxiv bo'sh")
    lines = [f"[{d}] {'MEN' if o else n} ({s}){' [OCHIRILGAN]' if x else ''}: {t}"
             for d, n, s, o, t, x in rows]
    f = io.BytesIO("\n".join(lines).encode())
    f.name = f"arxiv_{cid or 'hammasi'}.txt"
    await bot.send_file(OWNER_ID, f)


@bot.on(events.NewMessage(pattern=r"/login (\+?\d+)", func=owner))
@safe
async def login(e):
    phone = e.pattern_match.group(1)
    try:
        r = await user.send_code_request(phone)
    except Exception as ex:
        return await e.respond(f"Xato: {ex}")
    state.update(step="code", phone=phone, hash=r.phone_code_hash)
    await e.respond("Telegram yuborgan kodni raqamlar orasiga tire qo'yib yuboring (masalan 1-2-3-4-5). "
                    "Aks holda Telegram kodni bekor qiladi.")


@bot.on(events.NewMessage(func=lambda e: e.sender_id == OWNER_ID and bool(state.get("step"))))
@safe
async def login_steps(e):
    if (e.raw_text or "").startswith("/"):
        return
    try:
        if state["step"] == "code":
            code = "".join(c for c in e.raw_text if c.isdigit())
            await user.sign_in(state["phone"], code, phone_code_hash=state["hash"])
        else:
            await user.sign_in(password=e.raw_text)
            await e.delete()
        state.clear()
        sess = user.session.save()
        put("session", sess)
        msg = "Akkaunt ulandi ✅ /panel"
        if not SESSION_STRING:
            msg += ("\n\nRender qayta ishga tushsa ham qayta login qilmaslik uchun quyidagi qatorni "
                    "Render'da SESSION_STRING nomi bilan saqlang, so'ng bu xabarni O'CHIRIB TASHLANG "
                    "(u akkauntingizga to'liq kirish beradi):")
            await e.respond(msg)
            await e.respond(sess)
            return
        await e.respond(msg)
    except errors.SessionPasswordNeededError:
        state["step"] = "2fa"
        await e.respond("Ikki bosqichli parolni yuboring:")
    except Exception as ex:
        await e.respond(f"Xato: {ex}\n/login ni qayta yuboring")
        state.clear()


# ---------- Render uchun veb-server va uyg'oq tutish ----------
async def web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


async def keepalive():
    if not SELF_URL:
        return
    while True:
        await asyncio.sleep(600)
        try:
            async with ClientSession() as s:
                await s.get(SELF_URL, timeout=20)
        except Exception:
            pass


async def ensure(client, name, bot_mode=False):
    """Klientni ulaydi; ulanish uzilsa qayta ulaydi. Hech qachon qulamaydi."""
    while True:
        try:
            if not client.is_connected():
                if bot_mode:
                    await client.start(bot_token=BOT_TOKEN)
                else:
                    await client.connect()
                log.info("%s ulandi", name)
                if bot_mode:
                    try:
                        await client.send_message(OWNER_ID, "Bot ishga tushdi. /panel")
                    except Exception:
                        log.exception("Start xabari yuborilmadi")
        except Exception:
            log.exception("%s ulanishda xato", name)
            await asyncio.sleep(10)
            continue
        await asyncio.sleep(30)


async def main():
    await web_server()
    asyncio.create_task(bio_loop())
    asyncio.create_task(keepalive())
    asyncio.create_task(ensure(bot, "bot", True))
    asyncio.create_task(ensure(user, "user"))
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    loop.run_until_complete(main())
  
