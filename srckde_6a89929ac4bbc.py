import os
import re
import sqlite3
import random
import time
import logging
from datetime import datetime, date, timedelta
from contextlib import contextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ApplicationHandlerStop,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("lifebot")

DB_PATH = os.environ.get("LIFEBOT_DB", "lifebot.db")

BOT_TOKEN = "توکن-ربات-رو-اینجا-بذار"
BOT_TOKEN = os.environ.get("BOT_TOKEN", BOT_TOKEN)

ADMIN_IDS_RAW = "آیدی-عددی-ادمین-رو-اینجا-بذار"
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", ADMIN_IDS_RAW)
ADMIN_IDS = {int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


START_CASH = 50_000
START_BANK = 0
ENERGY_MAX = 100
HUNGER_MAX = 100
SLEEP_MAX = 100
HAPPINESS_MAX = 100
STRESS_MAX = 100
WORK_COOLDOWN_MIN = 30          # فاصله بین دو شیفت کاری (دقیقه، شبیه‌سازی‌شده با timestamp)
TRAIN_COOLDOWN_MIN = 20
SLEEP_COOLDOWN_MIN = 60
LOAN_INTEREST = 0.08            # ۸ درصد سود وام
LOAN_MAX_MULTIPLIER = 3         # سقف وام بر اساس امتیاز اعتبار

# ----------------------------------------------------------------------------
# داده‌های ثابت بازی (Static Game Data)
# ----------------------------------------------------------------------------

SKILLS = {
    "programming": "برنامه‌نویسی",
    "medicine": "پزشکی",
    "management": "مدیریت",
    "business": "تجارت",
    "driving": "رانندگی",
    "cooking": "آشپزی",
    "farming": "کشاورزی",
    "art": "هنر",
    "investing": "سرمایه‌گذاری",
}

# هر شغل: نیازمندی سطح مهارت، حقوق پایه هر شیفت، هزینه انرژی، XP
JOBS = {
    "worker":    {"name": "کارگر",        "skill": None,          "min_level": 0, "pay": 1200,  "energy": 20, "xp": 5,  "stress": 8},
    "seller":    {"name": "فروشنده",       "skill": "business",   "min_level": 0, "pay": 1800,  "energy": 15, "xp": 8,  "stress": 6},
    "driver":    {"name": "راننده",        "skill": "driving",    "min_level": 1, "pay": 2200,  "energy": 25, "xp": 10, "stress": 10},
    "cook":      {"name": "آشپز",          "skill": "cooking",    "min_level": 1, "pay": 2500,  "energy": 20, "xp": 10, "stress": 9},
    "teacher":   {"name": "معلم",          "skill": "management", "min_level": 2, "pay": 3200,  "energy": 18, "xp": 14, "stress": 7},
    "nurse":     {"name": "پرستار",        "skill": "medicine",   "min_level": 2, "pay": 3600,  "energy": 25, "xp": 15, "stress": 15},
    "engineer":  {"name": "مهندس",         "skill": "programming","min_level": 3, "pay": 5200,  "energy": 22, "xp": 20, "stress": 12},
    "developer": {"name": "برنامه‌نویس",    "skill": "programming","min_level": 4, "pay": 7000,  "energy": 20, "xp": 25, "stress": 14},
    "doctor":    {"name": "پزشک",          "skill": "medicine",   "min_level": 5, "pay": 9500,  "energy": 30, "xp": 30, "stress": 20},
    "manager":   {"name": "مدیر",          "skill": "management", "min_level": 5, "pay": 11000, "energy": 25, "xp": 30, "stress": 22},
    "investor":  {"name": "سرمایه‌گذار",   "skill": "investing",  "min_level": 6, "pay": 15000, "energy": 15, "xp": 35, "stress": 18},
}

# حداقل مدرک تحصیلی لازم برای بعضی مشاغل (سطح تحصیلات: تعریفش پایین‌تر در EDU_LEVELS اومده)
JOB_EDU_MIN = {
    "teacher": 1, "nurse": 1, "engineer": 2, "developer": 2,
    "doctor": 3, "manager": 2, "investor": 2,
}

# سطح مهارت لازم برای هر شغل به معنیِ سطح مهارت مرتبط (نه سطح کلی کاربر)
PROPERTIES = {
    "land_small":   {"name": "زمین کوچک",        "price": 80_000,    "income": 0,    "type": "زمین"},
    "apartment":    {"name": "آپارتمان",          "price": 250_000,   "income": 400,  "type": "مسکونی"},
    "house":        {"name": "خانه ویلایی",       "price": 600_000,   "income": 900,  "type": "مسکونی"},
    "shop":         {"name": "مغازه",             "price": 350_000,   "income": 700,  "type": "تجاری"},
    "villa":        {"name": "ویلا",              "price": 1_200_000, "income": 1500, "type": "مسکونی لوکس"},
    "factory":      {"name": "کارخانه",           "price": 3_000_000, "income": 5000, "type": "صنعتی"},
    "hotel":        {"name": "هتل",               "price": 5_000_000, "income": 9000, "type": "تجاری لوکس"},
}

VEHICLES = {
    "bike":       {"name": "دوچرخه",      "price": 3_000,      "happiness": 2},
    "motorcycle": {"name": "موتور",       "price": 25_000,     "happiness": 5},
    "car":        {"name": "ماشین",       "price": 180_000,    "happiness": 10},
    "luxury_car": {"name": "ماشین لوکس",  "price": 900_000,    "happiness": 20},
    "truck":      {"name": "کامیون",      "price": 400_000,    "happiness": 8},
    "boat":       {"name": "قایق",        "price": 1_500_000,  "happiness": 25},
    "helicopter": {"name": "هلیکوپتر",    "price": 6_000_000,  "happiness": 40},
    "airplane":   {"name": "هواپیما",     "price": 20_000_000, "happiness": 60},
}

DAILY_MISSIONS = [
    {"key": "work_3", "desc": "۳ بار کار کن", "target": 3, "reward_cash": 3000, "reward_xp": 15},
    {"key": "train_2", "desc": "۲ بار مهارت تمرین کن", "target": 2, "reward_cash": 2000, "reward_xp": 20},
    {"key": "sleep_1", "desc": "۱ بار بخواب", "target": 1, "reward_cash": 1000, "reward_xp": 5},
    {"key": "eat_2", "desc": "۲ بار غذا بخور", "target": 2, "reward_cash": 1500, "reward_xp": 8},
]

# آستانه‌های سطح‌بندی بر اساس دارایی خالص (Net Worth)
LEVEL_THRESHOLDS = [
    (0, "تازه‌وارد"),
    (50_000, "کارگر ساده"),
    (200_000, "شهروند معمولی"),
    (600_000, "شهروند فعال"),
    (1_500_000, "متمول"),
    (4_000_000, "ثروتمند"),
    (10_000_000, "الیت شهر"),
    (30_000_000, "میلیاردر"),
    (100_000_000, "افسانه"),
]

# ----------------------------------------------------------------------------
# لایه دیتابیس
# ----------------------------------------------------------------------------

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                bio TEXT DEFAULT 'یک شهروند تازه‌وارد',
                avatar TEXT DEFAULT '🙂',
                cash INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                credit_score INTEGER DEFAULT 500,
                age INTEGER DEFAULT 18,
                energy INTEGER DEFAULT 100,
                health INTEGER DEFAULT 100,
                happiness INTEGER DEFAULT 100,
                stress INTEGER DEFAULT 0,
                hunger INTEGER DEFAULT 0,
                sleep INTEGER DEFAULT 100,
                intelligence INTEGER DEFAULT 10,
                xp INTEGER DEFAULT 0,
                job TEXT DEFAULT NULL,
                loan_amount INTEGER DEFAULT 0,
                last_work REAL DEFAULT 0,
                last_train REAL DEFAULT 0,
                last_sleep REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                married_to INTEGER DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS user_skills (
                user_id INTEGER,
                skill TEXT,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, skill),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS user_properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prop_key TEXT,
                bought_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS user_vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                veh_key TEXT,
                bought_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS friendships (
                user_id INTEGER,
                friend_id INTEGER,
                PRIMARY KEY (user_id, friend_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                amount INTEGER,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_missions (
                user_id INTEGER,
                mission_key TEXT,
                progress INTEGER DEFAULT 0,
                claimed INTEGER DEFAULT 0,
                day TEXT,
                PRIMARY KEY (user_id, mission_key, day)
            );
            """
        )


def get_user(user_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def ensure_user(user_id: int, username: str):
    u = get_user(user_id)
    if u:
        return u
    with db() as conn:
        card_number = _generate_card_number(conn)
        conn.execute(
            "INSERT INTO users (user_id, username, cash, bank, card_number) VALUES (?,?,?,?,?)",
            (user_id, username, START_CASH, START_BANK, card_number),
        )
        for skill in SKILLS:
            conn.execute(
                "INSERT INTO user_skills (user_id, skill, level, xp) VALUES (?,?,1,0)",
                (user_id, skill),
            )
    return get_user(user_id)


def update_user(user_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with db() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE user_id=?", vals)


def log_tx(user_id: int, kind: str, amount: int, note: str = ""):
    with db() as conn:
        conn.execute(
            "INSERT INTO transactions (user_id, kind, amount, note) VALUES (?,?,?,?)",
            (user_id, kind, amount, note),
        )


def get_skill(user_id: int, skill: str):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM user_skills WHERE user_id=? AND skill=?", (user_id, skill)
        ).fetchone()
        return dict(row) if row else {"level": 1, "xp": 0}


def add_skill_xp(user_id: int, skill: str, amount: int):
    s = get_skill(user_id, skill)
    new_xp = s["xp"] + amount
    new_level = s["level"]
    needed = new_level * 100
    leveled_up = False
    while new_xp >= needed:
        new_xp -= needed
        new_level += 1
        needed = new_level * 100
        leveled_up = True
    with db() as conn:
        conn.execute(
            "UPDATE user_skills SET level=?, xp=? WHERE user_id=? AND skill=?",
            (new_level, new_xp, user_id, skill),
        )
    return new_level, leveled_up


def net_worth(u: dict) -> int:
    with db() as conn:
        props = conn.execute(
            "SELECT prop_key FROM user_properties WHERE user_id=?", (u["user_id"],)
        ).fetchall()
        vehs = conn.execute(
            "SELECT veh_key FROM user_vehicles WHERE user_id=?", (u["user_id"],)
        ).fetchall()
    prop_value = sum(PROPERTIES[p["prop_key"]]["price"] for p in props if p["prop_key"] in PROPERTIES)
    veh_value = sum(VEHICLES[v["veh_key"]]["price"] for v in vehs if v["veh_key"] in VEHICLES)
    return u["cash"] + u["bank"] + prop_value + veh_value - u["loan_amount"]


def level_for_networth(nw: int):
    title = LEVEL_THRESHOLDS[0][1]
    idx = 0
    for i, (threshold, name) in enumerate(LEVEL_THRESHOLDS):
        if nw >= threshold:
            title = name
            idx = i
        else:
            break
    return idx, title


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def today_str():
    return date.today().isoformat()


# ----------------------------------------------------------------------------
# ابزارهای کمکی برای ماموریت روزانه
# ----------------------------------------------------------------------------

def bump_mission(user_id: int, mission_key: str, amount: int = 1):
    day = today_str()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_missions WHERE user_id=? AND mission_key=? AND day=?",
            (user_id, mission_key, day),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE daily_missions SET progress=progress+? WHERE user_id=? AND mission_key=? AND day=?",
                (amount, user_id, mission_key, day),
            )
        else:
            conn.execute(
                "INSERT INTO daily_missions (user_id, mission_key, progress, day) VALUES (?,?,?,?)",
                (user_id, mission_key, amount, day),
            )


def get_missions_today(user_id: int):
    day = today_str()
    with db() as conn:
        rows = {
            r["mission_key"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM daily_missions WHERE user_id=? AND day=?", (user_id, day)
            ).fetchall()
        }
    result = []
    for m in DAILY_MISSIONS:
        state = rows.get(m["key"], {"progress": 0, "claimed": 0})
        result.append({**m, "progress": state["progress"], "claimed": state["claimed"]})
    return result


# ----------------------------------------------------------------------------
# دستورات بات (Handlers)
# ----------------------------------------------------------------------------

def fmt_money(n: int) -> str:
    return f"{n:,} تومان"


def md_escape(text) -> str:
    """فرار دادن کاراکترهای خاص Markdown (legacy) قبل از قرار دادن متن دلخواه (مثل یوزرنیم) داخل پیام‌های بولد/کد."""
    text = str(text)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def stat_bar(value, max_value=100, length=10):
    value = clamp(value, 0, max_value)
    filled = round((value / max_value) * length) if max_value else 0
    return "▰" * filled + "▱" * (length - filled)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    u = ensure_user(tg_user.id, tg_user.username or tg_user.first_name)
    if not await enforce_maintenance(update, context):
        return
    if not await enforce_force_join(update, context):
        return
    idx, title = level_for_networth(net_worth(u))
    text = (
        f"🎮 *به شبیه‌ساز زندگی خوش اومدی، {md_escape(tg_user.first_name)}!*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"💰 پول نقد اولیه: *{fmt_money(u['cash'])}*\n"
        f"🏅 رتبه فعلی: *{title}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "همه‌چیز از طریق دکمه‌های زیر قابل‌انجامه 👇\n"
        "_(هر وقت خواستی این منو رو دوباره ببینی، کافیه /menu رو بزنی)_"
    )
    is_private = update.effective_chat.type == "private"
    sent = await update.message.reply_text(
        text, reply_markup=main_menu_keyboard(tg_user.id, is_private), parse_mode="Markdown"
    )
    if not is_private:
        set_menu_owner(update.effective_chat.id, sent.message_id, tg_user.id)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 راهنمای دستورات\n\n"
        "👤 حساب کاربری:\n"
        "/start /profile /setbio <متن>\n\n"
        "💼 شغل و مهارت:\n"
        "/jobs — لیست شغل‌ها\n"
        "/apply <کد شغل> — درخواست استخدام\n"
        "/work — کار کن و حقوق بگیر\n"
        "/skills — مهارت‌های من\n"
        "/train <کد مهارت> — تمرین مهارت\n\n"
        "🏦 بانک:\n"
        "/bank — موجودی و اطلاعات بانکی\n"
        "/deposit <مبلغ> — واریز به بانک\n"
        "/withdraw <مبلغ> — برداشت از بانک\n"
        "/loan <مبلغ> — دریافت وام\n"
        "/payloan <مبلغ> — بازپرداخت وام\n\n"
        "🏠 دارایی:\n"
        "/shop — فروشگاه املاک و ماشین\n"
        "/buyprop <کد> — خرید ملک\n"
        "/buyveh <کد> — خرید وسیله نقلیه\n"
        "/myassets — دارایی‌های من\n\n"
        "❤️ زندگی روزمره:\n"
        "/sleep — استراحت (بازیابی انرژی/خواب)\n"
        "/eat — غذا خوردن (کاهش گرسنگی)\n\n"
        "🎯 سایر:\n"
        "/mission — ماموریت‌های روزانه\n"
        "/top — جدول رتبه‌بندی ثروتمندترین‌ها\n"
        "/give <ریپلای روی پیام فرد> <مبلغ> — هدیه پول\n"
    )
    await update.message.reply_text(text)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    u = ensure_user(tg_user.id, tg_user.username or tg_user.first_name)
    nw = net_worth(u)
    idx, title = level_for_networth(nw)
    job_name = get_job_label(u)
    text = (
        f"{u['avatar']} پروفایل {tg_user.first_name}\n"
        f"📝 بیو: {u['bio']}\n"
        f"🏅 رتبه: {title} (سطح {idx})\n"
        f"💵 پول نقد: {fmt_money(u['cash'])}\n"
        f"🏦 موجودی بانک: {fmt_money(u['bank'])}\n"
        f"📊 اعتبار بانکی: {u['credit_score']}\n"
        f"💎 دارایی خالص: {fmt_money(nw)}\n"
        f"💼 شغل: {job_name}\n"
        f"🎂 سن: {u['age']}\n"
        f"⚡ انرژی: {u['energy']}/{ENERGY_MAX}\n"
        f"❤️ سلامت: {u['health']}/100\n"
        f"😊 شادی: {u['happiness']}/100\n"
        f"😰 استرس: {u['stress']}/100\n"
        f"🍔 گرسنگی: {u['hunger']}/100\n"
        f"😴 خواب: {u['sleep']}/100\n"
        f"🧠 هوش: {u['intelligence']}\n"
        f"⭐ تجربه (XP): {u['xp']}\n"
    )
    if u["loan_amount"] > 0:
        text += f"💳 بدهی وام: {fmt_money(u['loan_amount'])}\n"
    await update.message.reply_text(text)


async def cmd_setbio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    bio = " ".join(context.args)
    if not bio:
        await update.message.reply_text("استفاده: /setbio متن بیو شما")
        return
    update_user(u["user_id"], bio=bio[:200])
    await update.message.reply_text("✅ بیو شما به‌روزرسانی شد.")


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["💼 لیست شغل‌های موجود:\n"]
    for key, j in JOBS.items():
        skill_txt = SKILLS.get(j["skill"], "بدون نیاز مهارتی") if j["skill"] else "بدون نیاز مهارتی"
        lines.append(
            f"• /apply {key} — {j['name']} | حقوق هر شیفت: {fmt_money(j['pay'])} | "
            f"نیاز: سطح {j['min_level']} مهارت {skill_txt}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /apply <کد شغل> — مثلا /apply driver")
        return
    key = context.args[0].lower()
    job = JOBS.get(key)
    if not job:
        await update.message.reply_text("چنین شغلی وجود نداره. با /jobs لیست شغل‌ها رو ببین.")
        return
    edu_min = JOB_EDU_MIN.get(key, 0)
    if edu_min > u.get("edu_level", 0):
        await update.message.reply_text(
            f"❌ برای این شغل به مدرک «{edu_name(edu_min)}» نیاز داری "
            f"(فعلی: {edu_name(u.get('edu_level', 0))}). با /study درس بخون."
        )
        return
    if job["skill"]:
        s = get_skill(u["user_id"], job["skill"])
        if s["level"] < job["min_level"]:
            await update.message.reply_text(
                f"❌ برای این شغل به سطح {job['min_level']} مهارت {SKILLS[job['skill']]} نیاز داری "
                f"(سطح فعلی تو: {s['level']}). با /train {job['skill']} تمرین کن."
            )
            return
    update_user(u["user_id"], job=key)
    await update.message.reply_text(f"✅ استخدام شدی! شغل جدید: {job['name']}\nحالا می‌تونی با /work کار کنی.")


async def cmd_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not u["job"]:
        await update.message.reply_text("❌ اول باید استخدام بشی. لیست شغل‌ها: /jobs")
        return
    if u.get("jailed_until", 0) and u["jailed_until"] > time.time():
        remain = int((u["jailed_until"] - time.time()) / 60) + 1
        await update.message.reply_text(f"🚔 تو در زندانی و {remain} دقیقه دیگه آزاد می‌شی. برای آزادی زودتر: /bail <مبلغ>")
        return

    job = JOBS[u["job"]]
    now = time.time()
    elapsed_min = (now - u["last_work"]) / 60
    if elapsed_min < WORK_COOLDOWN_MIN:
        remain = int(WORK_COOLDOWN_MIN - elapsed_min)
        await update.message.reply_text(f"⏳ باید {remain} دقیقه دیگه صبر کنی تا بتونی دوباره کار کنی.")
        return
    if u["energy"] < job["energy"]:
        await update.message.reply_text("⚡ انرژی کافی نداری! با /sleep استراحت کن.")
        return

    # عملکرد بر اساس سطح مهارت مرتبط، تصادفی بودن جزئی برای واقعی‌تر شدن
    skill_level = 1
    if job["skill"]:
        skill_level = get_skill(u["user_id"], job["skill"])["level"]
    performance = random.uniform(0.9, 1.15) * (1 + 0.05 * (skill_level - 1))
    pay = int(job["pay"] * performance)

    new_energy = clamp(u["energy"] - job["energy"], 0, ENERGY_MAX)
    new_stress = clamp(u["stress"] + job["stress"], 0, STRESS_MAX)
    new_hunger = clamp(u["hunger"] + 8, 0, HUNGER_MAX)

    update_user(
        u["user_id"],
        cash=u["cash"] + pay,
        energy=new_energy,
        stress=new_stress,
        hunger=new_hunger,
        xp=u["xp"] + job["xp"],
        last_work=now,
    )
    log_tx(u["user_id"], "salary", pay, job["name"])
    bump_mission(u["user_id"], "work_3")
    bump_periodic_mission(u["user_id"], "w_work_15", period_id_week())
    bump_periodic_mission(u["user_id"], "m_networth_up", period_id_month())

    level_up_note = ""
    if job["skill"]:
        new_level, leveled = add_skill_xp(u["user_id"], job["skill"], job["xp"])
        if leveled:
            level_up_note = f"\n🎉 مهارت {SKILLS[job['skill']]} به سطح {new_level} رسید!"

    await update.message.reply_text(
        f"🛠 یک شیفت کار کردی و {fmt_money(pay)} گرفتی.\n"
        f"⚡ انرژی: {new_energy}/{ENERGY_MAX} | 😰 استرس: {new_stress}{level_up_note}"
    )


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    lines = ["🧩 مهارت‌های شما:\n"]
    for key, name in SKILLS.items():
        s = get_skill(u["user_id"], key)
        needed = s["level"] * 100
        lines.append(f"• {name}: سطح {s['level']} (XP {s['xp']}/{needed})")
    lines.append("\nبرای تمرین: /train <کد مهارت>  — مثلا /train programming")
    await update.message.reply_text("\n".join(lines))


async def cmd_train(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /train <کد مهارت>. کدها: " + ", ".join(SKILLS.keys()))
        return
    skill = context.args[0].lower()
    if skill not in SKILLS:
        await update.message.reply_text("مهارت نامعتبره. کدها: " + ", ".join(SKILLS.keys()))
        return
    now = time.time()
    elapsed_min = (now - u["last_train"]) / 60
    if elapsed_min < TRAIN_COOLDOWN_MIN:
        remain = int(TRAIN_COOLDOWN_MIN - elapsed_min)
        await update.message.reply_text(f"⏳ {remain} دقیقه دیگه صبر کن.")
        return
    if u["energy"] < 15:
        await update.message.reply_text("⚡ انرژی کافی نداری.")
        return

    gained_xp = random.randint(8, 20) + u["intelligence"] // 5
    new_level, leveled = add_skill_xp(u["user_id"], skill, gained_xp)
    new_energy = clamp(u["energy"] - 15, 0, ENERGY_MAX)
    update_user(u["user_id"], energy=new_energy, last_train=now, intelligence=u["intelligence"] + 1)
    bump_mission(u["user_id"], "train_2")
    bump_periodic_mission(u["user_id"], "w_train_10", period_id_week())

    msg = f"📚 تمرین {SKILLS[skill]} انجام شد. +{gained_xp} XP مهارت."
    if leveled:
        msg += f"\n🎉 مهارت به سطح {new_level} رسید!"
    await update.message.reply_text(msg)


async def cmd_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    now = time.time()
    elapsed_min = (now - u["last_sleep"]) / 60
    if elapsed_min < SLEEP_COOLDOWN_MIN:
        remain = int(SLEEP_COOLDOWN_MIN - elapsed_min)
        await update.message.reply_text(f"⏳ هنوز خسته نیستی، {remain} دقیقه دیگه صبر کن.")
        return
    update_user(
        u["user_id"],
        energy=ENERGY_MAX,
        sleep=SLEEP_MAX,
        stress=clamp(u["stress"] - 30, 0, STRESS_MAX),
        last_sleep=now,
    )
    bump_mission(u["user_id"], "sleep_1")
    await update.message.reply_text("😴 خوب خوابیدی! انرژی و خواب کامل شد و استرس کم شد.")


async def cmd_eat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    cost = 500
    if u["cash"] < cost:
        await update.message.reply_text("💸 پول نقد کافی نداری (هزینه غذا: 500 تومان).")
        return
    update_user(
        u["user_id"],
        cash=u["cash"] - cost,
        hunger=clamp(u["hunger"] - 40, 0, HUNGER_MAX),
        happiness=clamp(u["happiness"] + 5, 0, HAPPINESS_MAX),
    )
    log_tx(u["user_id"], "food", -cost, "غذا")
    bump_mission(u["user_id"], "eat_2")
    await update.message.reply_text("🍔 غذا خوردی. گرسنگی کم شد و کمی شادتر شدی.")


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    max_loan = u["credit_score"] * get_loan_max_multiplier()
    await update.message.reply_text(
        f"🏦 وضعیت بانکی\n"
        f"💵 پول نقد: {fmt_money(u['cash'])}\n"
        f"🏦 موجودی بانک: {fmt_money(u['bank'])}\n"
        f"📊 امتیاز اعتبار: {u['credit_score']}\n"
        f"💳 بدهی وام فعلی: {fmt_money(u['loan_amount'])}\n"
        f"📈 سقف وام قابل دریافت: {fmt_money(max_loan)}\n\n"
        "دستورات: /deposit <مبلغ> — /withdraw <مبلغ> — /loan <مبلغ> — /payloan <مبلغ>"
    )


async def cmd_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /deposit <مبلغ>")
        return
    if amount > u["cash"]:
        await update.message.reply_text("پول نقد کافی نداری.")
        return
    update_user(u["user_id"], cash=u["cash"] - amount, bank=u["bank"] + amount)
    await update.message.reply_text(f"✅ {fmt_money(amount)} به بانک واریز شد.")


async def cmd_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /withdraw <مبلغ>")
        return
    if amount > u["bank"]:
        await update.message.reply_text("موجودی بانکی کافی نیست.")
        return
    update_user(u["user_id"], cash=u["cash"] + amount, bank=u["bank"] - amount)
    await update.message.reply_text(f"✅ {fmt_money(amount)} برداشت شد.")


async def cmd_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /loan <مبلغ>")
        return
    max_loan = u["credit_score"] * get_loan_max_multiplier()
    if u["loan_amount"] + amount > max_loan:
        await update.message.reply_text(f"❌ سقف وام قابل دریافت تو {fmt_money(max_loan)} است.")
        return
    total_due = int(amount * (1 + LOAN_INTEREST))
    update_user(
        u["user_id"],
        cash=u["cash"] + amount,
        loan_amount=u["loan_amount"] + total_due,
        credit_score=max(300, u["credit_score"] - 15),
    )
    log_tx(u["user_id"], "loan", amount, "دریافت وام")
    await update.message.reply_text(
        f"💰 وام {fmt_money(amount)} دریافت شد.\nبدهی با سود: {fmt_money(total_due)}\n"
        "توجه: امتیاز اعتباری تو کمی کاهش یافت تا وام رو بازپرداخت کنی."
    )


async def cmd_payloan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /payloan <مبلغ>")
        return
    if amount > u["cash"]:
        await update.message.reply_text("پول نقد کافی نداری.")
        return
    pay = min(amount, u["loan_amount"])
    update_user(
        u["user_id"],
        cash=u["cash"] - pay,
        loan_amount=u["loan_amount"] - pay,
        credit_score=min(900, u["credit_score"] + 10),
    )
    log_tx(u["user_id"], "loan_payment", -pay, "بازپرداخت وام")
    remain = u["loan_amount"] - pay
    msg = f"✅ {fmt_money(pay)} بازپرداخت شد."
    if remain <= 0:
        msg += "\n🎉 وام تو کاملاً تسویه شد و امتیاز اعتباری‌ات بهتر شد!"
    await update.message.reply_text(msg)


def _parse_amount(args):
    if not args:
        return None
    try:
        return int(args[0].replace(",", ""))
    except ValueError:
        return None


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🏠 فروشگاه املاک:\n"]
    for key, p in PROPERTIES.items():
        income_txt = f" | درآمد غیرفعال: {fmt_money(p['income'])}/شیفت" if p["income"] else ""
        lines.append(f"• /buyprop {key} — {p['name']} ({p['type']}) — {fmt_money(p['price'])}{income_txt}")
    lines.append("\n🚗 فروشگاه وسایل نقلیه:\n")
    for key, v in VEHICLES.items():
        lines.append(f"• /buyveh {key} — {v['name']} — {fmt_money(v['price'])} (+{v['happiness']} شادی)")
    await update.message.reply_text("\n".join(lines))


async def cmd_buyprop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /buyprop <کد ملک>")
        return
    key = context.args[0].lower()
    prop = PROPERTIES.get(key)
    if not prop:
        await update.message.reply_text("چنین ملکی وجود نداره. با /shop لیست رو ببین.")
        return
    total = u["cash"] + u["bank"]
    if total < prop["price"]:
        await update.message.reply_text(f"❌ دارایی کافی نداری. قیمت: {fmt_money(prop['price'])}")
        return
    # اول از پول نقد کم می‌کنه، بعد از بانک
    from_cash = min(u["cash"], prop["price"])
    from_bank = prop["price"] - from_cash
    with db() as conn:
        conn.execute(
            "UPDATE users SET cash=cash-?, bank=bank-? WHERE user_id=?",
            (from_cash, from_bank, u["user_id"]),
        )
        conn.execute(
            "INSERT INTO user_properties (user_id, prop_key) VALUES (?,?)", (u["user_id"], key)
        )
    log_tx(u["user_id"], "buy_property", -prop["price"], prop["name"])
    bump_periodic_mission(u["user_id"], "m_property", period_id_month())
    await update.message.reply_text(f"🏠 تبریک! {prop['name']} رو خریدی.")


async def cmd_buyveh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /buyveh <کد وسیله نقلیه>")
        return
    key = context.args[0].lower()
    veh = VEHICLES.get(key)
    if not veh:
        await update.message.reply_text("چنین وسیله‌ای وجود نداره. با /shop لیست رو ببین.")
        return
    total = u["cash"] + u["bank"]
    if total < veh["price"]:
        await update.message.reply_text(f"❌ دارایی کافی نداری. قیمت: {fmt_money(veh['price'])}")
        return
    from_cash = min(u["cash"], veh["price"])
    from_bank = veh["price"] - from_cash
    with db() as conn:
        conn.execute(
            "UPDATE users SET cash=cash-?, bank=bank-?, happiness=MIN(100, happiness+?) WHERE user_id=?",
            (from_cash, from_bank, veh["happiness"], u["user_id"]),
        )
        conn.execute("INSERT INTO user_vehicles (user_id, veh_key) VALUES (?,?)", (u["user_id"], key))
    log_tx(u["user_id"], "buy_vehicle", -veh["price"], veh["name"])
    bump_periodic_mission(u["user_id"], "m_property", period_id_month())
    await update.message.reply_text(f"🚗 تبریک! {veh['name']} رو خریدی.")


async def cmd_myassets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        props = conn.execute(
            "SELECT prop_key FROM user_properties WHERE user_id=?", (u["user_id"],)
        ).fetchall()
        vehs = conn.execute(
            "SELECT veh_key FROM user_vehicles WHERE user_id=?", (u["user_id"],)
        ).fetchall()
    lines = ["🏠 املاک شما:"]
    if props:
        for p in props:
            info = PROPERTIES.get(p["prop_key"])
            if info:
                lines.append(f"• {info['name']} — ارزش {fmt_money(info['price'])}")
    else:
        lines.append("چیزی نداری.")
    lines.append("\n🚗 وسایل نقلیه شما:")
    if vehs:
        for v in vehs:
            info = VEHICLES.get(v["veh_key"])
            if info:
                lines.append(f"• {info['name']} — ارزش {fmt_money(info['price'])}")
    else:
        lines.append("چیزی نداری.")
    await update.message.reply_text("\n".join(lines))


async def cmd_mission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    missions = get_missions_today(u["user_id"])
    lines = ["🎯 ماموریت‌های امروز:\n"]
    for m in missions:
        status = "✅ انجام شد" if m["progress"] >= m["target"] else f"{m['progress']}/{m['target']}"
        claimed = " (دریافت شده)" if m["claimed"] else ""
        lines.append(f"• {m['desc']} — {status}{claimed}")
    lines.append("\nبرای دریافت جایزه: /claim <کد ماموریت> — کدها: " + ", ".join(m["key"] for m in DAILY_MISSIONS))
    await update.message.reply_text("\n".join(lines))


async def cmd_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /claim <کد ماموریت>")
        return
    key = context.args[0]
    mission_def = next((m for m in DAILY_MISSIONS if m["key"] == key), None)
    if not mission_def:
        await update.message.reply_text("ماموریت نامعتبره.")
        return
    day = today_str()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_missions WHERE user_id=? AND mission_key=? AND day=?",
            (u["user_id"], key, day),
        ).fetchone()
    progress = row["progress"] if row else 0
    claimed = row["claimed"] if row else 0
    if claimed:
        await update.message.reply_text("قبلاً جایزه این ماموریت رو گرفتی.")
        return
    if progress < mission_def["target"]:
        await update.message.reply_text("هنوز این ماموریت رو کامل نکردی.")
        return
    with db() as conn:
        conn.execute(
            "UPDATE daily_missions SET claimed=1 WHERE user_id=? AND mission_key=? AND day=?",
            (u["user_id"], key, day),
        )
    update_user(u["user_id"], cash=u["cash"] + mission_def["reward_cash"], xp=u["xp"] + mission_def["reward_xp"])
    log_tx(u["user_id"], "mission_reward", mission_def["reward_cash"], mission_def["desc"])
    await update.message.reply_text(
        f"🎉 جایزه دریافت شد: {fmt_money(mission_def['reward_cash'])} + {mission_def['reward_xp']} XP"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    users = [dict(r) for r in rows]
    ranked = sorted(users, key=lambda u: net_worth(u), reverse=True)[:10]
    lines = ["🏆 جدول رتبه‌بندی ثروتمندترین شهروندان:\n"]
    for i, u in enumerate(ranked, 1):
        name = u["username"] or f"کاربر {u['user_id']}"
        lines.append(f"{i}. {name} — {fmt_money(net_worth(u))}")
    if not ranked:
        lines.append("هنوز کسی بازی نکرده!")
    await update.message.reply_text("\n".join(lines))


async def cmd_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("برای هدیه دادن، روی پیام فرد مقصد ریپلای کن و بنویس: /give <مبلغ>")
        return
    target = update.message.reply_to_message.from_user
    if target.id == u["user_id"]:
        await update.message.reply_text("نمی‌تونی به خودت هدیه بدی!")
        return
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: (ریپلای) /give <مبلغ>")
        return
    if amount > u["cash"]:
        await update.message.reply_text("پول نقد کافی نداری.")
        return
    ensure_user(target.id, target.username or target.first_name)
    target_u = get_user(target.id)
    update_user(u["user_id"], cash=u["cash"] - amount)
    update_user(target.id, cash=target_u["cash"] + amount)
    log_tx(u["user_id"], "gift_sent", -amount, f"به {target.first_name}")
    log_tx(target.id, "gift_received", amount, f"از {update.effective_user.first_name}")
    await update.message.reply_text(f"🎁 {fmt_money(amount)} به {target.first_name} هدیه دادی!")


# ----------------------------------------------------------------------------
# Job قدیمی: افت طبیعی وضعیت شخصیت با گذر زمان (شبیه‌سازی گذر واقعیِ زندگی)
# ----------------------------------------------------------------------------

async def periodic_decay(context: ContextTypes.DEFAULT_TYPE):
    """هر چند دقیقه یک بار روی همه کاربران اجرا می‌شه: گرسنگی بالا می‌ره،
    خواب و انرژی کم می‌شه، در صورت غفلت شادی افت می‌کنه."""
    with db() as conn:
        rows = conn.execute("SELECT user_id, hunger, sleep, energy, happiness, health, stress FROM users").fetchall()
        for r in rows:
            new_hunger = clamp(r["hunger"] + 2, 0, HUNGER_MAX)
            new_sleep = clamp(r["sleep"] - 2, 0, SLEEP_MAX)
            new_happiness = clamp(r["happiness"] - 1, 0, HAPPINESS_MAX)
            new_health = r["health"]
            if new_hunger >= 90 or new_sleep <= 10:
                new_health = clamp(r["health"] - 3, 0, 100)
            conn.execute(
                "UPDATE users SET hunger=?, sleep=?, happiness=?, health=? WHERE user_id=?",
                (new_hunger, new_sleep, new_happiness, new_health, r["user_id"]),
            )


# ==============================================================================
# فاز ۲ — سیستم‌های پیشرفته (شرکت‌ها، بازار آزاد، بورس، ازدواج/خانواده، شهرها،
# جرم و قانون، رویدادهای جهانی، سرگرمی‌ها، مشاور هوش مصنوعی، VIP، پنل مدیریت)
# ==============================================================================

# -*- coding: utf-8 -*-


GROUP_TYPES = ("group", "supergroup")

COMPANY_COST = 500_000
JAIL_BASE_SECONDS = 10 * 60  # ۱۰ دقیقه پایه برای زندان

# ----------------------------------------------------------------------------
# دیتابیس فاز ۲
# ----------------------------------------------------------------------------

def init_extra_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                name TEXT,
                capital INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS company_employees (
                company_id INTEGER,
                user_id INTEGER,
                salary INTEGER DEFAULT 2000,
                PRIMARY KEY (company_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER,
                item_name TEXT,
                price INTEGER,
                quantity INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS stocks (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                price INTEGER
            );
            CREATE TABLE IF NOT EXISTS user_stocks (
                user_id INTEGER,
                symbol TEXT,
                qty INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, symbol)
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposer_id INTEGER,
                target_id INTEGER,
                kind TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS families (
                user_id INTEGER PRIMARY KEY,
                spouse_id INTEGER,
                children INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cities (
                chat_id INTEGER PRIMARY KEY,
                name TEXT,
                mayor_id INTEGER,
                budget INTEGER DEFAULT 0,
                score INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS elections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS election_candidates (
                election_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (election_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS election_votes (
                election_id INTEGER,
                voter_id INTEGER,
                candidate_id INTEGER,
                PRIMARY KEY (election_id, voter_id)
            );
            CREATE TABLE IF NOT EXISTS crime_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                target_id INTEGER,
                kind TEXT,
                amount INTEGER DEFAULT 0,
                caught INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                target_id INTEGER,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                reviewed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS world_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                event TEXT DEFAULT 'normal',
                description TEXT DEFAULT 'اوضاع اقتصاد عادیه.',
                pay_multiplier REAL DEFAULT 1.0,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO world_state (id) VALUES (1)")
        # سهام پایه‌ی چند شرکت فرضی بزرگ برای شروع بورس
        default_stocks = [
            ("TAK", "شرکت فناوری تاک", 12_000),
            ("PRD", "هلدینگ پارادایس", 8_500),
            ("ENR", "انرژی نوین", 5_000),
            ("AGR", "کشاورزی سبز", 2_200),
            ("BLD", "ساختمانی بنا", 15_000),
        ]
        for sym, name, price in default_stocks:
            conn.execute(
                "INSERT OR IGNORE INTO stocks (symbol, name, price) VALUES (?,?,?)",
                (sym, name, price),
            )
    # ستون‌های اضافی روی جدول users (اگر قبلاً اضافه نشده باشن، خطا رو نادیده می‌گیریم)
    extra_columns = [
        ("jailed_until", "REAL DEFAULT 0"),
        ("banned", "INTEGER DEFAULT 0"),
    ]
    with db() as conn:
        for col, decl in extra_columns:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
            except Exception:
                pass  # ستون از قبل وجود داره


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_jailed(u: dict) -> bool:
    return bool(
        (u.get("jailed_until", 0) and u["jailed_until"] > time.time())
        or (u.get("detention_until", 0) and u["detention_until"] > time.time())
        or (u.get("prison_until", 0) and u["prison_until"] > time.time())
    )


def require_not_jailed(u: dict):
    now = time.time()
    if u.get("prison_until", 0) and u["prison_until"] > now:
        remain = int((u["prison_until"] - now) / 60) + 1
        return f"🔒 تو توی زندانی و {remain} دقیقه دیگه آزاد می‌شی."
    if u.get("detention_until", 0) and u["detention_until"] > now:
        remain = int((u["detention_until"] - now) / 60) + 1
        return f"🚔 تو توی بازداشتگاهی و {remain} دقیقه دیگه آزاد می‌شی."
    if is_jailed(u):
        remain = int((u["jailed_until"] - now) / 60) + 1
        return f"🚔 تو در زندانی و {remain} دقیقه دیگه آزاد می‌شی. برای آزادی زودتر: /bail <مبلغ>"
    return None


def require_not_banned(u: dict):
    return u.get("banned", 0) == 1


# ----------------------------------------------------------------------------
# ۱. شرکت‌ها
# ----------------------------------------------------------------------------

async def cmd_createcompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    name = " ".join(context.args)
    if not name:
        await update.message.reply_text(f"استفاده: /createcompany <نام شرکت>\nهزینه ثبت: {fmt_money(COMPANY_COST)}")
        return
    if u["cash"] < COMPANY_COST:
        await update.message.reply_text(f"❌ پول نقد کافی نداری. هزینه: {fmt_money(COMPANY_COST)}")
        return
    with db() as conn:
        existing = conn.execute("SELECT id FROM companies WHERE owner_id=?", (u["user_id"],)).fetchone()
        if existing:
            await update.message.reply_text("تو قبلاً یک شرکت داری. هر کاربر فقط یک شرکت می‌تونه ثبت کنه.")
            return
        conn.execute(
            "INSERT INTO companies (owner_id, name, capital) VALUES (?,?,?)",
            (u["user_id"], name[:50], 0),
        )
    update_user(u["user_id"], cash=u["cash"] - COMPANY_COST)
    log_tx(u["user_id"], "create_company", -COMPANY_COST, name)
    await update.message.reply_text(f"🏢 شرکت «{name}» ثبت شد! با /companyinvest سرمایه اضافه کن و با /hire (ریپلای) استخدام کن.")


async def cmd_mycompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (u["user_id"],)).fetchone()
        if not c:
            await update.message.reply_text("شرکتی نداری. با /createcompany <نام> یکی بساز.")
            return
        employees = conn.execute(
            "SELECT * FROM company_employees WHERE company_id=?", (c["id"],)
        ).fetchall()
    lines = [
        f"🏢 شرکت: {c['name']}",
        f"💰 سرمایه: {fmt_money(c['capital'])}",
        f"📈 سطح: {c['level']}",
        f"👥 کارمندان: {len(employees)}",
    ]
    for e in employees:
        emp_u = get_user(e["user_id"])
        name = emp_u["username"] if emp_u else str(e["user_id"])
        lines.append(f"  • {name} — حقوق: {fmt_money(e['salary'])}")
    await update.message.reply_text("\n".join(lines))


async def cmd_companyinvest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /companyinvest <مبلغ>")
        return
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (u["user_id"],)).fetchone()
        if not c:
            await update.message.reply_text("اول باید شرکت بسازی: /createcompany <نام>")
            return
        if amount > u["cash"]:
            await update.message.reply_text("پول نقد کافی نداری.")
            return
        new_capital = c["capital"] + amount
        new_level = 1 + new_capital // 1_000_000
        conn.execute("UPDATE companies SET capital=?, level=? WHERE id=?", (new_capital, new_level, c["id"]))
    update_user(u["user_id"], cash=u["cash"] - amount)
    await update.message.reply_text(f"✅ {fmt_money(amount)} سرمایه‌ی شرکت اضافه شد. سرمایه‌ی فعلی: {fmt_money(new_capital)}")


async def cmd_hire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام فردی که می‌خوای استخدام کنی ریپلای کن: (ریپلای) /hire <حقوق>")
        return
    target = update.message.reply_to_message.from_user
    salary = _parse_amount(context.args) or 2000
    ensure_user(target.id, target.username or target.first_name)  # قبل از باز کردن تراکنش دیگه، تا قفل نگیره
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (u["user_id"],)).fetchone()
        if not c:
            await update.message.reply_text("اول باید شرکت بسازی: /createcompany <نام>")
            return
        conn.execute(
            "INSERT OR REPLACE INTO company_employees (company_id, user_id, salary) VALUES (?,?,?)",
            (c["id"], target.id, salary),
        )
    await update.message.reply_text(f"✅ {target.first_name} با حقوق {fmt_money(salary)} استخدام شد.")


async def cmd_payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (u["user_id"],)).fetchone()
        if not c:
            await update.message.reply_text("شرکتی نداری.")
            return
        employees = conn.execute("SELECT * FROM company_employees WHERE company_id=?", (c["id"],)).fetchall()
        total_salary = sum(e["salary"] for e in employees)
        if total_salary > c["capital"]:
            await update.message.reply_text(
                f"❌ سرمایه‌ی شرکت کافی نیست. لازم: {fmt_money(total_salary)}، موجودی: {fmt_money(c['capital'])}"
            )
            return
        conn.execute("UPDATE companies SET capital=capital-? WHERE id=?", (total_salary, c["id"]))
    # پرداخت حقوق هر کارمند بعد از بسته‌شدن تراکنش بالا انجام می‌شه تا دیتابیس قفل نگیره
    for e in employees:
        emp = get_user(e["user_id"])
        if emp:
            update_user(e["user_id"], cash=emp["cash"] + e["salary"])
    await update.message.reply_text(f"✅ حقوق {len(employees)} کارمند پرداخت شد. مجموع: {fmt_money(total_salary)}")


async def cmd_companies_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM companies ORDER BY capital DESC LIMIT 10").fetchall()
    lines = ["🏆 بزرگ‌ترین شرکت‌ها:\n"]
    for i, c in enumerate(rows, 1):
        lines.append(f"{i}. {c['name']} — سرمایه: {fmt_money(c['capital'])} (سطح {c['level']})")
    if not rows:
        lines.append("هنوز شرکتی ثبت نشده.")
    await update.message.reply_text("\n".join(lines))


# ----------------------------------------------------------------------------
# ۲. بازار آزاد (قیمت‌گذاری توسط خود کاربران)
# ----------------------------------------------------------------------------

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if len(context.args) < 3:
        await update.message.reply_text("استفاده: /sell <نام کالا> <تعداد> <قیمت واحد>")
        return
    *name_parts, qty_s, price_s = context.args
    item_name = " ".join(name_parts)
    try:
        qty = int(qty_s)
        price = int(price_s)
    except ValueError:
        await update.message.reply_text("تعداد و قیمت باید عدد باشن.")
        return
    if qty <= 0 or price <= 0 or not item_name:
        await update.message.reply_text("مقادیر نامعتبر.")
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO market_listings (seller_id, item_name, price, quantity) VALUES (?,?,?,?)",
            (u["user_id"], item_name[:50], price, qty),
        )
    await update.message.reply_text(f"🛒 آگهی ثبت شد: {item_name} × {qty} به قیمت {fmt_money(price)} هرکدوم.")


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_listings WHERE quantity > 0 ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await update.message.reply_text("🛒 بازار خالیه. با /sell کالای خودت رو بفروش.")
        return
    lines = ["🛒 آگهی‌های بازار آزاد (قیمت توسط خود کاربران تعیین می‌شه):\n"]
    for r in rows:
        seller = get_user(r["seller_id"])
        seller_name = seller["username"] if seller else str(r["seller_id"])
        lines.append(
            f"#{r['id']} — {r['item_name']} × {r['quantity']} — {fmt_money(r['price'])}/عدد — فروشنده: {seller_name}"
        )
    lines.append("\nخرید: /buyitem <شماره آگهی> <تعداد>")
    await update.message.reply_text("\n".join(lines))


async def cmd_buyitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /buyitem <شماره آگهی> <تعداد>")
        return
    try:
        listing_id = int(context.args[0])
        qty = int(context.args[1])
    except ValueError:
        await update.message.reply_text("مقادیر نامعتبر.")
        return
    with db() as conn:
        listing = conn.execute("SELECT * FROM market_listings WHERE id=?", (listing_id,)).fetchone()
        if not listing or listing["quantity"] <= 0:
            await update.message.reply_text("این آگهی وجود نداره یا موجودی‌ش تموم شده.")
            return
        if listing["seller_id"] == u["user_id"]:
            await update.message.reply_text("نمی‌تونی از خودت بخری!")
            return
        qty = min(qty, listing["quantity"])
        total = qty * listing["price"]
        if total > u["cash"]:
            await update.message.reply_text(f"پول نقد کافی نداری. مبلغ لازم: {fmt_money(total)}")
            return
        conn.execute("UPDATE market_listings SET quantity=quantity-? WHERE id=?", (qty, listing_id))
    # خواندن/نوشتنِ کاربر فروشنده بعد از بسته‌شدن تراکنش بالا انجام می‌شه تا دیتابیس قفل نگیره
    seller = get_user(listing["seller_id"])
    update_user(u["user_id"], cash=u["cash"] - total)
    if seller:
        update_user(seller["user_id"], cash=seller["cash"] + total)
    log_tx(u["user_id"], "market_buy", -total, listing["item_name"])
    await update.message.reply_text(f"✅ {qty} عدد {listing['item_name']} خریداری شد به مبلغ {fmt_money(total)}.")


async def cmd_mylistings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_listings WHERE seller_id=? AND quantity>0", (u["user_id"],)
        ).fetchall()
    if not rows:
        await update.message.reply_text("آگهی فعالی نداری.")
        return
    lines = ["📋 آگهی‌های شما:\n"]
    for r in rows:
        lines.append(f"#{r['id']} — {r['item_name']} × {r['quantity']} — {fmt_money(r['price'])}/عدد")
    lines.append("\nلغو: /cancellisting <شماره>")
    await update.message.reply_text("\n".join(lines))


async def cmd_cancellisting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("استفاده: /cancellisting <شماره آگهی>")
        return
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شماره نامعتبر.")
        return
    with db() as conn:
        listing = conn.execute(
            "SELECT * FROM market_listings WHERE id=? AND seller_id=?", (listing_id, u["user_id"])
        ).fetchone()
        if not listing:
            await update.message.reply_text("چنین آگهی‌ای پیدا نشد.")
            return
        conn.execute("UPDATE market_listings SET quantity=0 WHERE id=?", (listing_id,))
    await update.message.reply_text("✅ آگهی لغو شد.")


# ----------------------------------------------------------------------------
# ۳. بورس (نوسان بر اساس معاملات واقعی = مدل عرضه/تقاضای ساده)
# ----------------------------------------------------------------------------

STOCK_IMPACT = 0.01  # هر واحد خرید/فروش چقدر روی قیمت اثر بذاره


async def cmd_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM stocks ORDER BY asset_type, symbol").fetchall()
    labels = {"stock": "📈 سهام شرکت‌ها", "crypto": "🪙 ارز دیجیتال", "gold": "🥇 طلا"}
    sections = {}
    for r in rows:
        atype = r["asset_type"] if "asset_type" in r.keys() and r["asset_type"] else "stock"
        sections.setdefault(atype, []).append(r)
    lines = []
    for atype, label in labels.items():
        if atype not in sections:
            continue
        lines.append(f"{label}:")
        for r in sections[atype]:
            lines.append(f"• {r['symbol']} ({r['name']}) — {fmt_money(r['price'])}")
        lines.append("")
    lines.append("خرید: /buystock <نماد> <تعداد>\nفروش: /sellstock <نماد> <تعداد>\nپرتفوی: /mystocks")
    await update.message.reply_text("\n".join(lines))


async def cmd_buystock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /buystock <نماد> <تعداد>")
        return
    symbol = context.args[0].upper()
    try:
        qty = int(context.args[1])
    except ValueError:
        await update.message.reply_text("تعداد نامعتبر.")
        return
    if qty <= 0:
        await update.message.reply_text("تعداد باید مثبت باشه.")
        return
    with db() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        if not stock:
            await update.message.reply_text("چنین نمادی وجود نداره. با /stocks لیست رو ببین.")
            return
        total = qty * stock["price"]
        if total > u["cash"]:
            await update.message.reply_text(f"پول نقد کافی نداری. مبلغ لازم: {fmt_money(total)}")
            return
        new_price = int(stock["price"] * (1 + STOCK_IMPACT * qty))
        conn.execute("UPDATE stocks SET price=? WHERE symbol=?", (new_price, symbol))
        existing = conn.execute(
            "SELECT * FROM user_stocks WHERE user_id=? AND symbol=?", (u["user_id"], symbol)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE user_stocks SET qty=qty+? WHERE user_id=? AND symbol=?",
                (qty, u["user_id"], symbol),
            )
        else:
            conn.execute(
                "INSERT INTO user_stocks (user_id, symbol, qty) VALUES (?,?,?)",
                (u["user_id"], symbol, qty),
            )
    update_user(u["user_id"], cash=u["cash"] - total)
    log_tx(u["user_id"], "buy_stock", -total, symbol)
    await update.message.reply_text(
        f"✅ {qty} سهم {symbol} خریداری شد به مبلغ {fmt_money(total)}.\n"
        f"📈 قیمت جدید سهم (اثر تقاضا): {fmt_money(new_price)}"
    )


async def cmd_sellstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /sellstock <نماد> <تعداد>")
        return
    symbol = context.args[0].upper()
    try:
        qty = int(context.args[1])
    except ValueError:
        await update.message.reply_text("تعداد نامعتبر.")
        return
    with db() as conn:
        owned = conn.execute(
            "SELECT * FROM user_stocks WHERE user_id=? AND symbol=?", (u["user_id"], symbol)
        ).fetchone()
        if not owned or owned["qty"] < qty:
            await update.message.reply_text("این تعداد سهم رو نداری.")
            return
        stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        total = qty * stock["price"]
        new_price = max(100, int(stock["price"] * (1 - STOCK_IMPACT * qty)))
        conn.execute("UPDATE stocks SET price=? WHERE symbol=?", (new_price, symbol))
        conn.execute(
            "UPDATE user_stocks SET qty=qty-? WHERE user_id=? AND symbol=?", (qty, u["user_id"], symbol)
        )
    update_user(u["user_id"], cash=u["cash"] + total)
    log_tx(u["user_id"], "sell_stock", total, symbol)
    await update.message.reply_text(
        f"✅ {qty} سهم {symbol} فروخته شد به مبلغ {fmt_money(total)}.\n"
        f"📉 قیمت جدید سهم (اثر عرضه): {fmt_money(new_price)}"
    )


async def cmd_mystocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM user_stocks WHERE user_id=? AND qty>0", (u["user_id"],)
        ).fetchall()
    if not rows:
        await update.message.reply_text("سهامی نداری.")
        return
    lines = ["💼 پرتفوی سهام شما:\n"]
    with db() as conn:
        for r in rows:
            stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (r["symbol"],)).fetchone()
            value = r["qty"] * stock["price"] if stock else 0
            lines.append(f"• {r['symbol']} × {r['qty']} — ارزش فعلی: {fmt_money(value)}")
    await update.message.reply_text("\n".join(lines))


async def stock_drift_job(context: ContextTypes.DEFAULT_TYPE):
    """نوسان تصادفی کوچک روی سهام برای شبیه‌سازی بازار، مستقل از معاملات کاربران."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM stocks").fetchall()
        for r in rows:
            change = random.uniform(-0.03, 0.03)
            new_price = max(100, int(r["price"] * (1 + change)))
            conn.execute("UPDATE stocks SET price=? WHERE symbol=?", (new_price, r["symbol"]))


# ----------------------------------------------------------------------------
# ۴. ازدواج، خانواده و طلاق
# ----------------------------------------------------------------------------

async def cmd_propose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام فردی که می‌خوای بهش پیشنهاد ازدواج بدی ریپلای کن: (ریپلای) /propose")
        return
    target = update.message.reply_to_message.from_user
    if target.id == u["user_id"]:
        await update.message.reply_text("نمی‌تونی به خودت پیشنهاد بدی!")
        return
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (u["user_id"],)).fetchone()
        if fam and fam["spouse_id"]:
            await update.message.reply_text("تو الان متأهلی. اول باید /divorce کنی.")
            return
        conn.execute(
            "INSERT INTO proposals (proposer_id, target_id, kind) VALUES (?,?, 'marriage')",
            (u["user_id"], target.id),
        )
    ensure_user(target.id, target.username or target.first_name)
    await update.message.reply_text(
        f"💍 پیشنهاد ازدواج به {target.first_name} ارسال شد!\n"
        f"{target.first_name} می‌تونه با ریپلای‌کردن روی این پیام دستور /accept رو بزنه."
    )


async def cmd_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        proposal = conn.execute(
            "SELECT * FROM proposals WHERE target_id=? AND kind='marriage' AND status='pending' ORDER BY id DESC LIMIT 1",
            (u["user_id"],),
        ).fetchone()
        if not proposal:
            await update.message.reply_text("پیشنهاد ازدواج فعالی برای تو ثبت نشده.")
            return
        proposer_id = proposal["proposer_id"]
        conn.execute("UPDATE proposals SET status='accepted' WHERE id=?", (proposal["id"],))
        conn.execute(
            "INSERT OR REPLACE INTO families (user_id, spouse_id, children) VALUES (?,?, COALESCE((SELECT children FROM families WHERE user_id=?),0))",
            (u["user_id"], proposer_id, u["user_id"]),
        )
        conn.execute(
            "INSERT OR REPLACE INTO families (user_id, spouse_id, children) VALUES (?,?, COALESCE((SELECT children FROM families WHERE user_id=?),0))",
            (proposer_id, u["user_id"], proposer_id),
        )
    update_user(u["user_id"], married_to=proposer_id)
    update_user(proposer_id, married_to=u["user_id"])
    proposer = get_user(proposer_id)
    await update.message.reply_text(
        f"💒 تبریک! تو و {proposer['username'] or proposer_id} الان رسماً ازدواج کردید!"
    )


async def cmd_divorce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (u["user_id"],)).fetchone()
        if not fam or not fam["spouse_id"]:
            await update.message.reply_text("تو الان متأهل نیستی.")
            return
        spouse_id = fam["spouse_id"]
        conn.execute("UPDATE families SET spouse_id=NULL WHERE user_id=?", (u["user_id"],))
        conn.execute("UPDATE families SET spouse_id=NULL WHERE user_id=?", (spouse_id,))
    update_user(u["user_id"], married_to=None, happiness=clamp(u["happiness"] - 20, 0, 100))
    update_user(spouse_id, married_to=None)
    await update.message.reply_text("💔 طلاق ثبت شد.")


async def cmd_family(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (u["user_id"],)).fetchone()
    if not fam or not fam["spouse_id"]:
        await update.message.reply_text("👤 تو الان مجرد هستی و فرزندی نداری.")
        return
    spouse = get_user(fam["spouse_id"])
    spouse_name = spouse["username"] if spouse else str(fam["spouse_id"])
    await update.message.reply_text(
        f"👨‍👩‍👧 خانواده شما:\n💑 همسر: {spouse_name}\n👶 تعداد فرزندان: {fam['children']}"
    )


CHILD_COST = 100_000


async def cmd_havechild(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (u["user_id"],)).fetchone()
        if not fam or not fam["spouse_id"]:
            await update.message.reply_text("برای بچه‌دار شدن اول باید ازدواج کنی.")
            return
        if u["cash"] < CHILD_COST:
            await update.message.reply_text(f"❌ هزینه بزرگ‌کردن بچه {fmt_money(CHILD_COST)} است و پول نقد کافی نداری.")
            return
        conn.execute("UPDATE families SET children=children+1 WHERE user_id=?", (u["user_id"],))
        conn.execute("UPDATE families SET children=children+1 WHERE user_id=?", (fam["spouse_id"],))
    update_user(u["user_id"], cash=u["cash"] - CHILD_COST, happiness=clamp(u["happiness"] + 15, 0, 100))
    await update.message.reply_text("👶 تبریک! صاحب فرزند شدید. شادی خانواده بالا رفت.")


# ----------------------------------------------------------------------------
# ۵. شهرها (گروه‌های تلگرام) — شهردار، انتخابات، بودجه، پروژه
# ----------------------------------------------------------------------------

def _in_group(update: Update) -> bool:
    return update.effective_chat.type in GROUP_TYPES


async def cmd_registercity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    name = " ".join(context.args) or update.effective_chat.title
    chat_id = update.effective_chat.id
    with db() as conn:
        existing = conn.execute("SELECT * FROM cities WHERE chat_id=?", (chat_id,)).fetchone()
        if existing:
            await update.message.reply_text(f"این گروه از قبل به‌عنوان شهر «{existing['name']}» ثبت شده.")
            return
        conn.execute("INSERT INTO cities (chat_id, name, budget) VALUES (?,?,0)", (chat_id, name))
    await update.message.reply_text(f"🏙 این گروه به‌عنوان شهر «{name}» ثبت شد!\nبرای شروع انتخابات: /runformayor")


async def cmd_cityinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    with db() as conn:
        city = conn.execute("SELECT * FROM cities WHERE chat_id=?", (update.effective_chat.id,)).fetchone()
    if not city:
        await update.message.reply_text("این گروه هنوز شهر ثبت‌شده نیست. با /registercity <نام> ثبتش کن.")
        return
    mayor_name = "بدون شهردار"
    if city["mayor_id"]:
        mayor = get_user(city["mayor_id"])
        mayor_name = mayor["username"] if mayor else str(city["mayor_id"])
    await update.message.reply_text(
        f"🏙 شهر: {city['name']}\n👑 شهردار: {mayor_name}\n💰 بودجه شهر: {fmt_money(city['budget'])}\n🏆 امتیاز شهر: {city['score']}"
    )


async def cmd_cityfund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /cityfund <مبلغ>")
        return
    if amount > u["cash"]:
        await update.message.reply_text("پول نقد کافی نداری.")
        return
    with db() as conn:
        city = conn.execute("SELECT * FROM cities WHERE chat_id=?", (update.effective_chat.id,)).fetchone()
        if not city:
            await update.message.reply_text("این گروه شهر ثبت‌شده نیست.")
            return
        conn.execute("UPDATE cities SET budget=budget+? WHERE chat_id=?", (amount, update.effective_chat.id))
    update_user(u["user_id"], cash=u["cash"] - amount)
    await update.message.reply_text(f"✅ {fmt_money(amount)} به بودجه شهر «{city['name']}» اضافه شد.")


async def cmd_runformayor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    chat_id = update.effective_chat.id
    with db() as conn:
        city = conn.execute("SELECT * FROM cities WHERE chat_id=?", (chat_id,)).fetchone()
        if not city:
            await update.message.reply_text("اول این گروه رو با /registercity <نام> به‌عنوان شهر ثبت کن.")
            return
        election = conn.execute(
            "SELECT * FROM elections WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()
        if not election:
            cur = conn.execute("INSERT INTO elections (chat_id, status) VALUES (?, 'open')", (chat_id,))
            election_id = cur.lastrowid
        else:
            election_id = election["id"]
        conn.execute(
            "INSERT OR IGNORE INTO election_candidates (election_id, user_id) VALUES (?,?)",
            (election_id, u["user_id"]),
        )
    await update.message.reply_text(
        f"🗳 {update.effective_user.first_name} کاندید شهرداری شد!\n"
        f"رای‌دهی: (ریپلای روی پیام کاندید) /vote\nپایان انتخابات: /endelection"
    )


async def cmd_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    voter = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام کاندید موردنظر ریپلای کن و بنویس /vote")
        return
    candidate = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    with db() as conn:
        election = conn.execute(
            "SELECT * FROM elections WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()
        if not election:
            await update.message.reply_text("انتخابات فعالی در این شهر نیست. با /runformayor شروعش کن.")
            return
        is_candidate = conn.execute(
            "SELECT 1 FROM election_candidates WHERE election_id=? AND user_id=?",
            (election["id"], candidate.id),
        ).fetchone()
        if not is_candidate:
            await update.message.reply_text("این فرد کاندید نیست.")
            return
        conn.execute(
            "INSERT OR REPLACE INTO election_votes (election_id, voter_id, candidate_id) VALUES (?,?,?)",
            (election["id"], voter["user_id"], candidate.id),
        )
    await update.message.reply_text(f"🗳 رای تو برای {candidate.first_name} ثبت شد.")


async def cmd_endelection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    chat_id = update.effective_chat.id
    with db() as conn:
        election = conn.execute(
            "SELECT * FROM elections WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()
        if not election:
            await update.message.reply_text("انتخابات فعالی نیست.")
            return
        votes = conn.execute(
            "SELECT candidate_id, COUNT(*) as c FROM election_votes WHERE election_id=? GROUP BY candidate_id ORDER BY c DESC",
            (election["id"],),
        ).fetchall()
        conn.execute("UPDATE elections SET status='closed' WHERE id=?", (election["id"],))
        if votes:
            winner_id = votes[0]["candidate_id"]
            conn.execute("UPDATE cities SET mayor_id=? WHERE chat_id=?", (winner_id, chat_id))
        else:
            winner_id = None
    if winner_id:
        winner = get_user(winner_id)
        name = winner["username"] if winner else str(winner_id)
        await update.message.reply_text(f"🏆 انتخابات پایان یافت! شهردار جدید: {name}")
    else:
        await update.message.reply_text("انتخابات بدون رای پایان یافت. شهرداری خالی ماند.")


async def cmd_cityproject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _in_group(update):
        await update.message.reply_text("این دستور فقط داخل گروه کار می‌کنه.")
        return
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /cityproject <نام پروژه> <هزینه>")
        return
    cost_s = context.args[-1]
    project_name = " ".join(context.args[:-1])
    try:
        cost = int(cost_s)
    except ValueError:
        await update.message.reply_text("هزینه باید عدد باشه.")
        return
    with db() as conn:
        city = conn.execute("SELECT * FROM cities WHERE chat_id=?", (update.effective_chat.id,)).fetchone()
        if not city:
            await update.message.reply_text("این گروه شهر ثبت‌شده نیست.")
            return
        if city["mayor_id"] != u["user_id"]:
            await update.message.reply_text("فقط شهردار می‌تونه پروژه شهری تعریف کنه.")
            return
        if cost > city["budget"]:
            await update.message.reply_text(f"❌ بودجه شهر کافی نیست. بودجه فعلی: {fmt_money(city['budget'])}")
            return
        new_score = city["score"] + cost // 10_000
        conn.execute(
            "UPDATE cities SET budget=budget-?, score=? WHERE chat_id=?",
            (cost, new_score, update.effective_chat.id),
        )
    await update.message.reply_text(f"🏗 پروژه «{project_name}» با هزینه {fmt_money(cost)} اجرا شد! امتیاز شهر افزایش یافت.")


async def cmd_citytop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM cities ORDER BY score DESC LIMIT 10").fetchall()
    lines = ["🏆 برترین شهرها:\n"]
    for i, c in enumerate(rows, 1):
        lines.append(f"{i}. {c['name']} — امتیاز: {c['score']} — بودجه: {fmt_money(c['budget'])}")
    if not rows:
        lines.append("هنوز شهری ثبت نشده.")
    await update.message.reply_text("\n".join(lines))


# ----------------------------------------------------------------------------
# ۶. جرم و قانون
# ----------------------------------------------------------------------------

async def cmd_crime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    jailed_msg = require_not_jailed(u)
    if jailed_msg:
        await update.message.reply_text(jailed_msg)
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام هدف ریپلای کن: (ریپلای) /crime")
        return
    target_tg = update.message.reply_to_message.from_user
    if target_tg.id == u["user_id"]:
        await update.message.reply_text("نمی‌تونی از خودت بدزدی!")
        return
    target = ensure_user(target_tg.id, target_tg.username or target_tg.first_name)
    if target["cash"] <= 0:
        await update.message.reply_text("این فرد پول نقدی برای دزدیدن نداره.")
        return

    caught = random.random() < 0.4  # ۴۰٪ احتمال گیر افتادن
    if caught:
        insured = u.get("insurance_until", 0) > time.time()
        fine = 2500 if insured else 5000
        jail_seconds = JAIL_BASE_SECONDS // 2 if insured else JAIL_BASE_SECONDS
        new_jail_until = time.time() + jail_seconds
        with db() as conn:
            conn.execute(
                "INSERT INTO crime_log (actor_id, target_id, kind, amount, caught) VALUES (?,?,'theft',0,1)",
                (u["user_id"], target["user_id"]),
            )
        update_user(
            u["user_id"],
            cash=max(0, u["cash"] - fine),
            jailed_until=new_jail_until,
            credit_score=max(300, u["credit_score"] - 30),
        )
        note = " (بیمه‌ات نصف جریمه و زندان رو کم کرد)" if insured else ""
        await update.message.reply_text(
            f"🚔 گیر افتادی! جریمه {fmt_money(fine)} و {jail_seconds // 60} دقیقه زندان.{note}"
        )
    else:
        stolen = int(target["cash"] * random.uniform(0.05, 0.15))
        with db() as conn:
            conn.execute(
                "INSERT INTO crime_log (actor_id, target_id, kind, amount, caught) VALUES (?,?,'theft',?,0)",
                (u["user_id"], target["user_id"], stolen),
            )
        update_user(u["user_id"], cash=u["cash"] + stolen)
        update_user(target["user_id"], cash=target["cash"] - stolen)
        await update.message.reply_text(f"🕵️ موفق شدی! {fmt_money(stolen)} دزدیدی.")


async def cmd_reportcrime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام فردی که می‌خوای گزارش بدی ریپلای کن: (ریپلای) /reportcrime <دلیل>")
        return
    target_tg = update.message.reply_to_message.from_user
    reason = " ".join(context.args) or "بدون توضیح"
    with db() as conn:
        conn.execute(
            "INSERT INTO reports (reporter_id, target_id, reason) VALUES (?,?,?)",
            (u["user_id"], target_tg.id, reason[:200]),
        )
    await update.message.reply_text("✅ گزارش ثبت شد و برای بررسی به مدیریت ارسال می‌شه.")


async def cmd_bail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not is_jailed(u):
        await update.message.reply_text("تو الان زندانی نیستی.")
        return
    amount = _parse_amount(context.args)
    if amount is None or amount <= 0:
        await update.message.reply_text("استفاده: /bail <مبلغ> — هر ۱۰۰۰ تومان، ۱ دقیقه از زندان کم می‌کنه.")
        return
    if amount > u["cash"]:
        await update.message.reply_text("پول نقد کافی نداری.")
        return
    reduce_seconds = (amount // 1000) * 60
    new_until = max(time.time(), u["jailed_until"] - reduce_seconds)
    update_user(u["user_id"], cash=u["cash"] - amount, jailed_until=new_until)
    if new_until <= time.time():
        await update.message.reply_text("🔓 آزاد شدی!")
    else:
        remain = int((new_until - time.time()) / 60) + 1
        await update.message.reply_text(f"⏳ {remain} دقیقه دیگه تا آزادی باقی مونده.")


# ----------------------------------------------------------------------------
# ۷. رویدادهای جهانی
# ----------------------------------------------------------------------------

EVENTS = [
    ("normal", "اوضاع اقتصاد عادیه.", 1.0),
    ("recession", "📉 رکود اقتصادی! حقوق‌ها و قیمت سهام افت کردن.", 0.75),
    ("boom", "📈 رونق اقتصادی! حقوق‌ها بالا رفته.", 1.3),
    ("energy_crisis", "⚡ بحران انرژی! هزینه‌های زندگی بالا رفته.", 0.85),
    ("festival", "🎉 جشنواره‌ی شهری! همه‌ی شهروندان شادترن.", 1.1),
]


async def cmd_worldstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        state = conn.execute("SELECT * FROM world_state WHERE id=1").fetchone()
    await update.message.reply_text(
        f"🌍 وضعیت جهانی بازی:\n{state['description']}\nضریب تاثیر بر حقوق: ×{state['pay_multiplier']}"
    )


async def cmd_setevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("این دستور فقط برای مدیران است.")
        return
    if not context.args:
        keys = ", ".join(e[0] for e in EVENTS)
        await update.message.reply_text(f"استفاده: /setevent <کد رویداد>\nکدها: {keys}")
        return
    key = context.args[0]
    ev = next((e for e in EVENTS if e[0] == key), None)
    if not ev:
        await update.message.reply_text("رویداد نامعتبر.")
        return
    with db() as conn:
        conn.execute(
            "UPDATE world_state SET event=?, description=?, pay_multiplier=?, updated_at=datetime('now') WHERE id=1",
            ev,
        )
    await update.message.reply_text(f"✅ رویداد جهانی به «{ev[0]}» تغییر کرد.")


async def world_event_job(context: ContextTypes.DEFAULT_TYPE):
    """هر چند ساعت یک‌بار به‌صورت تصادفی یک رویداد جهانی جدید انتخاب می‌کنه."""
    ev = random.choice(EVENTS)
    with db() as conn:
        conn.execute(
            "UPDATE world_state SET event=?, description=?, pay_multiplier=?, updated_at=datetime('now') WHERE id=1",
            ev,
        )
    logger.info("World event changed to: %s", ev[0])


# ----------------------------------------------------------------------------
# ۸. سرگرمی‌ها
# ----------------------------------------------------------------------------

async def cmd_gym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    cost = 1000
    if u["cash"] < cost:
        await update.message.reply_text("پول نقد کافی نداری (هزینه باشگاه: ۱۰۰۰ تومان).")
        return
    if u["energy"] < 15:
        await update.message.reply_text("انرژی کافی نداری.")
        return
    update_user(
        u["user_id"],
        cash=u["cash"] - cost,
        energy=clamp(u["energy"] - 15, 0, 100),
        health=clamp(u["health"] + 10, 0, 100),
        happiness=clamp(u["happiness"] + 5, 0, 100),
    )
    await update.message.reply_text("🏋️ تمرین کردی! سلامت و شادی بالا رفت.")


async def _minigame(update, u, energy_cost, skill, base_reward, label, emoji):
    if u["energy"] < energy_cost:
        await update.message.reply_text("انرژی کافی نداری.")
        return
    reward = int(base_reward * random.uniform(0.7, 1.4))
    update_user(
        u["user_id"],
        energy=clamp(u["energy"] - energy_cost, 0, 100),
        cash=u["cash"] + reward,
    )
    new_level, leveled = add_skill_xp(u["user_id"], skill, random.randint(5, 12))
    msg = f"{emoji} {label} انجام شد! +{fmt_money(reward)}"
    if leveled:
        msg += f"\n🎉 مهارت {SKILLS[skill]} به سطح {new_level} رسید!"
    await update.message.reply_text(msg)


async def cmd_fish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    await _minigame(update, u, 12, "farming", 1500, "ماهی‌گیری", "🎣")


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    await _minigame(update, u, 20, "business", 2500, "کار در معدن", "⛏")


async def cmd_farm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    await _minigame(update, u, 15, "farming", 1800, "کشاورزی", "🌾")


async def cmd_restaurant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    cost = 3000
    if u["cash"] < cost:
        await update.message.reply_text(f"پول نقد کافی نداری (هزینه: {fmt_money(cost)}).")
        return
    update_user(
        u["user_id"],
        cash=u["cash"] - cost,
        hunger=clamp(u["hunger"] - 60, 0, 100),
        happiness=clamp(u["happiness"] + 15, 0, 100),
    )
    await update.message.reply_text("🍽 شام خوبی خوردی! گرسنگی و شادی به‌طرز چشمگیری بهتر شد.")


RIDDLES = [
    ("عددی که دو برابرش با ۱۰ برابر می‌شه ۸ عدد میشه، اون عدد چیه؟", "اشتباه در طراحی -  رد شو بعدی"),
    ("چه چیزی هر چقدر از آن برداری بزرگ‌تر می‌شود؟", "چاله"),
    ("چه چیزی بی‌آنکه نفس بکشد می‌میرد؟", "باتری"),
    ("کدام کلمه هر چه بیشتر از آن کم کنی بزرگ‌تر می‌شود؟", "کوتاه"),
]


async def cmd_puzzle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    q, _ = random.choice(RIDDLES)
    reward = random.randint(500, 1500)
    update_user(u["user_id"], cash=u["cash"] + reward, xp=u["xp"] + 5, intelligence=u["intelligence"] + 1)
    await update.message.reply_text(f"🧩 معما: {q}\n\n(فقط برای سرگرمی — جایزه‌ی مشارکت: {fmt_money(reward)} + XP هوش)")


# ----------------------------------------------------------------------------
# ۹. مشاور هوش مصنوعی (نیازمند GROQ_API_KEY)
# ----------------------------------------------------------------------------

async def cmd_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GROQ_API_KEY:
        await update.message.reply_text(
            "🤖 مشاور هوش مصنوعی فعال نیست.\n"
            "برای فعال‌سازی، متغیر محیطی GROQ_API_KEY رو با کلید رایگان خودت از "
            "console.groq.com ست کن و بات رو ری‌استارت کن."
        )
        return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("استفاده: /advisor <سوالت درباره پول، شغل یا سرمایه‌گذاری داخل بازی>")
        return
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    try:
        import requests  # pip install requests

        nw = net_worth(u)
        prompt = (
            f"تو یک مشاور مالی/شغلی داخل یک بازی شبیه‌ساز زندگی تلگرامی هستی. "
            f"وضعیت کاربر: دارایی خالص {nw} تومان، پول نقد {u['cash']}، شغل {u['job']}.\n"
            f"سوال کاربر: {question}\n"
            "پاسخ کوتاه (حداکثر ۴-۵ خط)، دوستانه و عملی به فارسی بده."
        )
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        await update.message.reply_text(f"🤖 مشاور:\n{text}")
    except Exception as e:
        logger.exception("Advisor error")
        await update.message.reply_text(f"❌ خطا در ارتباط با مشاور هوش مصنوعی: {e}")


# ----------------------------------------------------------------------------
# ۱۰. درآمدزایی (VIP) — ساختار آماده، نیازمند Payment Provider Token واقعی
# ----------------------------------------------------------------------------

async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 اشتراک VIP (به‌زودی):\n"
        "برای فعال‌سازی پرداخت واقعی، باید یک Payment Provider Token از طریق "
        "@BotFather → Payments تهیه کنی (مثل استرایپ یا یکی از درگاه‌های پشتیبانی‌شده). "
        "بعد از تهیه توکن، دستور /vip می‌تونه با استفاده از متد sendInvoice تلگرام "
        "فاکتور واقعی بفرسته. این بخش به‌عمد به‌صورت راهنما ساخته شده تا اطلاعات "
        "پرداخت ساختگی یا گمراه‌کننده نشون داده نشه."
    )


AVATAR_OPTIONS = ["🙂", "😎", "🤠", "🥷", "🦸", "🧙", "👑", "🐺", "🦊", "🐯"]


async def cmd_buyavatar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not context.args or context.args[0] not in AVATAR_OPTIONS:
        await update.message.reply_text("انتخاب‌های موجود: " + " ".join(AVATAR_OPTIONS) + "\nاستفاده: /buyavatar <ایموجی>")
        return
    cost = 2000
    if u["cash"] < cost:
        await update.message.reply_text(f"پول نقد کافی نداری (هزینه: {fmt_money(cost)}).")
        return
    update_user(u["user_id"], cash=u["cash"] - cost, avatar=context.args[0])
    await update.message.reply_text(f"✅ آواتار تو به {context.args[0]} تغییر کرد.")


# ----------------------------------------------------------------------------
# ۱۱. امنیت و پنل مدیریت
# ----------------------------------------------------------------------------

async def cmd_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    lines = ["🔔 اعلان‌های شما:\n"]
    with db() as conn:
        proposals = conn.execute(
            "SELECT * FROM proposals WHERE target_id=? AND status='pending'", (u["user_id"],)
        ).fetchall()
    if proposals:
        for p in proposals:
            proposer = get_user(p["proposer_id"])
            name = proposer["username"] if proposer else str(p["proposer_id"])
            lines.append(f"💍 پیشنهاد ازدواج از {name} — با ریپلای روی پیامش /accept رو بزن.")
    if u["loan_amount"] > 0:
        lines.append(f"💳 بدهی وام فعال: {fmt_money(u['loan_amount'])} — /payloan برای پرداخت.")
    if is_jailed(u):
        lines.append("🚔 تو الان زندانی هستی. /bail برای آزادی زودتر.")
    if len(lines) == 1:
        lines.append("چیزی نداری.")
    await update.message.reply_text("\n".join(lines))




async def ban_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """این هندلر روی group=-1 (زودتر از بقیه) ثبت می‌شه تا کاربران بن‌شده رو متوقف کنه."""
    if not update.effective_user:
        return
    u = get_user(update.effective_user.id)
    if u and require_not_banned(u):
        if update.message:
            await update.message.reply_text("⛔️ حساب تو توسط مدیریت مسدود شده.")
        raise ApplicationHandlerStop


# ----------------------------------------------------------------------------
# ثبت هندلرها
# ----------------------------------------------------------------------------

def register_phase2(app):
    init_extra_db()

    # این هندلر با group=-1 زودتر از همه دستورات دیگه اجرا می‌شه تا کاربر بن‌شده
    # نتونه از هیچ دستوری (نه فقط دستورات فاز ۲) استفاده کنه.
    app.add_handler(MessageHandler(filters.ALL, ban_guard), group=-1)

    handlers = [
        ("createcompany", cmd_createcompany),
        ("mycompany", cmd_mycompany),
        ("companyinvest", cmd_companyinvest),
        ("hire", cmd_hire),
        ("payroll", cmd_payroll),
        ("companiestop", cmd_companies_top),

        ("sell", cmd_sell),
        ("market", cmd_market),
        ("buyitem", cmd_buyitem),
        ("mylistings", cmd_mylistings),
        ("cancellisting", cmd_cancellisting),

        ("stocks", cmd_stocks),
        ("buystock", cmd_buystock),
        ("sellstock", cmd_sellstock),
        ("mystocks", cmd_mystocks),

        ("propose", cmd_propose),
        ("accept", cmd_accept),
        ("divorce", cmd_divorce),
        ("family", cmd_family),
        ("havechild", cmd_havechild),

        ("registercity", cmd_registercity),
        ("cityinfo", cmd_cityinfo),
        ("cityfund", cmd_cityfund),
        ("runformayor", cmd_runformayor),
        ("vote", cmd_vote),
        ("endelection", cmd_endelection),
        ("cityproject", cmd_cityproject),
        ("citytop", cmd_citytop),

        ("crime", cmd_crime),
        ("reportcrime", cmd_reportcrime),
        ("bail", cmd_bail),

        ("worldstatus", cmd_worldstatus),
        ("setevent", cmd_setevent),

        ("gym", cmd_gym),
        ("fish", cmd_fish),
        ("mine", cmd_mine),
        ("farm", cmd_farm),
        ("restaurant", cmd_restaurant),
        ("puzzle", cmd_puzzle),

        ("advisor", cmd_advisor),

        ("vip", cmd_vip),
        ("buyavatar", cmd_buyavatar),

        ("notifications", cmd_notifications),
    ]
    for name, fn in handlers:
        app.add_handler(CommandHandler(name, fn))

    if app.job_queue:
        app.job_queue.run_repeating(stock_drift_job, interval=900, first=900)       # هر ۱۵ دقیقه
        app.job_queue.run_repeating(world_event_job, interval=6 * 3600, first=3600)  # هر ۶ ساعت

    logger.info("Phase 2 handlers registered: %d commands", len(handlers))


# ==============================================================================
# فاز ۴ — آموزش، دوستی، بیمه، ارز دیجیتال/طلا، دادگاه، ماموریت‌های تازه
# ==============================================================================

EDU_LEVELS = [
    (0, "بدون تحصیلات", 0),
    (1, "دیپلم دبیرستان", 100),
    (2, "کاردانی", 300),
    (3, "لیسانس", 700),
    (4, "فوق‌لیسانس / دکترا", 1500),
]


def edu_level_for_xp(edu_xp: int) -> int:
    lvl = 0
    for level, _name, need in EDU_LEVELS:
        if edu_xp >= need:
            lvl = level
    return lvl


def edu_name(level: int) -> str:
    for l, name, _need in EDU_LEVELS:
        if l == level:
            return name
    return "نامشخص"


WEEKLY_MISSIONS = [
    {"key": "w_work_15", "desc": "۱۵ بار کار کن", "target": 15, "reward_cash": 20000, "reward_xp": 80},
    {"key": "w_train_10", "desc": "۱۰ بار مهارت تمرین کن", "target": 10, "reward_cash": 15000, "reward_xp": 90},
    {"key": "w_study_5", "desc": "۵ بار درس بخون", "target": 5, "reward_cash": 12000, "reward_xp": 60},
]

MONTHLY_MISSIONS = [
    {"key": "m_networth_up", "desc": "۳۰ بار کار کن", "target": 30, "reward_cash": 80000, "reward_xp": 300},
    {"key": "m_property", "desc": "یک ملک یا وسیله نقلیه بخر", "target": 1, "reward_cash": 50000, "reward_xp": 150},
]

SECRET_MISSIONS = [
    {"key": "s_millionaire", "desc": "🎁 مأموریت مخفی: دارایی خالص بالای ۱,۰۰۰,۰۰۰ تومان", "target": 1, "reward_cash": 100000, "reward_xp": 500},
]


def period_id_week():
    y, w, _ = datetime.utcnow().isocalendar()
    return f"week:{y}-W{w}"


def period_id_month():
    return f"month:{datetime.utcnow().strftime('%Y-%m')}"


def period_id_secret():
    return "secret"


def bump_periodic_mission(user_id, mission_key, period_id, amount=1):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM periodic_missions WHERE user_id=? AND mission_key=? AND period_id=?",
            (user_id, mission_key, period_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE periodic_missions SET progress=progress+? WHERE user_id=? AND mission_key=? AND period_id=?",
                (amount, user_id, mission_key, period_id),
            )
        else:
            conn.execute(
                "INSERT INTO periodic_missions (user_id, mission_key, period_id, progress) VALUES (?,?,?,?)",
                (user_id, mission_key, period_id, amount),
            )


def get_periodic_missions(user_id, definitions, period_id):
    with db() as conn:
        rows = {
            r["mission_key"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM periodic_missions WHERE user_id=? AND period_id=?", (user_id, period_id)
            ).fetchall()
        }
    result = []
    for m in definitions:
        state = rows.get(m["key"], {"progress": 0, "claimed": 0})
        result.append({**m, "progress": state["progress"], "claimed": state["claimed"]})
    return result


def claim_periodic_mission(user_id, mission_key, period_id, definitions):
    m = next((x for x in definitions if x["key"] == mission_key), None)
    if not m:
        return None, "چنین ماموریتی وجود نداره."
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM periodic_missions WHERE user_id=? AND mission_key=? AND period_id=?",
            (user_id, mission_key, period_id),
        ).fetchone()
    if not row or row["progress"] < m["target"]:
        return None, "هنوز کامل نشده."
    if row["claimed"]:
        return None, "قبلاً دریافتش کردی."
    u = get_user(user_id)
    update_user(user_id, cash=u["cash"] + m["reward_cash"], xp=u["xp"] + m["reward_xp"])
    with db() as conn:
        conn.execute(
            "UPDATE periodic_missions SET claimed=1 WHERE user_id=? AND mission_key=? AND period_id=?",
            (user_id, mission_key, period_id),
        )
    return m, None


def init_phase4_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS periodic_missions (
                user_id INTEGER,
                mission_key TEXT,
                period_id TEXT,
                progress INTEGER DEFAULT 0,
                claimed INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, mission_key, period_id)
            );
            CREATE TABLE IF NOT EXISTS court_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER,
                defendant_id INTEGER,
                outcome TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS force_join_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                title TEXT,
                username TEXT,
                invite_link TEXT,
                added_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS menu_owners (
                chat_id INTEGER,
                message_id INTEGER,
                owner_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS gov_roles (
                user_id INTEGER PRIMARY KEY,
                role TEXT,
                assigned_by INTEGER,
                assigned_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS judicial_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                reason TEXT,
                detail TEXT,
                issuer_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS court_summons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                judge_id INTEGER,
                scheduled_at TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                verdict TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
    extra_columns = [
        ("edu_level", "INTEGER DEFAULT 0"),
        ("edu_xp", "INTEGER DEFAULT 0"),
        ("insurance_until", "REAL DEFAULT 0"),
        ("card_number", "TEXT"),
        ("detention_until", "REAL DEFAULT 0"),
        ("prison_until", "REAL DEFAULT 0"),
        ("bank_frozen", "INTEGER DEFAULT 0"),
    ]
    with db() as conn:
        for col, decl in extra_columns:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
            except Exception:
                pass  # ستون از قبل وجود داره
        try:
            conn.execute("ALTER TABLE stocks ADD COLUMN asset_type TEXT DEFAULT 'stock'")
        except Exception:
            pass
        default_crypto_gold = [
            ("BTS", "بیت‌کوین شهر", 900_000, "crypto"),
            ("ETC", "اتریوم شهر", 300_000, "crypto"),
            ("GLD", "طلای ۱۸ عیار (گرم)", 45_000, "gold"),
        ]
        for sym, name, price, atype in default_crypto_gold:
            conn.execute(
                "INSERT OR IGNORE INTO stocks (symbol, name, price, asset_type) VALUES (?,?,?,?)",
                (sym, name, price, atype),
            )
        rows = conn.execute("SELECT user_id FROM users WHERE card_number IS NULL OR card_number=''").fetchall()
        for r in rows:
            conn.execute("UPDATE users SET card_number=? WHERE user_id=?", (_generate_card_number(conn), r["user_id"]))
        for role, default_salary in (("police", 8000), ("soldier", 6000), ("captain", 12000), ("judge", 15000)):
            key = f"salary_{role}"
            existing = conn.execute("SELECT 1 FROM bot_settings WHERE key=?", (key,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO bot_settings (key, value) VALUES (?,?)", (key, str(default_salary)))


def _generate_card_number(conn):
    while True:
        num = "6219" + "".join(str(random.randint(0, 9)) for _ in range(12))
        exists = conn.execute("SELECT 1 FROM users WHERE card_number=?", (num,)).fetchone()
        if not exists:
            return num


def format_card_number(num):
    if not num:
        return "—"
    return "-".join(num[i:i + 4] for i in range(0, len(num), 4))


def get_setting(key, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def resolve_user_by_identifier(text):
    text = text.strip().lstrip("@")
    if text.isdigit():
        return get_user(int(text))
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (text,)).fetchone()
    return dict(row) if row else None


def get_force_join_list():
    with db() as conn:
        rows = conn.execute("SELECT * FROM force_join_channels ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_force_join_row(row_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM force_join_channels WHERE id=?", (row_id,)).fetchone()
    return dict(row) if row else None


def set_menu_owner(chat_id, message_id, owner_id):
    with db() as conn:
        conn.execute(
            "INSERT INTO menu_owners (chat_id, message_id, owner_id) VALUES (?,?,?) "
            "ON CONFLICT(chat_id, message_id) DO UPDATE SET owner_id=excluded.owner_id",
            (chat_id, message_id, owner_id),
        )


def get_loan_max_multiplier():
    val = get_setting("loan_max_multiplier")
    return int(val) if val and val.isdigit() else LOAN_MAX_MULTIPLIER


def get_menu_owner(chat_id, message_id):
    with db() as conn:
        row = conn.execute(
            "SELECT owner_id FROM menu_owners WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        ).fetchone()
    return row["owner_id"] if row else None


# ----------------------------------------------------------------------------
# سیستم مقام‌ها و عدالت (پلیس، سرباز، سروان، قاضی)
# ----------------------------------------------------------------------------

GOV_ROLES = ["police", "soldier", "captain", "judge"]
ROLE_NAMES = {"police": "پلیس", "soldier": "سرباز", "captain": "سروان", "judge": "قاضی"}
ROLE_ICONS = {"police": "👮", "soldier": "🪖", "captain": "🎖️", "judge": "⚖️"}


def get_gov_role(user_id):
    with db() as conn:
        row = conn.execute("SELECT role FROM gov_roles WHERE user_id=?", (user_id,)).fetchone()
    return row["role"] if row else None


def get_job_label(u):
    """نمایش «شغل» کاربر: اگه مقام دولتی داره (پلیس/سرباز/سروان/قاضی) همون نشون داده می‌شه،
    وگرنه شغل عادیش از JOBS، وگرنه «بیکار»."""
    role = get_gov_role(u["user_id"])
    if role:
        return f"{ROLE_ICONS.get(role, '')} {ROLE_NAMES.get(role, role)}".strip()
    job = JOBS.get(u["job"])
    return job["name"] if job else "بیکار"


def set_gov_role(user_id, role, assigned_by):
    with db() as conn:
        conn.execute(
            "INSERT INTO gov_roles (user_id, role, assigned_by) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, assigned_by=excluded.assigned_by, "
            "assigned_at=datetime('now')",
            (user_id, role, assigned_by),
        )


def remove_gov_role(user_id):
    with db() as conn:
        conn.execute("DELETE FROM gov_roles WHERE user_id=?", (user_id,))


def list_gov_roles():
    with db() as conn:
        rows = conn.execute("SELECT * FROM gov_roles ORDER BY role").fetchall()
    return [dict(r) for r in rows]


def get_role_salary(role):
    val = get_setting(f"salary_{role}")
    return int(val) if val and val.isdigit() else 0


def set_role_salary(role, amount):
    set_setting(f"salary_{role}", str(amount))


def add_judicial_record(user_id, kind, reason, detail, issuer_id):
    with db() as conn:
        conn.execute(
            "INSERT INTO judicial_records (user_id, kind, reason, detail, issuer_id) VALUES (?,?,?,?,?)",
            (user_id, kind, reason, detail, issuer_id),
        )


def get_judicial_records(user_id, limit=20):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM judicial_records WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


JUDICIAL_KIND_LABELS = {
    "arrest": "🚨 دستگیری",
    "summons": "📅 احضاریه دادگاه",
    "verdict": "⚖️ حکم دادگاه",
    "fine": "💸 جریمه نقدی",
    "detention": "🚔 بازداشت",
    "prison": "🔒 زندان",
    "release": "🔓 آزادی",
}


async def do_arrest(police_id, target_id, reason, duration_min, context):
    if police_id == target_id:
        return "❌ نمی‌تونی خودتو دستگیر کنی."
    target = get_user(target_id)
    if not target:
        return "❌ چنین کاربری پیدا نشد."
    until = time.time() + duration_min * 60
    update_user(target_id, detention_until=until)
    add_judicial_record(target_id, "arrest", reason, f"{duration_min} دقیقه", police_id)
    police = get_user(police_id)
    police_label = police.get("username") or police_id if police else police_id
    try:
        await context.bot.send_message(
            target_id,
            f"🚨 شما توسط پلیس دستگیر شدید و به بازداشتگاه منتقل شدید.\n"
            f"📄 دلیل: {reason}\n⏱ مدت بازداشت: {duration_min} دقیقه\n👮 دستگیرکننده: {police_label}",
        )
    except Exception:
        pass
    return f"✅ کاربر {target.get('username') or target_id} دستگیر و به بازداشتگاه منتقل شد."


async def do_release(target_id, which, issuer_id, context):
    target = get_user(target_id)
    if not target:
        return "❌ چنین کاربری پیدا نشد."
    if which == "detention":
        update_user(target_id, detention_until=0)
        label = "بازداشتگاه"
    else:
        update_user(target_id, prison_until=0)
        label = "زندان"
    add_judicial_record(target_id, "release", f"آزادی از {label}", "", issuer_id)
    try:
        await context.bot.send_message(target_id, f"🔓 شما از {label} آزاد شدید.")
    except Exception:
        pass
    return f"✅ کاربر {target.get('username') or target_id} از {label} آزاد شد."


async def do_court_summon(judge_id, target_id, scheduled_at, reason, context):
    target = get_user(target_id)
    if not target:
        return "❌ چنین کاربری پیدا نشد."
    with db() as conn:
        conn.execute(
            "INSERT INTO court_summons (user_id, judge_id, scheduled_at, reason) VALUES (?,?,?,?)",
            (target_id, judge_id, scheduled_at, reason),
        )
    add_judicial_record(target_id, "summons", reason, scheduled_at, judge_id)
    try:
        await context.bot.send_message(
            target_id,
            f"📩 احضاریه‌ی دادگاه\nشما به دادگاه احضار شدید.\n📅 زمان: {scheduled_at}\n📄 دلیل: {reason}",
        )
    except Exception:
        pass
    return f"✅ احضاریه برای {target.get('username') or target_id} صادر و ارسال شد."


VERDICT_TYPES = {
    "acquit": "✅ تبرئه",
    "fine": "💸 جریمه نقدی",
    "detention": "🚔 بازداشت",
    "prison": "🔒 زندان",
    "detention_fine": "🚔+💸 بازداشت + جریمه",
    "prison_fine": "🔒+💸 زندان + جریمه",
}


async def do_verdict(judge_id, target_id, verdict_type, fine_amount, duration_min, reason, context):
    target = get_user(target_id)
    if not target:
        return "❌ چنین کاربری پیدا نشد."
    judge = get_user(judge_id)
    judge_label = judge.get("username") or judge_id if judge else judge_id
    parts = [f"⚖️ حکم دادگاه\n📄 دلیل: {reason}\n👨‍⚖️ قاضی: {judge_label}\nحکم: {VERDICT_TYPES.get(verdict_type, verdict_type)}"]
    detail_bits = []

    if verdict_type in ("fine", "detention_fine", "prison_fine") and fine_amount:
        new_cash = max(0, target["cash"] - fine_amount)
        update_user(target_id, cash=new_cash)
        add_judicial_record(target_id, "fine", reason, fmt_money(fine_amount), judge_id)
        parts.append(f"💸 مبلغ جریمه: {fmt_money(fine_amount)}\nموجودی فعلی: {fmt_money(new_cash)}")
        detail_bits.append(f"جریمه {fmt_money(fine_amount)}")

    if verdict_type in ("detention", "detention_fine") and duration_min:
        until = time.time() + duration_min * 60
        update_user(target_id, detention_until=until)
        add_judicial_record(target_id, "detention", reason, f"{duration_min} دقیقه", judge_id)
        parts.append(f"🚔 مدت بازداشت: {duration_min} دقیقه")
        detail_bits.append(f"بازداشت {duration_min} دقیقه")

    if verdict_type in ("prison", "prison_fine") and duration_min:
        until = time.time() + duration_min * 60
        update_user(target_id, prison_until=until)
        add_judicial_record(target_id, "prison", reason, f"{duration_min} دقیقه", judge_id)
        parts.append(f"🔒 مدت زندان: {duration_min} دقیقه")
        detail_bits.append(f"زندان {duration_min} دقیقه")

    if verdict_type == "acquit":
        parts.append("✅ پرونده مختومه شد و کاربر تبرئه شد.")

    add_judicial_record(target_id, "verdict", reason, "؛ ".join(detail_bits) or "تبرئه", judge_id)
    with db() as conn:
        conn.execute(
            "UPDATE court_summons SET status='resolved', verdict=? WHERE user_id=? AND status='pending'",
            (verdict_type, target_id),
        )
    try:
        await context.bot.send_message(target_id, "\n".join(parts))
    except Exception:
        pass
    return f"✅ حکم برای {target.get('username') or target_id} صادر و اعلام شد."


async def judicial_release_job(context):
    now = time.time()
    with db() as conn:
        expired_detention = conn.execute(
            "SELECT user_id FROM users WHERE detention_until > 0 AND detention_until <= ?", (now,)
        ).fetchall()
        expired_prison = conn.execute(
            "SELECT user_id FROM users WHERE prison_until > 0 AND prison_until <= ?", (now,)
        ).fetchall()
    for r in expired_detention:
        update_user(r["user_id"], detention_until=0)
        add_judicial_record(r["user_id"], "release", "پایان مدت بازداشت", "", 0)
        try:
            await context.bot.send_message(r["user_id"], "🔓 مدت بازداشت شما تموم شد و آزاد شدید.")
        except Exception:
            pass
    for r in expired_prison:
        update_user(r["user_id"], prison_until=0)
        add_judicial_record(r["user_id"], "release", "پایان مدت زندان", "", 0)
        try:
            await context.bot.send_message(r["user_id"], "🔓 مدت زندان شما تموم شد و آزاد شدید.")
        except Exception:
            pass


async def pay_role_salaries(context, role_filter=None):
    roles = list_gov_roles()
    paid = []
    for r in roles:
        if role_filter and r["role"] != role_filter:
            continue
        salary = get_role_salary(r["role"])
        if salary <= 0:
            continue
        u = get_user(r["user_id"])
        if not u:
            continue
        update_user(r["user_id"], cash=u["cash"] + salary)
        paid.append((r["user_id"], salary))
        try:
            await context.bot.send_message(
                r["user_id"],
                f"💰 حقوق مقام {ROLE_ICONS.get(r['role'],'')} {ROLE_NAMES.get(r['role'], r['role'])} "
                f"واریز شد: {fmt_money(salary)}",
            )
        except Exception:
            pass
    return paid


def add_force_join_channel(chat_id, title, username, invite_link):
    with db() as conn:
        conn.execute(
            "INSERT INTO force_join_channels (chat_id, title, username, invite_link) VALUES (?,?,?,?)",
            (chat_id, title, username, invite_link),
        )


def remove_force_join_channel(row_id):
    with db() as conn:
        conn.execute("DELETE FROM force_join_channels WHERE id=?", (row_id,))


async def is_member_of_channel(context, chat_id, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def resolve_and_validate_channel(context, identifier):
    identifier = identifier.strip()
    if identifier.lstrip("-").isdigit():
        chat_ref = int(identifier)
    else:
        chat_ref = identifier if identifier.startswith("@") else f"@{identifier.lstrip('@')}"
    try:
        chat = await context.bot.get_chat(chat_ref)
    except Exception:
        return None, "کانال یا گروه پیدا نشد. آیدی عددی یا یوزرنیم درست رو بفرست."
    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat.id, me.id)
    except Exception:
        return None, "ربات به این کانال/گروه دسترسی نداره."
    if member.status not in ("administrator", "creator"):
        return None, "ربات باید عضو و ادمین اون کانال/گروه باشه. اول ربات رو اونجا اضافه و ادمین کن، بعد دوباره امتحان کن."
    invite_link = None
    if not chat.username:
        try:
            invite_link = await context.bot.export_chat_invite_link(chat.id)
        except Exception:
            invite_link = None
    return {
        "chat_id": str(chat.id),
        "title": chat.title or chat.username or str(chat.id),
        "username": chat.username or "",
        "invite_link": invite_link or "",
    }, None


async def enforce_force_join(update, context):
    user_id = update.effective_user.id
    if is_admin(user_id):
        return True
    channels = get_force_join_list()
    if not channels:
        return True
    missing = []
    for ch in channels:
        if not await is_member_of_channel(context, ch["chat_id"], user_id):
            missing.append(ch)
    if not missing:
        return True
    kb_rows = []
    for ch in missing:
        link = f"https://t.me/{ch['username']}" if ch.get("username") else ch.get("invite_link")
        if link:
            kb_rows.append([InlineKeyboardButton(f"📢 عضویت در {ch['title']}", url=link)])
    kb_rows.append([_btn("✅ عضو شدم", "checkjoin", style="success")])
    kb = InlineKeyboardMarkup(kb_rows)
    text = "برای استفاده از ربات باید اول عضو این کانال/گروه‌ها بشی، بعد دکمه‌ی «عضو شدم» رو بزن:\n\n" + "\n".join(
        f"• {ch['title']}" for ch in missing
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)
    return False


MAINTENANCE_KEY = "maintenance_mode"


async def enforce_maintenance(update, context):
    user_id = update.effective_user.id
    if is_admin(user_id):
        return True
    if get_setting(MAINTENANCE_KEY) != "1":
        return True
    text = "🔧 ربات از طرف مدیریت خاموش شده است."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    else:
        await update.message.reply_text(text)
    return False


# ---- آموزش: مدرسه / دانشگاه / درس خوندن / مدرک ----

async def cmd_addfriend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = ensure_user(update.effective_user.id, update.effective_user.username)
    if not update.message.reply_to_message:
        await update.message.reply_text("روی پیام فردی که می‌خوای دوستش بشی ریپلای کن و بنویس: (ریپلای) /addfriend")
        return
    target_tg = update.message.reply_to_message.from_user
    if target_tg.id == u["user_id"]:
        await update.message.reply_text("نمی‌تونی با خودت دوست بشی!")
        return
    ensure_user(target_tg.id, target_tg.username or target_tg.first_name)
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?,?)", (u["user_id"], target_tg.id))
        conn.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?,?)", (target_tg.id, u["user_id"]))
    await update.message.reply_text(f"🤝 حالا با {target_tg.first_name} دوستی!")


INSURANCE_PRICE = 5000
INSURANCE_DAYS = 7
COURT_FEE = 3000


def register_phase4(app):
    init_phase4_db()
    handlers = [
        ("addfriend", cmd_addfriend),
    ]
    for name, fn in handlers:
        app.add_handler(CommandHandler(name, fn))
    logger.info("Phase 4 handlers registered: %d commands", len(handlers))


# ==============================================================================
# فاز ۳ — منوی کاملاً دکمه‌ای (Inline Keyboard)
# ==============================================================================

# -*- coding: utf-8 -*-


PRESET_AMOUNTS = [1000, 5000, 10000, 50000, 100000, 500000]
PRESET_QTYS = [1, 5, 10, 25, 50]


# ----------------------------------------------------------------------------
# ابزارهای کمکی مشترک
# ----------------------------------------------------------------------------

def _back(cb="m:main", label="⬅️ بازگشت"):
    return InlineKeyboardButton(label, callback_data=cb)


def _btn(text, callback_data, style=None):
    if style:
        try:
            return InlineKeyboardButton(text, callback_data=callback_data, style=style)
        except TypeError:
            pass
    return InlineKeyboardButton(text, callback_data=callback_data)


def _rows(buttons, cols=2):
    """لیست دکمه‌ها رو به ردیف‌های ۲تایی/۳تایی می‌شکونه."""
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


async def _answer(query, text, keyboard=None, parse_mode=None):
    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=parse_mode)
    except Exception:
        # اگر متن تغییر نکرده باشه یا پیام قدیمی باشه، پیام جدید بفرست
        try:
            sent = await query.message.reply_text(text, reply_markup=keyboard, parse_mode=parse_mode)
        except Exception:
            # اگر parse_mode باعث خطا شد (مثلاً کاراکتر خاص escape‌نشده)، ساده بفرست
            sent = await query.message.reply_text(text, reply_markup=keyboard)
        if query.message.chat.type != "private":
            set_menu_owner(query.message.chat.id, sent.message_id, query.from_user.id)


def amount_keyboard(action, extra="", back_cb="m:main"):
    buttons = [
        _btn(f"{v:,}", f"amt:{action}:{extra}:{v}", style="primary")
        for v in PRESET_AMOUNTS
    ]
    rows = _rows(buttons, 3)
    rows.append([_btn("✏️ مبلغ دلخواه", f"amtcustom:{action}:{extra}", style="success")])
    rows.append([_back(back_cb)])
    return InlineKeyboardMarkup(rows)


def qty_keyboard(action, extra="", back_cb="m:main"):
    buttons = [
        _btn(str(v), f"qty:{action}:{extra}:{v}", style="primary")
        for v in PRESET_QTYS
    ]
    rows = _rows(buttons, 3)
    rows.append([_btn("✏️ تعداد دلخواه", f"qtycustom:{action}:{extra}", style="success")])
    rows.append([_back(back_cb)])
    return InlineKeyboardMarkup(rows)


# ----------------------------------------------------------------------------
# منوی اصلی
# ----------------------------------------------------------------------------

def main_menu_keyboard(user_id=None, is_private=True):
    rows = [
        [_btn("👤 پروفایل", "m:profile", style="primary"),
         _btn("💼 شغل و کار", "m:jobs", style="primary")],
        [_btn("🧩 مهارت‌ها", "m:skills", style="primary"),
         _btn("🎓 آموزش", "m:school", style="primary")],
        [_btn("🏦 بانک", "m:bank", style="primary"),
         _btn("🛡 بیمه", "m:insurance", style="primary")],
        [_btn("🏠 فروشگاه", "m:shop", style="primary"),
         _btn("🛒 بازار آزاد", "m:market", style="primary")],
        [_btn("📈 بورس", "m:stocks", style="primary"),
         _btn("🏢 شرکت من", "m:company", style="primary")],
        [_btn("❤️ زندگی روزمره", "m:life", style="primary"),
         _btn("🎮 سرگرمی", "m:fun", style="primary")],
        [_btn("🎯 ماموریت‌ها", "m:mission", style="primary"),
         _btn("🌟 ماموریت‌های ویژه", "m:missionshub", style="primary")],
        [_btn("👪 خانواده", "m:family", style="primary"),
         _btn("👥 دوستان", "m:friends", style="primary")],
        [_btn("⚖️ دادگاه", "m:court", style="primary"),
         _btn("🏆 رتبه‌بندی", "m:top", style="primary")],
        [_btn("🌍 وضعیت جهانی", "m:world", style="primary"),
         _btn("🔔 اعلان‌ها", "m:notif", style="primary")],
        [_btn("ℹ️ راهنما", "m:help", style="primary")],
    ]
    if user_id is not None:
        role = get_gov_role(user_id)
        if role:
            role_cb = {"police": "m:policepanel", "soldier": "m:soldierpanel",
                       "captain": "m:captainpanel", "judge": "m:judgepanel"}
            rows.append([_btn(f"{ROLE_ICONS[role]} مدیریت {ROLE_NAMES[role]}", role_cb[role], style="danger")])
    if user_id is not None and is_private and is_admin(user_id):
        rows.append([_btn("🛠 پنل مدیریت", "m:adminpanel", style="danger")])
    return InlineKeyboardMarkup(rows)


async def show_main(update_or_query, u=None):
    text = "🎮 پنل شهروندی — منوی اصلی\nیکی از بخش‌های زیر رو برای ادامه انتخاب کن:"
    if hasattr(update_or_query, "edit_message_text"):
        query = update_or_query
        is_private = query.message.chat.type == "private" if query.message else True
        kb = main_menu_keyboard(query.from_user.id, is_private)
        await _answer(query, text, kb)
    else:
        update = update_or_query
        is_private = update.effective_chat.type == "private"
        kb = main_menu_keyboard(update.effective_user.id, is_private)
        await update.message.reply_text(text, reply_markup=kb)


# ----------------------------------------------------------------------------
# پروفایل
# ----------------------------------------------------------------------------

async def render_profile(query, user_id):
    u = ensure_user(user_id, query.from_user.username or query.from_user.first_name)
    nw = net_worth(u)
    idx, title = level_for_networth(nw)
    job_name = get_job_label(u)
    text = (
        f"{u['avatar']} *پروفایل*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"📝 بیو: _{md_escape(u['bio'])}_\n"
        f"🏅 رتبه: *{title}* (سطح {idx})\n"
        f"💼 شغل: *{job_name}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💰 *دارایی*\n"
        f"▫️ پول نقد: *{fmt_money(u['cash'])}*\n"
        f"▫️ بانک: *{fmt_money(u['bank'])}*\n"
        f"▫️ دارایی خالص: *{fmt_money(nw)}*\n"
    )
    if u["loan_amount"] > 0:
        text += f"▫️ بدهی وام: *{fmt_money(u['loan_amount'])}*\n"
    text += (
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "📊 *وضعیت شخصیت*\n"
        f"⚡ انرژی   {stat_bar(u['energy'])} {u['energy']}%\n"
        f"❤️ سلامت   {stat_bar(u['health'])} {u['health']}%\n"
        f"😊 شادی    {stat_bar(u['happiness'])} {u['happiness']}%\n"
        f"😰 استرس   {stat_bar(u['stress'])} {u['stress']}%\n"
        f"🍔 گرسنگی  {stat_bar(u['hunger'])} {u['hunger']}%\n"
        f"😴 خواب    {stat_bar(u['sleep'])} {u['sleep']}%\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"🧠 هوش: *{u['intelligence']}*   ⭐ XP: *{u['xp']}*"
    )
    kb = InlineKeyboardMarkup([
        [_btn("🎒 دارایی‌های من", "m:myassets", style="primary")],
        [_back()],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


# ----------------------------------------------------------------------------
# شغل و کار
# ----------------------------------------------------------------------------

async def render_jobs(query, user_id):
    u = get_user(user_id)
    current = JOBS.get(u["job"])
    text = (
        "💼 *بازار کار*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"شغل فعلی: *{current['name'] if current else 'بیکار'}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "برای کار کردن یا تغییر شغل یکی رو انتخاب کن:"
    )
    job_icons = {
        "worker": "🛠", "seller": "🛍", "driver": "🚗", "cook": "🍳",
        "teacher": "🧑‍🏫", "nurse": "🩺", "engineer": "⚙️", "developer": "💻",
        "doctor": "🩺", "manager": "📊", "investor": "💹",
    }
    buttons = []
    for key, j in JOBS.items():
        icon = job_icons.get(key, "💼")
        buttons.append(_btn(f"{icon} {j['name']} ({j['pay']:,})", f"jobapply:{key}", style="success"))
    rows = _rows(buttons, 2)
    if u["job"]:
        rows.insert(0, [_btn("🛠 کار کن (شیفت فعلی)", "dowork", style="success")])
    rows.append([_back()])
    await _answer(query, text, InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def act_apply_job(query, user_id, key):
    job = JOBS.get(key)
    if not job:
        await query.answer("شغل نامعتبر", show_alert=True)
        return
    u = get_user(user_id)
    edu_min = JOB_EDU_MIN.get(key, 0)
    if edu_min > u.get("edu_level", 0):
        await query.answer(
            f"برای این شغل به مدرک «{edu_name(edu_min)}» نیاز داری (فعلی: {edu_name(u.get('edu_level', 0))}). با /study درس بخون.",
            show_alert=True,
        )
        return
    if job["skill"]:
        s = get_skill(user_id, job["skill"])
        if s["level"] < job["min_level"]:
            await query.answer(
                f"برای این شغل به سطح {job['min_level']} مهارت {SKILLS[job['skill']]} نیاز داری (فعلی: {s['level']})",
                show_alert=True,
            )
            return
    update_user(user_id, job=key)
    await query.answer(f"✅ شغل جدید: {job['name']}")
    await render_jobs(query, user_id)


async def act_work(query, user_id):
    u = get_user(user_id)
    if not u["job"]:
        await query.answer("اول باید یک شغل انتخاب کنی.", show_alert=True)
        return
    if is_jailed(u):
        await query.answer("🚔 تو زندانی هستی و نمی‌تونی کار کنی.", show_alert=True)
        return
    job = JOBS[u["job"]]
    now = time.time()
    elapsed_min = (now - u["last_work"]) / 60
    if elapsed_min < WORK_COOLDOWN_MIN:
        remain = int(WORK_COOLDOWN_MIN - elapsed_min)
        await query.answer(f"⏳ {remain} دقیقه دیگه صبر کن.", show_alert=True)
        return
    if u["energy"] < job["energy"]:
        await query.answer("⚡ انرژی کافی نداری.", show_alert=True)
        return

    import random
    skill_level = 1
    if job["skill"]:
        skill_level = get_skill(user_id, job["skill"])["level"]
    performance = random.uniform(0.9, 1.15) * (1 + 0.05 * (skill_level - 1))
    pay = int(job["pay"] * performance)
    new_energy = clamp(u["energy"] - job["energy"], 0, 100)
    new_stress = clamp(u["stress"] + job["stress"], 0, 100)
    new_hunger = clamp(u["hunger"] + 8, 0, 100)
    update_user(
        user_id, cash=u["cash"] + pay, energy=new_energy, stress=new_stress,
        hunger=new_hunger, xp=u["xp"] + job["xp"], last_work=now,
    )
    log_tx(user_id, "salary", pay, job["name"])
    bump_mission(user_id, "work_3")
    bump_periodic_mission(user_id, "w_work_15", period_id_week())
    bump_periodic_mission(user_id, "m_networth_up", period_id_month())
    note = ""
    if job["skill"]:
        new_level, leveled = add_skill_xp(user_id, job["skill"], job["xp"])
        if leveled:
            note = f" 🎉 مهارت به سطح {new_level} رسید!"
    await query.answer(f"💰 +{pay:,} تومان{note}")
    await render_jobs(query, user_id)


# ----------------------------------------------------------------------------
# مهارت‌ها
# ----------------------------------------------------------------------------

async def render_skills(query, user_id):
    lines = ["🧩 *مهارت‌های شما*", "_(برای تمرین رو دکمه‌ش بزن)_", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    skill_icons = {
        "programming": "💻", "medicine": "🩺", "management": "📊", "business": "💼",
        "driving": "🚗", "cooking": "🍳", "farming": "🌾", "art": "🎨", "investing": "💹",
    }
    buttons = []
    for key, name in SKILLS.items():
        s = get_skill(user_id, key)
        needed = s["level"] * 100
        bar = stat_bar(s["xp"], needed, length=8) if needed else "▰" * 8
        lines.append(f"▫️ {name} — سطح *{s['level']}*\n   {bar} {s['xp']}/{needed}")
        icon = skill_icons.get(key, "📚")
        buttons.append(_btn(f"{icon} {name}", f"train:{key}", style="primary"))
    rows = _rows(buttons, 2)
    rows.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def act_train(query, user_id, skill):
    if skill not in SKILLS:
        await query.answer("مهارت نامعتبر", show_alert=True)
        return
    u = get_user(user_id)
    now = time.time()
    elapsed_min = (now - u["last_train"]) / 60
    if elapsed_min < TRAIN_COOLDOWN_MIN:
        remain = int(TRAIN_COOLDOWN_MIN - elapsed_min)
        await query.answer(f"⏳ {remain} دقیقه دیگه صبر کن.", show_alert=True)
        return
    if u["energy"] < 15:
        await query.answer("⚡ انرژی کافی نداری.", show_alert=True)
        return
    import random
    gained_xp = random.randint(8, 20) + u["intelligence"] // 5
    new_level, leveled = add_skill_xp(user_id, skill, gained_xp)
    update_user(user_id, energy=clamp(u["energy"] - 15, 0, 100), last_train=now, intelligence=u["intelligence"] + 1)
    bump_mission(user_id, "train_2")
    bump_periodic_mission(user_id, "w_train_10", period_id_week())
    msg = f"📚 +{gained_xp} XP"
    if leveled:
        msg += f" 🎉 سطح {new_level}!"
    await query.answer(msg)
    await render_skills(query, user_id)


# ----------------------------------------------------------------------------
# بانک
# ----------------------------------------------------------------------------

def credit_tier(score: int):
    if score >= 750:
        return "🥇 طلایی"
    if score >= 550:
        return "🥈 نقره‌ای"
    return "🥉 برنزی"


async def render_bank(query, user_id):
    u = get_user(user_id)
    max_loan = u["credit_score"] * get_loan_max_multiplier()
    frozen = u.get("bank_frozen", 0) == 1
    status_line = "🧊 مسدود" if frozen else "🟢 فعال"
    text = (
        "🏦 *بانک شهروندی*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💳 *کارت بانکی*\n"
        f"`{format_card_number(u.get('card_number'))}`\n"
        f"وضعیت: {status_line}\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💰 *موجودی‌ها*\n"
        f"▫️ پول نقد: *{fmt_money(u['cash'])}*\n"
        f"▫️ حساب بانکی: *{fmt_money(u['bank'])}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "📊 *اعتبار*\n"
        f"{credit_tier(u['credit_score'])} — امتیاز {u['credit_score']}\n"
        f"▫️ بدهی وام: *{fmt_money(u['loan_amount'])}*\n"
        f"▫️ سقف وام: *{fmt_money(max_loan)}*"
    )
    kb = InlineKeyboardMarkup([
        [_btn("⬇️ واریز", "m:dep", style="success"), _btn("⬆️ برداشت", "m:with", style="primary")],
        [_btn("💳 دریافت وام", "m:loan", style="primary"), _btn("✅ بازپرداخت وام", "m:payl", style="success")],
        [_btn("🔄 انتقال با شماره کارت", "banktransfer:", style="success")],
        [_back()],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


FROZEN_MSG = "🧊 حساب بانکی شما مسدود شده و امکان انجام این عملیات نیست. برای پیگیری با مدیریت تماس بگیر."


async def do_deposit(user_id, amount):
    u = get_user(user_id)
    if u.get("bank_frozen", 0) == 1:
        return FROZEN_MSG
    if amount > u["cash"]:
        return "❌ پول نقد کافی نداری."
    update_user(user_id, cash=u["cash"] - amount, bank=u["bank"] + amount)
    return f"✅ {fmt_money(amount)} واریز شد."


async def do_card_transfer(sender_id, card_number, amount, context):
    if amount <= 0:
        return "مبلغ نامعتبره."
    sender = get_user(sender_id)
    if sender.get("bank_frozen", 0) == 1:
        return FROZEN_MSG
    if amount > sender["bank"]:
        return "❌ موجودی بانکی کافی نیست."
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE card_number=?", (card_number,)).fetchone()
    if not row:
        return "❌ همچین کارتی پیدا نشد."
    recipient = dict(row)
    if recipient["user_id"] == sender_id:
        return "نمی‌تونی به کارت خودت انتقال بدی!"
    if recipient.get("bank_frozen", 0) == 1:
        return "❌ حساب مقصد مسدوده و نمی‌تونه پول دریافت کنه."
    update_user(sender_id, bank=sender["bank"] - amount)
    new_recipient_bank = recipient["bank"] + amount
    update_user(recipient["user_id"], bank=new_recipient_bank)
    log_tx(sender_id, "transfer_out", -amount, f"به کارت {format_card_number(card_number)}")
    log_tx(recipient["user_id"], "transfer_in", amount, f"از کاربر {sender.get('username') or sender_id}")
    sender_name = sender.get("username") or sender_id
    try:
        await context.bot.send_message(
            recipient["user_id"],
            f"💳 مبلغ {fmt_money(amount)} از طرف {sender_name} به حسابتون واریز شد.\n"
            f"💰 موجودی بانکی فعلی: {fmt_money(new_recipient_bank)}",
        )
    except Exception:
        pass
    return f"✅ {fmt_money(amount)} به کارت {format_card_number(card_number)} منتقل شد."


async def do_withdraw(user_id, amount):
    u = get_user(user_id)
    if u.get("bank_frozen", 0) == 1:
        return FROZEN_MSG
    if amount > u["bank"]:
        return "❌ موجودی بانکی کافی نیست."
    update_user(user_id, cash=u["cash"] + amount, bank=u["bank"] - amount)
    return f"✅ {fmt_money(amount)} برداشت شد."


async def do_loan(user_id, amount):
    u = get_user(user_id)
    if u.get("bank_frozen", 0) == 1:
        return FROZEN_MSG
    max_loan = u["credit_score"] * get_loan_max_multiplier()
    if u["loan_amount"] + amount > max_loan:
        return f"❌ سقف وام تو {fmt_money(max_loan)} است."
    total_due = int(amount * (1 + LOAN_INTEREST))
    update_user(user_id, cash=u["cash"] + amount, loan_amount=u["loan_amount"] + total_due,
                      credit_score=max(300, u["credit_score"] - 15))
    return f"💰 وام {fmt_money(amount)} گرفتی. بدهی با سود: {fmt_money(total_due)}"


async def do_payloan(user_id, amount):
    u = get_user(user_id)
    if u.get("bank_frozen", 0) == 1:
        return FROZEN_MSG
    if amount > u["cash"]:
        return "❌ پول نقد کافی نداری."
    pay = min(amount, u["loan_amount"])
    update_user(user_id, cash=u["cash"] - pay, loan_amount=u["loan_amount"] - pay,
                      credit_score=min(900, u["credit_score"] + 10))
    return f"✅ {fmt_money(pay)} بازپرداخت شد."


# ----------------------------------------------------------------------------
# فروشگاه (املاک / وسایل نقلیه)
# ----------------------------------------------------------------------------

async def render_shop(query, user_id):
    kb = InlineKeyboardMarkup([
        [_btn("🏠 املاک", "m:shopprop", style="primary"),
         _btn("🚗 وسایل نقلیه", "m:shopveh", style="primary")],
        [_back()],
    ])
    text = "🏪 *فروشگاه شهر*\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\nیک دسته رو انتخاب کن:"
    await _answer(query, text, kb, parse_mode="Markdown")


async def render_shop_prop(query, user_id):
    prop_icons = {
        "land_small": "🟩", "apartment": "🏢", "house": "🏡", "shop": "🏪",
        "villa": "🏰", "factory": "🏭", "hotel": "🏨",
    }
    buttons = [
        _btn(f"{prop_icons.get(key, '🏠')} {p['name']} ({p['price']:,})", f"buyprop:{key}", style="success")
        for key, p in PROPERTIES.items()
    ]
    rows = _rows(buttons, 1)
    rows.append([_back("m:shop")])
    await _answer(query, "🏠 املاک موجود:", InlineKeyboardMarkup(rows))


async def render_shop_veh(query, user_id):
    veh_icons = {
        "bike": "🚲", "motorcycle": "🏍", "car": "🚗", "luxury_car": "🏎",
        "truck": "🚚", "boat": "🛥", "helicopter": "🚁", "airplane": "✈️",
    }
    buttons = [
        _btn(f"{veh_icons.get(key, '🚗')} {v['name']} ({v['price']:,})", f"buyveh:{key}", style="success")
        for key, v in VEHICLES.items()
    ]
    rows = _rows(buttons, 1)
    rows.append([_back("m:shop")])
    await _answer(query, "🚗 وسایل نقلیه موجود:", InlineKeyboardMarkup(rows))


async def act_buy_prop(query, user_id, key):
    prop = PROPERTIES.get(key)
    if not prop:
        await query.answer("نامعتبر", show_alert=True)
        return
    u = get_user(user_id)
    total = u["cash"] + u["bank"]
    if total < prop["price"]:
        await query.answer(f"❌ دارایی کافی نداری. قیمت: {prop['price']:,}", show_alert=True)
        return
    from_cash = min(u["cash"], prop["price"])
    from_bank = prop["price"] - from_cash
    with db() as conn:
        conn.execute("UPDATE users SET cash=cash-?, bank=bank-? WHERE user_id=?", (from_cash, from_bank, user_id))
        conn.execute("INSERT INTO user_properties (user_id, prop_key) VALUES (?,?)", (user_id, key))
    log_tx(user_id, "buy_property", -prop["price"], prop["name"])
    bump_periodic_mission(user_id, "m_property", period_id_month())
    await query.answer(f"🏠 {prop['name']} خریداری شد!")
    await render_shop_prop(query, user_id)


async def act_buy_veh(query, user_id, key):
    veh = VEHICLES.get(key)
    if not veh:
        await query.answer("نامعتبر", show_alert=True)
        return
    u = get_user(user_id)
    total = u["cash"] + u["bank"]
    if total < veh["price"]:
        await query.answer(f"❌ دارایی کافی نداری. قیمت: {veh['price']:,}", show_alert=True)
        return
    from_cash = min(u["cash"], veh["price"])
    from_bank = veh["price"] - from_cash
    with db() as conn:
        conn.execute(
            "UPDATE users SET cash=cash-?, bank=bank-?, happiness=MIN(100, happiness+?) WHERE user_id=?",
            (from_cash, from_bank, veh["happiness"], user_id),
        )
        conn.execute("INSERT INTO user_vehicles (user_id, veh_key) VALUES (?,?)", (user_id, key))
    log_tx(user_id, "buy_vehicle", -veh["price"], veh["name"])
    bump_periodic_mission(user_id, "m_property", period_id_month())
    await query.answer(f"🚗 {veh['name']} خریداری شد!")
    await render_shop_veh(query, user_id)


async def render_myassets(query, user_id):
    with db() as conn:
        props = conn.execute("SELECT prop_key FROM user_properties WHERE user_id=?", (user_id,)).fetchall()
        vehs = conn.execute("SELECT veh_key FROM user_vehicles WHERE user_id=?", (user_id,)).fetchall()
    lines = ["🏠 املاک شما:"]
    lines += [f"• {PROPERTIES[p['prop_key']]['name']}" for p in props if p["prop_key"] in PROPERTIES] or ["چیزی نداری."]
    lines.append("\n🚗 وسایل نقلیه شما:")
    lines += [f"• {VEHICLES[v['veh_key']]['name']}" for v in vehs if v["veh_key"] in VEHICLES] or ["چیزی نداری."]
    kb = InlineKeyboardMarkup([[_back("m:profile")]])
    await _answer(query, "\n".join(lines), kb)


# ----------------------------------------------------------------------------
# بازار آزاد (فاز ۲)
# ----------------------------------------------------------------------------

async def render_market(query, user_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_listings WHERE quantity > 0 ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
    lines = ["🛒 آگهی‌های بازار آزاد:\n"]
    buttons = []
    for r in rows:
        lines.append(f"#{r['id']} {r['item_name']} ×{r['quantity']} — {r['price']:,}/عدد")
        buttons.append(_btn(f"🛍 خرید #{r['id']}", f"buyitem:{r['id']}", style="success"))
    if not rows:
        lines.append("بازار خالیه.")
    rows_kb = _rows(buttons, 2)
    rows_kb.append([_btn("📋 آگهی‌های من", "m:mylistings", style="primary")])
    rows_kb.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(rows_kb))


async def render_mylistings(query, user_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_listings WHERE seller_id=? AND quantity>0", (user_id,)
        ).fetchall()
    lines = ["📋 آگهی‌های شما:\n"]
    buttons = []
    for r in rows:
        lines.append(f"#{r['id']} {r['item_name']} ×{r['quantity']} — {r['price']:,}/عدد")
        buttons.append(_btn(f"❌ لغو #{r['id']}", f"cancellisting:{r['id']}", style="danger"))
    if not rows:
        lines.append("آگهی فعالی نداری.\n(برای ثبت آگهی جدید هنوز باید /sell <کالا> <تعداد> <قیمت> رو تایپ کنی چون نام کالا دلخواهه و دکمه‌پذیر نیست.)")
    rows_kb = _rows(buttons, 1)
    rows_kb.append([_back("m:market")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(rows_kb))


async def act_buy_item(query, user_id, listing_id):
    with db() as conn:
        listing = conn.execute("SELECT * FROM market_listings WHERE id=?", (listing_id,)).fetchone()
    if not listing or listing["quantity"] <= 0:
        await query.answer("این آگهی موجودی نداره.", show_alert=True)
        return
    if listing["seller_id"] == user_id:
        await query.answer("نمی‌تونی از خودت بخری!", show_alert=True)
        return
    # همیشه ۱ عدد از دکمه سریع خریداری می‌شه؛ برای تعداد بیشتر دکمه‌ی تعداد نمایش داده می‌شه
    await _answer(
        query,
        f"چند عدد از «{listing['item_name']}» (موجودی {listing['quantity']}) می‌خوای بخری؟",
        qty_keyboard("buyitem", str(listing_id), back_cb="m:market"),
    )


async def do_buy_item(user_id, listing_id, qty):
    with db() as conn:
        listing = conn.execute("SELECT * FROM market_listings WHERE id=?", (listing_id,)).fetchone()
        if not listing or listing["quantity"] <= 0:
            return "این آگهی موجودی نداره."
        if listing["seller_id"] == user_id:
            return "نمی‌تونی از خودت بخری!"
        qty = min(qty, listing["quantity"])
        total = qty * listing["price"]
        cash_row = conn.execute("SELECT cash FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not cash_row or total > cash_row["cash"]:
            return f"پول نقد کافی نداری. لازم: {total:,}"
        conn.execute("UPDATE market_listings SET quantity=quantity-? WHERE id=?", (qty, listing_id))
        conn.execute("UPDATE users SET cash=cash-? WHERE user_id=?", (total, user_id))
        conn.execute("UPDATE users SET cash=cash+? WHERE user_id=?", (total, listing["seller_id"]))
    log_tx(user_id, "market_buy", -total, listing["item_name"])
    return f"✅ {qty} عدد {listing['item_name']} خریداری شد — {total:,} تومان."


async def act_cancel_listing(query, user_id, listing_id):
    with db() as conn:
        listing = conn.execute(
            "SELECT * FROM market_listings WHERE id=? AND seller_id=?", (listing_id, user_id)
        ).fetchone()
        if not listing:
            await query.answer("پیدا نشد.", show_alert=True)
            return
        conn.execute("UPDATE market_listings SET quantity=0 WHERE id=?", (listing_id,))
    await query.answer("لغو شد.")
    await render_mylistings(query, user_id)


# ----------------------------------------------------------------------------
# بورس (فاز ۲)
# ----------------------------------------------------------------------------

async def render_stocks(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT * FROM stocks").fetchall()
    lines = ["📈 بازار بورس:\n"]
    buttons = []
    for r in rows:
        lines.append(f"{r['symbol']} ({r['name']}) — {r['price']:,}")
        buttons.append(_btn(f"📊 {r['symbol']}", f"stocksym:{r['symbol']}", style="primary"))
    rows_kb = _rows(buttons, 3)
    rows_kb.append([_btn("💼 پرتفوی من", "m:mystocks", style="primary")])
    rows_kb.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(rows_kb))


async def render_stock_detail(query, user_id, symbol):
    with db() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        owned = conn.execute(
            "SELECT * FROM user_stocks WHERE user_id=? AND symbol=?", (user_id, symbol)
        ).fetchone()
    if not stock:
        await query.answer("نماد نامعتبر", show_alert=True)
        return
    owned_qty = owned["qty"] if owned else 0
    text = f"📈 {stock['name']} ({symbol})\nقیمت فعلی: {stock['price']:,}\nسهام شما: {owned_qty}"
    kb = InlineKeyboardMarkup([
        [_btn("خرید", f"m:stockbuy:{symbol}", style="success"),
         _btn("فروش", f"m:stocksell:{symbol}", style="danger")],
        [_back("m:stocks")],
    ])
    await _answer(query, text, kb)


async def do_stock_trade(user_id, symbol, qty, side):
    with db() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        if not stock:
            return "نماد نامعتبر."
        if side == "buy":
            total = qty * stock["price"]
            u_row = conn.execute("SELECT cash FROM users WHERE user_id=?", (user_id,)).fetchone()
            if total > u_row["cash"]:
                return f"پول نقد کافی نداری. لازم: {total:,}"
            new_price = int(stock["price"] * (1 + 0.01 * qty))
            conn.execute("UPDATE stocks SET price=? WHERE symbol=?", (new_price, symbol))
            existing = conn.execute(
                "SELECT * FROM user_stocks WHERE user_id=? AND symbol=?", (user_id, symbol)
            ).fetchone()
            if existing:
                conn.execute("UPDATE user_stocks SET qty=qty+? WHERE user_id=? AND symbol=?", (qty, user_id, symbol))
            else:
                conn.execute("INSERT INTO user_stocks (user_id, symbol, qty) VALUES (?,?,?)", (user_id, symbol, qty))
            conn.execute("UPDATE users SET cash=cash-? WHERE user_id=?", (total, user_id))
            result = f"✅ {qty} سهم {symbol} خریداری شد — {total:,} تومان.\n📈 قیمت جدید: {new_price:,}"
        else:
            owned = conn.execute(
                "SELECT * FROM user_stocks WHERE user_id=? AND symbol=?", (user_id, symbol)
            ).fetchone()
            if not owned or owned["qty"] < qty:
                return "این تعداد سهم رو نداری."
            total = qty * stock["price"]
            new_price = max(100, int(stock["price"] * (1 - 0.01 * qty)))
            conn.execute("UPDATE stocks SET price=? WHERE symbol=?", (new_price, symbol))
            conn.execute("UPDATE user_stocks SET qty=qty-? WHERE user_id=? AND symbol=?", (qty, user_id, symbol))
            conn.execute("UPDATE users SET cash=cash+? WHERE user_id=?", (total, user_id))
            result = f"✅ {qty} سهم {symbol} فروخته شد — {total:,} تومان.\n📉 قیمت جدید: {new_price:,}"
    # لاگ تراکنش بعد از بسته‌شدن (کامیت) تراکنش اصلی، تا دیتابیس قفل نگیره
    log_tx(user_id, "buy_stock" if side == "buy" else "sell_stock", -total if side == "buy" else total, symbol)
    return result


async def render_mystocks(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT * FROM user_stocks WHERE user_id=? AND qty>0", (user_id,)).fetchall()
    lines = ["💼 پرتفوی سهام شما:\n"]
    with db() as conn:
        for r in rows:
            stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (r["symbol"],)).fetchone()
            value = r["qty"] * stock["price"] if stock else 0
            lines.append(f"• {r['symbol']} × {r['qty']} — ارزش: {value:,}")
    if not rows:
        lines.append("سهامی نداری.")
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup([[_back("m:stocks")]]))


# ----------------------------------------------------------------------------
# شرکت من (فاز ۲) — ساخت شرکت هنوز نیاز به تایپ اسم داره (نام دلخواهه)
# ----------------------------------------------------------------------------

async def render_company(query, user_id):
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (user_id,)).fetchone()
    if not c:
        text = (
            "🏢 *شرکت من*\n"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "شرکتی نداری.\n"
            "برای ساخت شرکت باید نام دلخواهت رو تایپ کنی:\n"
            "`/createcompany <نام>`\n"
            "_(چون اسم شرکت کاملاً دلخواهه و قابل تبدیل به دکمه نیست)_"
        )
        await _answer(query, text, InlineKeyboardMarkup([[_back()]]), parse_mode="Markdown")
        return
    with db() as conn:
        employees = conn.execute("SELECT * FROM company_employees WHERE company_id=?", (c["id"],)).fetchall()
    text = (
        f"🏢 *{md_escape(c['name'])}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"💰 سرمایه: *{fmt_money(c['capital'])}*\n"
        f"📈 سطح: *{c['level']}*\n"
        f"👥 کارمندان: *{len(employees)}* نفر"
    )
    kb = InlineKeyboardMarkup([
        [_btn("💰 افزایش سرمایه", "m:cinv", style="success")],
        [_btn("💵 پرداخت حقوق", "doact:payroll:", style="primary")],
        [_back()],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


async def do_companyinvest(user_id, amount):
    u = get_user(user_id)
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (user_id,)).fetchone()
        if not c:
            return "شرکتی نداری."
        if amount > u["cash"]:
            return "پول نقد کافی نداری."
        new_capital = c["capital"] + amount
        new_level = 1 + new_capital // 1_000_000
        conn.execute("UPDATE companies SET capital=?, level=? WHERE id=?", (new_capital, new_level, c["id"]))
    update_user(user_id, cash=u["cash"] - amount)
    return f"✅ {amount:,} به سرمایه شرکت اضافه شد."


async def do_payroll(user_id):
    with db() as conn:
        c = conn.execute("SELECT * FROM companies WHERE owner_id=?", (user_id,)).fetchone()
        if not c:
            return "شرکتی نداری."
        employees = conn.execute("SELECT * FROM company_employees WHERE company_id=?", (c["id"],)).fetchall()
        total = sum(e["salary"] for e in employees)
        if total > c["capital"]:
            return f"سرمایه کافی نیست. لازم: {total:,}"
        for e in employees:
            conn.execute("UPDATE users SET cash=cash+? WHERE user_id=?", (e["salary"], e["user_id"]))
        conn.execute("UPDATE companies SET capital=capital-? WHERE id=?", (total, c["id"]))
    return f"✅ حقوق {len(employees)} کارمند پرداخت شد ({total:,} تومان)."


async def do_study(user_id):
    u = get_user(user_id)
    jailed_msg = require_not_jailed(u)
    if jailed_msg:
        return jailed_msg
    if u["energy"] < 15:
        return "انرژی کافی نداری (حداقل ۱۵ لازمه). یکم بخواب."
    gained = random.randint(15, 30) + u.get("intelligence", 10) // 5
    new_xp = u.get("edu_xp", 0) + gained
    old_level = u.get("edu_level", 0)
    new_level = edu_level_for_xp(new_xp)
    update_user(
        user_id,
        edu_xp=new_xp,
        edu_level=new_level,
        energy=clamp(u["energy"] - 15, 0, 100),
        intelligence=u.get("intelligence", 10) + 1,
        xp=u["xp"] + 10,
    )
    bump_periodic_mission(user_id, "w_study_5", period_id_week())
    msg = f"📖 درس خوندی و {gained} امتیاز تحصیلی گرفتی. (مجموع: {new_xp})"
    if new_level > old_level:
        msg += f" 🎓 به مدرک «{edu_name(new_level)}» رسیدی!"
    return msg


async def do_buyinsurance(user_id):
    u = get_user(user_id)
    if u.get("insurance_until", 0) > time.time():
        return "بیمه‌ات همین الان هم فعاله."
    if u["cash"] < INSURANCE_PRICE:
        return f"پول نقد کافی نداری. قیمت بیمه: {fmt_money(INSURANCE_PRICE)}"
    until = time.time() + INSURANCE_DAYS * 86400
    update_user(user_id, cash=u["cash"] - INSURANCE_PRICE, insurance_until=until)
    log_tx(user_id, "insurance", -INSURANCE_PRICE, f"{INSURANCE_DAYS} روز")
    return f"🛡 بیمه‌ی {INSURANCE_DAYS} روزه فعال شد! جریمه و زندان دزدی برات نصف می‌شه."


async def do_court(user_id, report_id):
    u = get_user(user_id)
    with db() as conn:
        report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        return "چنین گزارشی وجود نداره."
    if report["target_id"] != u["user_id"]:
        return "این گزارش علیه تو نیست."
    if report["reviewed"]:
        return "این گزارش قبلاً بررسی شده."
    if u["cash"] < COURT_FEE:
        return f"پول نقد کافی برای هزینه‌ی دادخواهی نداری ({fmt_money(COURT_FEE)})."
    update_user(u["user_id"], cash=u["cash"] - COURT_FEE)
    win_chance = clamp(0.3 + (u["credit_score"] - 500) / 1000, 0.1, 0.8)
    won = random.random() < win_chance
    with db() as conn:
        conn.execute("UPDATE reports SET reviewed=1 WHERE id=?", (report_id,))
        conn.execute(
            "INSERT INTO court_cases (report_id, defendant_id, outcome) VALUES (?,?,?)",
            (report_id, u["user_id"], "won" if won else "lost"),
        )
    if won:
        update_user(u["user_id"], credit_score=clamp(u["credit_score"] + 15, 300, 900))
        reporter = get_user(report["reporter_id"])
        if reporter:
            update_user(reporter["user_id"], credit_score=clamp(reporter["credit_score"] - 20, 300, 900))
        return "⚖️ دادگاه به نفعت رأی داد! گزارش رد شد و اعتبارت بالا رفت."
    fine = 4000
    update_user(u["user_id"], cash=max(0, u["cash"] - fine), credit_score=clamp(u["credit_score"] - 25, 300, 900))
    return f"⚖️ دادگاه علیه‌ت رأی داد. {fmt_money(fine)} جریمه شدی."


async def do_unfriend(user_id, friend_id):
    with db() as conn:
        conn.execute("DELETE FROM friendships WHERE user_id=? AND friend_id=?", (user_id, friend_id))
        conn.execute("DELETE FROM friendships WHERE user_id=? AND friend_id=?", (friend_id, user_id))
    return "دوستی حذف شد."


# ----------------------------------------------------------------------------
# زندگی روزمره
# ----------------------------------------------------------------------------

async def render_life(query, user_id):
    kb = InlineKeyboardMarkup([
        [_btn("😴 خواب", "doact:sleep:", style="primary"), _btn("🍔 غذا", "doact:eat:", style="primary")],
        [_btn("🏋️ باشگاه", "doact:gym:", style="primary"), _btn("🍽 رستوران", "doact:restaurant:", style="primary")],
        [_back()],
    ])
    text = "❤️ *زندگی روزمره*\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\nیکی رو انتخاب کن:"
    await _answer(query, text, kb, parse_mode="Markdown")


async def do_sleep(user_id):
    u = get_user(user_id)
    now = time.time()
    elapsed_min = (now - u["last_sleep"]) / 60
    if elapsed_min < SLEEP_COOLDOWN_MIN:
        remain = int(SLEEP_COOLDOWN_MIN - elapsed_min)
        return f"⏳ هنوز خسته نیستی، {remain} دقیقه دیگه صبر کن."
    update_user(user_id, energy=100, sleep=100, stress=clamp(u["stress"] - 30, 0, 100), last_sleep=now)
    bump_mission(user_id, "sleep_1")
    return "😴 خوب خوابیدی! انرژی و خواب کامل شد."


async def do_eat(user_id):
    u = get_user(user_id)
    cost = 500
    if u["cash"] < cost:
        return "💸 پول نقد کافی نداری."
    update_user(user_id, cash=u["cash"] - cost, hunger=clamp(u["hunger"] - 40, 0, 100),
                      happiness=clamp(u["happiness"] + 5, 0, 100))
    log_tx(user_id, "food", -cost, "غذا")
    bump_mission(user_id, "eat_2")
    return "🍔 غذا خوردی."


async def do_gym(user_id):
    u = get_user(user_id)
    cost = 1000
    if u["cash"] < cost:
        return "پول نقد کافی نداری."
    if u["energy"] < 15:
        return "انرژی کافی نداری."
    update_user(user_id, cash=u["cash"] - cost, energy=clamp(u["energy"] - 15, 0, 100),
                      health=clamp(u["health"] + 10, 0, 100), happiness=clamp(u["happiness"] + 5, 0, 100))
    return "🏋️ تمرین کردی! سلامت و شادی بالا رفت."


async def do_restaurant(user_id):
    u = get_user(user_id)
    cost = 3000
    if u["cash"] < cost:
        return f"پول نقد کافی نداری (هزینه: {cost:,})."
    update_user(user_id, cash=u["cash"] - cost, hunger=clamp(u["hunger"] - 60, 0, 100),
                      happiness=clamp(u["happiness"] + 15, 0, 100))
    return "🍽 شام خوبی خوردی!"


# ----------------------------------------------------------------------------
# سرگرمی
# ----------------------------------------------------------------------------

async def render_fun(query, user_id):
    kb = InlineKeyboardMarkup([
        [_btn("🎣 ماهی‌گیری", "doact:fish:", style="primary"), _btn("⛏ معدن", "doact:mine:", style="primary")],
        [_btn("🌾 کشاورزی", "doact:farm:", style="primary"), _btn("🧩 معما", "doact:puzzle:", style="primary")],
        [_back()],
    ])
    text = "🎮 *سرگرمی*\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\nیکی رو انتخاب کن:"
    await _answer(query, text, kb, parse_mode="Markdown")


async def _minigame(user_id, energy_cost, skill, base_reward, label, emoji):
    import random
    u = get_user(user_id)
    if u["energy"] < energy_cost:
        return "انرژی کافی نداری."
    reward = int(base_reward * random.uniform(0.7, 1.4))
    update_user(user_id, energy=clamp(u["energy"] - energy_cost, 0, 100), cash=u["cash"] + reward)
    new_level, leveled = add_skill_xp(user_id, skill, random.randint(5, 12))
    msg = f"{emoji} {label} انجام شد! +{reward:,}"
    if leveled:
        msg += f" 🎉 سطح {new_level}!"
    return msg


async def do_fish(user_id):
    return await _minigame(user_id, 12, "farming", 1500, "ماهی‌گیری", "🎣")


async def do_mine(user_id):
    return await _minigame(user_id, 20, "business", 2500, "معدن", "⛏")


async def do_farm(user_id):
    return await _minigame(user_id, 15, "farming", 1800, "کشاورزی", "🌾")


async def do_puzzle(user_id):
    import random
    u = get_user(user_id)
    reward = random.randint(500, 1500)
    q, _ = random.choice(RIDDLES)
    update_user(user_id, cash=u["cash"] + reward, xp=u["xp"] + 5, intelligence=u["intelligence"] + 1)
    return f"🧩 {q}\n\n+{reward:,} تومان و XP هوش"


# ----------------------------------------------------------------------------
# ماموریت‌ها
# ----------------------------------------------------------------------------

async def render_mission(query, user_id):
    missions = get_missions_today(user_id)
    lines = ["🎯 *ماموریت‌های امروز*", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    buttons = []
    for m in missions:
        status = "✅ کامل" if m["progress"] >= m["target"] else f"{m['progress']}/{m['target']}"
        claimed = " _(گرفته شد)_" if m["claimed"] else ""
        lines.append(f"▫️ {m['desc']} — *{status}*{claimed}")
        if m["progress"] >= m["target"] and not m["claimed"]:
            buttons.append(_btn(f"🎁 دریافت: {m['desc'][:15]}", f"claim:{m['key']}", style="success"))
    rows = _rows(buttons, 1)
    rows.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def act_claim(query, user_id, key):
    mission_def = next((m for m in DAILY_MISSIONS if m["key"] == key), None)
    if not mission_def:
        await query.answer("نامعتبر", show_alert=True)
        return
    day = today_str()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_missions WHERE user_id=? AND mission_key=? AND day=?", (user_id, key, day)
        ).fetchone()
    progress = row["progress"] if row else 0
    claimed = row["claimed"] if row else 0
    if claimed or progress < mission_def["target"]:
        await query.answer("هنوز آماده نیست.", show_alert=True)
        return
    with db() as conn:
        conn.execute(
            "UPDATE daily_missions SET claimed=1 WHERE user_id=? AND mission_key=? AND day=?", (user_id, key, day)
        )
    u = get_user(user_id)
    update_user(user_id, cash=u["cash"] + mission_def["reward_cash"], xp=u["xp"] + mission_def["reward_xp"])
    await query.answer(f"🎉 +{mission_def['reward_cash']:,} تومان")
    await render_mission(query, user_id)


# ----------------------------------------------------------------------------
# خانواده
# ----------------------------------------------------------------------------

async def render_family(query, user_id):
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (user_id,)).fetchone()
    if not fam or not fam["spouse_id"]:
        text = (
            "👪 *خانواده*\n"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "تو الان مجرد هستی.\n"
            "برای خواستگاری باید روی پیام فرد موردنظر توی گروه Reply بزنی و بنویسی `/propose`\n"
            "_(چون انتخاب یک نفر مشخص از بین اعضا با دکمه ممکن نیست)_"
        )
        await _answer(query, text, InlineKeyboardMarkup([[_back()]]), parse_mode="Markdown")
        return
    spouse = get_user(fam["spouse_id"])
    spouse_name = spouse["username"] if spouse else str(fam["spouse_id"])
    text = (
        "👪 *خانواده*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"👨‍👩‍👧 همسر: *{md_escape(spouse_name)}*\n"
        f"👶 فرزندان: *{fam['children']}*"
    )
    kb = InlineKeyboardMarkup([
        [_btn("👶 بچه‌دار شوید (۱۰۰,۰۰۰)", "doact:havechild:", style="success")],
        [_btn("💔 طلاق", "confirm:divorce:", style="danger")],
        [_back()],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


async def do_havechild(user_id):
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (user_id,)).fetchone()
        if not fam or not fam["spouse_id"]:
            return "اول باید ازدواج کنی."
        u_row = conn.execute("SELECT cash, happiness FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not u_row or u_row["cash"] < CHILD_COST:
            return f"پول نقد کافی نداری (هزینه: {CHILD_COST:,})."
        conn.execute("UPDATE families SET children=children+1 WHERE user_id=?", (user_id,))
        conn.execute("UPDATE families SET children=children+1 WHERE user_id=?", (fam["spouse_id"],))
        new_happiness = clamp(u_row["happiness"] + 15, 0, 100)
        conn.execute(
            "UPDATE users SET cash=cash-?, happiness=? WHERE user_id=?",
            (CHILD_COST, new_happiness, user_id),
        )
    return "👶 تبریک! صاحب فرزند شدید."


async def do_divorce(user_id):
    with db() as conn:
        fam = conn.execute("SELECT * FROM families WHERE user_id=?", (user_id,)).fetchone()
        if not fam or not fam["spouse_id"]:
            return "الان متأهل نیستی."
        spouse_id = fam["spouse_id"]
        conn.execute("UPDATE families SET spouse_id=NULL WHERE user_id=?", (user_id,))
        conn.execute("UPDATE families SET spouse_id=NULL WHERE user_id=?", (spouse_id,))
    u = get_user(user_id)
    update_user(user_id, married_to=None, happiness=clamp(u["happiness"] - 20, 0, 100))
    update_user(spouse_id, married_to=None)
    return "💔 طلاق ثبت شد."


# ----------------------------------------------------------------------------
# رتبه‌بندی / وضعیت جهانی / اعلان‌ها / راهنما
# ----------------------------------------------------------------------------

async def render_top(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    users = [dict(r) for r in rows]
    ranked = sorted(users, key=lambda u: net_worth(u), reverse=True)[:10]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 *ثروتمندترین شهروندان*", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    for i, u in enumerate(ranked, 1):
        name = md_escape(u["username"] or f"کاربر {u['user_id']}")
        rank = medals.get(i, f"{i}.")
        lines.append(f"{rank} {name} — *{fmt_money(net_worth(u))}*")
    kb_rows = [[_back()]]
    kb_rows.insert(0, [_btn("🏢 برترین شرکت‌ها", "m:companiestop", style="primary"),
                       _btn("🏙 برترین شهرها", "m:citytop", style="primary")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(kb_rows), parse_mode="Markdown")


async def render_companiestop(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT * FROM companies ORDER BY capital DESC LIMIT 10").fetchall()
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 *بزرگ‌ترین شرکت‌ها*", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    for i, c in enumerate(rows, 1):
        rank = medals.get(i, f"{i}.")
        lines.append(f"{rank} {md_escape(c['name'])} — *{fmt_money(c['capital'])}*")
    if not rows:
        lines.append("هنوز شرکتی نیست.")
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup([[_back("m:top")]]), parse_mode="Markdown")


async def render_citytop(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT * FROM cities ORDER BY score DESC LIMIT 10").fetchall()
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 *برترین شهرها*", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    for i, c in enumerate(rows, 1):
        rank = medals.get(i, f"{i}.")
        lines.append(f"{rank} {md_escape(c['name'])} — امتیاز *{c['score']}*")
    if not rows:
        lines.append("هنوز شهری نیست.")
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup([[_back("m:top")]]), parse_mode="Markdown")


async def render_world(query, user_id):
    with db() as conn:
        state = conn.execute("SELECT * FROM world_state WHERE id=1").fetchone()
    text = (
        "🌍 *وضعیت جهانی*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"{md_escape(state['description'])}\n"
        f"📊 ضریب تاثیر بر حقوق: *×{state['pay_multiplier']}*"
    )
    await _answer(query, text, InlineKeyboardMarkup([[_back()]]), parse_mode="Markdown")


async def render_notif(query, user_id):
    u = get_user(user_id)
    lines = ["🔔 *اعلان‌های شما*", "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"]
    with db() as conn:
        proposals = conn.execute(
            "SELECT * FROM proposals WHERE target_id=? AND status='pending'", (user_id,)
        ).fetchall()
    for p in proposals:
        proposer = get_user(p["proposer_id"])
        name = md_escape(proposer["username"] if proposer else str(p["proposer_id"]))
        lines.append(f"💍 پیشنهاد ازدواج از *{name}* — روی پیامش Reply بزن و بنویس `/accept`")
    if u["loan_amount"] > 0:
        lines.append(f"💳 بدهی وام: *{fmt_money(u['loan_amount'])}*")
    if is_jailed(u):
        lines.append("🚔 الان زندانی هستی.")
    if len(lines) == 2:
        lines.append("چیزی نداری. ✨")
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup([[_back()]]), parse_mode="Markdown")


async def render_school(query, user_id):
    u = get_user(user_id)
    lvl = u.get("edu_level", 0)
    xp = u.get("edu_xp", 0)
    nxt = lvl + 1 if lvl < EDU_LEVELS[-1][0] else None
    lines = [f"🎓 مدرک فعلی: {edu_name(lvl)} ({xp} امتیاز تحصیلی)"]
    if nxt is not None:
        remain = max(0, EDU_LEVELS[nxt][2] - xp)
        lines.append(f"برای رسیدن به «{edu_name(nxt)}» {remain} امتیاز دیگه لازمه.")
    else:
        lines.append("به بالاترین مدرک تحصیلی رسیدی!")
    lines.append("\nمشاغلی که به مدرک نیاز دارن:")
    for k, v in JOB_EDU_MIN.items():
        lines.append(f"• {JOBS[k]['name']} ← {edu_name(v)}")
    kb = InlineKeyboardMarkup([[_btn("📖 درس بخون", "doact:study:", style="success")], [_back()]])
    await _answer(query, "\n".join(lines), kb)


async def render_insurance(query, user_id):
    u = get_user(user_id)
    active = u.get("insurance_until", 0) > time.time()
    if active:
        remain_days = int((u["insurance_until"] - time.time()) / 86400) + 1
        text = f"🛡 بیمه‌ات فعاله و {remain_days} روز دیگه اعتبار داره."
        kb = InlineKeyboardMarkup([[_back()]])
    else:
        text = (
            f"بیمه نداری.\nقیمت: {fmt_money(INSURANCE_PRICE)} برای {INSURANCE_DAYS} روز.\n"
            "با بیمه، جریمه و زندان دزدی برات نصف می‌شه."
        )
        kb = InlineKeyboardMarkup([[_btn("🛡 خرید بیمه", "doact:buyinsurance:", style="success")], [_back()]])
    await _answer(query, text, kb)


async def render_friends(query, user_id):
    with db() as conn:
        rows = conn.execute("SELECT friend_id FROM friendships WHERE user_id=?", (user_id,)).fetchall()
    lines = ["👥 لیست دوستان:\n"]
    buttons = []
    if not rows:
        lines.append("هنوز دوستی نداری.")
    for r in rows:
        fu = get_user(r["friend_id"])
        if fu:
            lines.append(f"• {fu.get('username') or fu['user_id']}")
            buttons.append([_btn(f"❌ حذف {fu.get('username') or fu['user_id']}", f"unfriend:{fu['user_id']}", style="danger")])
    lines.append("\nبرای افزودن دوست، روی پیام فرد ریپلای کن و بنویس: /addfriend")
    buttons.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_court(query, user_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM reports WHERE target_id=? AND reviewed=0 ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()
    lines = [f"⚖️ دادگاه (هزینه‌ی هر دادخواهی: {fmt_money(COURT_FEE)})\n"]
    buttons = []
    if not rows:
        lines.append("هیچ گزارش بازی علیه‌ت وجود نداره.")
    for r in rows:
        lines.append(f"#{r['id']} — دلیل: {r['reason']}")
        buttons.append([_btn(f"⚖️ دادخواهی #{r['id']}", f"court:{r['id']}", style="primary")])
    buttons.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_missionshub(query, user_id):
    kb = InlineKeyboardMarkup([
        [_btn("🗓 هفتگی", "m:weeklym", style="primary"), _btn("📅 ماهانه", "m:monthlym", style="primary")],
        [_btn("🕵️ مخفی", "m:secretm", style="primary")],
        [_back()],
    ])
    await _answer(query, "🎯 ماموریت‌های ویژه — یکی رو انتخاب کن:", kb)


async def _render_periodic_missions(query, user_id, title, definitions, period_id, period_key):
    missions = get_periodic_missions(user_id, definitions, period_id)
    lines = [f"{title}\n"]
    buttons = []
    for m in missions:
        progress = min(m["progress"], m["target"])
        if m["claimed"]:
            lines.append(f"• {m['desc']} — {progress}/{m['target']} ✅")
        elif progress >= m["target"]:
            lines.append(f"• {m['desc']} — {progress}/{m['target']} 🎁")
            buttons.append([_btn(f"🎁 دریافت: {m['desc'][:20]}", f"claimp:{period_key}:{m['key']}", style="success")])
        else:
            lines.append(f"• {m['desc']} — {progress}/{m['target']}")
    buttons.append([_back("m:missionshub")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_weeklym(query, user_id):
    await _render_periodic_missions(query, user_id, "🗓 ماموریت‌های هفتگی:", WEEKLY_MISSIONS, period_id_week(), "weekly")


async def render_monthlym(query, user_id):
    await _render_periodic_missions(query, user_id, "📅 ماموریت‌های ماهانه:", MONTHLY_MISSIONS, period_id_month(), "monthly")


async def render_secretm(query, user_id):
    u = get_user(user_id)
    if net_worth(u) >= 1_000_000:
        current = get_periodic_missions(user_id, SECRET_MISSIONS, period_id_secret())
        s_mission = next((m for m in current if m["key"] == "s_millionaire"), None)
        if s_mission and s_mission["progress"] < s_mission["target"]:
            bump_periodic_mission(user_id, "s_millionaire", period_id_secret(), amount=s_mission["target"])
    await _render_periodic_missions(query, user_id, "🕵️ ماموریت‌های مخفی:", SECRET_MISSIONS, period_id_secret(), "secret")


async def render_help(query, user_id):
    text = (
        "ℹ️ *راهنما*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "تقریباً همه‌چیز از همین منوی دکمه‌ای قابل‌انجامه.\n"
        "فقط این چند مورد چون به «انتخاب یک فرد مشخص» یا «متن دلخواه» نیاز دارن، "
        "هنوز باید تایپ بشن _(همیشه با Reply روی پیام همون فرد، داخل گروه)_:\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "▫️ `/give <مبلغ>` — هدیه پول (ریپلای)\n"
        "▫️ `/propose` — خواستگاری (ریپلای)\n"
        "▫️ `/hire <حقوق>` — استخدام در شرکت (ریپلای)\n"
        "▫️ `/crime` — دزدی (ریپلای)\n"
        "▫️ `/vote` — رای به کاندید شهردار (ریپلای)\n"
        "▫️ `/reportcrime <دلیل>` — گزارش تخلف (ریپلای)\n"
        "▫️ `/addfriend` — افزودن دوست (ریپلای)\n"
        "▫️ `/createcompany <نام>` — چون اسم دلخواهه\n"
        "▫️ `/sell <کالا> <تعداد> <قیمت>` — ثبت آگهی با نام دلخواه\n"
        "▫️ `/registercity <نام>` — ثبت شهر (فقط داخل گروه)\n"
        "▫️ `/runformayor` — کاندیداتوری شهرداری (داخل گروه)\n"
        "▫️ `/advisor <سوالت>` — مشاور هوش مصنوعی\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "برای بازگشت به منو همیشه `/menu` رو بزن."
    )
    await _answer(query, text, InlineKeyboardMarkup([[_back()]]), parse_mode="Markdown")


# ----------------------------------------------------------------------------
# پنل مدیریت
# ----------------------------------------------------------------------------

async def render_adminpanel(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [_btn("📊 آمار کلی", "m:adminstats2", style="primary")],
        [_btn("🚩 گزارش‌های بررسی‌نشده", "m:adminreports2", style="primary")],
        [_btn("🚫 بن / رفع بن کاربر", "m:adminban", style="danger")],
        [_btn("💰 موجودی کاربر", "m:adminbalance", style="success")],
        [_btn("📢 عضویت اجباری کانال", "m:adminforcejoin", style="primary")],
        [_btn("🔧 روشن/خاموش کردن ربات", "m:adminmaintenance", style="danger")],
        [_btn("👮 مدیریت مقام‌ها", "m:adminroles", style="primary")],
        [_btn("🏦 مدیریت بانک", "m:adminbank", style="primary")],
        [_back()],
    ])
    await _answer(query, "🛠 پنل مدیریت", kb)


async def render_adminstats2(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    with db() as conn:
        user_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        company_count = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
        city_count = conn.execute("SELECT COUNT(*) c FROM cities").fetchone()["c"]
        report_count = conn.execute("SELECT COUNT(*) c FROM reports WHERE reviewed=0").fetchone()["c"]
        banned_count = conn.execute("SELECT COUNT(*) c FROM users WHERE banned=1").fetchone()["c"]
    text = (
        f"📊 آمار کلی بازی:\n👥 کاربران: {user_count}\n🏢 شرکت‌ها: {company_count}\n"
        f"🏙 شهرها: {city_count}\n🚩 گزارش‌های بررسی‌نشده: {report_count}\n🚫 کاربران بن‌شده: {banned_count}"
    )
    await _answer(query, text, InlineKeyboardMarkup([[_back("m:adminpanel")]]))


async def render_adminreports2(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    with db() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE reviewed=0 ORDER BY id DESC LIMIT 10").fetchall()
    lines = ["🚩 گزارش‌های بررسی‌نشده:\n"]
    buttons = []
    if not rows:
        lines.append("گزارش بررسی‌نشده‌ای نیست.")
    for r in rows:
        lines.append(f"#{r['id']} — گزارش‌دهنده: {r['reporter_id']} | هدف: {r['target_id']} | دلیل: {r['reason']}")
        buttons.append([_btn(f"✅ بررسی‌شده #{r['id']}", f"adminreview:{r['id']}", style="success")])
    buttons.append([_back("m:adminpanel")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_adminban(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [_btn("🚫 بن کردن کاربر", "adm:ban", style="danger")],
        [_btn("✅ رفع بن کاربر", "adm:unban", style="success")],
        [_back("m:adminpanel")],
    ])
    await _answer(query, "🚫 بن / رفع بن — یک عملیات رو انتخاب کن:", kb)


async def render_adminbalance(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [_btn("➕ افزایش موجودی", "adm:addbal", style="success")],
        [_btn("➖ کاهش موجودی", "adm:subbal", style="danger")],
        [_back("m:adminpanel")],
    ])
    await _answer(query, "💰 موجودی کاربر — یک عملیات رو انتخاب کن:", kb)


async def render_adminforcejoin(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    channels = get_force_join_list()
    lines = ["📢 عضویت اجباری کانال/گروه‌ها:\n"]
    kb_rows = []
    if not channels:
        lines.append("چیزی تنظیم نشده — یعنی هیچ محدودیتی برای کاربرا نیست.")
    else:
        for i, ch in enumerate(channels, start=1):
            lines.append(f"{i}. {ch['title']}")
            kb_rows.append([_btn(f"🗑 حذف #{i}", f"adm:rmchannel:{ch['id']}", style="danger")])
    kb_rows.append([_btn("➕ افزودن کانال/گروه", "adm:addchannel", style="success")])
    kb_rows.append([_back("m:adminpanel")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(kb_rows))


async def render_adminmaintenance(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    on = get_setting(MAINTENANCE_KEY) == "1"
    status = "🔴 روشن (ربات برای کاربران عادی خاموشه)" if on else "🟢 خاموش (ربات فعاله)"
    btn_label = "🟢 روشن کردن ربات" if on else "🔴 خاموش کردن ربات"
    kb = InlineKeyboardMarkup([
        [_btn(btn_label, "adm:togglemaint", style="success" if on else "danger")],
        [_back("m:adminpanel")],
    ])
    await _answer(query, f"🔧 حالت تعمیر و نگهداری\nوضعیت فعلی: {status}", kb)


# ----------------------------------------------------------------------------
# پنل‌های مقام‌ها (پلیس، سرباز، سروان، قاضی)
# ----------------------------------------------------------------------------

def _has_role_or_admin(user_id, role):
    return get_gov_role(user_id) == role or is_admin(user_id)


async def render_policepanel(query, user_id):
    if not _has_role_or_admin(user_id, "police"):
        await query.answer("این بخش فقط برای پلیس است.", show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [_btn("🚨 گرفتن مجرم", "gov:arrest", style="danger")],
        [_btn("🚔 لیست بازداشتگاه", "m:detentionlist", style="primary")],
        [_back()],
    ])
    await _answer(query, "👮 پنل مدیریت پلیس", kb)


async def render_judgepanel(query, user_id):
    if not _has_role_or_admin(user_id, "judge"):
        await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
        return
    with db() as conn:
        pending_count = conn.execute("SELECT COUNT(*) c FROM court_summons WHERE status='pending'").fetchone()["c"]
    kb = InlineKeyboardMarkup([
        [_btn("📅 احضار به دادگاه", "gov:summon", style="primary")],
        [_btn(f"📋 احضاریه‌های در انتظار ({pending_count})", "m:pendingsummons", style="primary")],
        [_btn("⚖️ صدور حکم مستقیم", "gov:verdict", style="danger")],
        [_btn("🚔 بازداشتگاه", "m:detentionlist", style="primary"),
         _btn("🔒 زندان", "m:prisonlist", style="primary")],
        [_back()],
    ])
    await _answer(query, "⚖️ پنل مدیریت قاضی", kb)


async def render_soldierpanel(query, user_id):
    if not _has_role_or_admin(user_id, "soldier"):
        await query.answer("این بخش فقط برای سرباز است.", show_alert=True)
        return
    salary = get_role_salary("soldier")
    text = f"🪖 پنل سرباز\n💰 حقوق: {fmt_money(salary)}\n\nبرای امنیت شهر تلاش کن!"
    await _answer(query, text, InlineKeyboardMarkup([[_back()]]))


async def render_captainpanel(query, user_id):
    if not _has_role_or_admin(user_id, "captain"):
        await query.answer("این بخش فقط برای سروان است.", show_alert=True)
        return
    salary = get_role_salary("captain")
    kb = InlineKeyboardMarkup([
        [_btn("🚔 لیست بازداشتگاه", "m:detentionlist", style="primary")],
        [_back()],
    ])
    await _answer(query, f"🎖️ پنل سروان\n💰 حقوق: {fmt_money(salary)}", kb)


async def render_detentionlist(query, user_id):
    role = get_gov_role(user_id)
    if not role and not is_admin(user_id):
        await query.answer("دسترسی نداری.", show_alert=True)
        return
    now = time.time()
    with db() as conn:
        rows = conn.execute("SELECT * FROM users WHERE detention_until > ? ORDER BY detention_until", (now,)).fetchall()
    lines = ["🚔 لیست بازداشتگاه:\n"]
    buttons = []
    if not rows:
        lines.append("کسی در بازداشتگاه نیست.")
    can_release = role in ("police", "judge") or is_admin(user_id)
    for r in rows:
        remain = int((r["detention_until"] - now) / 60) + 1
        label = r["username"] or r["user_id"]
        lines.append(f"• {label} — {remain} دقیقه مونده")
        if can_release:
            buttons.append([_btn(f"🔓 آزاد کردن {label}", f"gov:release:detention:{r['user_id']}", style="success")])
    buttons.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_prisonlist(query, user_id):
    role = get_gov_role(user_id)
    if not role and not is_admin(user_id):
        await query.answer("دسترسی نداری.", show_alert=True)
        return
    now = time.time()
    with db() as conn:
        rows = conn.execute("SELECT * FROM users WHERE prison_until > ? ORDER BY prison_until", (now,)).fetchall()
    lines = ["🔒 لیست زندان:\n"]
    buttons = []
    if not rows:
        lines.append("کسی در زندان نیست.")
    can_release = role == "judge" or is_admin(user_id)
    for r in rows:
        remain = int((r["prison_until"] - now) / 60) + 1
        label = r["username"] or r["user_id"]
        lines.append(f"• {label} — {remain} دقیقه مونده")
        if can_release:
            buttons.append([_btn(f"🔓 آزاد کردن {label}", f"gov:release:prison:{r['user_id']}", style="success")])
    buttons.append([_back()])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_pendingsummons(query, user_id):
    if not _has_role_or_admin(user_id, "judge"):
        await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
        return
    with db() as conn:
        rows = conn.execute("SELECT * FROM court_summons WHERE status='pending' ORDER BY id DESC LIMIT 15").fetchall()
    lines = ["📋 احضاریه‌های در انتظار حکم:\n"]
    buttons = []
    if not rows:
        lines.append("چیزی در انتظار نیست.")
    for r in rows:
        u = get_user(r["user_id"])
        label = (u.get("username") or r["user_id"]) if u else r["user_id"]
        lines.append(f"#{r['id']} — {label} — {r['scheduled_at']} — دلیل: {r['reason']}")
        buttons.append([_btn(f"⚖️ صدور حکم برای {label}", f"gov:verdictfor:{r['user_id']}", style="danger")])
    buttons.append([_back("m:judgepanel")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


USER_PICK_PAGE_SIZE = 8

USER_PICK_TITLES = {
    "assignrole": "👮 یک کاربر رو برای تعیین مقام انتخاب کن:",
    "removerole": "➖ یک کاربر رو برای عزل از مقام انتخاب کن:",
    "arrest": "🚨 مظنون رو انتخاب کن:",
    "summon": "📅 فردی که می‌خوای احضار کنی رو انتخاب کن:",
    "verdict": "⚖️ فردی که می‌خوای براش حکم صادر کنی رو انتخاب کن:",
    "lookuprecord": "🔍 کاربر مورد نظر رو برای دیدن سوابق انتخاب کن:",
}

USER_PICK_PERMISSION = {
    "assignrole": lambda uid: is_admin(uid),
    "removerole": lambda uid: is_admin(uid),
    "arrest": lambda uid: _has_role_or_admin(uid, "police"),
    "summon": lambda uid: _has_role_or_admin(uid, "judge"),
    "verdict": lambda uid: _has_role_or_admin(uid, "judge"),
    "lookuprecord": lambda uid: _has_role_or_admin(uid, "judge") or is_admin(uid),
}

USER_PICK_BACK = {
    "assignrole": "m:adminroles",
    "removerole": "m:adminroles",
    "arrest": "m:policepanel",
    "summon": "m:judgepanel",
    "verdict": "m:judgepanel",
    "lookuprecord": "m:adminroles",
}


SELF_EXCLUDED_PICK_PURPOSES = {"arrest"}


async def render_pick_mode(query, purpose, title):
    """قبل از انتخاب کاربر، می‌پرسه از لیست انتخاب کنه یا با آیدی/یوزرنیم جستجو کنه."""
    text = f"{title}\nچطور می‌خوای کاربر رو انتخاب کنی؟"
    kb = InlineKeyboardMarkup([
        [_btn("📋 نمایش لیست کاربران", f"pickuser:{purpose}:0", style="primary")],
        [_btn("🔍 جستجو با آیدی/یوزرنیم", f"gov:searchpurpose:{purpose}", style="success")],
        [_back(USER_PICK_BACK.get(purpose, "m:main"))],
    ])
    await _answer(query, text, kb)


async def render_user_picker(query, viewer_id, purpose, page=0):
    check = USER_PICK_PERMISSION.get(purpose)
    if not check or not check(viewer_id):
        await query.answer("دسترسی نداری.", show_alert=True)
        return
    exclude_self = purpose in SELF_EXCLUDED_PICK_PURPOSES
    with db() as conn:
        if exclude_self:
            total = conn.execute(
                "SELECT COUNT(*) c FROM users WHERE user_id != ?", (viewer_id,)
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM users WHERE user_id != ? ORDER BY user_id LIMIT ? OFFSET ?",
                (viewer_id, USER_PICK_PAGE_SIZE, page * USER_PICK_PAGE_SIZE),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM users ORDER BY user_id LIMIT ? OFFSET ?",
                (USER_PICK_PAGE_SIZE, page * USER_PICK_PAGE_SIZE),
            ).fetchall()
    total_pages = max(1, (total + USER_PICK_PAGE_SIZE - 1) // USER_PICK_PAGE_SIZE)
    lines = [f"{USER_PICK_TITLES.get(purpose, 'انتخاب کاربر')}\n(صفحه {page + 1} از {total_pages})"]
    buttons = []
    for r in rows:
        label = r["username"] or str(r["user_id"])
        role = get_gov_role(r["user_id"])
        role_tag = f" {ROLE_ICONS[role]}" if role else ""
        buttons.append([_btn(f"{label}{role_tag}", f"pickeduser:{purpose}:{r['user_id']}:{page}", style="primary")])
    nav_row = []
    if page > 0:
        nav_row.append(_btn("◀️ قبلی", f"pickuser:{purpose}:{page - 1}", style="primary"))
    if (page + 1) < total_pages:
        nav_row.append(_btn("بعدی ▶️", f"pickuser:{purpose}:{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([_back(USER_PICK_BACK.get(purpose, "m:main"))])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def render_adminroles(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    roles = list_gov_roles()
    lines = ["👮 مدیریت مقام‌ها\n"]
    if not roles:
        lines.append("هیچ مقامی تعیین نشده.")
    else:
        for r in roles:
            u = get_user(r["user_id"])
            label = (u.get("username") or r["user_id"]) if u else r["user_id"]
            lines.append(f"{ROLE_ICONS.get(r['role'], '')} {label} — {ROLE_NAMES.get(r['role'], r['role'])}")
    kb = InlineKeyboardMarkup([
        [_btn("➕ تعیین مقام", "gov:assignrole", style="success")],
        [_btn("➖ عزل از مقام", "gov:removerole", style="danger")],
        [_btn("💰 حقوق مقام‌ها", "m:adminsalaries", style="primary")],
        [_btn("🔍 مشاهده سوابق قضایی", "gov:lookuprecord", style="primary")],
        [_btn("🚔 بازداشتگاه", "m:detentionlist", style="primary"),
         _btn("🔒 زندان", "m:prisonlist", style="primary")],
        [_back("m:adminpanel")],
    ])
    await _answer(query, "\n".join(lines), kb)


async def render_adminsalaries(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    lines = ["💰 حقوق مقام‌ها:\n"]
    buttons = []
    for role in GOV_ROLES:
        salary = get_role_salary(role)
        lines.append(f"{ROLE_ICONS[role]} {ROLE_NAMES[role]}: {fmt_money(salary)}")
        buttons.append([_btn(f"✏️ تغییر حقوق {ROLE_NAMES[role]}", f"gov:editsalary:{role}", style="primary")])
    buttons.append([_btn("💵 پرداخت حقوق همه (الان)", "gov:payallsalaries", style="success")])
    buttons.append([_back("m:adminroles")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons))


# ----------------------------------------------------------------------------
# مدیریت بانک (پنل ادمین)
# ----------------------------------------------------------------------------

BANK_PAGE_SIZE = 8


async def render_adminbank(query, user_id):
    if not is_admin(user_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    max_mult = get_loan_max_multiplier()
    with db() as conn:
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        frozen_count = conn.execute("SELECT COUNT(*) c FROM users WHERE bank_frozen=1").fetchone()["c"]
        total_bank = conn.execute("SELECT COALESCE(SUM(bank),0) s FROM users").fetchone()["s"]
    text = (
        "🏦 *مدیریت بانک*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"👥 کل حساب‌ها: *{total_users}*\n"
        f"🧊 حساب‌های مسدود: *{frozen_count}*\n"
        f"💰 مجموع موجودی بانکی شهر: *{fmt_money(total_bank)}*\n"
        f"📈 ضریب سقف وام: *{max_mult}×* امتیاز اعتبار\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "برای مدیریت حساب هر کاربر، از لیست پایین استفاده کن — نیازی به آیدی نیست."
    )
    kb = InlineKeyboardMarkup([
        [_btn("🔍 جستجوی کاربر (آیدی/یوزرنیم)", "gov:searchuser", style="success")],
        [_btn("📋 لیست کاربران بانک", "bankpage:0", style="primary")],
        [_btn("💳 واریز سریع با شماره کارت", "gov:bankdepositcard", style="success")],
        [_btn("📈 تعیین سقف وام", "gov:setloancap", style="primary")],
        [_back("m:adminpanel")],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


async def render_bank_user_list(query, admin_id, page=0):
    if not is_admin(admin_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM users ORDER BY user_id LIMIT ? OFFSET ?", (BANK_PAGE_SIZE, page * BANK_PAGE_SIZE)
        ).fetchall()
    total_pages = max(1, (total + BANK_PAGE_SIZE - 1) // BANK_PAGE_SIZE)
    lines = [f"📋 *لیست کاربران بانک* (صفحه {page + 1} از {total_pages})\n"]
    buttons = []
    for r in rows:
        label = r["username"] or str(r["user_id"])
        frozen_dot = "🧊" if r["bank_frozen"] else "🟢"
        buttons.append([
            _btn(
                f"{frozen_dot} {label} — 💰{fmt_money(r['bank'])}",
                f"bankuser:{r['user_id']}:{page}",
                style="primary",
            )
        ])
    nav_row = []
    if page > 0:
        nav_row.append(_btn("◀️ قبلی", f"bankpage:{page - 1}", style="primary"))
    if (page + 1) < total_pages:
        nav_row.append(_btn("بعدی ▶️", f"bankpage:{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([_back("m:adminbank")])
    await _answer(query, "\n".join(lines), InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def render_bank_user_detail(query, admin_id, target_id, back_page=0):
    if not is_admin(admin_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    target = get_user(target_id)
    if not target:
        await query.answer("کاربر پیدا نشد.", show_alert=True)
        return
    frozen = target.get("bank_frozen", 0) == 1
    status_line = "🧊 مسدود" if frozen else "🟢 فعال"
    text = (
        f"👤 *{md_escape(target.get('username') or target_id)}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💳 *کارت بانکی*\n"
        f"`{format_card_number(target.get('card_number'))}`\n"
        f"وضعیت: {status_line}\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💰 *موجودی‌ها*\n"
        f"▫️ پول نقد: *{fmt_money(target['cash'])}*\n"
        f"▫️ حساب بانکی: *{fmt_money(target['bank'])}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "📊 *اعتبار*\n"
        f"{credit_tier(target['credit_score'])} — امتیاز {target['credit_score']}\n"
        f"▫️ بدهی وام: *{fmt_money(target['loan_amount'])}*"
    )
    freeze_btn = (
        _btn("🔓 رفع مسدودی", f"bankfreeze:{target_id}:{back_page}", style="success")
        if frozen else
        _btn("🧊 مسدود کردن حساب", f"bankfreeze:{target_id}:{back_page}", style="danger")
    )
    kb = InlineKeyboardMarkup([
        [freeze_btn],
        [_btn("➕ شارژ حساب", f"bankcharge:{target_id}:{back_page}", style="success"),
         _btn("➖ کسر از حساب", f"bankdeduct:{target_id}:{back_page}", style="danger")],
        [_back(f"bankpage:{back_page}")],
    ])
    await _answer(query, text, kb, parse_mode="Markdown")


def build_user_search_panel(target_id):
    """متن و کیبورد پنل ترکیبی «جستجوی کاربر» رو می‌سازه: هم مدیریت بانک هم مدیریت مقام."""
    target = get_user(target_id)
    if not target:
        return None
    frozen = target.get("bank_frozen", 0) == 1
    status_line = "🧊 مسدود" if frozen else "🟢 فعال"
    role = get_gov_role(target_id)
    role_line = f"{ROLE_ICONS.get(role, '')} {ROLE_NAMES.get(role, role)}" if role else "بدون مقام"
    text = (
        f"👤 *{md_escape(target.get('username') or target_id)}*\n"
        f"🆔 آیدی: `{target_id}`\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💳 *کارت بانکی*\n"
        f"`{format_card_number(target.get('card_number'))}`\n"
        f"وضعیت حساب: {status_line}\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "💰 *موجودی‌ها*\n"
        f"▫️ پول نقد: *{fmt_money(target['cash'])}*\n"
        f"▫️ حساب بانکی: *{fmt_money(target['bank'])}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "📊 *اعتبار*\n"
        f"{credit_tier(target['credit_score'])} — امتیاز {target['credit_score']}\n"
        f"▫️ بدهی وام: *{fmt_money(target['loan_amount'])}*\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"👮 *مقام فعلی:* {role_line}"
    )
    freeze_btn = (
        _btn("🔓 رفع مسدودی", f"usearchfreeze:{target_id}", style="success")
        if frozen else
        _btn("🧊 مسدود کردن حساب", f"usearchfreeze:{target_id}", style="danger")
    )
    role_row = (
        [_btn("🔁 تغییر مقام", f"usearchrole:{target_id}", style="primary"),
         _btn("➖ عزل از مقام", f"usearchrolerm:{target_id}", style="danger")]
        if role else
        [_btn("👮 تعیین مقام", f"usearchrole:{target_id}", style="success")]
    )
    kb = InlineKeyboardMarkup([
        [freeze_btn],
        [_btn("➕ شارژ حساب", f"usearchcharge:{target_id}", style="success"),
         _btn("➖ کسر از حساب", f"usearchdeduct:{target_id}", style="danger")],
        role_row,
        [_btn("🔍 جستجوی کاربر دیگر", "gov:searchuser", style="primary")],
        [_back("m:adminbank")],
    ])
    return text, kb


async def render_user_search_panel(query, admin_id, target_id):
    if not is_admin(admin_id):
        await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
        return
    built = build_user_search_panel(target_id)
    if not built:
        await query.answer("کاربر پیدا نشد.", show_alert=True)
        return
    text, kb = built
    await _answer(query, text, kb, parse_mode="Markdown")




SIMPLE_ACTIONS = {
    "sleep": do_sleep, "eat": do_eat, "gym": do_gym, "restaurant": do_restaurant,
    "fish": do_fish, "mine": do_mine, "farm": do_farm, "puzzle": do_puzzle,
    "havechild": do_havechild, "payroll": do_payroll,
    "study": do_study, "buyinsurance": do_buyinsurance,
}

MENU_RENDERERS = {
    "main": None,  # جدا هندل می‌شه
    "profile": render_profile,
    "jobs": render_jobs,
    "skills": render_skills,
    "bank": render_bank,
    "shop": render_shop,
    "shopprop": render_shop_prop,
    "shopveh": render_shop_veh,
    "myassets": render_myassets,
    "market": render_market,
    "mylistings": render_mylistings,
    "stocks": render_stocks,
    "mystocks": render_mystocks,
    "company": render_company,
    "life": render_life,
    "fun": render_fun,
    "mission": render_mission,
    "family": render_family,
    "top": render_top,
    "companiestop": render_companiestop,
    "citytop": render_citytop,
    "world": render_world,
    "notif": render_notif,
    "help": render_help,
    "school": render_school,
    "insurance": render_insurance,
    "friends": render_friends,
    "court": render_court,
    "missionshub": render_missionshub,
    "weeklym": render_weeklym,
    "monthlym": render_monthlym,
    "secretm": render_secretm,
    "adminpanel": render_adminpanel,
    "adminstats2": render_adminstats2,
    "adminreports2": render_adminreports2,
    "adminban": render_adminban,
    "adminbalance": render_adminbalance,
    "adminforcejoin": render_adminforcejoin,
    "adminmaintenance": render_adminmaintenance,
    "policepanel": render_policepanel,
    "judgepanel": render_judgepanel,
    "soldierpanel": render_soldierpanel,
    "captainpanel": render_captainpanel,
    "detentionlist": render_detentionlist,
    "prisonlist": render_prisonlist,
    "pendingsummons": render_pendingsummons,
    "adminroles": render_adminroles,
    "adminbank": render_adminbank,
    "adminsalaries": render_adminsalaries,
}


class _SafeCallbackQuery:
    __slots__ = ("_q", "_answered")

    def __init__(self, query):
        self._q = query
        self._answered = False

    async def answer(self, *args, **kwargs):
        if self._answered:
            return None
        self._answered = True
        try:
            return await self._q.answer(*args, **kwargs)
        except Exception as e:
            logger.warning("callback answer failed (ignored): %s", e)
            return None

    def __getattr__(self, name):
        return getattr(self._q, name)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = _SafeCallbackQuery(update.callback_query)
    user_id = query.from_user.id
    ensure_user(user_id, query.from_user.username or query.from_user.first_name)
    data = query.data or ""

    if query.message and query.message.chat.type != "private":
        owner_id = get_menu_owner(query.message.chat.id, query.message.message_id)
        if owner_id is not None and owner_id != user_id:
            await query.answer("این منو مال شما نیست! برای داشتن منوی خودت /menu رو بزن.", show_alert=True)
            return

    if not await enforce_maintenance(update, context):
        await query.answer()
        return

    if data != "checkjoin" and not await enforce_force_join(update, context):
        await query.answer()
        return

    try:
        await _dispatch_callback(query, context, user_id, data)
    finally:
        await query.answer()


async def _dispatch_callback(query, context, user_id, data):
    nav_prefixes = ("m:", "bankpage:", "bankuser:", "bankfreeze:")
    if any(data.startswith(p) for p in nav_prefixes):
        # با هر پیمایش توی منو (از جمله دکمه‌ی بازگشت)، هر فرایند چندمرحله‌ای نیمه‌کاره
        # (مثل انتقال با شماره کارت، فرم‌های پنل مدیریت و مقام‌ها) لغو می‌شه تا پیام بعدیِ
        # کاربر با یه فرم قدیمیِ رهاشده قاطی نشه.
        context.user_data.pop("pending_transfer", None)
        context.user_data.pop("pending_amount", None)
        context.user_data.pop("pending_qty", None)
        context.user_data.pop("pending_admin", None)
        context.user_data.pop("pending_gov", None)
        context.user_data.pop("pending_bank", None)

    if data == "m:main":
        await query.answer()
        await show_main(query)
        return

    if data.startswith("m:"):
        key = data[2:].split(":")[0]
        renderer = MENU_RENDERERS.get(key)
        if renderer:
            await query.answer()
            await renderer(query, user_id)
            return
        if key in ("dep", "with", "loan", "payl", "cinv"):
            await query.answer()
            action_map = {"dep": "واریز", "with": "برداشت", "loan": "دریافت وام", "payl": "بازپرداخت وام", "cinv": "افزایش سرمایه شرکت"}
            back = "m:bank" if key != "cinv" else "m:company"
            await _answer(query, f"مبلغ {action_map[key]} رو انتخاب کن:", amount_keyboard(key, back_cb=back))
            return
        if key == "stockbuy" or key == "stocksell":
            await query.answer()
            symbol = data.split(":")[2]
            side = "buy" if key == "stockbuy" else "sell"
            await _answer(
                query, f"چند سهم {symbol} می‌خوای {'بخری' if side=='buy' else 'بفروشی'}؟",
                qty_keyboard(f"stock{side}", symbol, back_cb=f"stocksym:{symbol}"),
            )
            return

    if data.startswith("jobapply:"):
        await act_apply_job(query, user_id, data.split(":", 1)[1])
        return
    if data == "dowork":
        await act_work(query, user_id)
        return
    if data.startswith("train:"):
        await act_train(query, user_id, data.split(":", 1)[1])
        return
    if data.startswith("buyprop:"):
        await act_buy_prop(query, user_id, data.split(":", 1)[1])
        return
    if data.startswith("buyveh:"):
        await act_buy_veh(query, user_id, data.split(":", 1)[1])
        return
    if data.startswith("stocksym:"):
        await render_stock_detail(query, user_id, data.split(":", 1)[1])
        return
    if data.startswith("buyitem:"):
        await act_buy_item(query, user_id, int(data.split(":", 1)[1]))
        return
    if data.startswith("cancellisting:"):
        await act_cancel_listing(query, user_id, int(data.split(":", 1)[1]))
        return
    if data.startswith("claim:"):
        await act_claim(query, user_id, data.split(":", 1)[1])
        return
    if data.startswith("confirm:divorce"):
        msg = await do_divorce(user_id)
        await query.answer(msg, show_alert=True)
        await render_family(query, user_id)
        return
    if data.startswith("court:"):
        report_id = int(data.split(":", 1)[1])
        msg = await do_court(user_id, report_id)
        await query.answer(msg[:200], show_alert=True)
        await render_court(query, user_id)
        return
    if data.startswith("unfriend:"):
        friend_id = int(data.split(":", 1)[1])
        msg = await do_unfriend(user_id, friend_id)
        await query.answer(msg[:200], show_alert=True)
        await render_friends(query, user_id)
        return
    if data.startswith("claimp:"):
        _, period_key, mission_key = data.split(":")
        period_map = {
            "weekly": (WEEKLY_MISSIONS, period_id_week(), render_weeklym),
            "monthly": (MONTHLY_MISSIONS, period_id_month(), render_monthlym),
            "secret": (SECRET_MISSIONS, period_id_secret(), render_secretm),
        }
        definitions, period_id, renderer = period_map[period_key]
        m, err = claim_periodic_mission(user_id, mission_key, period_id, definitions)
        if err:
            await query.answer(err, show_alert=True)
        else:
            await query.answer(f"🎉 گرفتی: {fmt_money(m['reward_cash'])} و {m['reward_xp']} XP", show_alert=True)
        await renderer(query, user_id)
        return

    if data == "checkjoin":
        channels = get_force_join_list()
        missing = [ch for ch in channels if not await is_member_of_channel(context, ch["chat_id"], user_id)]
        if not missing:
            await query.answer("✅ عضویت تایید شد!", show_alert=True)
            await show_main(query)
        else:
            await query.answer("هنوز عضو همه‌ی کانال/گروه‌ها نشدی: " + "، ".join(ch["title"] for ch in missing), show_alert=True)
        return

    if data.startswith("adminreview:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        report_id = int(data.split(":", 1)[1])
        with db() as conn:
            conn.execute("UPDATE reports SET reviewed=1 WHERE id=?", (report_id,))
        await query.answer("✅ گزارش بررسی‌شده علامت خورد.")
        await render_adminreports2(query, user_id)
        return

    if data == "banktransfer:":
        context.user_data["pending_transfer"] = {"stage": "card"}
        await query.answer()
        await _answer(
            query,
            "💳 شماره کارت ۱۶ رقمی گیرنده رو بفرست (با خط تیره یا بدون خط تیره):",
            InlineKeyboardMarkup([[_back("m:bank")]]),
        )
        return

    if data.startswith("adm:rmchannel:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        row_id = int(data.split(":")[2])
        row = get_force_join_row(row_id)
        if not row:
            await query.answer("پیدا نشد.", show_alert=True)
            await render_adminforcejoin(query, user_id)
            return
        kb = InlineKeyboardMarkup([
            [_btn("✅ بله، حذف کن", f"adm:rmchannelconfirm:{row_id}", style="danger")],
            [_back("m:adminforcejoin")],
        ])
        await query.answer()
        await _answer(query, f"مطمئنی می‌خوای «{row['title']}» رو از لیست عضویت اجباری حذف کنی؟", kb)
        return

    if data.startswith("adm:rmchannelconfirm:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        row_id = int(data.split(":")[2])
        remove_force_join_channel(row_id)
        await query.answer("✅ حذف شد.", show_alert=True)
        await render_adminforcejoin(query, user_id)
        return

    if data == "adm:confirmaddchannel":
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        pending = context.user_data.get("pending_admin")
        if not pending or pending.get("action") != "addchannel" or pending.get("stage") != "confirm":
            await query.answer("چیزی برای تایید نیست.", show_alert=True)
            return
        add_force_join_channel(pending["chat_id"], pending["title"], pending["username"], pending["invite_link"])
        context.user_data.pop("pending_admin", None)
        await query.answer("✅ اضافه شد.", show_alert=True)
        await render_adminforcejoin(query, user_id)
        return

    if data.startswith("adm:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        action = data.split(":", 1)[1]
        prompts = {
            "ban": "🚫 آیدی عددی یا یوزرنیم (بدون @) کاربری که می‌خوای بن کنی رو بفرست:",
            "unban": "✅ آیدی عددی یا یوزرنیم (بدون @) کاربری که می‌خوای رفع بن کنی رو بفرست:",
            "addbal": "➕ آیدی عددی یا یوزرنیم (بدون @) کاربر مورد نظر رو بفرست:",
            "subbal": "➖ آیدی عددی یا یوزرنیم (بدون @) کاربر مورد نظر رو بفرست:",
            "addchannel": "✏️ آیدی عددی، یوزرنیم (@channel) یا آیدی گروه رو بفرست.\nربات باید از قبل عضو و ادمین اون کانال/گروه باشه:",
        }
        if action == "togglemaint":
            on = get_setting(MAINTENANCE_KEY) == "1"
            set_setting(MAINTENANCE_KEY, "0" if on else "1")
            await query.answer("✅ وضعیت ربات تغییر کرد.", show_alert=True)
            await render_adminmaintenance(query, user_id)
            return
        context.user_data["pending_admin"] = {"action": action, "stage": "target"}
        await query.answer()
        await _answer(query, prompts[action], InlineKeyboardMarkup([[_back("m:adminpanel")]]))
        return

    if data.startswith("pickuser:"):
        _, purpose, page_str = data.split(":")
        await query.answer()
        await render_user_picker(query, user_id, purpose, int(page_str))
        return

    if data.startswith("pickeduser:"):
        _, purpose, target_str, page_str = data.split(":")
        target_id = int(target_str)
        check = USER_PICK_PERMISSION.get(purpose)
        if not check or not check(user_id):
            await query.answer("دسترسی نداری.", show_alert=True)
            return
        target = get_user(target_id)
        if not target:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        target_label = target.get("username") or target_id

        if purpose == "assignrole":
            kb_rows = [
                [_btn(f"{ROLE_ICONS[r]} {ROLE_NAMES[r]}", f"gov:setrole:{target_id}:{r}", style="primary")]
                for r in GOV_ROLES
            ]
            kb_rows.append([_back("m:adminroles")])
            await query.answer()
            await _answer(query, f"مقام مورد نظر برای {target_label} رو انتخاب کن:", InlineKeyboardMarkup(kb_rows))
            return

        if purpose == "removerole":
            old_role = get_gov_role(target_id)
            if not old_role:
                await query.answer("این کاربر اصلاً مقامی نداره.", show_alert=True)
                return
            remove_gov_role(target_id)
            await query.answer(f"✅ مقام {ROLE_NAMES.get(old_role, old_role)} از {target_label} گرفته شد.", show_alert=True)
            try:
                await context.bot.send_message(target_id, f"⚠️ شما از مقام {ROLE_NAMES.get(old_role, old_role)} عزل شدید.")
            except Exception:
                pass
            await render_adminroles(query, user_id)
            return

        if purpose == "arrest":
            if target_id == user_id:
                await query.answer("نمی‌تونی خودتو دستگیر کنی!", show_alert=True)
                return
            context.user_data["pending_gov"] = {"flow": "arrest", "stage": "reason", "target_id": target_id}
            await query.answer()
            await _answer(
                query, f"📄 دلیل دستگیری {target_label} رو بنویس:",
                InlineKeyboardMarkup([[_back("m:policepanel")]]),
            )
            return

        if purpose == "summon":
            context.user_data["pending_gov"] = {"flow": "summon", "stage": "datetime", "target_id": target_id}
            await query.answer()
            await _answer(
                query, f"📅 تاریخ و ساعت دادگاه برای {target_label} رو بنویس (مثلاً 1404/06/01 ساعت 16:00):",
                InlineKeyboardMarkup([[_back("m:judgepanel")]]),
            )
            return

        if purpose == "verdict":
            kb_rows = [
                [_btn(label, f"gov:vtype:{target_id}:{vt}", style="success" if vt == "acquit" else "danger")]
                for vt, label in VERDICT_TYPES.items()
            ]
            kb_rows.append([_back("m:judgepanel")])
            await query.answer()
            await _answer(query, f"⚖️ نوع حکم برای {target_label} رو انتخاب کن:", InlineKeyboardMarkup(kb_rows))
            return

        if purpose == "lookuprecord":
            await query.answer()
            records = get_judicial_records(target_id)
            if not records:
                lines = [f"📋 هیچ سابقه‌ای برای {target_label} ثبت نشده."]
            else:
                lines = [f"📋 سوابق قضایی {target_label}:\n"]
                for r in records:
                    issuer = get_user(r["issuer_id"]) if r["issuer_id"] else None
                    issuer_label = (issuer.get("username") or r["issuer_id"]) if issuer else "سیستم"
                    detail = f" ({r['detail']})" if r["detail"] else ""
                    lines.append(
                        f"{JUDICIAL_KIND_LABELS.get(r['kind'], r['kind'])} — {r['reason']}{detail} — "
                        f"صادرکننده: {issuer_label} — {r['created_at']}"
                    )
            await _answer(query, "\n".join(lines)[:4000], InlineKeyboardMarkup([[_back("m:adminroles")]]))
            return
        return

    if data.startswith("bankpage:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        page = int(data.split(":")[1])
        await query.answer()
        await render_bank_user_list(query, user_id, page)
        return

    if data.startswith("bankuser:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        _, target_str, page_str = data.split(":")
        await query.answer()
        await render_bank_user_detail(query, user_id, int(target_str), int(page_str))
        return

    if data.startswith("bankfreeze:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        _, target_str, page_str = data.split(":")
        target_id = int(target_str)
        target = get_user(target_id)
        if not target:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        new_state = 0 if target.get("bank_frozen", 0) == 1 else 1
        update_user(target_id, bank_frozen=new_state)
        if new_state == 1:
            await query.answer("🧊 حساب مسدود شد.", show_alert=True)
            note = "🧊 حساب بانکی شما از طرف مدیریت مسدود شد."
        else:
            await query.answer("🔓 حساب رفع مسدودی شد.", show_alert=True)
            note = "🔓 حساب بانکی شما از طرف مدیریت از حالت مسدودی خارج شد."
        try:
            await context.bot.send_message(target_id, note)
        except Exception:
            pass
        await render_bank_user_detail(query, user_id, target_id, int(page_str))
        return

    if data.startswith("bankcharge:") or data.startswith("bankdeduct:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        kind, target_str, page_str = data.split(":")
        context.user_data["pending_bank"] = {
            "flow": "chargeaccount" if kind == "bankcharge" else "deductaccount",
            "stage": "amount", "target_id": int(target_str), "back_page": int(page_str),
        }
        await query.answer()
        verb = "شارژ" if kind == "bankcharge" else "کسر"
        await _answer(
            query, f"مبلغ مورد نظر برای {verb} حساب رو بفرست (فقط رقم):",
            InlineKeyboardMarkup([[_back(f"bankuser:{target_str}:{page_str}")]]),
        )
        return

    if data.startswith("gov:release:"):
        _, _, which, target_str = data.split(":")
        role = get_gov_role(user_id)
        if which == "detention" and role not in ("police", "judge") and not is_admin(user_id):
            await query.answer("دسترسی نداری.", show_alert=True)
            return
        if which == "prison" and role != "judge" and not is_admin(user_id):
            await query.answer("دسترسی نداری.", show_alert=True)
            return
        msg = await do_release(int(target_str), which, user_id, context)
        await query.answer(msg, show_alert=True)
        await (render_detentionlist if which == "detention" else render_prisonlist)(query, user_id)
        return

    if data.startswith("gov:vtype:"):
        _, _, target_str, vtype = data.split(":")
        if not _has_role_or_admin(user_id, "judge"):
            await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
            return
        pending = {"flow": "verdict", "target_id": int(target_str), "vtype": vtype}
        needs_fine = vtype in ("fine", "detention_fine", "prison_fine")
        needs_duration = vtype in ("detention", "prison", "detention_fine", "prison_fine")
        if needs_fine:
            pending["stage"] = "fine"
            prompt = "💸 مبلغ جریمه رو به تومان بفرست (فقط رقم):"
        elif needs_duration:
            pending["stage"] = "duration"
            prompt = "⏱ مدت زمان رو به دقیقه بفرست (فقط رقم):"
        else:
            pending["stage"] = "reason"
            prompt = "📄 دلیل حکم رو بنویس:"
        context.user_data["pending_gov"] = pending
        await query.answer()
        await _answer(query, prompt, InlineKeyboardMarkup([[_back("m:judgepanel")]]))
        return

    if data.startswith("gov:verdictfor:"):
        target_id = int(data.split(":")[2])
        if not _has_role_or_admin(user_id, "judge"):
            await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
            return
        kb_rows = [
            [_btn(label, f"gov:vtype:{target_id}:{vt}", style="success" if vt == "acquit" else "danger")]
            for vt, label in VERDICT_TYPES.items()
        ]
        kb_rows.append([_back("m:pendingsummons")])
        await query.answer()
        await _answer(query, "⚖️ نوع حکم رو انتخاب کن:", InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("gov:setrole:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        _, _, target_str, role = data.split(":")
        target_id = int(target_str)
        set_gov_role(target_id, role, user_id)
        context.user_data.pop("pending_gov", None)
        await query.answer(f"✅ مقام {ROLE_NAMES[role]} تعیین شد.", show_alert=True)
        try:
            await context.bot.send_message(
                target_id, f"{ROLE_ICONS[role]} تبریک! شما به مقام {ROLE_NAMES[role]} منصوب شدید."
            )
        except Exception:
            pass
        await render_adminroles(query, user_id)
        return

    if data.startswith("gov:"):
        role = get_gov_role(user_id)
        action = data.split(":", 1)[1]

        if action == "arrest":
            if role != "police" and not is_admin(user_id):
                await query.answer("این بخش فقط برای پلیس است.", show_alert=True)
                return
            await query.answer()
            await render_user_picker(query, user_id, "arrest", 0)
            return

        if action == "summon":
            if role != "judge" and not is_admin(user_id):
                await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
                return
            await query.answer()
            await render_user_picker(query, user_id, "summon", 0)
            return

        if action == "verdict":
            if role != "judge" and not is_admin(user_id):
                await query.answer("این بخش فقط برای قاضی است.", show_alert=True)
                return
            await query.answer()
            await render_user_picker(query, user_id, "verdict", 0)
            return

        if action == "assignrole":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            await query.answer()
            await render_pick_mode(query, "assignrole", USER_PICK_TITLES["assignrole"])
            return

        if action == "removerole":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            await query.answer()
            await render_pick_mode(query, "removerole", USER_PICK_TITLES["removerole"])
            return

        if action.startswith("editsalary:"):
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            role_key = action.split(":")[1]
            context.user_data["pending_gov"] = {"flow": "editsalary", "stage": "amount", "role": role_key}
            await query.answer()
            await _answer(
                query, f"مبلغ حقوق جدید برای {ROLE_NAMES.get(role_key, role_key)} رو بفرست (فقط رقم):",
                InlineKeyboardMarkup([[_back("m:adminsalaries")]]),
            )
            return

        if action == "payallsalaries":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            paid = await pay_role_salaries(context)
            await query.answer(f"✅ حقوق {len(paid)} نفر پرداخت شد.", show_alert=True)
            await render_adminsalaries(query, user_id)
            return

        if action == "lookuprecord":
            if role != "judge" and not is_admin(user_id):
                await query.answer("این بخش فقط برای قاضی یا مدیر است.", show_alert=True)
                return
            await query.answer()
            await render_pick_mode(query, "lookuprecord", USER_PICK_TITLES["lookuprecord"])
            return

        if action.startswith("searchpurpose:"):
            purpose = action.split(":", 1)[1]
            check = USER_PICK_PERMISSION.get(purpose)
            if not check or not check(user_id):
                await query.answer("دسترسی نداری.", show_alert=True)
                return
            context.user_data["pending_gov"] = {"flow": "searchpurpose", "purpose": purpose, "stage": "identifier"}
            await query.answer()
            await _answer(
                query, "🔍 آیدی عددی یا یوزرنیم (بدون @) کاربر مورد نظر رو بفرست:",
                InlineKeyboardMarkup([[_back(USER_PICK_BACK.get(purpose, "m:adminroles"))]]),
            )
            return

        if action == "searchuser":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            context.user_data["pending_bank"] = {"flow": "searchuser", "stage": "identifier"}
            await query.answer()
            await _answer(
                query, "🔍 آیدی عددی یا یوزرنیم (بدون @) کاربر مورد نظر رو بفرست:",
                InlineKeyboardMarkup([[_back("m:adminbank")]]),
            )
            return

        if action == "bankdepositcard":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            context.user_data["pending_bank"] = {"flow": "depositcard", "stage": "card"}
            await query.answer()
            await _answer(
                query, "💳 شماره کارت ۱۶ رقمی مقصد رو بفرست:",
                InlineKeyboardMarkup([[_back("m:adminbank")]]),
            )
            return

        if action == "setloancap":
            if not is_admin(user_id):
                await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
                return
            context.user_data["pending_bank"] = {"flow": "setloancap", "stage": "amount"}
            await query.answer()
            await _answer(
                query,
                f"ضریب سقف وام فعلی: {get_loan_max_multiplier()}×\n"
                "عدد جدید رو بفرست (مثلاً 3 یعنی سقف وام = ۳ برابر امتیاز اعتباری):",
                InlineKeyboardMarkup([[_back("m:adminbank")]]),
            )
            return

    if data.startswith("usearch:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        target_id = int(data.split(":")[1])
        await query.answer()
        await render_user_search_panel(query, user_id, target_id)
        return

    if data.startswith("usearchfreeze:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        target_id = int(data.split(":")[1])
        target = get_user(target_id)
        if not target:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        new_state = 0 if target.get("bank_frozen", 0) == 1 else 1
        update_user(target_id, bank_frozen=new_state)
        if new_state == 1:
            await query.answer("🧊 حساب مسدود شد.", show_alert=True)
            note = "🧊 حساب بانکی شما از طرف مدیریت مسدود شد."
        else:
            await query.answer("🔓 حساب رفع مسدودی شد.", show_alert=True)
            note = "🔓 حساب بانکی شما از طرف مدیریت از حالت مسدودی خارج شد."
        try:
            await context.bot.send_message(target_id, note)
        except Exception:
            pass
        await render_user_search_panel(query, user_id, target_id)
        return

    if data.startswith("usearchcharge:") or data.startswith("usearchdeduct:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        kind, target_str = data.split(":")
        context.user_data["pending_bank"] = {
            "flow": "searchcharge" if kind == "usearchcharge" else "searchdeduct",
            "stage": "amount", "target_id": int(target_str),
        }
        await query.answer()
        verb = "شارژ" if kind == "usearchcharge" else "کسر"
        await _answer(
            query, f"مبلغ مورد نظر برای {verb} حساب رو بفرست (فقط رقم):",
            InlineKeyboardMarkup([[_back(f"usearch:{target_str}")]]),
        )
        return

    if data.startswith("usearchrole:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        target_id = int(data.split(":")[1])
        target = get_user(target_id)
        if not target:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        target_label = target.get("username") or target_id
        kb_rows = [
            [_btn(f"{ROLE_ICONS[r]} {ROLE_NAMES[r]}", f"usearchsetrole:{target_id}:{r}", style="primary")]
            for r in GOV_ROLES
        ]
        kb_rows.append([_back(f"usearch:{target_id}")])
        await query.answer()
        await _answer(query, f"مقام مورد نظر برای {target_label} رو انتخاب کن:", InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("usearchsetrole:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        _, target_str, role = data.split(":")
        target_id = int(target_str)
        set_gov_role(target_id, role, user_id)
        await query.answer(f"✅ مقام {ROLE_NAMES[role]} تعیین شد.", show_alert=True)
        try:
            await context.bot.send_message(
                target_id, f"{ROLE_ICONS[role]} تبریک! شما به مقام {ROLE_NAMES[role]} منصوب شدید."
            )
        except Exception:
            pass
        await render_user_search_panel(query, user_id, target_id)
        return

    if data.startswith("usearchrolerm:"):
        if not is_admin(user_id):
            await query.answer("این بخش فقط برای مدیران است.", show_alert=True)
            return
        target_id = int(data.split(":")[1])
        old_role = get_gov_role(target_id)
        if not old_role:
            await query.answer("این کاربر اصلاً مقامی نداره.", show_alert=True)
            return
        remove_gov_role(target_id)
        target = get_user(target_id)
        target_label = (target.get("username") or target_id) if target else target_id
        await query.answer(f"✅ مقام {ROLE_NAMES.get(old_role, old_role)} از {target_label} گرفته شد.", show_alert=True)
        try:
            await context.bot.send_message(target_id, f"⚠️ شما از مقام {ROLE_NAMES.get(old_role, old_role)} عزل شدید.")
        except Exception:
            pass
        await render_user_search_panel(query, user_id, target_id)
        return

    if data.startswith("doact:"):
        _, action, _ = data.split(":")
        fn = SIMPLE_ACTIONS.get(action)
        if fn:
            msg = await fn(user_id)
            await query.answer(msg[:200], show_alert=True)
            # صفحه‌ی مربوطه رو دوباره رسم کن تا وضعیت بروز بشه
            back_map = {
                "sleep": render_life, "eat": render_life, "gym": render_life, "restaurant": render_life,
                "fish": render_fun, "mine": render_fun, "farm": render_fun, "puzzle": render_fun,
                "havechild": render_family, "payroll": render_company,
                "study": render_school, "buyinsurance": render_insurance,
            }
            renderer = back_map.get(action)
            if renderer:
                await renderer(query, user_id)
        return

    if data.startswith("amt:"):
        _, action, extra, value = data.split(":")
        await _handle_amount(query, user_id, action, extra, int(value))
        return
    if data.startswith("amtcustom:"):
        await query.answer()
        _, action, extra = data.split(":")
        context.user_data["pending_amount"] = {"action": action, "extra": extra}
        await _answer(query, "✏️ یک عدد بفرست (فقط رقم، بدون کاما):", InlineKeyboardMarkup([[_back()]]))
        return
    if data.startswith("qty:"):
        _, action, extra, value = data.split(":")
        await _handle_qty(query, user_id, action, extra, int(value))
        return
    if data.startswith("qtycustom:"):
        await query.answer()
        _, action, extra = data.split(":")
        context.user_data["pending_qty"] = {"action": action, "extra": extra}
        await _answer(query, "✏️ یک عدد بفرست (فقط رقم):", InlineKeyboardMarkup([[_back()]]))
        return


async def _handle_amount(query, user_id, action, extra, value):
    if action == "dep":
        msg = await do_deposit(user_id, value)
        await query.answer(msg[:200], show_alert=True)
        await render_bank(query, user_id)
    elif action == "with":
        msg = await do_withdraw(user_id, value)
        await query.answer(msg[:200], show_alert=True)
        await render_bank(query, user_id)
    elif action == "loan":
        msg = await do_loan(user_id, value)
        await query.answer(msg[:200], show_alert=True)
        await render_bank(query, user_id)
    elif action == "payl":
        msg = await do_payloan(user_id, value)
        await query.answer(msg[:200], show_alert=True)
        await render_bank(query, user_id)
    elif action == "cinv":
        msg = await do_companyinvest(user_id, value)
        await query.answer(msg[:200], show_alert=True)
        await render_company(query, user_id)


async def _handle_qty(query, user_id, action, extra, value):
    if action == "buyitem":
        msg = await do_buy_item(user_id, int(extra), value)
        await query.answer(msg[:200], show_alert=True)
        await render_market(query, user_id)
    elif action == "stockbuy":
        msg = await do_stock_trade(user_id, extra, value, "buy")
        await query.answer(msg[:200], show_alert=True)
        await render_stock_detail(query, user_id, extra)
    elif action == "stocksell":
        msg = await do_stock_trade(user_id, extra, value, "sell")
        await query.answer(msg[:200], show_alert=True)
        await render_stock_detail(query, user_id, extra)


async def on_bank_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_bank")
    if not pending:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        context.user_data.pop("pending_bank", None)
        return
    text = (update.message.text or "").strip()
    flow = pending["flow"]

    if flow in ("chargeaccount", "deductaccount"):
        amount_text = text.replace(",", "")
        if not amount_text.isdigit():
            await update.message.reply_text("فقط عدد بفرست.")
            return
        amount = int(amount_text)
        target_id = pending["target_id"]
        target = get_user(target_id)
        context.user_data.pop("pending_bank", None)
        if not target:
            await update.message.reply_text("کاربر پیدا نشد.")
            return
        if flow == "chargeaccount":
            new_bank = target["bank"] + amount
            update_user(target_id, bank=new_bank)
            msg = f"✅ {fmt_money(amount)} به حساب {target.get('username') or target_id} شارژ شد."
            notify = f"💰 حساب بانکی شما از طرف مدیریت شارژ شد: {fmt_money(amount)}\nموجودی بانکی فعلی: {fmt_money(new_bank)}"
        else:
            new_bank = max(0, target["bank"] - amount)
            update_user(target_id, bank=new_bank)
            msg = f"✅ {fmt_money(amount)} از حساب {target.get('username') or target_id} کسر شد."
            notify = f"💸 از حساب بانکی شما از طرف مدیریت مبلغی کسر شد: {fmt_money(amount)}\nموجودی بانکی فعلی: {fmt_money(new_bank)}"
        log_tx(target_id, "admin_bank_adjust", amount if flow == "chargeaccount" else -amount, "توسط مدیریت")
        await update.message.reply_text(msg)
        try:
            await context.bot.send_message(target_id, notify)
        except Exception:
            pass
        return

    if flow == "depositcard":
        if pending["stage"] == "card":
            card = text.replace("-", "").replace(" ", "")
            if not card.isdigit() or len(card) != 16:
                await update.message.reply_text("شماره کارت نامعتبره. یه شماره کارت ۱۶ رقمی بفرست.")
                return
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE card_number=?", (card,)).fetchone()
            if not row:
                await update.message.reply_text("همچین کارتی پیدا نشد.")
                return
            pending.update({"stage": "amount", "card": card})
            await update.message.reply_text("مبلغ واریزی رو بفرست (فقط رقم):")
            return
        if pending["stage"] == "amount":
            amount_text = text.replace(",", "")
            if not amount_text.isdigit():
                await update.message.reply_text("فقط عدد بفرست.")
                return
            amount = int(amount_text)
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE card_number=?", (pending["card"],)).fetchone()
            context.user_data.pop("pending_bank", None)
            if not row:
                await update.message.reply_text("کارت دیگه معتبر نیست.")
                return
            target = dict(row)
            new_bank = target["bank"] + amount
            update_user(target["user_id"], bank=new_bank)
            log_tx(target["user_id"], "admin_bank_adjust", amount, "واریز مستقیم مدیریت با شماره کارت")
            await update.message.reply_text(
                f"✅ {fmt_money(amount)} به کارت {format_card_number(pending['card'])} واریز شد."
            )
            try:
                await context.bot.send_message(
                    target["user_id"],
                    f"💰 حساب بانکی شما از طرف مدیریت شارژ شد: {fmt_money(amount)}\n"
                    f"موجودی بانکی فعلی: {fmt_money(new_bank)}",
                )
            except Exception:
                pass
            return
        return

    if flow == "setloancap":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("یه عدد مثبت بفرست.")
            return
        set_setting("loan_max_multiplier", text)
        context.user_data.pop("pending_bank", None)
        await update.message.reply_text(f"✅ ضریب سقف وام روی {text}× تنظیم شد.")
        return

    if flow == "searchuser":
        target = resolve_user_by_identifier(text)
        if not target:
            await update.message.reply_text("چنین کاربری پیدا نشد. دوباره امتحان کن.")
            return
        context.user_data.pop("pending_bank", None)
        built = build_user_search_panel(target["user_id"])
        if not built:
            await update.message.reply_text("کاربر پیدا نشد.")
            return
        text_out, kb = built
        await update.message.reply_text(text_out, reply_markup=kb, parse_mode="Markdown")
        return

    if flow in ("searchcharge", "searchdeduct"):
        amount_text = text.replace(",", "")
        if not amount_text.isdigit():
            await update.message.reply_text("فقط عدد بفرست.")
            return
        amount = int(amount_text)
        target_id = pending["target_id"]
        target = get_user(target_id)
        context.user_data.pop("pending_bank", None)
        if not target:
            await update.message.reply_text("کاربر پیدا نشد.")
            return
        if flow == "searchcharge":
            new_bank = target["bank"] + amount
            update_user(target_id, bank=new_bank)
            msg = f"✅ {fmt_money(amount)} به حساب {target.get('username') or target_id} شارژ شد."
            notify = f"💰 حساب بانکی شما از طرف مدیریت شارژ شد: {fmt_money(amount)}\nموجودی بانکی فعلی: {fmt_money(new_bank)}"
            tx_amount = amount
        else:
            new_bank = max(0, target["bank"] - amount)
            update_user(target_id, bank=new_bank)
            msg = f"✅ {fmt_money(amount)} از حساب {target.get('username') or target_id} کسر شد."
            notify = f"💸 از حساب بانکی شما از طرف مدیریت مبلغی کسر شد: {fmt_money(amount)}\nموجودی بانکی فعلی: {fmt_money(new_bank)}"
            tx_amount = -amount
        log_tx(target_id, "admin_bank_adjust", tx_amount, "توسط مدیریت (جستجوی کاربر)")
        await update.message.reply_text(msg)
        built = build_user_search_panel(target_id)
        if built:
            text_out, kb = built
            await update.message.reply_text(text_out, reply_markup=kb, parse_mode="Markdown")
        try:
            await context.bot.send_message(target_id, notify)
        except Exception:
            pass
        return


async def on_gov_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_gov")
    if not pending:
        return
    user_id = update.effective_user.id
    role = get_gov_role(user_id)
    text = (update.message.text or "").strip()
    flow = pending["flow"]

    if flow == "arrest":
        if role != "police" and not is_admin(user_id):
            context.user_data.pop("pending_gov", None)
            return
        if pending["stage"] == "reason":
            pending.update({"stage": "duration", "reason": text})
            await update.message.reply_text("\u23f1 مدت بازداشت رو به دقیقه بفرست (فقط رقم):")
            return
        if pending["stage"] == "duration":
            if not text.isdigit():
                await update.message.reply_text("فقط عدد بفرست.")
                return
            duration = int(text)
            context.user_data.pop("pending_gov", None)
            msg = await do_arrest(user_id, pending["target_id"], pending["reason"], duration, context)
            await update.message.reply_text(msg)
            return
        return

    if flow == "summon":
        if role != "judge" and not is_admin(user_id):
            context.user_data.pop("pending_gov", None)
            return
        if pending["stage"] == "datetime":
            pending.update({"stage": "reason", "scheduled_at": text})
            await update.message.reply_text("\U0001f4c4 دلیل احضار رو بنویس:")
            return
        if pending["stage"] == "reason":
            context.user_data.pop("pending_gov", None)
            msg = await do_court_summon(user_id, pending["target_id"], pending["scheduled_at"], text, context)
            await update.message.reply_text(msg)
            return
        return

    if flow == "verdict":
        if role != "judge" and not is_admin(user_id):
            context.user_data.pop("pending_gov", None)
            return
        if pending["stage"] == "fine":
            amount_text = text.replace(",", "")
            if not amount_text.isdigit():
                await update.message.reply_text("فقط عدد بفرست.")
                return
            pending["fine"] = int(amount_text)
            needs_duration = pending["vtype"] in ("detention_fine", "prison_fine")
            pending["stage"] = "duration" if needs_duration else "reason"
            await update.message.reply_text("\u23f1 مدت زمان رو به دقیقه بفرست:" if needs_duration else "\U0001f4c4 دلیل حکم رو بنویس:")
            return
        if pending["stage"] == "duration":
            if not text.isdigit():
                await update.message.reply_text("فقط عدد بفرست.")
                return
            pending["duration"] = int(text)
            pending["stage"] = "reason"
            await update.message.reply_text("\U0001f4c4 دلیل حکم رو بنویس:")
            return
        if pending["stage"] == "reason":
            fine = pending.get("fine")
            duration = pending.get("duration")
            target_id = pending["target_id"]
            vtype = pending["vtype"]
            context.user_data.pop("pending_gov", None)
            msg = await do_verdict(user_id, target_id, vtype, fine, duration, text, context)
            await update.message.reply_text(msg)
            return
        return

    if flow == "editsalary":
        if not is_admin(user_id):
            context.user_data.pop("pending_gov", None)
            return
        if not text.isdigit():
            await update.message.reply_text("فقط عدد بفرست.")
            return
        role_key = pending["role"]
        set_role_salary(role_key, int(text))
        context.user_data.pop("pending_gov", None)
        await update.message.reply_text(f"\u2705 حقوق {ROLE_NAMES.get(role_key, role_key)} روی {fmt_money(int(text))} تنظیم شد.")
        return

    if flow == "searchpurpose":
        purpose = pending["purpose"]
        check = USER_PICK_PERMISSION.get(purpose)
        if not check or not check(user_id):
            context.user_data.pop("pending_gov", None)
            return
        target = resolve_user_by_identifier(text)
        if not target:
            await update.message.reply_text("چنین کاربری پیدا نشد. دوباره امتحان کن.")
            return
        context.user_data.pop("pending_gov", None)
        target_id = target["user_id"]
        target_label = target.get("username") or target_id

        if purpose == "assignrole":
            kb_rows = [
                [_btn(f"{ROLE_ICONS[r]} {ROLE_NAMES[r]}", f"gov:setrole:{target_id}:{r}", style="primary")]
                for r in GOV_ROLES
            ]
            kb_rows.append([_back("m:adminroles")])
            await update.message.reply_text(
                f"مقام مورد نظر برای {target_label} رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(kb_rows)
            )
            return

        if purpose == "removerole":
            old_role = get_gov_role(target_id)
            if not old_role:
                await update.message.reply_text("این کاربر اصلاً مقامی نداره.")
                return
            remove_gov_role(target_id)
            await update.message.reply_text(f"✅ مقام {ROLE_NAMES.get(old_role, old_role)} از {target_label} گرفته شد.")
            try:
                await context.bot.send_message(target_id, f"⚠️ شما از مقام {ROLE_NAMES.get(old_role, old_role)} عزل شدید.")
            except Exception:
                pass
            return

        if purpose == "lookuprecord":
            records = get_judicial_records(target_id)
            if not records:
                lines = [f"📋 هیچ سابقه‌ای برای {target_label} ثبت نشده."]
            else:
                lines = [f"📋 سوابق قضایی {target_label}:\n"]
                for r in records:
                    issuer = get_user(r["issuer_id"]) if r["issuer_id"] else None
                    issuer_label = (issuer.get("username") or r["issuer_id"]) if issuer else "سیستم"
                    detail = f" ({r['detail']})" if r["detail"] else ""
                    lines.append(
                        f"{JUDICIAL_KIND_LABELS.get(r['kind'], r['kind'])} — {r['reason']}{detail} — "
                        f"صادرکننده: {issuer_label} — {r['created_at']}"
                    )
            await update.message.reply_text("\n".join(lines)[:4000])
            return

        if purpose == "arrest":
            if target_id == user_id:
                await update.message.reply_text("نمی‌تونی خودتو دستگیر کنی!")
                return
            context.user_data["pending_gov"] = {"flow": "arrest", "stage": "reason", "target_id": target_id}
            await update.message.reply_text(f"📄 دلیل دستگیری {target_label} رو بنویس:")
            return

        if purpose == "summon":
            context.user_data["pending_gov"] = {"flow": "summon", "stage": "datetime", "target_id": target_id}
            await update.message.reply_text(
                f"📅 تاریخ و ساعت دادگاه برای {target_label} رو بنویس (مثلاً 1404/06/01 ساعت 16:00):"
            )
            return

        if purpose == "verdict":
            kb_rows = [
                [_btn(label, f"gov:vtype:{target_id}:{vt}", style="success" if vt == "acquit" else "danger")]
                for vt, label in VERDICT_TYPES.items()
            ]
            kb_rows.append([_back("m:judgepanel")])
            await update.message.reply_text(
                f"⚖️ نوع حکم برای {target_label} رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(kb_rows)
            )
            return
        return


async def on_admin_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_admin")
    if not pending:
        return
    if not is_admin(update.effective_user.id):
        context.user_data.pop("pending_admin", None)
        return
    text = (update.message.text or "").strip()
    action = pending["action"]

    if action == "addchannel":
        resolved, err = await resolve_and_validate_channel(context, text)
        if err:
            await update.message.reply_text(f"❌ {err}")
            return
        context.user_data["pending_admin"] = {"action": "addchannel", "stage": "confirm", **resolved}
        kb = InlineKeyboardMarkup([
            [_btn("✅ تایید و افزودن", "adm:confirmaddchannel", style="success")],
            [_back("m:adminforcejoin")],
        ])
        await update.message.reply_text(
            f"شناسایی شد: {resolved['title']}\nبرای افزودنش به لیست عضویت اجباری تایید کن:", reply_markup=kb
        )
        return

    if pending["stage"] == "target":
        target = resolve_user_by_identifier(text)
        if not target:
            await update.message.reply_text("چنین کاربری پیدا نشد. دوباره امتحان کن.")
            return
        if action == "ban":
            update_user(target["user_id"], banned=1)
            context.user_data.pop("pending_admin", None)
            await update.message.reply_text(f"✅ کاربر {target.get('username') or target['user_id']} بن شد.")
            try:
                await context.bot.send_message(target["user_id"], "🚫 حساب شما از طرف مدیریت بن شد.")
            except Exception:
                pass
            return
        if action == "unban":
            update_user(target["user_id"], banned=0)
            context.user_data.pop("pending_admin", None)
            await update.message.reply_text(f"✅ کاربر {target.get('username') or target['user_id']} رفع بن شد.")
            try:
                await context.bot.send_message(target["user_id"], "✅ حساب شما از طرف مدیریت از بن خارج شد.")
            except Exception:
                pass
            return
        if action in ("addbal", "subbal"):
            context.user_data["pending_admin"] = {"action": action, "stage": "amount", "target_id": target["user_id"]}
            label = target.get("username") or target["user_id"]
            verb = "افزایش" if action == "addbal" else "کاهش"
            await update.message.reply_text(f"مبلغ مورد نظر برای {verb} موجودی {label} رو بفرست (فقط رقم):")
            return
        return

    if pending["stage"] == "amount":
        amount_text = text.replace(",", "")
        if not amount_text.isdigit():
            await update.message.reply_text("فقط عدد بفرست.")
            return
        amount = int(amount_text)
        target_id = pending["target_id"]
        target = get_user(target_id)
        context.user_data.pop("pending_admin", None)
        if not target:
            await update.message.reply_text("کاربر پیدا نشد.")
            return
        if action == "addbal":
            new_cash = target["cash"] + amount
            update_user(target_id, cash=new_cash)
            msg = f"✅ {fmt_money(amount)} به موجودی {target.get('username') or target_id} اضافه شد."
            notify = f"💰 از طرف مدیریت {fmt_money(amount)} برای شما واریز شد.\nموجودی فعلی: {fmt_money(new_cash)}"
        else:
            new_cash = max(0, target["cash"] - amount)
            update_user(target_id, cash=new_cash)
            msg = f"✅ {fmt_money(amount)} از موجودی {target.get('username') or target_id} کم شد."
            notify = f"💸 از طرف مدیریت {fmt_money(amount)} از موجودی بازی شما کم شد.\nموجودی فعلی: {fmt_money(new_cash)}"
        await update.message.reply_text(msg)
        try:
            await context.bot.send_message(target_id, notify)
        except Exception:
            pass
        return


async def on_pending_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی کاربر بعد از انتخاب «مبلغ دلخواه»/«تعداد دلخواه» یک عدد ساده تایپ می‌کنه."""
    user_id = update.effective_user.id
    ensure_user(user_id, update.effective_user.username)
    is_private = update.effective_chat.type == "private"

    pending_transfer = context.user_data.get("pending_transfer")
    if pending_transfer:
        text_raw = (update.message.text or "").strip()
        if pending_transfer["stage"] == "card":
            card = text_raw.replace("-", "").replace(" ", "")
            if not card.isdigit() or len(card) != 16:
                await update.message.reply_text("شماره کارت نامعتبره. یه شماره کارت ۱۶ رقمی بفرست.")
                return
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE card_number=?", (card,)).fetchone()
            if not row:
                await update.message.reply_text("همچین کارتی پیدا نشد. دوباره امتحان کن.")
                return
            if row["user_id"] == user_id:
                await update.message.reply_text("نمی‌تونی به کارت خودت انتقال بدی!")
                return
            context.user_data["pending_transfer"] = {"stage": "amount", "card": card}
            await update.message.reply_text("مبلغ انتقال رو بفرست (فقط رقم):")
            return
        if pending_transfer["stage"] == "amount":
            amount_text = text_raw.replace(",", "")
            if not amount_text.isdigit():
                await update.message.reply_text("فقط عدد بفرست.")
                return
            amount = int(amount_text)
            context.user_data.pop("pending_transfer", None)
            msg = await do_card_transfer(user_id, pending_transfer["card"], amount, context)
            await update.message.reply_text(msg, reply_markup=main_menu_keyboard(user_id, is_private))
            return

    text = (update.message.text or "").strip().replace(",", "")
    if not text.isdigit():
        return  # به پیام‌های غیرعددی کاری نداریم؛ ممکنه دستور دیگه‌ای باشه
    value = int(text)

    pending_amt = context.user_data.pop("pending_amount", None)
    if pending_amt:
        action, extra = pending_amt["action"], pending_amt["extra"]
        fn_map = {"dep": do_deposit, "with": do_withdraw, "loan": do_loan, "payl": do_payloan}
        if action in fn_map:
            msg = await fn_map[action](user_id, value)
        elif action == "cinv":
            msg = await do_companyinvest(user_id, value)
        else:
            msg = "عملیات نامعتبر."
        await update.message.reply_text(msg, reply_markup=main_menu_keyboard(user_id, is_private))
        return

    pending_qty = context.user_data.pop("pending_qty", None)
    if pending_qty:
        action, extra = pending_qty["action"], pending_qty["extra"]
        if action == "buyitem":
            msg = await do_buy_item(user_id, int(extra), value)
        elif action == "stockbuy":
            msg = await do_stock_trade(user_id, extra, value, "buy")
        elif action == "stocksell":
            msg = await do_stock_trade(user_id, extra, value, "sell")
        else:
            msg = "عملیات نامعتبر."
        await update.message.reply_text(msg, reply_markup=main_menu_keyboard(user_id, is_private))
        return


# ----------------------------------------------------------------------------
# دستور /menu و ثبت هندلرها
# ----------------------------------------------------------------------------

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id, update.effective_user.username)
    if not await enforce_maintenance(update, context):
        return
    if not await enforce_force_join(update, context):
        return
    is_private = update.effective_chat.type == "private"
    kb = main_menu_keyboard(update.effective_user.id, is_private)
    sent = await update.message.reply_text("🎮 منوی اصلی:", reply_markup=kb)
    if not is_private:
        set_menu_owner(update.effective_chat.id, sent.message_id, update.effective_user.id)


MENU_KEYWORDS = {"منو", "منو باز کن", "منوی اصلی", "menu"}

_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")


def _normalize_text(text: str) -> str:
    """حذف کاراکترهای نامرئی (RTL/LRM/ZWNJ و...) که بعضی کیبوردهای فارسی موبایل اضافه می‌کنن."""
    text = _INVISIBLE_CHARS_RE.sub("", text or "")
    return text.strip()


async def on_menu_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = _normalize_text(update.message.text or "")
    if text.lower() not in MENU_KEYWORDS:
        return
    context.user_data.pop("pending_admin", None)
    context.user_data.pop("pending_transfer", None)
    context.user_data.pop("pending_amount", None)
    context.user_data.pop("pending_qty", None)
    context.user_data.pop("pending_gov", None)
    context.user_data.pop("pending_bank", None)
    await cmd_menu(update, context)


def register_menu(app):
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu_keyword), group=3)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_pending_text),
        group=4,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_gov_pending_text),
        group=5,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_bank_pending_text),
        group=6,
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_pending_number), group=7)
    logger.info("Menu (inline keyboard) handlers registered.")


# ----------------------------------------------------------------------------
# اجرای بات
# ----------------------------------------------------------------------------

def main():
    if BOT_TOKEN == "توکن-ربات-رو-اینجا-بذار":
        raise SystemExit(
            "❌ توکن بات تنظیم نشده.\n"
            "بالای فایل، مقدار BOT_TOKEN رو با توکن واقعی‌ای که از @BotFather گرفتی جایگزین کن."
        )

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("setbio", cmd_setbio))

    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("apply", cmd_apply))
    app.add_handler(CommandHandler("work", cmd_work))

    app.add_handler(CommandHandler("skills", cmd_skills))
    app.add_handler(CommandHandler("train", cmd_train))

    app.add_handler(CommandHandler("sleep", cmd_sleep))
    app.add_handler(CommandHandler("eat", cmd_eat))

    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("deposit", cmd_deposit))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CommandHandler("loan", cmd_loan))
    app.add_handler(CommandHandler("payloan", cmd_payloan))

    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("buyprop", cmd_buyprop))
    app.add_handler(CommandHandler("buyveh", cmd_buyveh))
    app.add_handler(CommandHandler("myassets", cmd_myassets))

    app.add_handler(CommandHandler("mission", cmd_mission))
    app.add_handler(CommandHandler("claim", cmd_claim))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("give", cmd_give))

    # افت دوره‌ای وضعیت شخصیت هر ۱۰ دقیقه
    if app.job_queue:
        app.job_queue.run_repeating(periodic_decay, interval=600, first=600)
        app.job_queue.run_repeating(judicial_release_job, interval=60, first=60)
        app.job_queue.run_repeating(pay_role_salaries, interval=86400, first=86400)

    register_phase2(app)
    register_phase4(app)
    register_menu(app)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
