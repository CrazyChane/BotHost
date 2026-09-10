from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import os
import secrets
import string
from collections import defaultdict
from typing import Any, Callable

from supabase import Client, create_client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# ЛОГИРОВАНИЕ
# =========================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("nfa_vpn_bot")


# =========================================================
# КОНФИГУРАЦИЯ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")

# Здесь должен находиться именно service_role key.
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
)

ADMIN_ID_RAW = os.getenv("ADMIN_ID")

CONTACT_USERNAME = os.getenv(
    "CONTACT_USERNAME",
    "user123311a",
).lstrip("@")

BOT_USERNAME = os.getenv(
    "BOT_USERNAME",
    "NFA_vpn_bot",
).lstrip("@")

MAX_CREATE_KEYS = 100
MAX_LINKS_PER_FILE = 5000
MAX_LINK_LENGTH = 2048
MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024
MAX_SCREENSHOT_NAME_LENGTH = 100
TELEGRAM_TEXT_LIMIT = 3900


def require_environment() -> int:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL не задан")

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY не задан"
        )

    if not ADMIN_ID_RAW:
        raise RuntimeError("ADMIN_ID не задан")

    try:
        return int(ADMIN_ID_RAW)
    except ValueError as error:
        raise RuntimeError(
            "ADMIN_ID должен быть целым числом"
        ) from error


ADMIN_ID = require_environment()

CONTACT_URL = f"https://t.me/{CONTACT_USERNAME}"
CONTACT_TEXT = f"@{CONTACT_USERNAME}"

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def is_admin(user_id: int | None) -> bool:
    return user_id == ADMIN_ID


def escape(value: Any) -> str:
    return html.escape(str(value))


def format_expiration(value: Any) -> str:
    if not value:
        return "бессрочно"
    return str(value)


def format_timestamp(value: Any) -> str:
    if not value:
        return "неизвестно"

    return str(value).replace("T", " ")[:16]


def status_text(status: str) -> str:
    return {
        "yes": "✅ Работает",
        "no": "❌ Не работает",
        "pending": "⏳ Ожидает проверки",
    }.get(status, "❓ Неизвестно")


def split_plain_text(
    text: str,
    limit: int = TELEGRAM_TEXT_LIMIT,
) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""

            for index in range(0, len(line), limit):
                chunks.append(line[index:index + limit].rstrip())

            continue

        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line

    if current:
        chunks.append(current.rstrip())

    return chunks or [""]


async def send_long_text(
    message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chunks = split_plain_text(text)

    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.reply_text(
            chunk,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def safe_edit(
    query,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


def parse_rpc_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data

    if isinstance(data, list) and data:
        if isinstance(data[0], dict):
            return data[0]

    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return {}


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

class Database:
    def __init__(self, client: Client):
        self.client = client

    async def _run(
        self,
        operation: Callable[[], Any],
    ) -> Any:
        """
        supabase-py синхронный, поэтому запросы выполняются
        в отдельном потоке и не блокируют Telegram-бота.
        """
        return await asyncio.to_thread(operation)

    async def _rpc(
        self,
        function_name: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._run(
            lambda: self.client.rpc(
                function_name,
                params or {},
            ).execute()
        )
        return response.data

    async def activate_key(
        self,
        key_text: str,
        owner_id: int,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "activate_key",
            {
                "p_key_text": key_text,
                "p_owner_id": owner_id,
            },
        )
        return parse_rpc_dict(data)

    async def issue_link(
        self,
        owner_id: int,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "issue_link",
            {"p_owner_id": owner_id},
        )
        return parse_rpc_dict(data)

    async def set_link_status(
        self,
        history_id: int,
        owner_id: int,
        status: str,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "set_link_status",
            {
                "p_history_id": history_id,
                "p_owner_id": owner_id,
                "p_status": status,
            },
        )
        return parse_rpc_dict(data)

    async def get_user_stats(
        self,
        owner_id: int,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "get_user_stats",
            {"p_owner_id": owner_id},
        )
        return parse_rpc_dict(data)

    async def get_admin_stats(self) -> dict[str, Any]:
        data = await self._rpc("get_admin_stats")
        return parse_rpc_dict(data)

    async def refill_key(
        self,
        key_text: str,
        amount: int,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "admin_refill_key",
            {
                "p_key_text": key_text,
                "p_amount": amount,
            },
        )
        return parse_rpc_dict(data)

    async def delete_user(
        self,
        owner_id: int,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "admin_delete_user",
            {"p_owner_id": owner_id},
        )
        return parse_rpc_dict(data)

    async def delete_key(
        self,
        key_text: str,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "admin_delete_key",
            {"p_key_text": key_text},
        )
        return parse_rpc_dict(data)

    async def delete_unused_keys(
        self,
    ) -> dict[str, Any]:
        data = await self._rpc(
            "admin_delete_unused_keys"
        )
        return parse_rpc_dict(data)

    async def add_links(
        self,
        urls: list[str],
    ) -> int:
        added = 0
        batch_size = 500

        for offset in range(0, len(urls), batch_size):
            batch = urls[offset:offset + batch_size]

            data = await self._rpc(
                "admin_add_links",
                {"p_urls": batch},
            )

            result = parse_rpc_dict(data)
            added += int(result.get("added", 0))

        return added

    async def take_links(
        self,
        count: int,
    ) -> list[str]:
        data = await self._rpc(
            "admin_take_links",
            {"p_count": count},
        )

        result: list[str] = []

        for row in data or []:
            if isinstance(row, dict) and row.get("url"):
                result.append(str(row["url"]))
            elif isinstance(row, str):
                result.append(row)

        return result

    async def top_users(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        data = await self._rpc(
            "admin_top_users",
            {"p_limit": limit},
        )
        return data or []

    async def admin_user_info(
        self,
        owner_id: int,
    ) -> list[dict[str, Any]]:
        data = await self._rpc(
            "admin_user_info",
            {"p_owner_id": owner_id},
        )
        return data or []

    async def count_links(self) -> int:
        response = await self._run(
            lambda: self.client.table("links")
            .select("id", count="exact")
            .limit(1)
            .execute()
        )
        return int(response.count or 0)

    async def get_history(
        self,
        owner_id: int,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        response = await self._run(
            lambda: self.client.table("link_history")
            .select(
                "id,key_text,link,status,created_at"
            )
            .eq("owner_id", owner_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    async def get_all_user_keys(
        self,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 1000
        offset = 0

        while True:
            response = await self._run(
                lambda current_offset=offset: (
                    self.client.table("user_keys")
                    .select(
                        "owner_id,key_text,"
                        "remaining_links,expires"
                    )
                    .order("owner_id")
                    .range(
                        current_offset,
                        current_offset + page_size - 1,
                    )
                    .execute()
                )
            )

            page = response.data or []
            rows.extend(page)

            if len(page) < page_size:
                break

            offset += page_size

        return rows

    async def create_keys(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        response = await self._run(
            lambda: self.client.table("keys")
            .insert(rows)
            .execute()
        )

        if response.data is None:
            return len(rows)

        return len(response.data)

    async def get_key_details(
        self,
        key_text: str,
    ) -> dict[str, Any] | None:
        key_response = await self._run(
            lambda: self.client.table("keys")
            .select(
                "key_text,type,expires,max_links,"
                "active,owner_id,created"
            )
            .eq("key_text", key_text)
            .limit(1)
            .execute()
        )

        if not key_response.data:
            return None

        result = dict(key_response.data[0])

        user_key_response = await self._run(
            lambda: self.client.table("user_keys")
            .select("owner_id,remaining_links,expires")
            .eq("key_text", key_text)
            .limit(1)
            .execute()
        )

        result["user_key"] = (
            user_key_response.data[0]
            if user_key_response.data
            else None
        )

        return result

    async def set_key_active(
        self,
        key_text: str,
        active: bool,
    ) -> bool:
        details = await self.get_key_details(key_text)

        if not details:
            return False

        await self._run(
            lambda: self.client.table("keys")
            .update({"active": active})
            .eq("key_text", key_text)
            .execute()
        )
        return True

    async def count_unused_keys(self) -> int:
        response = await self._run(
            lambda: self.client.table("keys")
            .select("key_text", count="exact")
            .is_("owner_id", "null")
            .limit(1)
            .execute()
        )
        return int(response.count or 0)

    async def list_keys(
        self,
        active: bool | None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        def operation():
            query = self.client.table("keys").select(
                "key_text,type,expires,max_links,"
                "active,owner_id,created",
                count="exact",
            )

            if active is not None:
                query = query.eq("active", active)

            return (
                query.order("created", desc=True)
                .limit(limit)
                .execute()
            )

        response = await self._run(operation)

        return response.data or [], int(response.count or 0)

    async def get_screenshots(
        self,
    ) -> list[dict[str, Any]]:
        response = await self._run(
            lambda: self.client.table("screenshots")
            .select("id,name,file_id,created_at")
            .order("id")
            .execute()
        )
        return response.data or []

    async def save_screenshot(
        self,
        name: str,
        file_id: str,
    ) -> bool:
        response = await self._run(
            lambda: self.client.table("screenshots")
            .insert({
                "name": name,
                "file_id": file_id,
            })
            .execute()
        )
        return bool(response.data)

    async def get_screenshot(
        self,
        screenshot_id: int,
    ) -> dict[str, Any] | None:
        response = await self._run(
            lambda: self.client.table("screenshots")
            .select("id,name,file_id")
            .eq("id", screenshot_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            return None

        return response.data[0]

    async def delete_screenshot(
        self,
        screenshot_id: int,
    ) -> bool:
        screenshot = await self.get_screenshot(screenshot_id)

        if not screenshot:
            return False

        await self._run(
            lambda: self.client.table("screenshots")
            .delete()
            .eq("id", screenshot_id)
            .execute()
        )
        return True


db = Database(supabase)


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛒 Купить ключ",
                url=CONTACT_URL,
            ),
            InlineKeyboardButton(
                "📖 Информация",
                callback_data="menu:info",
            ),
        ],
        [
            InlineKeyboardButton(
                "📎 Получить ссылку",
                callback_data="menu:get",
            ),
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="menu:stats",
            ),
        ],
        [
            InlineKeyboardButton(
                "📜 История",
                callback_data="menu:history",
            ),
            InlineKeyboardButton(
                "❓ Помощь",
                callback_data="menu:help",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 Главное меню",
                callback_data="menu:start",
            ),
        ],
    ])


def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Общая статистика",
                callback_data="admin:stats",
            ),
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin:users",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 Создать ключи",
                callback_data="admin:create",
            ),
            InlineKeyboardButton(
                "📤 Добавить ссылки",
                callback_data="admin:addlinks",
            ),
        ],
        [
            InlineKeyboardButton(
                "📎 Количество ссылок",
                callback_data="admin:links",
            ),
            InlineKeyboardButton(
                "🏆 Топ пользователей",
                callback_data="admin:top",
            ),
        ],
        [
            InlineKeyboardButton(
                "🖼️ Скриншоты",
                callback_data="admin:screens",
            ),
            InlineKeyboardButton(
                "📎 Выдать ссылки",
                callback_data="admin:get",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔓 Активировать ключ",
                callback_data="admin:activate",
            ),
            InlineKeyboardButton(
                "🔒 Деактивировать ключ",
                callback_data="admin:deactivate",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Пополнить ключ",
                callback_data="admin:refill",
            ),
            InlineKeyboardButton(
                "🗑️ Удалить ключ",
                callback_data="admin:deletekey",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 Все ключи",
                callback_data="admin:allkeys",
            ),
            InlineKeyboardButton(
                "🗑️ Удалить неиспользуемые",
                callback_data="admin:unused:ask",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 Удалить пользователя",
                callback_data="admin:deleteuser",
            ),
            InlineKeyboardButton(
                "📋 Помощь",
                callback_data="admin:help",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 Главное меню",
                callback_data="menu:start",
            ),
        ],
    ])


def keyboard_for_user(user_id: int) -> InlineKeyboardMarkup:
    if is_admin(user_id):
        return get_admin_keyboard()

    return get_main_keyboard()


def status_keyboard(
    history_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Работает",
                callback_data=f"status:yes:{history_id}",
            ),
            InlineKeyboardButton(
                "❌ Не работает",
                callback_data=f"status:no:{history_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 Главное меню",
                callback_data="menu:start",
            ),
        ],
    ])


def get_keys_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🟢 Активные",
                callback_data="admin:keys:active",
            ),
            InlineKeyboardButton(
                "🔴 Неактивные",
                callback_data="admin:keys:inactive",
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Все ключи",
                callback_data="admin:keys:all",
            ),
            InlineKeyboardButton(
                "🏠 Назад",
                callback_data="menu:start",
            ),
        ],
    ])


# =========================================================
# ТЕКСТЫ И ОТОБРАЖЕНИЕ
# =========================================================

def main_menu_text() -> str:
    return (
        "👋 <b>Добро пожаловать в NFAvpn!</b>\n\n"
        "Вы можете активировать несколько ключей "
        "на одном Telegram-аккаунте.\n"
        "Каждый ключ добавляет собственный лимит ссылок.\n\n"
        "Для активации используйте:\n"
        "<code>/key ВАШ-КЛЮЧ</code>\n\n"
        f"По вопросам покупки: {escape(CONTACT_TEXT)}"
    )


def help_text(admin: bool = False) -> str:
    text = (
        "❓ <b>Доступные команды</b>\n\n"
        "<code>/key КЛЮЧ</code> — активировать ключ\n"
        "<code>/get</code> — получить ссылку\n"
        "<code>/stat</code> — статистика\n"
        "<code>/history</code> — история ссылок\n"
        "<code>/info</code> — информация о покупке\n"
        "<code>/help</code> — помощь\n"
        "<code>/cancel</code> — отменить текущий ввод\n\n"
        f"Поддержка: {escape(CONTACT_TEXT)}"
    )

    if admin:
        text += (
            "\n\n🔐 <b>Администратор:</b>\n"
            "<code>/admin_help</code>"
        )

    return text


def admin_help_text() -> str:
    return (
        "🔐 <b>Административные команды</b>\n\n"
        "<code>/admin_stats</code> — общая статистика\n"
        "<code>/admin_links</code> — количество ссылок\n"
        "<code>/admin_users</code> — пользователи\n"
        "<code>/admin_userinfo ID</code> — профиль\n\n"
        "<code>/admin_create TYPE COUNT LIMIT</code>\n"
        "Пример: <code>/admin_create premium 5 100</code>\n\n"
        "<code>/admin_activate KEY</code>\n"
        "<code>/admin_deactivate KEY</code>\n"
        "<code>/admin_refill KEY AMOUNT</code>\n"
        "<code>/admin_deletekey KEY</code>\n"
        "<code>/admin_deleteuser ID</code>\n\n"
        "<code>/admin_addlinks</code> — добавить ссылки\n"
        "<code>/admin_get COUNT</code> — получить ссылки\n"
        "<code>/admin_linkstats</code> — топ пользователей\n\n"
        "<code>/admin_add_screenshot [название]</code>\n"
        "<code>/admin_list_screenshots</code>\n"
        "<code>/admin_del_screenshot ID</code>\n\n"
        "<code>/admin_all_keys</code>\n"
        "<code>/admin_delete_unused</code>\n"
        "<code>/cancel</code> — отменить ввод"
    )


def info_text() -> str:
    return (
        "📋 <b>NFAvpn — информация</b>\n\n"
        "<b>🔑 Как получить ключ:</b>\n"
        f"1. Напишите администратору: {escape(CONTACT_TEXT)}\n"
        "2. Укажите необходимое количество ключей\n"
        "3. Оплатите заказ\n"
        "4. Получите ключ и активируйте его командой:\n"
        "<code>/key ВАШ-КЛЮЧ</code>\n\n"
        "<b>💰 Стоимость:</b>\n"
        "• 1 ключ на 5 использований — 100 ₽\n"
        f"• Оптовые закупки — {escape(CONTACT_TEXT)}\n\n"
        "<b>🔄 Гарантия:</b>\n"
        "Если ни одна из пяти ссылок не работает, "
        "обратитесь к администратору и приложите "
        "подтверждающие скриншоты.\n\n"
        "<b>📞 Контакты:</b>\n"
        f"Администратор: {escape(CONTACT_TEXT)}\n"
        f"Бот: @{escape(BOT_USERNAME)}"
    )


async def build_user_stats_text(
    user_id: int,
) -> str:
    stats_result, total_pool = await asyncio.gather(
        db.get_user_stats(user_id),
        db.count_links(),
    )

    keys_info = stats_result.get("keys_info") or []

    lines = [
        "📊 Ваша статистика:",
        "",
        (
            "Активных ключей: "
            f"{stats_result.get('active_keys_count', 0)}"
        ),
        (
            "Осталось использований: "
            f"{stats_result.get('total_remaining', 0)}"
        ),
        f"Ссылок в общем пуле: {total_pool}",
    ]

    if keys_info:
        lines.extend(["", "🔑 Ключи:"])

        for item in keys_info:
            active = bool(item.get("active"))
            remaining = int(
                item.get("remaining_links", 0)
            )

            marker = "🟢" if active and remaining > 0 else "🔴"

            lines.append(
                f"{marker} {item.get('key_text')} — "
                f"осталось {remaining}, "
                f"до {format_expiration(item.get('expires'))}"
            )

    return "\n".join(lines)


async def build_history_text(
    user_id: int,
) -> str:
    history_rows = await db.get_history(user_id, 10)

    if not history_rows:
        return "📭 История пуста"

    lines = ["📜 Последние 10 ссылок:", ""]

    for index, row in enumerate(history_rows, start=1):
        lines.append(
            f"{index}. {row.get('link')}\n"
            f"   {status_text(str(row.get('status')))} | "
            f"{format_timestamp(row.get('created_at'))}"
        )

    return "\n".join(lines)


async def send_info_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    user_id = update.effective_user.id
    keyboard = keyboard_for_user(user_id)

    if edit and update.callback_query:
        await safe_edit(
            update.callback_query,
            info_text(),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_message.reply_text(
            info_text(),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    screenshots = await db.get_screenshots()

    for screenshot in screenshots:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=screenshot["file_id"],
                caption=str(screenshot["name"])[:1024],
            )
        except Exception:
            logger.exception(
                "Не удалось отправить скриншот id=%s",
                screenshot.get("id"),
            )


# =========================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_id = update.effective_user.id

    await update.message.reply_text(
        main_menu_text(),
        reply_markup=keyboard_for_user(user_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_id = update.effective_user.id

    await update.message.reply_text(
        help_text(is_admin(user_id)),
        reply_markup=keyboard_for_user(user_id),
        parse_mode=ParseMode.HTML,
    )


async def info_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await send_info_content(update, context)


async def set_key(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите ключ.\n\n"
            "Пример:\n"
            "/key PREMIUM-2026-ABC123",
            reply_markup=get_main_keyboard(),
        )
        return

    key_text = context.args[0].strip().upper()

    if len(key_text) > 200:
        await update.message.reply_text(
            "❌ Слишком длинный ключ",
            reply_markup=get_main_keyboard(),
        )
        return

    result = await db.activate_key(
        key_text,
        update.effective_user.id,
    )

    if result.get("success"):
        await update.message.reply_text(
            "✅ Ключ успешно активирован!\n\n"
            f"Ключ: {result.get('key_text')}\n"
            f"Тип: {result.get('type')}\n"
            f"Срок: {format_expiration(result.get('expires'))}\n"
            f"Добавлено использований: "
            f"{result.get('max_links', 0)}",
            reply_markup=get_main_keyboard(),
        )
        return

    error_messages = {
        "empty_key": "Ключ не указан",
        "invalid_key": "Неверный ключ",
        "inactive_key": "Ключ деактивирован",
        "expired_key": "Срок действия ключа истёк",
        "empty_limit": "В ключе нет доступных использований",
        "owned_by_another": (
            "Ключ уже активирован другим пользователем"
        ),
        "already_activated": (
            "Этот ключ уже активирован вами"
        ),
    }

    code = str(result.get("code"))

    await update.message.reply_text(
        f"❌ {error_messages.get(code, 'Ошибка активации ключа')}",
        reply_markup=get_main_keyboard(),
    )


async def send_issued_link_to_message(
    update: Update,
) -> None:
    result = await db.issue_link(
        update.effective_user.id
    )

    if not result.get("success"):
        messages = {
            "no_active_keys": (
                "Нет активных ключей с доступным лимитом"
            ),
            "no_links": (
                "Ссылки временно закончились. "
                "Лимит ключа не был списан."
            ),
        }

        await update.message.reply_text(
            f"❌ {messages.get(result.get('code'), 'Ошибка выдачи')}",
            reply_markup=get_main_keyboard(),
        )
        return

    await update.message.reply_text(
        "📎 Ваша ссылка:\n"
        f"{result.get('link')}\n\n"
        f"Ключ: {result.get('key_text')}\n"
        f"Осталось использований: "
        f"{result.get('remaining', 0)}\n\n"
        "Пожалуйста, укажите, работает ли ссылка:",
        reply_markup=status_keyboard(
            int(result["history_id"])
        ),
        disable_web_page_preview=True,
    )


async def get_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await send_issued_link_to_message(update)


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = await build_user_stats_text(
        update.effective_user.id
    )

    await update.message.reply_text(
        text,
        reply_markup=get_main_keyboard(),
    )


async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = await build_history_text(
        update.effective_user.id
    )

    await update.message.reply_text(
        text,
        reply_markup=get_main_keyboard(),
        disable_web_page_preview=True,
    )


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    had_state = context.user_data.pop(
        "input_state",
        None,
    )

    if had_state:
        text = "✅ Текущий ввод отменён"
    else:
        text = "ℹ️ Нет активного ввода"

    await update.message.reply_text(
        text,
        reply_markup=keyboard_for_user(
            update.effective_user.id
        ),
    )


# =========================================================
# ПРОВЕРКА АДМИНИСТРАТОРА
# =========================================================

async def require_admin(
    update: Update,
) -> bool:
    if is_admin(update.effective_user.id):
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён"
        )

    return False


# =========================================================
# АДМИНИСТРАТИВНЫЕ КОМАНДЫ
# =========================================================

async def admin_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    await update.message.reply_text(
        admin_help_text(),
        reply_markup=get_admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def admin_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    result = await db.get_admin_stats()

    text = (
        "📊 Общая статистика:\n\n"
        f"Всего ключей: {result.get('total_keys', 0)}\n"
        f"Пользователей: {result.get('total_users', 0)}\n"
        f"Ссылок в пуле: {result.get('total_links', 0)}\n"
        f"Выдано ссылок: {result.get('total_issued', 0)}"
    )

    await update.message.reply_text(
        text,
        reply_markup=get_admin_keyboard(),
    )


async def admin_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    count = await db.count_links()

    await update.message.reply_text(
        f"🔗 Ссылок в пуле: {count}",
        reply_markup=get_admin_keyboard(),
    )


def render_users(
    rows: list[dict[str, Any]],
    max_length: int | None = None,
) -> str:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        owner_id = row.get("owner_id")

        if owner_id is not None:
            grouped[int(owner_id)].append(row)

    if not grouped:
        return "👥 Нет пользователей"

    lines = ["👥 Пользователи и ключи:", ""]

    for owner_id, keys in grouped.items():
        lines.append(
            f"🆔 {owner_id} — ключей: {len(keys)}"
        )

        for item in keys:
            lines.append(
                f"  🔑 {item.get('key_text')} — "
                f"{item.get('remaining_links', 0)}"
            )

        lines.append(
            f"  /admin_userinfo {owner_id}"
        )
        lines.append("")

        if max_length:
            current_text = "\n".join(lines)

            if len(current_text) > max_length:
                lines.append("... список сокращён")
                break

    return "\n".join(lines)


async def admin_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    rows = await db.get_all_user_keys()
    text = render_users(rows)

    await send_long_text(
        update.message,
        text,
        get_admin_keyboard(),
    )


async def admin_userinfo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_userinfo USER_ID",
            reply_markup=get_admin_keyboard(),
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ ID должен быть числом",
            reply_markup=get_admin_keyboard(),
        )
        return

    rows = await db.admin_user_info(user_id)

    if not rows:
        await update.message.reply_text(
            "❌ Пользователь не найден или у него нет ключей",
            reply_markup=get_admin_keyboard(),
        )
        return

    lines = [
        "👤 Профиль пользователя",
        f"🆔 ID: {user_id}",
        f"🔑 Ключей: {len(rows)}",
        "",
    ]

    total_remaining = 0

    for row in rows:
        remaining = int(row.get("remaining_links", 0))
        total_remaining += remaining

        lines.extend([
            f"🔑 {row.get('key_text')}",
            f"   Осталось: {remaining}",
            (
                "   Срок: "
                f"{format_expiration(row.get('expires'))}"
            ),
            (
                "   Состояние: "
                f"{'активен' if row.get('active') else 'неактивен'}"
            ),
        ])

        if row.get("last_link"):
            lines.extend([
                f"   Последняя ссылка: {row.get('last_link')}",
                (
                    "   Статус: "
                    f"{status_text(str(row.get('last_status')))}"
                ),
                (
                    "   Дата: "
                    f"{format_timestamp(row.get('last_created_at'))}"
                ),
            ])

        lines.append("")

    lines.append(
        f"📊 Всего осталось: {total_remaining}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑️ Удалить пользователя",
                callback_data=f"admin:deluser:ask:{user_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 Главное меню",
                callback_data="menu:start",
            ),
        ],
    ])

    await send_long_text(
        update.message,
        "\n".join(lines),
        keyboard,
    )


def generate_key(key_type: str) -> str:
    prefix = "FREE" if key_type == "trial" else "PREMIUM"
    year = datetime.datetime.now(
        datetime.timezone.utc
    ).year

    alphabet = string.ascii_uppercase + string.digits

    random_part = "".join(
        secrets.choice(alphabet)
        for _ in range(12)
    )

    return f"{prefix}-{year}-{random_part}"


async def admin_create(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if len(context.args) != 3:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_create TYPE COUNT LIMIT\n\n"
            "Пример:\n"
            "/admin_create premium 5 100\n\n"
            "TYPE: trial или premium",
            reply_markup=get_admin_keyboard(),
        )
        return

    key_type = context.args[0].lower().strip()

    if key_type not in {"trial", "premium"}:
        await update.message.reply_text(
            "❌ Тип должен быть trial или premium",
            reply_markup=get_admin_keyboard(),
        )
        return

    try:
        count = int(context.args[1])
        max_links = int(context.args[2])
    except ValueError:
        await update.message.reply_text(
            "❌ COUNT и LIMIT должны быть числами",
            reply_markup=get_admin_keyboard(),
        )
        return

    if not 1 <= count <= MAX_CREATE_KEYS:
        await update.message.reply_text(
            f"❌ Можно создать от 1 до "
            f"{MAX_CREATE_KEYS} ключей за раз",
            reply_markup=get_admin_keyboard(),
        )
        return

    if not 1 <= max_links <= 10_000_000:
        await update.message.reply_text(
            "❌ Лимит должен быть от 1 до 10000000",
            reply_markup=get_admin_keyboard(),
        )
        return

    now = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    keys = [generate_key(key_type) for _ in range(count)]

    rows = [
        {
            "key_text": key,
            "type": key_type,
            "expires": None,
            "max_links": max_links,
            "active": True,
            "owner_id": None,
            "created": now,
        }
        for key in keys
    ]

    created_count = await db.create_keys(rows)

    text = (
        f"✅ Создано ключей: {created_count}\n\n"
        + "\n".join(keys)
    )

    await send_long_text(
        update.message,
        text,
        get_admin_keyboard(),
    )


async def change_key_active(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    active: bool,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        command = (
            "/admin_activate KEY"
            if active
            else "/admin_deactivate KEY"
        )

        await update.message.reply_text(
            f"Использование:\n{command}",
            reply_markup=get_admin_keyboard(),
        )
        return

    key_text = context.args[0].strip().upper()
    updated = await db.set_key_active(key_text, active)

    if not updated:
        await update.message.reply_text(
            "❌ Ключ не найден",
            reply_markup=get_admin_keyboard(),
        )
        return

    action = "активирован" if active else "деактивирован"

    await update.message.reply_text(
        f"✅ Ключ {key_text} {action}",
        reply_markup=get_admin_keyboard(),
    )


async def admin_activate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await change_key_active(
        update,
        context,
        active=True,
    )


async def admin_deactivate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await change_key_active(
        update,
        context,
        active=False,
    )


async def admin_refill(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_refill KEY AMOUNT",
            reply_markup=get_admin_keyboard(),
        )
        return

    key_text = context.args[0].strip().upper()

    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Количество должно быть числом",
            reply_markup=get_admin_keyboard(),
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "❌ Количество должно быть больше нуля",
            reply_markup=get_admin_keyboard(),
        )
        return

    result = await db.refill_key(key_text, amount)

    if not result.get("success"):
        await update.message.reply_text(
            "❌ Ключ не активирован пользователем",
            reply_markup=get_admin_keyboard(),
        )
        return

    await update.message.reply_text(
        f"✅ Добавлено: {amount}\n"
        f"Текущий остаток: {result.get('remaining', 0)}",
        reply_markup=get_admin_keyboard(),
    )


async def admin_deletekey(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_deletekey KEY",
            reply_markup=get_admin_keyboard(),
        )
        return

    key_text = context.args[0].strip().upper()
    details = await db.get_key_details(key_text)

    if not details:
        await update.message.reply_text(
            "❌ Ключ не найден",
            reply_markup=get_admin_keyboard(),
        )
        return

    token = secrets.token_urlsafe(8)

    pending = context.user_data.setdefault(
        "pending_key_deletes",
        {},
    )
    pending.clear()
    pending[token] = key_text

    user_key = details.get("user_key") or {}
    owner_id = details.get("owner_id")
    remaining = user_key.get("remaining_links", 0)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Да, удалить",
                callback_data=f"admin:delkey:yes:{token}",
            ),
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data=f"admin:delkey:no:{token}",
            ),
        ],
    ])

    await update.message.reply_text(
        "⚠️ Подтвердите удаление ключа\n\n"
        f"Ключ: {key_text}\n"
        f"Владелец: {owner_id or 'не привязан'}\n"
        f"Осталось: {remaining}\n\n"
        "История выданных ссылок сохранится, "
        "но ключ и его остаток будут удалены.",
        reply_markup=keyboard,
    )


async def admin_deleteuser(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_deleteuser USER_ID",
            reply_markup=get_admin_keyboard(),
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ ID должен быть числом",
            reply_markup=get_admin_keyboard(),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Да, удалить",
                callback_data=f"admin:deluser:yes:{user_id}",
            ),
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="admin:cancel",
            ),
        ],
    ])

    await update.message.reply_text(
        f"⚠️ Удалить пользователя {user_id}?\n\n"
        "Будут удалены его ключи и история. "
        "Исходные ключи снова станут непривязанными.",
        reply_markup=keyboard,
    )


async def admin_addlinks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    context.user_data["input_state"] = {
        "mode": "links"
    }

    await update.message.reply_text(
        "📤 Отправьте текстовый файл .txt "
        "или список ссылок текстом.\n\n"
        "Одна ссылка — одна строка.\n"
        "Для отмены: /cancel",
        reply_markup=get_admin_keyboard(),
    )


async def admin_delete_unused(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    count = await db.count_unused_keys()

    if count == 0:
        await update.message.reply_text(
            "📭 Неиспользуемых ключей нет",
            reply_markup=get_admin_keyboard(),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Удалить все",
                callback_data="admin:unused:yes",
            ),
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="admin:cancel",
            ),
        ],
    ])

    await update.message.reply_text(
        f"⚠️ Найдено неиспользуемых ключей: {count}\n\n"
        "Удалить их безвозвратно?",
        reply_markup=keyboard,
    )


async def admin_linkstats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    rows = await db.top_users(10)

    if not rows:
        await update.message.reply_text(
            "📭 Статистика отсутствует",
            reply_markup=get_admin_keyboard(),
        )
        return

    lines = ["🏆 Топ пользователей:", ""]

    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. {row.get('user_id')} — "
            f"{row.get('issued_count', 0)} ссылок"
        )

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=get_admin_keyboard(),
    )


async def admin_get(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_get COUNT\n\n"
            "Максимум: 50",
            reply_markup=get_admin_keyboard(),
        )
        return

    try:
        count = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Количество должно быть числом",
            reply_markup=get_admin_keyboard(),
        )
        return

    if not 1 <= count <= 50:
        await update.message.reply_text(
            "❌ Количество должно быть от 1 до 50",
            reply_markup=get_admin_keyboard(),
        )
        return

    links = await db.take_links(count)

    if not links:
        await update.message.reply_text(
            "❌ Ссылок в пуле нет",
            reply_markup=get_admin_keyboard(),
        )
        return

    remaining = await db.count_links()

    lines = [
        f"📎 Получено ссылок: {len(links)}",
        "",
    ]

    for index, link in enumerate(links, start=1):
        lines.append(f"{index}. {link}")

    lines.extend([
        "",
        f"📊 Осталось в пуле: {remaining}",
    ])

    await send_long_text(
        update.message,
        "\n".join(lines),
        get_admin_keyboard(),
    )


async def admin_all_keys(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    await update.message.reply_text(
        "🔑 Выберите фильтр ключей:",
        reply_markup=get_keys_filter_keyboard(),
    )


async def admin_add_screenshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if context.args:
        name = " ".join(context.args).strip()

        if len(name) > MAX_SCREENSHOT_NAME_LENGTH:
            await update.message.reply_text(
                "❌ Слишком длинное название",
                reply_markup=get_admin_keyboard(),
            )
            return

        context.user_data["input_state"] = {
            "mode": "screenshot_photo",
            "name": name,
        }

        await update.message.reply_text(
            f"Теперь отправьте изображение.\n"
            f"Название: {name}\n\n"
            "Для отмены: /cancel",
            reply_markup=get_admin_keyboard(),
        )
        return

    context.user_data["input_state"] = {
        "mode": "screenshot_name"
    }

    await update.message.reply_text(
        "Отправьте название скриншота.\n\n"
        "После этого бот попросит изображение.\n"
        "Для отмены: /cancel",
        reply_markup=get_admin_keyboard(),
    )


async def admin_list_screenshots(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    screenshots = await db.get_screenshots()

    if not screenshots:
        await update.message.reply_text(
            "📭 Скриншотов нет",
            reply_markup=get_admin_keyboard(),
        )
        return

    lines = ["📸 Скриншоты:", ""]

    for screenshot in screenshots:
        lines.append(
            f"ID {screenshot.get('id')} — "
            f"{screenshot.get('name')}"
        )

    lines.extend([
        "",
        "Удаление:",
        "/admin_del_screenshot ID",
    ])

    await send_long_text(
        update.message,
        "\n".join(lines),
        get_admin_keyboard(),
    )


async def admin_del_screenshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/admin_del_screenshot ID",
            reply_markup=get_admin_keyboard(),
        )
        return

    try:
        screenshot_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ ID должен быть числом",
            reply_markup=get_admin_keyboard(),
        )
        return

    screenshot = await db.get_screenshot(screenshot_id)

    if not screenshot:
        await update.message.reply_text(
            "❌ Скриншот не найден",
            reply_markup=get_admin_keyboard(),
        )
        return

    await db.delete_screenshot(screenshot_id)

    await update.message.reply_text(
        f"✅ Скриншот «{screenshot.get('name')}» удалён",
        reply_markup=get_admin_keyboard(),
    )


# =========================================================
# ОБРАБОТКА ТЕКСТА, ФАЙЛОВ И ФОТО
# =========================================================

def sanitize_links(
    raw_lines: list[str],
) -> tuple[list[str], int]:
    valid: list[str] = []
    invalid_count = 0
    seen: set[str] = set()

    for raw_line in raw_lines:
        link = raw_line.strip()

        if not link:
            continue

        if len(link) > MAX_LINK_LENGTH:
            invalid_count += 1
            continue

        if any(character.isspace() for character in link):
            invalid_count += 1
            continue

        if link in seen:
            continue

        seen.add(link)
        valid.append(link)

        if len(valid) >= MAX_LINKS_PER_FILE:
            break

    return valid, invalid_count


async def handle_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_admin(update.effective_user.id):
        return

    state = context.user_data.get("input_state")

    if not state:
        return

    mode = state.get("mode")

    if mode == "links":
        raw_lines: list[str]

        if update.message.document:
            document = update.message.document

            if (
                document.file_size
                and document.file_size > MAX_TEXT_FILE_SIZE
            ):
                await update.message.reply_text(
                    "❌ Файл слишком большой. Максимум 2 МБ."
                )
                return

            filename = (document.file_name or "").lower()

            if filename and not filename.endswith(".txt"):
                await update.message.reply_text(
                    "❌ Поддерживаются только .txt файлы"
                )
                return

            telegram_file = await context.bot.get_file(
                document.file_id
            )
            content = await telegram_file.download_as_bytearray()

            try:
                decoded = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                await update.message.reply_text(
                    "❌ Файл должен быть в кодировке UTF-8"
                )
                return

            raw_lines = decoded.splitlines()

        elif update.message.text:
            raw_lines = update.message.text.splitlines()

        else:
            await update.message.reply_text(
                "❌ Отправьте текст или .txt файл"
            )
            return

        links, invalid_count = sanitize_links(raw_lines)

        if not links:
            await update.message.reply_text(
                "❌ Подходящие ссылки не найдены"
            )
            return

        added = await db.add_links(links)

        context.user_data.pop("input_state", None)

        await update.message.reply_text(
            f"✅ Добавлено новых ссылок: {added}\n"
            f"Получено корректных строк: {len(links)}\n"
            f"Пропущено некорректных: {invalid_count}\n"
            f"Дубликаты автоматически проигнорированы.",
            reply_markup=get_admin_keyboard(),
        )
        return

    if mode == "screenshot_name":
        if update.message.photo:
            name = (
                update.message.caption
                or "Скриншот"
            ).strip()

            file_id = update.message.photo[-1].file_id

            await db.save_screenshot(name, file_id)
            context.user_data.pop("input_state", None)

            await update.message.reply_text(
                f"✅ Скриншот «{name}» сохранён",
                reply_markup=get_admin_keyboard(),
            )
            return

        if not update.message.text:
            await update.message.reply_text(
                "❌ Отправьте название текстом"
            )
            return

        name = update.message.text.strip()

        if not name:
            await update.message.reply_text(
                "❌ Название не должно быть пустым"
            )
            return

        if len(name) > MAX_SCREENSHOT_NAME_LENGTH:
            await update.message.reply_text(
                "❌ Максимальная длина названия — 100 символов"
            )
            return

        context.user_data["input_state"] = {
            "mode": "screenshot_photo",
            "name": name,
        }

        await update.message.reply_text(
            f"✅ Название сохранено: {name}\n\n"
            "Теперь отправьте изображение."
        )
        return

    if mode == "screenshot_photo":
        if not update.message.photo:
            await update.message.reply_text(
                "❌ Отправьте изображение как фотографию"
            )
            return

        name = str(state.get("name") or "Скриншот")
        file_id = update.message.photo[-1].file_id

        saved = await db.save_screenshot(
            name,
            file_id,
        )

        if not saved:
            await update.message.reply_text(
                "❌ Не удалось сохранить скриншот"
            )
            return

        context.user_data.pop("input_state", None)

        await update.message.reply_text(
            f"✅ Скриншот «{name}» сохранён",
            reply_markup=get_admin_keyboard(),
        )


# =========================================================
# CALLBACK-КНОПКИ
# =========================================================

async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    data = query.data or ""
    user_id = update.effective_user.id

    if data.startswith("admin:") and not is_admin(user_id):
        await query.answer(
            "Доступ запрещён",
            show_alert=True,
        )
        return

    await query.answer()

    # -----------------------------------------------------
    # Статус конкретной ссылки
    # -----------------------------------------------------

    if data.startswith("status:"):
        try:
            _, status, history_id_raw = data.split(":", 2)
            history_id = int(history_id_raw)
        except (ValueError, TypeError):
            await safe_edit(
                query,
                "❌ Некорректные данные",
                get_main_keyboard(),
            )
            return

        result = await db.set_link_status(
            history_id,
            user_id,
            status,
        )

        if result.get("success"):
            await safe_edit(
                query,
                (
                    "✅ Статус сохранён: "
                    + (
                        "ссылка работает"
                        if status == "yes"
                        else "ссылка не работает"
                    )
                ),
                get_main_keyboard(),
            )
            return

        messages = {
            "not_found": "Запись истории не найдена",
            "already_set": "Статус уже был установлен",
            "invalid_status": "Некорректный статус",
        }

        await safe_edit(
            query,
            f"❌ {messages.get(result.get('code'), 'Ошибка')}",
            get_main_keyboard(),
        )
        return

    # -----------------------------------------------------
    # Пользовательское меню
    # -----------------------------------------------------

    if data == "menu:start":
        await safe_edit(
            query,
            main_menu_text(),
            keyboard_for_user(user_id),
            ParseMode.HTML,
        )
        return

    if data == "menu:info":
        await send_info_content(
            update,
            context,
            edit=True,
        )
        return

    if data == "menu:help":
        await safe_edit(
            query,
            help_text(is_admin(user_id)),
            keyboard_for_user(user_id),
            ParseMode.HTML,
        )
        return

    if data == "menu:stats":
        text = await build_user_stats_text(user_id)

        await safe_edit(
            query,
            text,
            get_main_keyboard(),
        )
        return

    if data == "menu:history":
        text = await build_history_text(user_id)

        await safe_edit(
            query,
            text,
            get_main_keyboard(),
        )
        return

    if data == "menu:get":
        result = await db.issue_link(user_id)

        if not result.get("success"):
            messages = {
                "no_active_keys": (
                    "Нет активных ключей с доступным лимитом"
                ),
                "no_links": (
                    "Ссылки временно закончились. "
                    "Лимит не был списан."
                ),
            }

            await safe_edit(
                query,
                f"❌ {messages.get(result.get('code'), 'Ошибка')}",
                get_main_keyboard(),
            )
            return

        await safe_edit(
            query,
            "📎 Ваша ссылка:\n"
            f"{result.get('link')}\n\n"
            f"Ключ: {result.get('key_text')}\n"
            f"Осталось: {result.get('remaining', 0)}\n\n"
            "Укажите, работает ли ссылка:",
            status_keyboard(int(result["history_id"])),
        )
        return

    # -----------------------------------------------------
    # Административное меню
    # -----------------------------------------------------

    if data == "admin:stats":
        result = await db.get_admin_stats()

        text = (
            "📊 Общая статистика:\n\n"
            f"Ключей: {result.get('total_keys', 0)}\n"
            f"Пользователей: {result.get('total_users', 0)}\n"
            f"Ссылок в пуле: {result.get('total_links', 0)}\n"
            f"Выдано ссылок: {result.get('total_issued', 0)}"
        )

        await safe_edit(
            query,
            text,
            get_admin_keyboard(),
        )
        return

    if data == "admin:users":
        rows = await db.get_all_user_keys()
        text = render_users(rows, 3500)

        await safe_edit(
            query,
            text,
            get_admin_keyboard(),
        )
        return

    if data == "admin:create":
        await safe_edit(
            query,
            "Создание ключей:\n\n"
            "/admin_create TYPE COUNT LIMIT\n\n"
            "Пример:\n"
            "/admin_create premium 5 100",
            get_admin_keyboard(),
        )
        return

    if data == "admin:addlinks":
        context.user_data["input_state"] = {
            "mode": "links"
        }

        await safe_edit(
            query,
            "📤 Отправьте .txt файл или ссылки текстом.\n"
            "Одна ссылка — одна строка.\n\n"
            "Для отмены: /cancel",
            get_admin_keyboard(),
        )
        return

    if data == "admin:links":
        count = await db.count_links()

        await safe_edit(
            query,
            f"🔗 Ссылок в пуле: {count}",
            get_admin_keyboard(),
        )
        return

    if data == "admin:top":
        rows = await db.top_users(10)

        if not rows:
            text = "📭 Статистика отсутствует"
        else:
            lines = ["🏆 Топ пользователей:", ""]

            for index, row in enumerate(rows, start=1):
                lines.append(
                    f"{index}. {row.get('user_id')} — "
                    f"{row.get('issued_count', 0)} ссылок"
                )

            text = "\n".join(lines)

        await safe_edit(
            query,
            text,
            get_admin_keyboard(),
        )
        return

    if data == "admin:get":
        await safe_edit(
            query,
            "Выдача ссылок администратору:\n\n"
            "/admin_get COUNT\n\n"
            "Пример:\n"
            "/admin_get 5",
            get_admin_keyboard(),
        )
        return

    if data == "admin:activate":
        await safe_edit(
            query,
            "Активация ключа:\n\n"
            "/admin_activate KEY",
            get_admin_keyboard(),
        )
        return

    if data == "admin:deactivate":
        await safe_edit(
            query,
            "Деактивация ключа:\n\n"
            "/admin_deactivate KEY",
            get_admin_keyboard(),
        )
        return

    if data == "admin:refill":
        await safe_edit(
            query,
            "Пополнение ключа:\n\n"
            "/admin_refill KEY AMOUNT",
            get_admin_keyboard(),
        )
        return

    if data == "admin:deletekey":
        await safe_edit(
            query,
            "Удаление ключа:\n\n"
            "/admin_deletekey KEY",
            get_admin_keyboard(),
        )
        return

    if data == "admin:deleteuser":
        await safe_edit(
            query,
            "Удаление пользователя:\n\n"
            "/admin_deleteuser USER_ID",
            get_admin_keyboard(),
        )
        return

    if data == "admin:help":
        await safe_edit(
            query,
            admin_help_text(),
            get_admin_keyboard(),
            ParseMode.HTML,
        )
        return

    if data == "admin:allkeys":
        await safe_edit(
            query,
            "🔑 Выберите фильтр ключей:",
            get_keys_filter_keyboard(),
        )
        return

    if data.startswith("admin:keys:"):
        filter_name = data.rsplit(":", 1)[-1]

        filters_map = {
            "active": True,
            "inactive": False,
            "all": None,
        }

        if filter_name not in filters_map:
            await safe_edit(
                query,
                "❌ Некорректный фильтр",
                get_admin_keyboard(),
            )
            return

        rows, total = await db.list_keys(
            filters_map[filter_name],
            50,
        )

        if not rows:
            await safe_edit(
                query,
                "📭 Ключи не найдены",
                get_keys_filter_keyboard(),
            )
            return

        lines = [
            f"🔑 Найдено ключей: {total}",
            "",
        ]

        for row in rows:
            marker = "🟢" if row.get("active") else "🔴"
            owner = row.get("owner_id")

            lines.append(
                f"{marker} {row.get('key_text')}\n"
                f"   Владелец: "
                f"{owner if owner is not None else 'не привязан'}\n"
                f"   Лимит: {row.get('max_links', 0)}"
            )

        if total > len(rows):
            lines.extend([
                "",
                f"... и ещё {total - len(rows)}",
            ])

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data=data,
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔙 К фильтрам",
                    callback_data="admin:allkeys",
                ),
                InlineKeyboardButton(
                    "🏠 Меню",
                    callback_data="menu:start",
                ),
            ],
        ])

        text = "\n".join(lines)

        if len(text) > TELEGRAM_TEXT_LIMIT:
            text = (
                text[:TELEGRAM_TEXT_LIMIT - 50]
                + "\n\n... список сокращён"
            )

        await safe_edit(
            query,
            text,
            keyboard,
        )
        return

    if data == "admin:unused:ask":
        count = await db.count_unused_keys()

        if count == 0:
            await safe_edit(
                query,
                "📭 Неиспользуемых ключей нет",
                get_admin_keyboard(),
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Удалить все",
                    callback_data="admin:unused:yes",
                ),
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="admin:cancel",
                ),
            ],
        ])

        await safe_edit(
            query,
            f"⚠️ Неиспользуемых ключей: {count}\n\n"
            "Удалить их безвозвратно?",
            keyboard,
        )
        return

    if data == "admin:unused:yes":
        result = await db.delete_unused_keys()

        await safe_edit(
            query,
            f"✅ Удалено ключей: "
            f"{result.get('deleted_count', 0)}",
            get_admin_keyboard(),
        )
        return

    if data.startswith("admin:deluser:ask:"):
        try:
            target_user_id = int(data.rsplit(":", 1)[-1])
        except ValueError:
            await safe_edit(
                query,
                "❌ Некорректный ID",
                get_admin_keyboard(),
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Да, удалить",
                    callback_data=(
                        f"admin:deluser:yes:{target_user_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="admin:cancel",
                ),
            ],
        ])

        await safe_edit(
            query,
            f"⚠️ Удалить пользователя {target_user_id}?\n\n"
            "Будут удалены его ключи и история.",
            keyboard,
        )
        return

    if data.startswith("admin:deluser:yes:"):
        try:
            target_user_id = int(data.rsplit(":", 1)[-1])
        except ValueError:
            await safe_edit(
                query,
                "❌ Некорректный ID",
                get_admin_keyboard(),
            )
            return

        result = await db.delete_user(target_user_id)

        await safe_edit(
            query,
            "✅ Пользователь удалён\n\n"
            f"Удалено ключей: "
            f"{result.get('deleted_keys', 0)}\n"
            f"Удалено записей истории: "
            f"{result.get('deleted_history', 0)}",
            get_admin_keyboard(),
        )
        return

    if data.startswith("admin:delkey:yes:"):
        token = data.rsplit(":", 1)[-1]

        pending = context.user_data.get(
            "pending_key_deletes",
            {},
        )

        key_text = pending.pop(token, None)

        if not key_text:
            await safe_edit(
                query,
                "❌ Подтверждение устарело",
                get_admin_keyboard(),
            )
            return

        result = await db.delete_key(key_text)

        if result.get("success"):
            text = f"✅ Ключ {key_text} удалён"
        else:
            text = "❌ Ключ не найден"

        await safe_edit(
            query,
            text,
            get_admin_keyboard(),
        )
        return

    if data.startswith("admin:delkey:no:"):
        token = data.rsplit(":", 1)[-1]

        pending = context.user_data.get(
            "pending_key_deletes",
            {},
        )
        pending.pop(token, None)

        await safe_edit(
            query,
            "❌ Удаление ключа отменено",
            get_admin_keyboard(),
        )
        return

    if data == "admin:screens":
        screenshots = await db.get_screenshots()

        if screenshots:
            lines = ["📸 Скриншоты:", ""]

            for screenshot in screenshots[:50]:
                lines.append(
                    f"ID {screenshot.get('id')} — "
                    f"{screenshot.get('name')}"
                )

            text = "\n".join(lines)
        else:
            text = "📭 Скриншотов нет"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Добавить",
                    callback_data="admin:screens:add",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏠 Главное меню",
                    callback_data="menu:start",
                ),
            ],
        ])

        await safe_edit(
            query,
            text,
            keyboard,
        )
        return

    if data == "admin:screens:add":
        context.user_data["input_state"] = {
            "mode": "screenshot_name"
        }

        await safe_edit(
            query,
            "Отправьте название скриншота.\n\n"
            "После этого отправьте изображение.\n"
            "Для отмены: /cancel",
            get_admin_keyboard(),
        )
        return

    if data == "admin:cancel":
        context.user_data.pop("input_state", None)

        await safe_edit(
            query,
            "❌ Операция отменена",
            get_admin_keyboard(),
        )
        return

    await safe_edit(
        query,
        "❌ Неизвестная команда",
        keyboard_for_user(user_id),
    )


# =========================================================
# ОБРАБОТКА ОШИБОК
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error = context.error

    logger.error(
        "Необработанная ошибка",
        exc_info=(
            type(error),
            error,
            error.__traceback__,
        ) if error else None,
    )

    if not isinstance(update, Update):
        return

    try:
        if update.callback_query:
            await update.callback_query.answer(
                "Произошла внутренняя ошибка",
                show_alert=True,
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла внутренняя ошибка. "
                "Попробуйте ещё раз позже."
            )
    except Exception:
        logger.exception(
            "Не удалось отправить сообщение об ошибке"
        )


# =========================================================
# СОЗДАНИЕ ПРИЛОЖЕНИЯ
# =========================================================

def create_application() -> Application:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Пользовательские команды
    application.add_handler(
        CommandHandler("start", start)
    )
    application.add_handler(
        CommandHandler("help", help_command)
    )
    application.add_handler(
        CommandHandler("info", info_command)
    )
    application.add_handler(
        CommandHandler("key", set_key)
    )
    application.add_handler(
        CommandHandler("get", get_link)
    )
    application.add_handler(
        CommandHandler("stat", stats_command)
    )
    application.add_handler(
        CommandHandler("history", history_command)
    )
    application.add_handler(
        CommandHandler("cancel", cancel_command)
    )

    # Административные команды
    application.add_handler(
        CommandHandler("admin_help", admin_help)
    )
    application.add_handler(
        CommandHandler("admin_stats", admin_stats)
    )
    application.add_handler(
        CommandHandler("admin_links", admin_links)
    )
    application.add_handler(
        CommandHandler("admin_users", admin_users)
    )
    application.add_handler(
        CommandHandler(
            "admin_userinfo",
            admin_userinfo,
        )
    )
    application.add_handler(
        CommandHandler("admin_create", admin_create)
    )
    application.add_handler(
        CommandHandler(
            "admin_activate",
            admin_activate,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_deactivate",
            admin_deactivate,
        )
    )
    application.add_handler(
        CommandHandler("admin_refill", admin_refill)
    )
    application.add_handler(
        CommandHandler(
            "admin_deletekey",
            admin_deletekey,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_confirm_delete",
            admin_deletekey,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_confirm_delete_key",
            admin_deletekey,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_deleteuser",
            admin_deleteuser,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_addlinks",
            admin_addlinks,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_delete_unused",
            admin_delete_unused,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_linkstats",
            admin_linkstats,
        )
    )
    application.add_handler(
        CommandHandler("admin_get", admin_get)
    )
    application.add_handler(
        CommandHandler(
            "admin_all_keys",
            admin_all_keys,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_add_screenshot",
            admin_add_screenshot,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_list_screenshots",
            admin_list_screenshots,
        )
    )
    application.add_handler(
        CommandHandler(
            "admin_del_screenshot",
            admin_del_screenshot,
        )
    )

    # Один обработчик всех inline-кнопок
    application.add_handler(
        CallbackQueryHandler(button_callback)
    )

    # Один обработчик административного ввода.
    # Команды исключены, поэтому они не перехватываются.
    input_filter = (
        (filters.TEXT & ~filters.COMMAND)
        | filters.Document.ALL
        | filters.PHOTO
    )

    application.add_handler(
        MessageHandler(
            input_filter,
            handle_admin_input,
        )
    )

    application.add_error_handler(error_handler)

    return application


# =========================================================
# ЗАПУСК
# =========================================================

def main() -> None:
    logger.info("Запуск NFAvpn Telegram-бота")
    logger.info("ADMIN_ID=%s", ADMIN_ID)

    application = create_application()

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
