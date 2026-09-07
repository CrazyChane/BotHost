import os
import random
import datetime
import asyncio
import tempfile
import string
import secrets
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from supabase import create_client, Client

# ---------- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "741695652"))
PORT = int(os.getenv("PORT", 10000))

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения!")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL и SUPABASE_KEY должны быть заданы!")

# ---------- ИНИЦИАЛИЗАЦИЯ ----------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)
CORS(app)

print(f"✅ Бот инициализирован")
print(f"🔑 ADMIN_ID: {ADMIN_ID}")
print(f"🌐 Порт: {PORT}")

# ---------- ПРОВЕРКА СТРУКТУРЫ ТАБЛИЦ ----------
def ensure_tables():
    try:
        supabase.table('keys').select('owner_id').limit(1).execute()
        print("✅ Таблицы в порядке")
    except Exception as e:
        if 'column "owner_id" does not exist' in str(e):
            print("⚠️ Добавляем колонку owner_id...")
            try:
                supabase.sql("ALTER TABLE keys ADD COLUMN owner_id BIGINT DEFAULT NULL").execute()
                print("✅ Колонка добавлена")
            except Exception as err:
                print(f"❌ Ошибка добавления колонки: {err}")
        else:
            print(f"⚠️ Ошибка проверки таблиц: {e}")

# ---------- РАБОТА С БАЗОЙ ДАННЫХ ----------
def load_keys():
    try:
        response = supabase.table('keys').select('*').execute()
        keys_dict = {}
        for row in response.data:
            key_text = row.pop('key_text')
            if 'owner_id' not in row:
                row['owner_id'] = None
            keys_dict[key_text] = row
        return keys_dict
    except Exception as e:
        print(f"❌ load_keys: {e}")
        return {}

def load_user_keys(owner_id=None):
    try:
        query = supabase.table('user_keys').select('*')
        if owner_id is not None:
            query = query.eq('owner_id', owner_id)
        response = query.execute()
        return response.data
    except Exception as e:
        print(f"❌ load_user_keys: {e}")
        return []

def save_user_key(key_text, owner_id, remaining_links, expires):
    try:
        supabase.table('user_keys').insert({
            'key_text': key_text,
            'owner_id': owner_id,
            'remaining_links': remaining_links,
            'expires': expires
        }).execute()
        return True
    except Exception as e:
        print(f"❌ save_user_key: {e}")
        return False

def update_user_key_remaining(key_text, owner_id, new_remaining):
    try:
        supabase.table('user_keys')\
            .update({'remaining_links': new_remaining})\
            .eq('key_text', key_text)\
            .eq('owner_id', owner_id)\
            .execute()
        print(f"🔄 Остаток {key_text}: {new_remaining}")
        return True
    except Exception as e:
        print(f"❌ update_user_key_remaining: {e}")
        return False

def load_data(owner_id=None):
    try:
        query = supabase.table('user_data').select('*')
        if owner_id is not None:
            query = query.eq('owner_id', owner_id)
        response = query.execute()
        data_dict = {}
        for row in response.data:
            key_text = row['key_text']
            data_dict[key_text] = {
                'used_links': row.get('used_links', []),
                'links_history': row.get('links_history', []),
                'activated': row.get('activated', '')
            }
        return data_dict
    except Exception as e:
        print(f"❌ load_data: {e}")
        return {}

def save_data(data_dict, owner_id):
    for key_text, data in data_dict.items():
        try:
            supabase.table('user_data').upsert({
                'key_text': key_text,
                'owner_id': owner_id,
                'used_links': data.get('used_links', []),
                'links_history': data.get('links_history', []),
                'activated': data.get('activated', datetime.datetime.now().isoformat())
            }).execute()
        except Exception as e:
            print(f"❌ save_data: {e}")

def load_links():
    try:
        response = supabase.table('links').select('url').execute()
        links = [row['url'] for row in response.data]
        print(f"🔗 Загружено {len(links)} ссылок")
        return links
    except Exception as e:
        print(f"❌ load_links: {e}")
        return []

def add_links_to_db(links_list):
    added = 0
    for url in links_list:
        url = url.strip()
        if url:
            try:
                supabase.table('links').insert({'url': url}).execute()
                added += 1
            except Exception as e:
                print(f"⚠️ Ошибка добавления {url}: {e}")
    return added

# ---------- БИЗНЕС-ЛОГИКА ----------
def validate_key_logic(key, user_id=None):
    try:
        if not key:
            return {"success": False, "message": "Ключ не указан"}
        keys = load_keys()
        if key not in keys:
            return {"success": False, "message": "Неверный ключ"}
        info = keys[key]
        if not info.get('active', True):
            return {"success": False, "message": "Ключ деактивирован"}
        expires = datetime.datetime.strptime(info['expires'], "%Y-%m-%d")
        if expires < datetime.datetime.now():
            return {"success": False, "message": "Срок действия истёк"}

        owner_id = info.get('owner_id')
        if owner_id is not None and owner_id != user_id:
            return {"success": False, "message": "Ключ уже активирован на другом аккаунте"}

        if owner_id is None:
            try:
                supabase.table('keys').update({'owner_id': user_id}).eq('key_text', key).execute()
                info['owner_id'] = user_id
                print(f"✅ Ключ {key} привязан к {user_id}")
            except Exception as e:
                print(f"❌ Ошибка привязки: {e}")
                return {"success": False, "message": "Ошибка привязки ключа"}

        user_keys = load_user_keys(user_id)
        if user_keys is not None:
            for uk in user_keys:
                if uk.get('key_text') == key:
                    return {"success": False, "message": "Ключ уже активирован вами"}

        max_links = info.get('max_links', 0)
        if max_links <= 0:
            return {"success": False, "message": "Нет доступных ссылок в ключе"}
        success = save_user_key(key, user_id, max_links, info['expires'])
        if not success:
            return {"success": False, "message": "Ошибка активации ключа"}

        return {
            "success": True,
            "type": info['type'],
            "expires": info['expires'],
            "max_links": max_links,
            "message": f"Ключ активирован! Добавлено {max_links} ссылок."
        }
    except Exception as e:
        print(f"❌ validate_key_logic: {e}")
        return {"success": False, "message": f"Ошибка: {e}"}

def get_link_logic(owner_id):
    try:
        user_keys = load_user_keys(owner_id)
        active = []
        today = datetime.datetime.now().date()
        for uk in user_keys:
            expires = datetime.datetime.strptime(uk['expires'], "%Y-%m-%d").date()
            if expires >= today and uk['remaining_links'] > 0:
                active.append(uk)
        if not active:
            return {"success": False, "message": "Нет доступных ссылок"}

        chosen = max(active, key=lambda x: x['remaining_links'])
        key_text = chosen['key_text']
        new_remaining = chosen['remaining_links'] - 1
        update_user_key_remaining(key_text, owner_id, new_remaining)

        all_links = load_links()
        if not all_links:
            return {"success": False, "message": "Нет доступных ссылок"}

        chosen_link = random.choice(all_links)
        try:
            supabase.table('links').delete().eq('url', chosen_link).execute()
            print(f"✅ Выдана ссылка: {chosen_link}")
        except Exception as e:
            print(f"❌ Ошибка удаления: {e}")
            return {"success": False, "message": "Ошибка при выдаче"}

        user_data = load_data(owner_id)
        if key_text not in user_data:
            user_data[key_text] = {'used_links': [], 'links_history': []}
        user_data[key_text]['used_links'].append(chosen_link)
        user_data[key_text]['links_history'].append({
            'link': chosen_link,
            'status': 'pending',
            'timestamp': datetime.datetime.now().isoformat()
        })
        save_data(user_data, owner_id)

        return {"success": True, "link": chosen_link, "remaining": new_remaining, "key": key_text}
    except Exception as e:
        print(f"❌ get_link_logic: {e}")
        return {"success": False, "message": f"Ошибка: {e}"}

def set_status_logic(key_text, owner_id, status):
    if not key_text or status not in ('да', 'нет'):
        return {"success": False, "message": "Некорректные данные"}
    user_data = load_data(owner_id)
    if key_text not in user_data or not user_data[key_text]['links_history']:
        return {"success": False, "message": "Нет ссылок для обновления"}
    last = user_data[key_text]['links_history'][-1]
    if last['status'] != 'pending':
        return {"success": False, "message": "Статус уже установлен"}
    last['status'] = status
    save_data(user_data, owner_id)
    return {"success": True, "message": "Статус обновлён"}

def history_logic(owner_id):
    try:
        if not owner_id:
            return {"success": False, "message": "Пользователь не указан"}
        response = supabase.table('user_data')\
            .select('links_history')\
            .eq('owner_id', owner_id)\
            .execute()
        all_history = []
        for row in response.data:
            all_history.extend(row.get('links_history', []))
        all_history.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return {"success": True, "history": all_history}
    except Exception as e:
        print(f"❌ history_logic: {e}")
        return {"success": False, "message": "Не удалось загрузить историю"}

def stats_logic(owner_id):
    user_keys = load_user_keys(owner_id)
    total_remaining = 0
    active_keys = []
    today = datetime.datetime.now().date()
    for uk in user_keys:
        expires = datetime.datetime.strptime(uk['expires'], "%Y-%m-%d").date()
        if expires >= today:
            active_keys.append(uk)
            total_remaining += uk['remaining_links']
    total_links_available = len(load_links())
    return {
        "success": True,
        "total_remaining": total_remaining,
        "active_keys_count": len(active_keys),
        "total_links_available": total_links_available,
        "keys_info": active_keys
    }

# ---------- АДМИН-ФУНКЦИИ ----------
def is_admin(user_id):
    return user_id == ADMIN_ID

def generate_key_string(key_type, days, max_links):
    prefix = "FREE" if key_type == 'trial' else "PREMIUM"
    year = datetime.datetime.now().year
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    key = f"{prefix}-{year}-{random_part}"
    expiry_date = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    return key, expiry_date

def admin_create_key(key_type, days, max_links):
    key, expires = generate_key_string(key_type, days, max_links)
    try:
        supabase.table('keys').insert({
            'key_text': key,
            'type': key_type,
            'expires': expires,
            'max_links': max_links,
            'active': True,
            'created': datetime.datetime.now().isoformat()
        }).execute()
        return key
    except Exception as e:
        print(f"❌ admin_create_key: {e}")
        return None

def admin_generate_multiple(count, key_type, days, max_links):
    created = []
    for _ in range(count):
        key = admin_create_key(key_type, days, max_links)
        if key:
            created.append(key)
    return created

def admin_delete_user(user_id):
    try:
        result1 = supabase.table('user_keys').delete().eq('owner_id', user_id).execute()
        result2 = supabase.table('user_data').delete().eq('owner_id', user_id).execute()
        return len(result1.data) + len(result2.data)
    except Exception as e:
        print(f"❌ admin_delete_user: {e}")
        return 0

# ---------- ТЕЛЕГРАМ-ОБРАБОТЧИКИ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в NFAvpn!\n\n"
        "Вы можете активировать несколько ключей на одном аккаунте.\n"
        "Каждый ключ добавляет свой лимит ссылок.\n\n"
        "Команды:\n"
        "/key ВАШ_КЛЮЧ - активировать ключ\n"
        "/get - получить случайную ссылку\n"
        "/stat - статистика по всем вашим ключам\n"
        "/history - история всех полученных ссылок\n"
        "/help - это сообщение"
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("❌ Укажите ключ после команды, например:\n/key FREE-2024-ABCD")
            return
        key = args[0].strip()
        user_id = update.effective_user.id
        result = validate_key_logic(key, user_id)
        if not result.get("success"):
            await update.message.reply_text(f"❌ Ошибка: {result.get('message', 'неизвестная')}")
            return
        await update.message.reply_text(
            f"✅ {result['message']}\n"
            f"Тип: {result['type']}\n"
            f"Действителен до: {result['expires']}\n"
            f"Добавлено ссылок: {result['max_links']}"
        )
    except Exception as e:
        print(f"❌ set_key: {e}")
        await update.message.reply_text("❌ Произошла внутренняя ошибка. Попробуйте позже.")

async def get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    result = get_link_logic(user_id)
    if not result.get("success"):
        await update.message.reply_text(f"❌ {result.get('message', 'ошибка')}")
        return
    link = result["link"]
    key = result["key"]
    remaining = result["remaining"]
    context.user_data["last_key"] = key
    keyboard = [
        [
            InlineKeyboardButton("✅ Работает", callback_data="status_да"),
            InlineKeyboardButton("❌ Не работает", callback_data="status_нет"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📎 Ваша ссылка:\n{link}\n\n"
        f"Осталось ссылок по этому ключу: {remaining}\n"
        f"Пожалуйста, укажите, работает ли она:",
        reply_markup=reply_markup
    )

async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    status = query.data.split("_")[1]
    key = context.user_data.get("last_key")
    if not key:
        await query.edit_message_text("❌ Не найден ключ для обновления статуса.")
        return
    user_id = update.effective_user.id
    result = set_status_logic(key, user_id, status)
    if result.get("success"):
        await query.edit_message_text(f"✅ Статус сохранён: {'работает' if status == 'да' else 'не работает'}")
    else:
        await query.edit_message_text(f"❌ Ошибка: {result.get('message', '')}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    result = stats_logic(user_id)
    if not result.get("success"):
        await update.message.reply_text("❌ Не удалось получить статистику")
        return
    text = f"📊 Ваша статистика:\n"
    text += f"Всего активных ключей: {result['active_keys_count']}\n"
    text += f"Осталось ссылок: {result['total_remaining']}\n"
    text += f"Всего доступно ссылок на сервере: {result['total_links_available']}\n\n"
    if result['keys_info']:
        text += "🔑 Детали по ключам:\n"
        for uk in result['keys_info']:
            text += f"  {uk['key_text']} – осталось {uk['remaining_links']} ссылок, до {uk['expires']}\n"
    await update.message.reply_text(text)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    result = history_logic(user_id)
    if not result.get("success"):
        await update.message.reply_text(f"❌ {result.get('message', 'ошибка')}")
        return
    history_list = result.get("history", [])
    if not history_list:
        await update.message.reply_text("📭 История пуста")
        return
    lines = []
    for i, entry in enumerate(history_list[:10], 1):
        emoji = "✅" if entry["status"] == "да" else ("❌" if entry["status"] == "нет" else "⏳")
        lines.append(f"{i}. {entry['link']} {emoji} ({entry['timestamp'][:16]})")
    await update.message.reply_text("📜 Последние 10 ссылок:\n" + "\n".join(lines))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 Доступные команды:\n"
        "/key <ключ> - активировать новый ключ\n"
        "/get - получить ссылку\n"
        "/stat - статистика по всем вашим ключам\n"
        "/history - история всех полученных ссылок\n"
        "/help - это сообщение\n"
    )
    if is_admin(update.effective_user.id):
        text += "\n🔐 Админ-команды:\n/admin_help - список"
    await update.message.reply_text(text)

# ---------- АДМИН-КОМАНДЫ (сокращённые, без изменений логики) ----------
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    text = (
        "🔐 Админ-команды:\n\n"
        "/admin_stats - общая статистика\n"
        "/admin_links - количество ссылок в пуле\n"
        "/admin_users - список пользователей\n"
        "/admin_create <тип> <кол-во> <дни> <лимит> - создать ключи\n"
        "/admin_deactivate <ключ> - деактивировать ключ\n"
        "/admin_activate <ключ> - активировать ключ\n"
        "/admin_refill <ключ> <количество> - пополнить остаток\n"
        "/admin_deletekey <ключ> - удалить ключ\n"
        "/admin_deleteuser <user_id> - удалить пользователя\n"
        "/admin_addlinks - добавить ссылки (файл или текст)\n"
        "/admin_linkstats - топ пользователей\n"
        "/admin_help - это сообщение"
    )
    await update.message.reply_text(text)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    try:
        keys_all = load_keys()
        user_keys_all = load_user_keys()
        users = set(uk['owner_id'] for uk in user_keys_all if uk.get('owner_id'))
        total_links = len(load_links())
        user_data_all = load_data()
        total_issued = sum(len(data.get('used_links', [])) for data in user_data_all.values())
        text = (
            f"📊 Общая статистика:\n"
            f"Всего ключей: {len(keys_all)}\n"
            f"Пользователей: {len(users)}\n"
            f"Ссылок в пуле: {total_links}\n"
            f"Выдано ссылок: {total_issued}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ---------- ДРУГИЕ АДМИН-КОМАНДЫ (шаблон) ----------
async def admin_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    links = load_links()
    await update.message.reply_text(f"🔗 Всего ссылок: {len(links)}")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    user_keys = load_user_keys()
    users_dict = {}
    for uk in user_keys:
        uid = uk.get('owner_id')
        if uid:
            users_dict[uid] = users_dict.get(uid, 0) + 1
    if not users_dict:
        await update.message.reply_text("Нет пользователей")
        return
    text = "👥 Пользователи:\n"
    for uid, count in users_dict.items():
        text += f"{uid} – {count} ключей\n"
    await update.message.reply_text(text)

async def admin_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Использование: /admin_create <тип> <кол-во> <дни> <лимит>")
        return
    try:
        key_type = args[0].strip().lower()
        if key_type not in ('trial', 'premium'):
            await update.message.reply_text("Тип: trial или premium")
            return
        count = int(args[1])
        days = int(args[2])
        max_links = int(args[3])
        created = admin_generate_multiple(count, key_type, days, max_links)
        text = f"✅ Создано {len(created)} ключей:\n" + "\n".join(created) if created else "❌ Ошибка"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Укажите ключ")
        return
    key = args[0].strip()
    try:
        supabase.table('keys').update({'active': False}).eq('key_text', key).execute()
        await update.message.reply_text(f"✅ Ключ {key} деактивирован")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Укажите ключ")
        return
    key = args[0].strip()
    try:
        supabase.table('keys').update({'active': True}).eq('key_text', key).execute()
        await update.message.reply_text(f"✅ Ключ {key} активирован")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /admin_refill <ключ> <количество>")
        return
    key = args[0].strip()
    try:
        amount = int(args[1])
        result = supabase.table('user_keys').select('*').eq('key_text', key).execute()
        if result.data:
            for row in result.data:
                new_remaining = row['remaining_links'] + amount
                update_user_key_remaining(key, row['owner_id'], new_remaining)
            await update.message.reply_text(f"✅ Добавлено {amount} ссылок к ключу {key}")
        else:
            await update.message.reply_text(f"❌ Ключ {key} не активирован")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_deletekey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Укажите ключ")
        return
    key = args[0].strip()
    await update.message.reply_text(f"⚠️ Подтвердите удаление: /admin_confirm_delete {key}")

async def admin_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Укажите ключ")
        return
    key = args[0].strip()
    try:
        supabase.table('keys').delete().eq('key_text', key).execute()
        await update.message.reply_text(f"✅ Ключ {key} удалён")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_deleteuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Укажите ID пользователя: /admin_deleteuser <user_id>")
        return
    try:
        user_id = int(args[0])
        keyboard = [
            [
                InlineKeyboardButton("✅ Да", callback_data=f"confirm_deluser_{user_id}"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel_deluser")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"⚠️ Удалить пользователя {user_id}?", reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def deleteuser_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel_deluser":
        await query.edit_message_text("❌ Отменено")
        return
    if data.startswith("confirm_deluser_"):
        user_id = int(data.split("_")[2])
        deleted = admin_delete_user(user_id)
        await query.edit_message_text(f"✅ Удалено {deleted} записей")

async def admin_addlinks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    await update.message.reply_text("📤 Отправьте файл .txt со ссылками или текст")
    context.user_data['waiting_links'] = True

async def handle_links_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get('waiting_links'):
        return
    links = []
    if update.message.document:
        file = await update.message.document.get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            await file.download_to_drive(tmp.name)
            with open(tmp.name, 'r', encoding='utf-8') as f:
                links = [line.strip() for line in f if line.strip()]
            os.unlink(tmp.name)
    elif update.message.text:
        links = [line.strip() for line in update.message.text.splitlines() if line.strip()]
    else:
        await update.message.reply_text("Неверный формат")
        return
    if not links:
        await update.message.reply_text("Ссылок не найдено")
        return
    added = add_links_to_db(links)
    await update.message.reply_text(f"✅ Добавлено {added} ссылок")
    context.user_data['waiting_links'] = False

async def admin_linkstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    try:
        response = supabase.table('user_data').select('owner_id, used_links').execute()
        stats = {}
        for row in response.data:
            uid = row.get('owner_id')
            if uid:
                stats[uid] = stats.get(uid, 0) + len(row.get('used_links', []))
        sorted_users = sorted(stats.items(), key=lambda x: x[1], reverse=True)
        if not sorted_users:
            await update.message.reply_text("Нет данных")
            return
        text = "🏆 Топ пользователей:\n"
        for idx, (uid, count) in enumerate(sorted_users[:5], 1):
            text += f"{idx}. {uid} – {count} ссылок\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ---------- СОЗДАНИЕ ПРИЛОЖЕНИЯ БОТА ----------
bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("help", help_command))
bot_app.add_handler(CommandHandler("key", set_key))
bot_app.add_handler(CommandHandler("get", get_link))
bot_app.add_handler(CommandHandler("stat", stats))
bot_app.add_handler(CommandHandler("history", history))
bot_app.add_handler(CommandHandler("admin_help", admin_help))
bot_app.add_handler(CommandHandler("admin_stats", admin_stats))
bot_app.add_handler(CommandHandler("admin_links", admin_links))
bot_app.add_handler(CommandHandler("admin_users", admin_users))
bot_app.add_handler(CommandHandler("admin_create", admin_create))
bot_app.add_handler(CommandHandler("admin_deactivate", admin_deactivate))
bot_app.add_handler(CommandHandler("admin_activate", admin_activate))
bot_app.add_handler(CommandHandler("admin_refill", admin_refill))
bot_app.add_handler(CommandHandler("admin_deletekey", admin_deletekey))
bot_app.add_handler(CommandHandler("admin_confirm_delete", admin_confirm_delete))
bot_app.add_handler(CommandHandler("admin_deleteuser", admin_deleteuser))
bot_app.add_handler(CommandHandler("admin_addlinks", admin_addlinks))
bot_app.add_handler(CommandHandler("admin_linkstats", admin_linkstats))
bot_app.add_handler(CallbackQueryHandler(deleteuser_callback, pattern="^(confirm_deluser_|cancel_deluser)"))
bot_app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_links_input))
bot_app.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))

# ---------- ВЕБХУК ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, bot_app.bot)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot_app.process_update(update))
        finally:
            loop.close()
        return "ok", 200
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return "error", 500

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    from urllib.parse import urljoin
    webhook_url = urljoin(request.url_root, 'webhook')
    import requests
    response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    return jsonify(response.json())

@app.route('/', methods=['GET'])
def index():
    return "✅ Бот работает!"

# ---------- ЗАПУСК ----------
if __name__ == "__main__":
    ensure_tables()
    if not load_keys():
        sample_keys = {
            "FREE-2024-ABCD": {
                "type": "trial",
                "expires": "2024-12-31",
                "max_links": 10,
                "active": True,
                "created": datetime.datetime.now().isoformat()
            },
            "PREMIUM-2024-XYZ": {
                "type": "premium",
                "expires": "2025-12-31",
                "max_links": 999999,
                "active": True,
                "created": datetime.datetime.now().isoformat()
            }
        }
        for key_text, data in sample_keys.items():
            data_to_insert = data.copy()
            data_to_insert['key_text'] = key_text
            try:
                supabase.table('keys').upsert(data_to_insert).execute()
            except Exception as e:
                print(f"⚠️ Ошибка создания тестовых ключей: {e}")
        print("✅ Тестовые ключи созданы")
    app.run(host='0.0.0.0', port=PORT)