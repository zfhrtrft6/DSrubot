import os
import hashlib
import sqlite3
import re
import random
from datetime import datetime, timedelta
import cv2
import discord
from discord.ext import tasks
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer

def run_web_server():
    class DummyServer(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is running active!")
    
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)

ROLE_NOVICE = "новичёк"
ROLE_OLD = "старец"
ROLE_DEMO = "DEMO"
ROLE_GOLD = "золотой"
ROLE_SILVER = "серебряный"
ROLE_ARTIST = "творец-художник"
ROLE_ANIMATOR = "творец-аниматор"

ALLOWED_CHANNELS = ["творчество", "творчество 18"]


def init_db():
    conn = sqlite3.connect("server_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            joined_at TEXT,
            art_count INTEGER DEFAULT 0,
            anim_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_hashes (
            file_hash TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()


def get_file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


def get_video_duration(video_path):
    video = cv2.VideoCapture(video_path)
    fps = video.get(cv2.CAP_PROP_FPS)
    frames = video.get(cv2.CAP_PROP_FRAME_COUNT)
    if fps > 0:
        return frames / fps
    return 0


@bot.event
async def on_ready():
    init_db()
    print(f"=== Bot {bot.user.name} is online! ===")
    check_old_members.start()
    clear_expired_warns.start()


@tasks.loop(minutes=1)
async def clear_expired_warns():
    now = datetime.now()
    conn = sqlite3.connect("server_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, timestamp FROM warnings")
    rows = cursor.fetchall()
    
    for row in rows:
        warn_id, user_id, timestamp_str = row
        warn_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        if now - warn_time >= timedelta(hours=48):
            cursor.execute("DELETE FROM warnings WHERE id = ?", (warn_id,))
            print(f"Warn ID {warn_id} expired.")
            
    conn.commit()
    conn.close()


@bot.event
async def on_member_join(member):
    if member.bot:
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("server_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, joined_at) VALUES (?, ?)", (member.id, now_str))
    conn.commit()
    conn.close()

    guild = member.guild
    human_members = [m for m in guild.members if not m.bot]
    total_humans = len(human_members)

    novice_role = discord.utils.get(guild.roles, name=ROLE_NOVICE)
    if novice_role:
        await member.add_roles(novice_role)

    if total_humans <= 50:
        target_role_name = None
        if total_humans <= 5:
            target_role_name = ROLE_DEMO
        elif total_humans <= 15:
            target_role_name = ROLE_GOLD
        elif total_humans <= 50:
            target_role_name = ROLE_SILVER

        if target_role_name:
            extra_role = discord.utils.get(guild.roles, name=target_role_name)
            if extra_role:
                await member.add_roles(extra_role)


@tasks.loop(hours=1)
async def check_old_members():
    now = datetime.now()
    conn = sqlite3.connect("server_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, joined_at FROM users")
    rows = cursor.fetchall()
    
    for row in rows:
        user_id, joined_at_str = row
        joined_at = datetime.strptime(joined_at_str, "%Y-%m-%d %H:%M:%S")
        if now - joined_at >= timedelta(days=365):
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    novice_role = discord.utils.get(guild.roles, name=ROLE_NOVICE)
                    old_role = discord.utils.get(guild.roles, name=ROLE_OLD)
                    if novice_role in member.roles:
                        try:
                            await member.remove_roles(novice_role)
                            await member.add_roles(old_role)
                        except discord.Forbidden:
                            pass
    conn.close()


async def apply_warning(message, member, count_to_add, reason):
    conn = sqlite3.connect("server_data.db")
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for _ in range(count_to_add):
        cursor.execute("INSERT INTO warnings (user_id, reason, timestamp) VALUES (?, ?, ?)", (member.id, reason, now_str))
    
    cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (member.id,))
    active_warns_row = cursor.fetchone()
    active_warns = active_warns_row[0] if active_warns_row else 0
    conn.commit()
    conn.close()

    try:
        await message.delete()
    except discord.DiscordException:
        pass

    if active_warns == 1:
        await message.channel.send(
            f"Предупреждение для {member.mention}! Причина: {reason}. Варн сгорит через 48 часов. Всего активных варнов: 1/4.",
            delete_after=15
        )
    elif active_warns == 2:
        try:
            await member.timeout(timedelta(minutes=10), reason=reason)
            await message.channel.send(f"Пользователь {member.mention} отправлен в мут на 10 минут за 2-й активный варн! Причина: {reason}.")
        except discord.Forbidden:
            pass
    elif active_warns == 3:
        try:
            await member.timeout(timedelta(days=1), reason=reason)
            await message.channel.send(f"Пользователь {member.mention} отправлен в мут на 24 часа за 3-й активный варн! Причина: {reason}.")
        except discord.Forbidden:
            pass
    elif active_warns >= 4:
        bot_choice = random.choice(["mute", "ban"])
        conn = sqlite3.connect("server_data.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM warnings WHERE user_id = ?", (member.id,))
        conn.commit()
        conn.close()

        if bot_choice == "mute":
            try:
                await member.timeout(timedelta(hours=50), reason="4 варна (Решение бота: Мут 50ч)")
                await message.channel.send(f"Бот принял решение: {member.mention} получает супер-мут на 50 часов за 4 нарушения правил!")
            except discord.Forbidden:
                pass
        elif bot_choice == "ban":
            try:
                random_ban_hours = random.randint(24, 72)
                await member.ban(reason=f"4 варна. Бот выбрал бан на {random_ban_hours}ч", delete_message_days=1)
                await message.channel.send(f"Бот принял решение: Пользователь {member.name} отправлен в временный бан на {random_ban_hours} часов!")
            except discord.Forbidden:
                pass


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    text = message.content
    text_lower = text.lower()
    member = message.author
    channel_name = message.channel.name

    if channel_name != "творчество 18":
        letters = re.findall(r'[а-яА-Яa-zA-Z]', text)
        if len(letters) > 5:
            caps_letters = [l for l in letters if l.isupper()]
            if len(caps_letters) / len(letters) > 0.7:
                await apply_warning(message, member, 1, "Злоупотребление CAPS LOCK")
                return

        phone_pattern = r'(\+?\d[ \-\(\)]?\d{3}[ \-\(\)]?\d{3}[ \-\(\)]?\d{2}[ \-\(\)]?\d{2})'
        if re.search(phone_pattern, text) or "фио" in text_lower or "мой номер" in text_lower:
            await apply_warning(message, member, 1, "Публикация личных данных (ФИО/Телефон)")
            return

        if "свастика" in text_lower or "нацизм" in text_lower or "卐" in text:
            await apply_warning(message, member, 1, "Использование нацистской символики/свастики")
            return

        lgbt_words = ["лгбт", "гей", "лесбиянка", "гомосексуал", "трансгендер"]
        if any(word in text_lower for word in lgbt_words):
            await apply_warning(message, member, 2, "Пропаганда меньшинств/ЛГБТ (Нарушение правил)")
            return

        family_insults = ["мать", "мамку", "отец", "родители", "родных"]
        if any(word in text_lower for word in family_insults) and ("бля" in text_lower or "сук" in text_lower or "хер" in text_lower):
            await apply_warning(message, member, 1, "Основательные оскорбления родственников")
            return

        nsfw_words = ["порно", "porno", "хентай", "hentai", "секс", "сиськи"]
        if channel_name != "творчество":
            if any(word in text_lower for word in nsfw_words):
                await apply_warning(message, member, 1, "18+ контент вне творческой 18+ зоны")
                return

    if channel_name in ALLOWED_CHANNELS and message.attachments:
        conn = sqlite3.connect("server_data.db")
        cursor = conn.cursor()
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR IGNORE INTO users (user_id, joined_at) VALUES (?, ?)", (member.id, now_str))

        for attachment in message.attachments:
            file_bytes = await attachment.read()
            file_hash = get_file_hash(file_bytes)

