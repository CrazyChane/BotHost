
3️⃣ **Нажмите Enter** — ключ активирован!

---

**📞 Контакты:**

👤 Администратор: @user123311a
📱 Бот: @NFAvpn_bot

---

🤝 **Благодарим за использование NFAvpn!**"""
    )

# ========== АДМИН-КОМАНДЫ ==========
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
        "/admin_get <количество> - получить N ссылок без ключа\n"
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

async def admin_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админская команда: получить N ссылок без ключа"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Укажите количество ссылок:\n"
            "/admin_get <количество>\n\n"
            "Пример: /admin_get 5"
        )
        return
    
    try:
        count = int(args[0])
        if count <= 0:
            await update.message.reply_text("❌ Количество должно быть больше 0")
            return
        if count > 50:
            await update.message.reply_text("❌ Максимум 50 ссылок за раз")
            return
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: /admin_get 5")
        return
    
    try:
        all_links = load_links()
        if not all_links:
            await update.message.reply_text("❌ Нет доступных ссылок в пуле")
            return
        
        if count > len(all_links):
            count = len(all_links)
            await update.message.reply_text(f"⚠️ В пуле только {count} ссылок, выдаю все")
        
        chosen_links = random.sample(all_links, count)
        
        deleted_count = 0
        for link in chosen_links:
            try:
                supabase.table('links').delete().eq('url', link).execute()
                deleted_count += 1
            except Exception as e:
                print(f"❌ Ошибка удаления {link}: {e}")
        
        remaining = len(load_links())
        links_text = "\n".join([f"{i+1}. {link}" for i, link in enumerate(chosen_links)])
        
        await update.message.reply_text(
            f"📎 Получено {deleted_count} ссылок:\n\n"
            f"{links_text}\n\n"
            f"📊 Осталось ссылок в пуле: {remaining}"
        )
        
        print(f"✅ Админ выдал {deleted_count} ссылок")
        
    except Exception as e:
        print(f"❌ admin_get: {e}")
        await update.message.reply_text("❌ Произошла ошибка")

# ========== СОЗДАНИЕ ПРИЛОЖЕНИЯ БОТА ==========
bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("help", help_command))
bot_app.add_handler(CommandHandler("key", set_key))
bot_app.add_handler(CommandHandler("get", get_link))
bot_app.add_handler(CommandHandler("stat", stats))
bot_app.add_handler(CommandHandler("history", history))
bot_app.add_handler(CommandHandler("info", info_command))
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
bot_app.add_handler(CommandHandler("admin_get", admin_get))
bot_app.add_handler(CallbackQueryHandler(deleteuser_callback, pattern="^(confirm_deluser_|cancel_deluser)"))
bot_app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_links_input))
bot_app.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))

# ========== ЗАПУСК ==========
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
    
    print("🚀 Запуск бота в режиме Long Polling...")
    bot_app.run_polling()
