import asyncio, io, json, logging, os, re, sqlite3, time
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
DB_PATH = os.path.join(DATA_DIR, "factory.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, phone TEXT, name TEXT,
  session TEXT NOT NULL, added TEXT);
CREATE TABLE IF NOT EXISTS acfg(acc INTEGER, k TEXT, v TEXT, PRIMARY KEY(acc, k));
CREATE TABLE IF NOT EXISTS msgs(acc INTEGER, id INTEGER, chat INTEGER, sender INTEGER,
  name TEXT, out INTEGER, text TEXT, date TEXT, deleted INTEGER DEFAULT 0,
  PRIMARY KEY(acc, id, chat));
CREATE TABLE IF NOT EXISTS greeted(acc INTEGER, uid INTEGER, ts REAL, PRIMARY KEY(acc, uid));
CREATE TABLE IF NOT EXISTS skip(acc INTEGER, uid INTEGER, name TEXT, PRIMARY KEY(acc, uid));
CREATE TABLE IF NOT EXISTS gcfg(k TEXT PRIMARY KEY, v TEXT);
"""
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript(SCHEMA)

GREET_DEFAULT = ["Hello", "안녕하세요", "Привет, здравствуйте!",
                 "Ассалом Алейкум, онлайн болсам алокага чикаман)."]
# Birinchi ishga tushganda HAMMA funksiya o'chiq. Kerakligini panelda yoqasiz.
DEFAULTS = {
    "greet": "0", "name_clock": "0", "bio": "0", "edits": "0", "deletes": "0",
    "archive": "0", "hide_online": "0",
    "name_text": "", "name_mode": "after",
    "bio_text": "da ham meni hayolimdasan", "bio_mode": "before",
    "greet_steps": json.dumps(GREET_DEFAULT, ensure_ascii=False),
    "greet_delay": "1.5", "greet_react": "👍",
}
LABELS = {"greet": "Salomlashish", "name_clock": "Ismda soat", "bio": "Bioda soat",
          "edits": "Tahrir kuzatuv", "deletes": "O'chirish kuzatuv",
          "archive": "Arxivlash", "hide_online": "Online yashirish"}
MODE_LBL = {"after": "ism + soat", "before": "soat + ism", "only": "faqat soat"}
MODE_NEXT = {"after": "before", "before": "only", "only": "after"}


# ---------- DB yordamchilari ----------
def ag(acc, k):
    r = db.execute("SELECT v FROM acfg WHERE acc=? AND k=?", (acc, k)).fetchone()
    return r[0] if r else DEFAULTS[k]


def ap(acc, k, v):
    db.execute("INSERT OR REPLACE INTO acfg VALUES(?,?,?)", (acc, k, v))
    db.commit()


def aon(acc, k):
    return ag(acc, k) == "1"


def gget(k):
    r = db.execute("SELECT v FROM gcfg WHERE k=?", (k,)).fetchone()
    return r[0] if r else ""


def gput(k, v):
    db.execute("INSERT OR REPLACE INTO gcfg VALUES(?,?)", (k, v))
    db.commit()


def gsteps(acc):
    try:
        v = json.loads(ag(acc, "greet_steps"))
        return [str(x) for x in v]
    except Exception:
        return list(GREET_DEFAULT)


def acc_row(acc):
    return db.execute("SELECT id,phone,name FROM accounts WHERE id=?", (acc,)).fetchone()


def acc_name(acc):
    r = acc_row(acc)
    return (r[2] or str(acc)) if r else str(acc)


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def cut(t, n=1800):
    t = t or ""
    return t if len(t) <= n else t[:n] + "…"


def base_name(n):
    return re.sub(r"\s*\d{1,2}:\d{2}\s*", " ", n or "").strip()


# ---------- Klientlar ----------
clients = {}   # acc -> TelegramClient
meta = {}      # acc -> {"premium": bool, "dead": bool}
tasks = {}     # acc -> supervisor task
flood = {}     # acc -> FloodWait tugash vaqti
last = {}      # (acc, "n"/"b") -> oxirgi yuborilgan matn


def new_client(session=""):
    return TelegramClient(StringSession(session), API_ID, API_HASH,
                          device_model=DEVICE, system_version="Android 16",
                          app_version="12.0.0", connection_retries=None,
                          retry_delay=5, auto_reconnect=True)


bot = TelegramClient(StringSession(), API_ID, API_HASH,
                     connection_retries=None, retry_delay=5, auto_reconnect=True)


async def notify(text, **kw):
    try:
        await bot.send_message(OWNER_ID, text, **kw)
    except Exception:
        log.exception("notify xato")


def status(acc):
    if meta.get(acc, {}).get("dead"):
        return "🔴 sessiya bekor qilingan"
    c = clients.get(acc)
    return "🟢 ulangan" if c and c.is_connected() else "🟡 ulanmoqda"


def bio_limit(acc):
    return 140 if meta.get(acc, {}).get("premium") else 70


# ---------- Akkaunt hodisalari ----------
def is_skipped(acc, uid):
    return db.execute("SELECT 1 FROM skip WHERE acc=? AND uid=?", (acc, uid)).fetchone() is not None


def greeted_today(acc, uid):
    r = db.execute("SELECT ts FROM greeted WHERE acc=? AND uid=?", (acc, uid)).fetchone()
    return bool(r) and datetime.fromtimestamp(r[0], TZ).date() == datetime.now(TZ).date()


def mark_greeted(acc, uid):
    db.execute("INSERT OR REPLACE INTO greeted VALUES(?,?,?)", (acc, uid, time.time()))
    db.commit()


async def go_offline(acc):
    c = clients.get(acc)
    if c and aon(acc, "hide_online"):
        try:
            await c(UpdateStatusRequest(offline=True))
        except Exception:
            pass


async def h_save(acc, e):
    if not (aon(acc, "archive") or aon(acc, "edits") or aon(acc, "deletes")):
        return
    if e.chat_id == acc or e.sender_id == 777000:
        return
    s = await e.get_sender()
    if not s or getattr(s, "bot", False):   # botlar saqlanmaydi -> ularniki xabar ham kelmaydi
        return
    name = " ".join(filter(None, [getattr(s, "first_name", ""), getattr(s, "last_name", "")])) or "?"
    text = e.raw_text or ("[media]" if e.media else "")
    db.execute("INSERT OR REPLACE INTO msgs(acc,id,chat,sender,name,out,text,date) VALUES(?,?,?,?,?,?,?,?)",
               (acc, e.id, e.chat_id, e.sender_id, name, int(bool(e.out)), text,
                e.date.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()


async def h_mine(acc, e):
    # Men birinchi yozsam, o'sha kuni u menga javob yozganda salom bermaydi
    if e.chat_id != acc:
        mark_greeted(acc, e.chat_id)


async def h_greet(acc, e):
    if not aon(acc, "greet"):
        return
    uid = e.sender_id
    if uid in (777000, OWNER_ID, acc) or uid in clients:
        return
    s = await e.get_sender()
    if not s or getattr(s, "bot", False):
        return
    if is_skipped(acc, uid) or greeted_today(acc, uid):
        return
    steps = gsteps(acc)
    if not steps:
        return
    mark_greeted(acc, uid)
    emo = ag(acc, "greet_react")
    if emo:
        try:
            await e.client(SendReactionRequest(peer=e.chat_id, msg_id=e.id,
                                               reaction=[ReactionEmoji(emoticon=emo)]))
        except Exception as ex:
            log.warning("reaksiya xato: %s", ex)
    m = await e.respond(steps[0])
    try:
        delay = float(ag(acc, "greet_delay"))
    except ValueError:
        delay = 1.5
    for t in steps[1:]:
        await asyncio.sleep(delay)
        try:
            await m.edit(t)
        except errors.MessageNotModifiedError:
            pass
    await go_offline(acc)


async def h_edit(acc, e):
    if e.out:
        return
    r = db.execute("SELECT text,name FROM msgs WHERE acc=? AND id=? AND chat=? AND out=0",
                   (acc, e.id, e.chat_id)).fetchone()
    if not r:   # saqlanmagan (bot yoki yoqilmagan) -> hech narsa yuborilmaydi
        return
    new = e.raw_text or "[media]"
    if aon(acc, "edits") and r[0] != new:
        await notify(f"✏️ [{acc_name(acc)}] {r[1]} ({e.chat_id}) xabarni tahrirladi\n\n"
                     f"Asl matn:\n{cut(r[0])}\n\nTahrirlangan:\n{cut(new)}")
    db.execute("UPDATE msgs SET text=? WHERE acc=? AND id=? AND chat=?", (new, acc, e.id, e.chat_id))
    db.commit()


async def h_del(acc, e):
    for mid in e.deleted_ids:
        q = "SELECT text,name,chat FROM msgs WHERE acc=? AND id=? AND out=0 AND deleted=0"
        args = [acc, mid]
        if e.chat_id:
            q += " AND chat=?"
            args.append(e.chat_id)
        r = db.execute(q, args).fetchone()
        if not r:
            continue
        if aon(acc, "deletes"):
            await notify(f"🗑 [{acc_name(acc)}] {r[1]} ({r[2]}) xabarni o'chirdi:\n\n{cut(r[0])}")
        db.execute("UPDATE msgs SET deleted=1 WHERE acc=? AND id=? AND chat=?", (acc, mid, r[2]))
    db.commit()


def attach(acc, c):
    def reg(fn, ev):
        async def w(e):
            try:
                await fn(acc, e)
            except Exception:
                log.exception("handler xato: %s", fn.__name__)
        c.add_event_handler(w, ev)

    priv = lambda e: e.is_private
    reg(h_save, events.NewMessage(func=priv))
    reg(h_mine, events.NewMessage(outgoing=True, func=priv))
    reg(h_greet, events.NewMessage(incoming=True, func=priv))
    reg(h_edit, events.MessageEdited(func=priv))
    reg(h_del, events.MessageDeleted())


# ---------- Soat (ism va bio) ----------
def fit(mode, clock, text, limit):
    if mode == "only" or not text:
        return clock
    text = text[:max(limit - len(clock) - 1, 0)].rstrip()
    if not text:
        return clock
    return f"{clock} {text}" if mode == "before" else f"{text} {clock}"


DEAD_ERRORS = (errors.AuthKeyUnregisteredError, errors.UserDeactivatedError,
               errors.SessionRevokedError)


async def tick(acc, hm):
    c = clients.get(acc)
    if not c or not c.is_connected() or meta.get(acc, {}).get("dead"):
        return
    if time.time() < flood.get(acc, 0):
        return
    kw = {}
    if aon(acc, "name_clock"):
        v = fit(ag(acc, "name_mode"), hm, ag(acc, "name_text"), 64)
        if last.get((acc, "n")) != v:
            kw["first_name"] = v
    if aon(acc, "bio"):
        v = fit(ag(acc, "bio_mode"), hm, ag(acc, "bio_text"), bio_limit(acc))
        if last.get((acc, "b")) != v:
            kw["about"] = v
    if not kw:
        return
    try:
        await c(UpdateProfileRequest(**kw))
        if "first_name" in kw:
            last[(acc, "n")] = kw["first_name"]
        if "about" in kw:
            last[(acc, "b")] = kw["about"]
    except errors.FloodWaitError as f:
        flood[acc] = time.time() + f.seconds + 1
        log.warning("%s FloodWait %ss", acc, f.seconds)
    except DEAD_ERRORS:
        meta.setdefault(acc, {})["dead"] = True
        await notify(f"🔴 {acc_name(acc)}: sessiya bekor qilingan. Panelda o'chirib, qayta ulang.")
    except Exception as ex:
        log.warning("%s profil xato: %s", acc, ex)


async def apply_now(acc):
    await tick(acc, datetime.now(TZ).strftime("%H:%M"))


async def restore(acc, what):
    """Soat o'chirilganda ism/bioni soatsiz holatga qaytaradi."""
    c = clients.get(acc)
    if not c:
        return
    try:
        if what == "name":
            last.pop((acc, "n"), None)
            if ag(acc, "name_text"):
                await c(UpdateProfileRequest(first_name=ag(acc, "name_text")))
        else:
            last.pop((acc, "b"), None)
            await c(UpdateProfileRequest(about=ag(acc, "bio_text")))
    except Exception as ex:
        log.warning("restore xato: %s", ex)


async def clock_loop():
    while True:
        hm = datetime.now(TZ).strftime("%H:%M")
        await asyncio.gather(*(tick(a, hm) for a in list(clients)), return_exceptions=True)
        await asyncio.sleep(60 - time.time() % 60 + 0.3)


async def offline_loop():
    while True:
        await asyncio.sleep(45)
        for a, c in list(clients.items()):
            if aon(a, "hide_online") and c.is_connected():
                try:
                    await c(UpdateStatusRequest(offline=True))
                except Exception:
                    pass


# ---------- Akkauntlarni ishga tushirish ----------
async def supervise(acc, c):
    while clients.get(acc) is c:
        try:
            if not c.is_connected():
                await c.connect()
                log.info("%s qayta ulandi", acc)
        except Exception:
            log.exception("%s ulanishda xato", acc)
        await asyncio.sleep(30)


def register(acc, c, me):
    clients[acc] = c
    meta[acc] = {"premium": bool(getattr(me, "premium", False)), "dead": False}
    attach(acc, c)
    tasks[acc] = asyncio.create_task(supervise(acc, c))


async def bring_up(acc, session):
    while acc not in clients and acc_row(acc):
        c = new_client(session)
        try:
            await c.connect()
            if not await c.is_user_authorized():
                await c.disconnect()
                meta[acc] = {"dead": True}
                await notify(f"🔴 {acc_name(acc)}: sessiya yaroqsiz. Panelda o'chirib, qayta ulang.")
                return
            register(acc, c, await c.get_me())
            log.info("%s ulandi", acc)
            return
        except Exception:
            log.exception("%s ishga tushmadi", acc)
            try:
                await c.disconnect()
            except Exception:
                pass
            await asyncio.sleep(15)


async def deactivate(acc):
    c = clients.pop(acc, None)
    t = tasks.pop(acc, None)
    if t:
        t.cancel()
    if c:
        try:
            await c.disconnect()
        except Exception:
            pass
    meta.pop(acc, None)


async def import_env():
    """SESSION_STRING (Render env) bo'lsa, birinchi akkaunt sifatida qo'shadi."""
    if not SESSION_STRING or gget("env_sess") == SESSION_STRING[-24:]:
        return
    c = new_client(SESSION_STRING)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            log.warning("SESSION_STRING yaroqsiz")
            return
        me = await c.get_me()
        name = " ".join(filter(None, [me.first_name, me.last_name])) or str(me.id)
        db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?,?,?)",
                   (me.id, f"+{me.phone}" if me.phone else "", name, SESSION_STRING, now_str()))
        if not db.execute("SELECT 1 FROM acfg WHERE acc=? AND k='name_text'", (me.id,)).fetchone():
            ap(me.id, "name_text", base_name(me.first_name))
        db.commit()
        gput("env_sess", SESSION_STRING[-24:])
    except Exception:
        log.exception("SESSION_STRING import xato")
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


# ---------- Boshqaruv boti: ekranlar ----------
pending = {}   # kutilayotgan matn kiritish
login = {}     # akkaunt qo'shish jarayoni


def owner(e):
    return e.sender_id == OWNER_ID


def b(text, data):
    return Button.inline(text, data.encode())


def screen_main():
    rows = db.execute("SELECT id,name,phone FROM accounts ORDER BY added").fetchall()
    btn = []
    for i, n, p in rows:
        st = status(i)[0]
        btn.append([b(f"{st} {n} {p or ''}".strip(), f"a:{i}")])
    btn.append([b("➕ Akkaunt qo'shish", "a:add")])
    btn.append([b("💾 Backup", "bk")])
    return f"🏭 Boshqaruv paneli\nUlangan akkauntlar: {len(rows)}", btn


def screen_acc(acc):
    r = acc_row(acc)
    if not r:
        return screen_main()
    btn = [[b(f"{'✅' if aon(acc, k) else '❌'} {n}", f"t:{acc}:{k}")] for k, n in LABELS.items()]
    btn += [[b("🕒 Soat sozlamalari", f"c:{acc}"), b("👋 Salom matnlari", f"g:{acc}")],
            [b("🚫 Cheklov ro'yxati", f"s:{acc}"), b("📄 Arxiv eksport", f"x:{acc}")],
            [b("🔑 Session", f"ss:{acc}"), b("🗑 O'chirish", f"d:{acc}")],
            [b("⬅️ Orqaga", "m")]]
    return f"📱 {r[2]} {r[1] or ''}\nID: {acc}\nHolat: {status(acc)}", btn


def screen_clock(acc):
    hm = datetime.now(TZ).strftime("%H:%M")
    pn = fit(ag(acc, "name_mode"), hm, ag(acc, "name_text"), 64)
    pb = fit(ag(acc, "bio_mode"), hm, ag(acc, "bio_text"), bio_limit(acc))
    text = (f"🕒 Soat sozlamalari — {acc_name(acc)}\n\n"
            f"Ism {'✅' if aon(acc, 'name_clock') else '❌'}\n"
            f"Ism matni: {ag(acc, 'name_text') or '—'}\nKo'rinishi: {pn}\n\n"
            f"Bio {'✅' if aon(acc, 'bio') else '❌'}\n"
            f"Bio matni: {ag(acc, 'bio_text') or '—'}\nKo'rinishi: {pb}\n\n"
            f"Yoqish/o'chirish akkaunt sahifasida.")
    btn = [[b(f"Ism: {MODE_LBL[ag(acc, 'name_mode')]}", f"cm:{acc}:name_mode")],
           [b("✏️ Ism matni", f"ce:{acc}:name_text")],
           [b(f"Bio: {MODE_LBL[ag(acc, 'bio_mode')]}", f"cm:{acc}:bio_mode")],
           [b("✏️ Bio matni", f"ce:{acc}:bio_text")],
           [b("⬅️ Orqaga", f"a:{acc}")]]
    return text, btn


def screen_greet(acc):
    steps = gsteps(acc)
    lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) or "— bo'sh —"
    text = (f"👋 Salom matnlari — {acc_name(acc)}\n\n{lines}\n\n"
            "1-qator yuboriladi, qolganlari ketma-ket shu xabarga tahrirlanadi.")
    btn = [[b(f"✏️ {i + 1}. {s[:18]}", f"ge:{acc}:{i}"), b("🗑", f"gd:{acc}:{i}")]
           for i, s in enumerate(steps)]
    btn += [[b("➕ Qo'shish", f"ga:{acc}")],
            [b(f"⏱ Kechikish: {ag(acc, 'greet_delay')}s", f"gt:{acc}"),
             b(f"{ag(acc, 'greet_react') or '—'} Reaksiya", f"gr:{acc}")],
            [b("♻️ Standart", f"gx:{acc}")],
            [b("⬅️ Orqaga", f"a:{acc}")]]
    return text, btn


def screen_skip(acc):
    rows = db.execute("SELECT uid,name FROM skip WHERE acc=?", (acc,)).fetchall()
    text = (f"🚫 Salomlashish cheklovi — {acc_name(acc)}\n"
            + ("Bosib olib tashlang:" if rows else "Ro'yxat bo'sh"))
    btn = [[b(f"❌ {n}", f"su:{acc}:{u}")] for u, n in rows]
    btn += [[b("➕ Kontakt / ID qo'shish", f"sa:{acc}")], [b("⬅️ Orqaga", f"a:{acc}")]]
    return text, btn


def render(key):
    k, _, a = key.partition(":")
    if k == "a":
        return screen_acc(int(a))
    if k == "c":
        return screen_clock(int(a))
    if k == "g":
        return screen_greet(int(a))
    if k == "s":
        return screen_skip(int(a))
    return screen_main()


async def show(e, key):
    text, btn = render(key)
    try:
        await e.edit(text, buttons=btn)
    except errors.MessageNotModifiedError:
        pass


async def show_new(e, key):
    text, btn = render(key)
    await e.respond(text, buttons=btn)


async def ask(e, text, back):
    try:
        await e.edit(text, buttons=[[b("⬅️ Bekor qilish", back)]])
    except errors.MessageNotModifiedError:
        pass


async def delete_later(m, sec):
    await asyncio.sleep(sec)
    try:
        await m.delete()
    except Exception:
        pass


# ---------- Akkaunt qo'shish (kod tugmalar orqali) ----------
def kp():
    rows = [[b(str(n), f"k:{n}") for n in (1, 2, 3)],
            [b(str(n), f"k:{n}") for n in (4, 5, 6)],
            [b(str(n), f"k:{n}") for n in (7, 8, 9)],
            [b("⌫", "k:b"), b("0", "k:0"), b("✅", "k:ok")],
            [b("❌ Bekor qilish", "k:x")]]
    return rows


def kp_text():
    code = " ".join(login.get("code", "")) or "—"
    return (f"📲 {login.get('phone')}\nTelegram yuborgan kodni tugmalar bilan kiriting.\n"
            f"(Kod Telegram ilovangizdagi 'Telegram' xizmat chatiga keladi.)\n\nKod: {code}")


async def cancel_login():
    c = login.get("client")
    login.clear()
    if c:
        try:
            await c.disconnect()
        except Exception:
            pass


async def begin_login(phone, reply):
    await cancel_login()
    c = new_client()
    try:
        await c.connect()
        r = await c.send_code_request(phone)
    except Exception as ex:
        try:
            await c.disconnect()
        except Exception:
            pass
        return await reply(f"Xato: {ex}")
    login.update(step="code", client=c, phone=phone, hash=r.phone_code_hash,
                 code="", len=getattr(r.type, "length", None))
    await reply(kp_text(), buttons=kp())


async def finish_login(reply):
    c = login["client"]
    phone = login["phone"]
    me = await c.get_me()
    acc = me.id
    if acc in clients:
        await cancel_login()
        return await reply("Bu akkaunt allaqachon ulangan.")
    name = " ".join(filter(None, [me.first_name, me.last_name])) or str(acc)
    db.execute("INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?)",
               (acc, phone, name, c.session.save(), now_str()))
    ap(acc, "name_text", base_name(me.first_name))
    login.clear()
    register(acc, c, me)
    await reply(f"✅ {name} ulandi.\nBarcha funksiyalar o'chiq — kerakligini yoqing.",
                buttons=[[b("⚙️ Sozlash", f"a:{acc}")]])


async def try_code(code, reply):
    c = login.get("client")
    if not c:
        return
    try:
        await c.sign_in(login["phone"], code, phone_code_hash=login["hash"])
    except errors.SessionPasswordNeededError:
        login["step"] = "2fa"
        return await reply("🔐 Ikki bosqichli parolni yuboring (xabar darrov o'chiriladi):")
    except errors.PhoneCodeInvalidError:
        login["code"] = ""
        return await reply("Kod noto'g'ri. Qayta kiriting:\n\n" + kp_text(), buttons=kp())
    except (errors.PhoneCodeExpiredError, errors.PhoneCodeEmptyError):
        await cancel_login()
        return await reply("Kod eskirgan. Qaytadan boshlang: /login")
    except Exception as ex:
        await cancel_login()
        return await reply(f"Xato: {ex}\nQaytadan boshlang: /login")
    await finish_login(reply)


async def try_password(pw, reply):
    c = login.get("client")
    if not c:
        return
    try:
        await c.sign_in(password=pw)
    except errors.PasswordHashInvalidError:
        return await reply("Parol noto'g'ri. Qayta yuboring:")
    except Exception as ex:
        await cancel_login()
        return await reply(f"Xato: {ex}\nQaytadan boshlang: /login")
    await finish_login(reply)


async def keypad(e, x):
    if login.get("step") != "code":
        return await e.answer("Faol kirish jarayoni yo'q", alert=True)
    await e.answer()
    if x == "x":
        await cancel_login()
        return await show(e, "m")
    if x == "ok":
        if login["code"]:
            await try_code(login["code"], e.respond)
        return
    if x == "b":
        login["code"] = login["code"][:-1]
    elif x.isdigit() and len(login["code"]) < 8:
        login["code"] += x
    try:
        await e.edit(kp_text(), buttons=kp())
    except errors.MessageNotModifiedError:
        pass
    if login.get("len") and len(login["code"]) == login["len"]:
        await try_code(login["code"], e.respond)


# ---------- Backup ----------
def make_backup():
    path = os.path.join(DATA_DIR, "backup.db")
    if os.path.exists(path):
        os.remove(path)
    d = sqlite3.connect(path)
    d.executescript(SCHEMA)
    for t in ("accounts", "acfg", "skip"):
        rows = db.execute(f"SELECT * FROM {t}").fetchall()
        if rows:
            d.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
    d.commit()
    d.close()
    return path


# ---------- Boshqaruv boti: buyruqlar ----------
@bot.on(events.NewMessage(pattern=r"^/(start|panel)", func=owner))
async def cmd_start(e):
    pending.clear()
    await show_new(e, "m")


@bot.on(events.NewMessage(pattern=r"^/cancel", func=owner))
async def cmd_cancel(e):
    pending.clear()
    await cancel_login()
    await show_new(e, "m")


@bot.on(events.NewMessage(pattern=r"^/login ?(\+?\d+)?", func=owner))
async def cmd_login(e):
    phone = e.pattern_match.group(1)
    if phone:
        return await begin_login(phone if phone.startswith("+") else "+" + phone, e.respond)
    pending.clear()
    pending["kind"] = "phone"
    await e.respond("📱 Telefon raqamini yuboring (+998...). Bekor qilish: /cancel")


@bot.on(events.NewMessage(func=lambda e: e.sender_id == OWNER_ID and bool(e.message.document)
                          and (e.raw_text or "").startswith("/restore")))
async def cmd_restore(e):
    try:
        path = os.path.join(DATA_DIR, "restore.db")
        await e.download_media(path)
        src = sqlite3.connect(path)
        n = 0
        for t in ("accounts", "acfg", "skip"):
            rows = src.execute(f"SELECT * FROM {t}").fetchall()
            if rows:
                db.executemany(f"INSERT OR REPLACE INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        for acc, sess in src.execute("SELECT id,session FROM accounts").fetchall():
            n += 1
            if acc not in clients:
                asyncio.create_task(bring_up(acc, sess))
        db.commit()
        src.close()
        os.remove(path)
        await e.respond(f"♻️ Tiklandi: {n} ta akkaunt. /panel")
    except Exception as ex:
        await e.respond(f"Restore xato: {ex}")


@bot.on(events.NewMessage(func=owner))
async def inbox(e):
    try:
        await inbox_inner(e)
    except Exception:
        log.exception("inbox xato")
        await e.respond("Xatolik yuz berdi, /panel")


async def inbox_inner(e):
    txt = (e.raw_text or "").strip()
    if txt.startswith("/"):
        return
    step = login.get("step")
    if step == "code" and re.search(r"\d", txt):
        code = re.sub(r"\D", "", txt)
        try:
            await e.delete()
        except Exception:
            pass
        return await try_code(code, e.respond)
    if step == "2fa":
        try:
            await e.delete()
        except Exception:
            pass
        return await try_password(txt, e.respond)

    kind = pending.get("kind")
    if not kind:
        return
    acc = pending.get("acc")

    if kind == "phone":
        phone = re.sub(r"[^\d+]", "", txt)
        if len(phone) < 7:
            return await e.respond("Raqam noto'g'ri. Qayta yuboring (+998...):")
        pending.clear()
        return await begin_login(phone if phone.startswith("+") else "+" + phone, e.respond)

    if kind in ("name_text", "bio_text"):
        ap(acc, kind, txt[:64 if kind == "name_text" else 140])
        pending.clear()
        await apply_now(acc)
        return await show_new(e, f"c:{acc}")

    if kind in ("g_add", "g_edit"):
        steps = gsteps(acc)
        if kind == "g_add":
            steps.append(txt)
        elif pending["idx"] < len(steps):
            steps[pending["idx"]] = txt
        ap(acc, "greet_steps", json.dumps(steps, ensure_ascii=False))
        pending.clear()
        return await show_new(e, f"g:{acc}")

    if kind == "g_delay":
        try:
            v = min(max(float(txt.replace(",", ".")), 0.3), 30)
        except ValueError:
            return await e.respond("Raqam yuboring (masalan 1.5):")
        ap(acc, "greet_delay", str(v))
        pending.clear()
        return await show_new(e, f"g:{acc}")

    if kind == "g_react":
        ap(acc, "greet_react", "" if txt == "-" else txt[:8])
        pending.clear()
        return await show_new(e, f"g:{acc}")

    if kind == "skip":
        c = e.message.contact
        if c:
            uid = c.user_id
            name = " ".join(filter(None, [c.first_name, c.last_name])) or str(uid)
        elif txt.lstrip("-").isdigit():
            uid, name = int(txt), txt
        else:
            return await e.respond("Kontakt yoki foydalanuvchi ID yuboring.")
        if not uid:
            return await e.respond("Bu kontakt Telegramda topilmadi ❌")
        if is_skipped(acc, uid):
            db.execute("DELETE FROM skip WHERE acc=? AND uid=?", (acc, uid))
        else:
            db.execute("INSERT OR REPLACE INTO skip VALUES(?,?,?)", (acc, uid, name))
        db.commit()
        pending.clear()
        return await show_new(e, f"s:{acc}")


@bot.on(events.CallbackQuery(func=lambda e: e.sender_id == OWNER_ID))
async def cb(e):
    try:
        await cb_inner(e)
    except Exception:
        log.exception("callback xato")
        try:
            await e.respond("Xatolik yuz berdi, /panel")
        except Exception:
            pass


async def cb_inner(e):
    d = e.data.decode().split(":")
    op = d[0]
    if op == "k":
        return await keypad(e, d[1])
    await e.answer()
    pending.clear()
    acc = int(d[1]) if len(d) > 1 and d[1].lstrip("-").isdigit() else None

    if op == "m":
        return await show(e, "m")
    if op == "a":
        if d[1] == "add":
            pending["kind"] = "phone"
            return await ask(e, "📱 Telefon raqamini yuboring (+998...)", "m")
        return await show(e, f"a:{acc}")
    if op in ("c", "g", "s"):
        return await show(e, f"{op}:{acc}")

    if op == "t":
        k = d[2]
        if k not in LABELS:
            return
        turn_on = not aon(acc, k)
        ap(acc, k, "1" if turn_on else "0")
        if k in ("name_clock", "bio"):
            if turn_on:
                last.pop((acc, "n" if k == "name_clock" else "b"), None)
                await apply_now(acc)
            else:
                await restore(acc, "name" if k == "name_clock" else "bio")
        elif k == "hide_online" and turn_on:
            await go_offline(acc)
        return await show(e, f"a:{acc}")

    if op == "cm":
        k = d[2]
        ap(acc, k, MODE_NEXT[ag(acc, k)])
        await apply_now(acc)
        return await show(e, f"c:{acc}")
    if op == "ce":
        pending.update(kind=d[2], acc=acc)
        what = "ism" if d[2] == "name_text" else "bio"
        return await ask(e, f"✏️ Yangi {what} matnini yuboring (soatsiz):", f"c:{acc}")

    if op == "ga":
        pending.update(kind="g_add", acc=acc)
        return await ask(e, "➕ Yangi salom matnini yuboring:", f"g:{acc}")
    if op == "ge":
        idx = int(d[2])
        pending.update(kind="g_edit", acc=acc, idx=idx)
        cur = gsteps(acc)[idx] if idx < len(gsteps(acc)) else ""
        return await ask(e, f"✏️ {idx + 1}-matn hozir:\n{cur}\n\nYangisini yuboring:", f"g:{acc}")
    if op == "gd":
        steps = gsteps(acc)
        idx = int(d[2])
        if idx < len(steps):
            steps.pop(idx)
            ap(acc, "greet_steps", json.dumps(steps, ensure_ascii=False))
        return await show(e, f"g:{acc}")
    if op == "gt":
        pending.update(kind="g_delay", acc=acc)
        return await ask(e, "⏱ Matnlar orasidagi kechikishni soniyada yuboring (masalan 1.5):", f"g:{acc}")
    if op == "gr":
        pending.update(kind="g_react", acc=acc)
        return await ask(e, "Reaksiya emojisini yuboring (o'chirish uchun -):", f"g:{acc}")
    if op == "gx":
        db.execute("DELETE FROM acfg WHERE acc=? AND k IN ('greet_steps','greet_delay','greet_react')", (acc,))
        db.commit()
        return await show(e, f"g:{acc}")

    if op == "sa":
        pending.update(kind="skip", acc=acc)
        return await ask(e, "Kontakt yuboring yoki foydalanuvchi ID yozing.\n"
                            "Ro'yxatda bor bo'lsa — olib tashlanadi.", f"s:{acc}")
    if op == "su":
        db.execute("DELETE FROM skip WHERE acc=? AND uid=?", (acc, int(d[2])))
        db.commit()
        return await show(e, f"s:{acc}")

    if op == "x":
        rows = db.execute("SELECT date,name,sender,out,text,deleted FROM msgs WHERE acc=? "
                          "ORDER BY chat,date", (acc,)).fetchall()
        if not rows:
            return await e.respond("Arxiv bo'sh")
        lines = [f"[{dt}] {'MEN' if o else n} ({s}){' [OCHIRILGAN]' if x else ''}: {t}"
                 for dt, n, s, o, t, x in rows]
        f = io.BytesIO("\n".join(lines).encode())
        f.name = f"arxiv_{acc}.txt"
        return await bot.send_file(OWNER_ID, f)

    if op == "ss":
        c = clients.get(acc)
        r = db.execute("SELECT session FROM accounts WHERE id=?", (acc,)).fetchone()
        sess = c.session.save() if c else (r[0] if r else "")
        m = await e.respond(f"🔑 {acc_name(acc)} ({DEVICE})\n\n`{sess}`\n\n"
                            "⚠️ Bu akkauntga to'liq kirish beradi. 60 soniyadan keyin o'chiriladi.")
        asyncio.create_task(delete_later(m, 60))
        return

    if op == "d":
        try:
            await e.edit(f"🗑 {acc_name(acc)} akkauntini o'chirasizmi?",
                         buttons=[[b("🗑 Ha (sessiya Telegramda qoladi)", f"dy:{acc}:0")],
                                  [b("🚪 Ha + Telegramdan ham chiqarish", f"dy:{acc}:1")],
                                  [b("⬅️ Yo'q", f"a:{acc}")]])
        except errors.MessageNotModifiedError:
            pass
        return
    if op == "dy":
        if aon(acc, "name_clock"):
            await restore(acc, "name")
        if aon(acc, "bio"):
            await restore(acc, "bio")
        c = clients.get(acc)
        if d[2] == "1" and c:
            try:
                await c.log_out()
            except Exception as ex:
                log.warning("logout xato: %s", ex)
        await deactivate(acc)
        for t in ("accounts", "acfg", "msgs", "greeted", "skip"):
            db.execute(f"DELETE FROM {t} WHERE {'id' if t == 'accounts' else 'acc'}=?", (acc,))
        db.commit()
        for k in [k for k in last if k[0] == acc]:
            last.pop(k, None)
        return await show(e, "m")

    if op == "bk":
        path = make_backup()
        await bot.send_file(OWNER_ID, path,
                            caption="💾 Backup. Tiklash: shu faylni /restore sarlavhasi bilan yuboring.\n"
                                    "⚠️ Ichida sessiyalar bor — hech kimga bermang.")
        os.remove(path)
        return


# ---------- Render uchun veb-server va uyg'oq tutish ----------
async def web_server():
    async def ok(request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


async def keepalive():
    if not SELF_URL:
        return
    while True:
        await asyncio.sleep(420)
        try:
            async with ClientSession() as s:
                await s.get(SELF_URL, timeout=20)
        except Exception:
            pass


async def ensure_bot():
    """Boshqaruv botini ulaydi; uzilsa qayta ulaydi. Hech qachon qulamaydi."""
    while True:
        try:
            if not bot.is_connected():
                await bot.start(bot_token=BOT_TOKEN)
                log.info("bot ulandi")
                try:
                    await bot.send_message(OWNER_ID, "Bot ishga tushdi. /panel")
                except Exception:
                    log.exception("Start xabari yuborilmadi")
        except Exception:
            log.exception("bot ulanishda xato")
            await asyncio.sleep(10)
            continue
        await asyncio.sleep(30)


async def main():
    await web_server()
    asyncio.create_task(ensure_bot())
    asyncio.create_task(keepalive())
    await import_env()
    for acc, sess in db.execute("SELECT id,session FROM accounts").fetchall():
        asyncio.create_task(bring_up(acc, sess))
    asyncio.create_task(clock_loop())
    asyncio.create_task(offline_loop())
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    loop.run_until_complete(main())
