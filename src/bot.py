# KAI Privacy Kit - "Privacy Sentry" Bot
#
# @authors: Кирило Ревякін (Team Lead / Arch)
#            Олександр Лєбєдєв (Tech Lead)
# @link:     https://github.com/Kirill3224/KAI-Privacy-Kit
# @license:  MIT License (see LICENSE file)
#
# -*- coding: utf-8 -*-
"""
Головний файл бота "Privacy Sentry" (v4.5 - Stable Release)

Виправлення v4.5:
- CRITICAL FIX: Виправлено `KeyError: 'summary_text'`. Тепер `get_checklist_template_data`
  правильно викликає генератор історії.
- UX: Уніфіковано нумерацію кроків у Чек-лісті (Крок X/10).
- UX: Перевірено логіку видалення повідомлень переходу (Upsell).
"""

import logging
import os
import html
from datetime import date
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# Локальні імпорти
import templates
# Ми припускаємо, що pdf_utils.py працює коректно (v3.2 від товариша або v4.2 наш)
from pdf_utils import create_pdf_from_markdown, clear_temp_file

# Налаштування логування
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("telegram.ext.JobQueue").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Завантаження конфігурації ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("!!! Змінна BOT_TOKEN не знайдена в .env файлі !!!")
    exit()

# === Етапи для Conversation Handlers ===

# --- Етапи для "Політики" ---
(
    POLICY_Q_CONTACT,
    POLICY_Q_DATA_COLLECTED,
    POLICY_Q_DATA_STORAGE,
    POLICY_Q_DELETE_MECHANISM,
    POLICY_GENERATE,
) = range(10, 15) 

# --- Етапи для "DPIA" ---
(
    DPIA_Q_TEAM,
    DPIA_Q_GOAL,
    DPIA_Q_DATA_LIST,
    DPIA_Q_MINIMIZATION_START,
    DPIA_Q_MINIMIZATION_REASON,
    DPIA_Q_MINIMIZATION_STATUS,
    DPIA_Q_RETENTION_PERIOD,
    DPIA_Q_RETENTION_MECHANISM,
    DPIA_Q_STORAGE,
    DPIA_Q_RISK,
    DPIA_Q_MITIGATION,
    DPIA_GENERATE,
) = range(20, 32)

# --- Етапи для "Чек-ліста" ---
(
    CHECKLIST_Q_PROJECT_NAME,
    C1_S1_NOTE, C1_S2_STATUS, C1_S2_NOTE, C1_S3_STATUS, C1_S3_NOTE,
    C2_S1_STATUS, C2_S1_NOTE, C2_S2_STATUS, C2_S2_NOTE, C2_S3_STATUS, C2_S3_NOTE,
    C3_S1_STATUS, C3_S1_NOTE, C3_S2_STATUS, C3_S2_NOTE, C3_S3_STATUS, C3_S3_NOTE,
    CHECKLIST_GENERATE,
) = range(40, 59)


# === 1. Клавіатури (Keyboards) ===

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("1️⃣ Крок 1: Оцінка Ризиків (DPIA)", callback_data="start_dpia")],
        [InlineKeyboardButton("2️⃣ Крок 2: Політика Приватності", callback_data="start_policy")],
        [InlineKeyboardButton("3️⃣ Крок 3: Технічний Чек-ліст", callback_data="start_checklist")],
        [
            InlineKeyboardButton("❓ Допомога", callback_data="show_help"),
            InlineKeyboardButton("🔒 Наша Політика", callback_data="show_privacy")
        ],
        [InlineKeyboardButton("🐙 GitHub (Open Source)", url="https://github.com/Kirill3224/KAI-Privacy-Kit")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_post_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Повернутись до головного меню", callback_data="start_menu_post_generation")
    ]])

def get_dpia_upsell_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📄 Створити Політику (Крок 2)", callback_data="start_policy_upsell")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="start_menu_post_generation")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_policy_upsell_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✅ Пройти Чек-ліст (Крок 3)", callback_data="start_checklist_upsell")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="start_menu_post_generation")]
    ]
    return InlineKeyboardMarkup(keyboard)


# === 2. Функції Безпеки ===

def safe_user_input(text: str) -> str:
    if not text: return ""
    return html.escape(text)

def safe_pdf_input(text: str) -> str:
    if not text: return ""
    safe = html.escape(text)
    safe = safe.replace("|", "/") 
    safe = safe.replace("\n", "<br>")
    return safe

def clear_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data:
        context.user_data.clear()

# === ХЕЛПЕРИ ПОВІДОМЛЕНЬ ===

async def delete_main_message(context: ContextTypes.DEFAULT_TYPE, message_id: int = None) -> None:
    msg_id_to_delete = message_id or context.user_data.pop('main_message_id', None)
    chat_id = context._chat_id
    if msg_id_to_delete:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id_to_delete)
        except BadRequest:
            pass

async def edit_main_message(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup = None, new_message: bool = False) -> None:
    message_id = context.user_data.get('main_message_id')
    chat_id = context._chat_id
    
    if new_message and message_id:
        await delete_main_message(context)
        message_id = None

    try:
        if not message_id or new_message:
            sent_message = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            context.user_data['main_message_id'] = sent_message.message_id
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        elif "message to edit not found" in str(e):
             await edit_main_message(context, text, reply_markup, new_message=True)
        else:
            if message_id and not new_message:
                await edit_main_message(context, text, reply_markup, new_message=True)

async def delete_user_text_reply(update: Update) -> None:
    try:
        await update.message.delete()
    except BadRequest:
        pass

# === 3. Базові команди ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_user_data(context)
    query = update.callback_query
    
    text = (
        "👋 <b>Привіт! Я — Privacy Sentry.</b>\n\n"
        "Я допоможу вам зробити ваш студентський проєкт безпечним та законним. "
        "Я не зберігаю ваші дані («stateless»), тому ми будемо працювати крок за кроком.\n\n"
        "👇 <b>Ваша Дорожня Карта (натискайте по черзі):</b>"
    )
    reply_markup = get_main_menu_keyboard()

    if query:
        try:
            await query.answer()
            if query.data in ("start_menu", "start_menu_post_generation"):
                await delete_main_message(context, query.message.message_id)
            
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except BadRequest:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            
    return ConversationHandler.END 

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message: return 
    await update.message.reply_text(templates.BOT_HELP, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def show_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message: return
    await update.message.reply_text(templates.BOT_PRIVACY_POLICY, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def show_help_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="start_menu")]]
    try:
        await query.edit_message_text(templates.BOT_HELP, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest: pass

async def show_privacy_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="start_menu")]]
    try:
        await query.edit_message_text(templates.BOT_PRIVACY_POLICY, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest: pass

class _FakeUpdate:
    def __init__(self, chat_id, bot):
        self.callback_query = None
        self.message = self._Message(chat_id, bot)
    class _Message:
        def __init__(self, chat_id, bot):
            self.chat = self._Chat(chat_id)
            self._bot = bot
        class _Chat:
            def __init__(self, chat_id):
                self.id = chat_id
        async def reply_text(self, text, reply_markup, parse_mode):
            await self._bot.send_message(chat_id=self.chat.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_user_data(context)
    query = update.callback_query
    message = update.message
    cancel_text = "🚫 Дію скасовано. Усі дані з пам'яті видалено."
    
    chat_id = None
    if query:
        await query.answer()
        chat_id = query.message.chat_id
        await delete_main_message(context, query.message.message_id) 
        await context.bot.send_message(chat_id=chat_id, text=cancel_text)
    elif message:
        chat_id = message.chat_id
        await message.reply_text(cancel_text, reply_markup=ReplyKeyboardRemove())
    
    if chat_id:
        await start(_FakeUpdate(chat_id, context.bot), context)
    return ConversationHandler.END

async def _delete_blocker_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    message_id = context.job.data.get('message_id')
    chat_id = context.job.data.get('chat_id')
    if message_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass

async def block_workflow_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer() 
    current_state = context.user_data.get('current_state') 
    
    if current_state is None:
        return await cancel(update, context)

    try:
        keyboard = [[InlineKeyboardButton("❌ Скасувати поточний аудит", callback_data="cancel_from_block")]]
        sent_message = await query.message.reply_text(
            "⚠️ <b>Ви вже заповнюєте інший документ.</b>\n\n"
            "Будь ласка, спочатку завершіть поточний аудит, або натисніть 'Скасувати' нижче.\n"
            "<i>(Це повідомлення зникне через 5 секунд)</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML 
        )
        if context.job_queue:
            context.job_queue.run_once(
                _delete_blocker_message,
                5,
                data={'message_id': sent_message.message_id, 'chat_id': sent_message.chat_id}
            )
    except BadRequest:
        pass
    return current_state

async def cancel_from_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except BadRequest:
        pass
    return await cancel(update, context)

# === 4. POLICY ===

def get_policy_template_data(data: dict) -> dict:
    return {
        'project_name': safe_user_input(data.get('project_name', '...')),
        'contact': safe_user_input(data.get('contact', '...')),
        'data_collected': safe_user_input(data.get('data_collected', '...')),
        'data_storage': safe_user_input(data.get('data_storage', '...')),
        'delete_mechanism': safe_user_input(data.get('delete_mechanism', '...')),
    }

async def start_policy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    clear_user_data(context)
    context.user_data['policy'] = {}
    text = templates.POLICY_Q_PROJECT_NAME
    await edit_main_message(context, text, new_message=True)
    context.user_data['current_state'] = POLICY_Q_CONTACT
    return POLICY_Q_CONTACT

async def start_policy_from_upsell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    # Тут важливо: видаляємо повідомлення з кнопкою Upsell
    await delete_main_message(context, query.message.message_id)
    return await start_policy(update, context)

async def policy_q_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['policy']['project_name'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.POLICY_Q_CONTACT.format(**get_policy_template_data(context.user_data['policy']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = POLICY_Q_DATA_COLLECTED
    return POLICY_Q_DATA_COLLECTED

async def policy_q_data_collected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['policy']['contact'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.POLICY_Q_DATA_COLLECTED.format(**get_policy_template_data(context.user_data['policy']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = POLICY_Q_DATA_STORAGE
    return POLICY_Q_DATA_STORAGE

async def policy_q_data_storage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['policy']['data_collected'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.POLICY_Q_DATA_STORAGE.format(**get_policy_template_data(context.user_data['policy']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = POLICY_Q_DELETE_MECHANISM
    return POLICY_Q_DELETE_MECHANISM

async def policy_q_delete_mechanism(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['policy']['data_storage'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.POLICY_Q_DELETE_MECHANISM.format(**get_policy_template_data(context.user_data['policy']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = POLICY_GENERATE
    return POLICY_GENERATE

async def policy_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['policy']['delete_mechanism'] = update.message.text
    user_id = update.effective_user.id
    await delete_user_text_reply(update)
    await delete_main_message(context)
    generating_msg = await update.message.reply_text("⏳ Генерую ваш PDF...")

    data_raw = context.user_data['policy']
    data_dict = {
        'project_name': safe_pdf_input(data_raw.get('project_name', '[Назва]')),
        'contact': safe_pdf_input(data_raw.get('contact', '[Контакт]')),
        'data_collected': safe_pdf_input(data_raw.get('data_collected', '[Дані]')),
        'data_storage': safe_pdf_input(data_raw.get('data_storage', '[Зберігання]')),
        'delete_mechanism': safe_pdf_input(data_raw.get('delete_mechanism', '[Видалення]')),
        'date': date.today().strftime("%d.%m.%Y"),
    }
    clear_user_data(context)

    try:
        filled_markdown = templates.POLICY_TEMPLATE.format(**data_dict)
        pdf_path = create_pdf_from_markdown(filled_markdown, is_html=False, output_filename=f"policy_{user_id}.pdf")
        await context.bot.send_document(chat_id=update.message.chat_id, document=open(pdf_path, 'rb'))
        
        upsell_msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=templates.POST_POLICY_UPSELL,
            reply_markup=get_policy_upsell_keyboard(),
            parse_mode=ParseMode.HTML
        )
        context.user_data['main_message_id'] = upsell_msg.message_id
        clear_temp_file(pdf_path)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Сталася помилка при генерації.")
        await start(_FakeUpdate(update.message.chat.id, context.bot), context)
    finally:
        try: await generating_msg.delete()
        except: pass
        return ConversationHandler.END

# === 5. DPIA ===

def get_dpia_template_data(data: dict) -> dict:
    minimization_text = ""
    minimization_data = data.get('minimization_data', [])
    if data.get('data_list') and not minimization_data:
        for i, item in enumerate(data.get('data_list', [])):
             minimization_text += f"\n<b>{i+1}. {safe_user_input(item)}:</b> [Очікує...] "
    else:
        for i, item_data in enumerate(minimization_data):
            item = safe_user_input(item_data['item'])
            reason = safe_user_input(item_data['reason'])
            if item_data['needed']:
                minimization_text += f"\n<b>{i+1}. {item}:</b> ✅ <b>Так</b> (Навіщо: <code>{reason}</code>)"
            else:
                minimization_text += f"\n<b>{i+1}. {item}:</b> ❌ <b>Ні</b> (<code>{reason}</code>)"

    raw_list = data.get('data_list', [])
    formatted_list = "\n".join([f"• <code>{safe_user_input(i)}</code>" for i in raw_list])

    return {
        'project_name': safe_user_input(data.get('project_name', '...')),
        'team': safe_user_input(data.get('team', '...')),
        'goal': safe_user_input(data.get('goal', '...')),
        'data_list': formatted_list, 
        'minimization_summary': minimization_text.strip(),
        'retention_period': safe_user_input(data.get('retention_period', '...')),
        'retention_mechanism': safe_user_input(data.get('retention_mechanism', '...')),
        'storage': safe_user_input(data.get('storage', '...')),
        'risk': safe_user_input(data.get('risk', '...')),
        'mitigation': safe_user_input(data.get('mitigation', '...')),
    }

async def start_dpia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    clear_user_data(context)
    context.user_data['dpia'] = {'minimization_data': [], 'data_list': [], 'current_data_index': 0}
    text = templates.DPIA_Q_PROJECT_NAME
    await edit_main_message(context, text, new_message=True)
    context.user_data['current_state'] = DPIA_Q_TEAM
    return DPIA_Q_TEAM

async def dpia_q_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['project_name'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_TEAM.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_GOAL
    return DPIA_Q_GOAL

async def dpia_q_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['team'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_GOAL.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_DATA_LIST
    return DPIA_Q_DATA_LIST

async def dpia_q_data_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['goal'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_DATA_LIST.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_MINIMIZATION_START
    return DPIA_Q_MINIMIZATION_START

async def dpia_q_minimization_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data_list = [item.strip() for item in update.message.text.split('\n') if item.strip()]
    await delete_user_text_reply(update)
    if not data_list:
        text = templates.DPIA_Q_DATA_LIST_ERROR
        await edit_main_message(context, text)
        context.user_data['current_state'] = DPIA_Q_MINIMIZATION_START
        return DPIA_Q_MINIMIZATION_START
    context.user_data['dpia']['data_list'] = data_list
    context.user_data['dpia']['current_data_index'] = 0
    context.user_data['dpia']['minimization_data'] = []
    return await dpia_ask_minimization_status(context)

async def dpia_ask_minimization_status(context: ContextTypes.DEFAULT_TYPE) -> int:
    index = context.user_data['dpia']['current_data_index']
    data_list = context.user_data['dpia']['data_list']
    if index >= len(data_list):
        return await dpia_minimization_finished(context)
    
    current_data_item = data_list[index]
    context.user_data['dpia']['current_data_item'] = current_data_item
    
    keyboard = [[InlineKeyboardButton("✅ Так", callback_data="min_yes"), InlineKeyboardButton("❌ Ні", callback_data="min_no")]]
    template_data = get_dpia_template_data(context.user_data['dpia'])
    safe_item = f"<code>{safe_user_input(current_data_item)}</code>"
    
    text = templates.DPIA_Q_MINIMIZATION_ASK.format(
        **template_data,
        count=f"{index + 1}/{len(data_list)}",
        item=safe_item
    )
    await edit_main_message(context, text, InlineKeyboardMarkup(keyboard))
    context.user_data['current_state'] = DPIA_Q_MINIMIZATION_REASON
    return DPIA_Q_MINIMIZATION_REASON

async def dpia_q_minimization_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    current_data_item = context.user_data['dpia'].get('current_data_item', '...')
    safe_item = f"<code>{safe_user_input(current_data_item)}</code>"
    
    if query.data == "min_yes":
        context.user_data['dpia']['minimization_data'].append({"item": current_data_item, "needed": True, "reason": ""})
        template_data = get_dpia_template_data(context.user_data['dpia'])
        text = templates.DPIA_Q_MINIMIZATION_REASON.format(**template_data, item=safe_item)
        await edit_main_message(context, text)
        context.user_data['current_state'] = DPIA_Q_MINIMIZATION_STATUS
        return DPIA_Q_MINIMIZATION_STATUS
    elif query.data == "min_no":
        context.user_data['dpia']['minimization_data'].append({"item": current_data_item, "needed": False, "reason": "Відмовлено"})
        context.user_data['dpia']['current_data_index'] += 1
        return await dpia_ask_minimization_status(context)

async def dpia_q_minimization_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reason = update.message.text
    await delete_user_text_reply(update)
    if context.user_data['dpia']['minimization_data']:
        context.user_data['dpia']['minimization_data'][-1]['reason'] = reason
    context.user_data['dpia']['current_data_index'] += 1
    return await dpia_ask_minimization_status(context)

async def dpia_minimization_finished(context: ContextTypes.DEFAULT_TYPE) -> int:
    text = templates.DPIA_Q_RETENTION_PERIOD.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_RETENTION_MECHANISM
    return DPIA_Q_RETENTION_MECHANISM

async def dpia_q_retention_mechanism(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['retention_period'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_RETENTION_MECHANISM.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_STORAGE
    return DPIA_Q_STORAGE

async def dpia_q_storage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['retention_mechanism'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_STORAGE.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_RISK
    return DPIA_Q_RISK

async def dpia_q_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['storage'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_RISK.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_Q_MITIGATION
    return DPIA_Q_MITIGATION

async def dpia_q_mitigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['risk'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.DPIA_Q_MITIGATION.format(**get_dpia_template_data(context.user_data['dpia']))
    await edit_main_message(context, text)
    context.user_data['current_state'] = DPIA_GENERATE
    return DPIA_GENERATE

async def dpia_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['dpia']['mitigation'] = update.message.text
    user_id = update.effective_user.id
    await delete_user_text_reply(update)
    await delete_main_message(context)
    generating_msg = await update.message.reply_text("⏳ Генерую ваш PDF...")

    data_raw = context.user_data['dpia']
    table_rows = []
    table_rows.append(f"| Назва проєкту: | {safe_pdf_input(data_raw.get('project_name'))} |")
    table_rows.append(f"| Керівник/Розробник: | {safe_pdf_input(data_raw.get('team'))} |")
    table_rows.append(f"| Мета: | {safe_pdf_input(data_raw.get('goal'))} |")
    
    minimization_data = data_raw.get('minimization_data', [])
    if not minimization_data:
        table_rows.append("| Дані: | [Не вказано] |")
    else:
        for i, item in enumerate(minimization_data):
            data_name = f"Дані (пункт {i+1}):"
            item_name = safe_pdf_input(item['item'])
            item_reason = safe_pdf_input(item['reason'])
            if item['needed']:
                data_value = f"{item_name} (✅ **Навіщо:** {item_reason})"
            else:
                data_value = f"~~{item_name}~~ (❌ **Відмовлено**)"
            table_rows.append(f"| {data_name} | {data_value} |")

    table_rows.append(f"| Строк Зберігання: | {safe_pdf_input(data_raw.get('retention_period'))} |")
    table_rows.append(f"| Механізм Видалення: | {safe_pdf_input(data_raw.get('retention_mechanism'))} |")
    table_rows.append(f"| Місце Зберігання: | {safe_pdf_input(data_raw.get('storage'))} |")
    table_rows.append(f"| Головний Ризик: | {safe_pdf_input(data_raw.get('risk'))} |")
    table_rows.append(f"| Мінімізація Ризику: | {safe_pdf_input(data_raw.get('mitigation'))} |")

    table_header = "| Питання | Відповідь |\n| :--- | :--- |\n"
    dpia_table_string = table_header + "\n".join(table_rows)
    
    data_dict = {
        'project_name': safe_pdf_input(data_raw.get('project_name')),
        'date': date.today().strftime("%d.%m.%Y"),
        'dpia_table': dpia_table_string
    }
    clear_user_data(context)

    try:
        filled_markdown = templates.DPIA_TEMPLATE.format(**data_dict)
        pdf_path = create_pdf_from_markdown(filled_markdown, is_html=False, output_filename=f"dpia_{user_id}.pdf")
        await context.bot.send_document(chat_id=update.message.chat_id, document=open(pdf_path, 'rb'))
        
        upsell_msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=templates.POST_DPIA_UPSELL, 
            reply_markup=get_dpia_upsell_keyboard(),
            parse_mode=ParseMode.HTML
        )
        context.user_data['main_message_id'] = upsell_msg.message_id
        clear_temp_file(pdf_path)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Сталася помилка при генерації.")
        await start(_FakeUpdate(update.message.chat.id, context.bot), context)
    finally:
        try: await generating_msg.delete()
        except: pass
        return ConversationHandler.END

# === 6. Checklist (v4.5 - FIXED) ===

def get_status_text_html(status: str) -> str:
    if status == "yes": return "✅ <b>Виконано</b>"
    elif status == "no": return "❌ <b>Не виконано</b>"
    return "" 

def get_note_text_html(note: str) -> str:
    if not note: return ""
    if note == "*Пропущено*": return "Нотатка: <i>Пропущено</i>"
    return f"Нотатка: <code>{safe_user_input(note)}</code>"

# (v4.5 FIX) Ця функція тепер використовується у get_checklist_template_data
def get_checklist_summary_text(cl_data: dict) -> str:
    summary = f"✅ <b>Назва Проєкту:</b> <code>{safe_user_input(cl_data.get('project_name', '...'))}</code>\n\n"
    
    items = [
        ('c1_s1', "1.1. 2FA"),
        ('c1_s2', "1.2. 'Найменші привілеї'"),
        ('c1_s3', "1.3. БЕЗ ПУБЛІЧНИХ ПОСИЛАНЬ"),
        ('c2_s1', "2.1. Публічна Політика"),
        ('c2_s2', "2.2. Механізм Видалення"),
        ('c2_s3', "2.3. Контакт для скарг"),
        ('c3_s1', "3.1. Безпека Токенів"),
        ('c3_s2', "3.2. Планування Строків"),
        ('c3_s3', "3.3. Шифрування"),
    ]
    
    last_category = ""
    for key, name in items:
        status_key = f"{key}_status"
        note_key = f"{key}_note"
        status_val = cl_data.get(status_key)
        note_val = cl_data.get(note_key)
        
        if status_val:
            category_num = key[1]
            if category_num != last_category:
                if last_category != "": summary += "\n"
                cat_name = "Контроль Доступу"
                if category_num == '2': cat_name = "Права Користувачів"
                elif category_num == '3': cat_name = "Технічна Гігієна"
                summary += f"<b>Категорія {category_num} ({cat_name}):</b>\n"
                last_category = category_num

            summary += f"<b>{name}:</b> {get_status_text_html(status_val)}\n"
            if note_val:
                summary += f"{get_note_text_html(note_val)}\n"
                
    return summary.strip()

def get_checklist_template_data(cl_data: dict) -> dict:
    # (v4.5 FIX) Тепер повертає ПОВНИЙ набір даних, включаючи summary_text
    return {
        'project_name': safe_user_input(cl_data.get('project_name', '...')),
        'summary_text': get_checklist_summary_text(cl_data),
        'c1_s1_status': get_status_text_html(cl_data.get('c1_s1_status', '')),
        'c1_s1_note': get_note_text_html(cl_data.get('c1_s1_note', '')),
        'c1_s2_status': get_status_text_html(cl_data.get('c1_s2_status', '')),
        'c1_s2_note': get_note_text_html(cl_data.get('c1_s2_note', '')),
        'c1_s3_status': get_status_text_html(cl_data.get('c1_s3_status', '')),
        'c1_s3_note': get_note_text_html(cl_data.get('c1_s3_note', '')),
        'c2_s1_status': get_status_text_html(cl_data.get('c2_s1_status', '')),
        'c2_s1_note': get_note_text_html(cl_data.get('c2_s1_note', '')),
        'c2_s2_status': get_status_text_html(cl_data.get('c2_s2_status', '')),
        'c2_s2_note': get_note_text_html(cl_data.get('c2_s2_note', '')),
        'c2_s3_status': get_status_text_html(cl_data.get('c2_s3_status', '')),
        'c2_s3_note': get_note_text_html(cl_data.get('c2_s3_note', '')),
        'c3_s1_status': get_status_text_html(cl_data.get('c3_s1_status', '')),
        'c3_s1_note': get_note_text_html(cl_data.get('c3_s1_note', '')),
        'c3_s2_status': get_status_text_html(cl_data.get('c3_s2_status', '')),
        'c3_s2_note': get_note_text_html(cl_data.get('c3_s2_note', '')),
        'c3_s3_status': get_status_text_html(cl_data.get('c3_s3_status', '')),
        'c3_s3_note': get_note_text_html(cl_data.get('c3_s3_note', '')),
    }

def get_checklist_status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Виконано", callback_data="cl_yes"),
        InlineKeyboardButton("❌ Не виконано", callback_data="cl_no"),
    ]])

def get_skip_note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Пропустити нотатку", callback_data="cl_skip_note"),
    ]])

async def start_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    clear_user_data(context)
    context.user_data['cl'] = {} 
    text = templates.CHECKLIST_Q_PROJECT_NAME
    await edit_main_message(context, text, new_message=True)
    context.user_data['current_state'] = CHECKLIST_Q_PROJECT_NAME
    return CHECKLIST_Q_PROJECT_NAME

async def start_checklist_from_upsell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await delete_main_message(context, query.message.message_id) 
    clear_user_data(context)
    context.user_data['cl'] = {} 
    text = templates.CHECKLIST_Q_PROJECT_NAME
    await edit_main_message(context, text, new_message=True)
    context.user_data['current_state'] = CHECKLIST_Q_PROJECT_NAME
    return CHECKLIST_Q_PROJECT_NAME

async def checklist_q_project_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['cl']['project_name'] = update.message.text
    await delete_user_text_reply(update)
    text = templates.CHECKLIST_C1_S1_STATUS.format(**get_checklist_template_data(context.user_data['cl']))
    await edit_main_message(context, text, get_checklist_status_keyboard())
    context.user_data['current_state'] = C1_S1_NOTE
    return C1_S1_NOTE

# Helper to reduce boilerplate
async def _handle_status(update, context, status_key, next_tmpl, next_state):
    query = update.callback_query
    await query.answer()
    context.user_data['cl'][status_key] = "yes" if query.data == "cl_yes" else "no"
    
    td = get_checklist_template_data(context.user_data['cl'])
    text = next_tmpl.format(**td)
    await edit_main_message(context, text, get_skip_note_keyboard())
    context.user_data['current_state'] = next_state
    return next_state

async def _handle_note(update, context, note_key, next_tmpl, next_state, is_skip=False):
    if is_skip:
        query = update.callback_query
        await query.answer()
        context.user_data['cl'][note_key] = "*Пропущено*"
    else:
        context.user_data['cl'][note_key] = update.message.text
        await delete_user_text_reply(update)
    
    td = get_checklist_template_data(context.user_data['cl'])
    text = next_tmpl.format(**td)
    await edit_main_message(context, text, get_checklist_status_keyboard())
    context.user_data['current_state'] = next_state
    return next_state

# --- Category 1 ---
async def checklist_c1_s1_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c1_s1_status', templates.CHECKLIST_C1_S1_NOTE, C1_S2_STATUS)

async def checklist_c1_s2_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s1_note', templates.CHECKLIST_C1_S2_STATUS, C1_S2_NOTE)
async def checklist_c1_s2_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s1_note', templates.CHECKLIST_C1_S2_STATUS, C1_S2_NOTE, True)

async def checklist_c1_s2_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c1_s2_status', templates.CHECKLIST_C1_S2_NOTE, C1_S3_STATUS)

async def checklist_c1_s3_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s2_note', templates.CHECKLIST_C1_S3_STATUS, C1_S3_NOTE)
async def checklist_c1_s3_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s2_note', templates.CHECKLIST_C1_S3_STATUS, C1_S3_NOTE, True)

async def checklist_c1_s3_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c1_s3_status', templates.CHECKLIST_C1_S3_NOTE, C2_S1_STATUS)

# --- Category 2 ---
async def checklist_c2_s1_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s3_note', templates.CHECKLIST_C2_S1_STATUS, C2_S1_NOTE)
async def checklist_c2_s1_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c1_s3_note', templates.CHECKLIST_C2_S1_STATUS, C2_S1_NOTE, True)

async def checklist_c2_s1_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c2_s1_status', templates.CHECKLIST_C2_S1_NOTE, C2_S2_STATUS)

async def checklist_c2_s2_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s1_note', templates.CHECKLIST_C2_S2_STATUS, C2_S2_NOTE)
async def checklist_c2_s2_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s1_note', templates.CHECKLIST_C2_S2_STATUS, C2_S2_NOTE, True)

async def checklist_c2_s2_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c2_s2_status', templates.CHECKLIST_C2_S2_NOTE, C2_S3_STATUS)

async def checklist_c2_s3_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s2_note', templates.CHECKLIST_C2_S3_STATUS, C2_S3_NOTE)
async def checklist_c2_s3_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s2_note', templates.CHECKLIST_C2_S3_STATUS, C2_S3_NOTE, True)

async def checklist_c2_s3_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c2_s3_status', templates.CHECKLIST_C2_S3_NOTE, C3_S1_STATUS)

# --- Category 3 ---
async def checklist_c3_s1_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s3_note', templates.CHECKLIST_C3_S1_STATUS, C3_S1_NOTE)
async def checklist_c3_s1_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c2_s3_note', templates.CHECKLIST_C3_S1_STATUS, C3_S1_NOTE, True)

async def checklist_c3_s1_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c3_s1_status', templates.CHECKLIST_C3_S1_NOTE, C3_S2_STATUS)

async def checklist_c3_s2_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c3_s1_note', templates.CHECKLIST_C3_S2_STATUS, C3_S2_NOTE)
async def checklist_c3_s2_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c3_s1_note', templates.CHECKLIST_C3_S2_STATUS, C3_S2_NOTE, True)

async def checklist_c3_s2_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c3_s2_status', templates.CHECKLIST_C3_S2_NOTE, C3_S3_STATUS)

async def checklist_c3_s3_status_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c3_s2_note', templates.CHECKLIST_C3_S3_STATUS, C3_S3_NOTE)
async def checklist_c3_s3_status_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_note(update, context, 'c3_s2_note', templates.CHECKLIST_C3_S3_STATUS, C3_S3_NOTE, True)

async def checklist_c3_s3_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_status(update, context, 'c3_s3_status', templates.CHECKLIST_C3_S3_NOTE, CHECKLIST_GENERATE)

async def checklist_generate_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['cl']['c3_s3_note'] = update.message.text
    await delete_user_text_reply(update)
    return await checklist_generate(update, context)

async def checklist_generate_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['cl']['c3_s3_note'] = "*Пропущено*"
    return await checklist_generate(update, context)

async def checklist_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = context._user_id
    await delete_main_message(context)
    chat_id = update.message.chat_id if update.message else update.callback_query.message.chat_id
    generating_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Генерую ваш PDF...")

    data = context.user_data['cl']
    def get_status_pdf(key): return "Виконано" if data.get(key)=="yes" else "Не виконано"
    def get_note_pdf(key):
        val = data.get(key, "*Не заповнено*")
        if val == "*Пропущено*": return "Пропущено"
        return safe_pdf_input(val)

    rows = []
    rows.append(f"| 1.1. 2FA | {get_status_pdf('c1_s1_status')} | {get_note_pdf('c1_s1_note')} |")
    rows.append(f"| 1.2. Привілеї | {get_status_pdf('c1_s2_status')} | {get_note_pdf('c1_s2_note')} |")
    rows.append(f"| 1.3. Публічні посилання | {get_status_pdf('c1_s3_status')} | {get_note_pdf('c1_s3_note')} |")
    rows.append(f"| 2.1. Політика | {get_status_pdf('c2_s1_status')} | {get_note_pdf('c2_s1_note')} |")
    rows.append(f"| 2.2. Видалення | {get_status_pdf('c2_s2_status')} | {get_note_pdf('c2_s2_note')} |")
    rows.append(f"| 2.3. Контакт | {get_status_pdf('c2_s3_status')} | {get_note_pdf('c2_s3_note')} |")
    rows.append(f"| 3.1. Токени | {get_status_pdf('c3_s1_status')} | {get_note_pdf('c3_s1_note')} |")
    rows.append(f"| 3.2. Retention | {get_status_pdf('c3_s2_status')} | {get_note_pdf('c3_s2_note')} |")
    rows.append(f"| 3.3. Шифрування | {get_status_pdf('c3_s3_status')} | {get_note_pdf('c3_s3_note')} |")
    
    header = "| Пункт | Статус | Нотатки |\n| :--- | :--- | :--- |\n"
    c1 = "### Категорія 1: Контроль Доступу\n\n" + header + "\n".join(rows[0:3])
    c2 = "\n\n### Категорія 2: Права Користувачів\n\n" + header + "\n".join(rows[3:6])
    c3 = "\n\n### Категорія 3: Технічна Гігієна\n\n" + header + "\n".join(rows[6:9])
    content = c1 + c2 + c3
    
    data_dict = {
        'project_name': safe_pdf_input(data.get('project_name', '...')),
        'date': date.today().strftime("%d.%m.%Y"),
        'checklist_content': content 
    }
    clear_user_data(context)

    try:
        filled_md = templates.CHECKLIST_TEMPLATE_PDF.format(**data_dict)
        pdf_path = create_pdf_from_markdown(filled_md, False, f"checklist_{user_id}.pdf")
        await context.bot.send_document(chat_id=chat_id, document=open(pdf_path, 'rb'))
        
        upsell_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=templates.POST_CHECKLIST_SUCCESS,
            reply_markup=get_post_action_keyboard(),
            parse_mode=ParseMode.HTML
        )
        context.user_data['main_message_id'] = upsell_msg.message_id

        clear_temp_file(pdf_path)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Сталася помилка.")
        await start(_FakeUpdate(chat_id, context.bot), context)
    
    try: await generating_msg.delete()
    except: pass
    return ConversationHandler.END

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    main_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_policy, pattern="^start_policy$"),
            CallbackQueryHandler(start_dpia, pattern="^start_dpia$"),
            CallbackQueryHandler(start_checklist, pattern="^start_checklist$"),
            CallbackQueryHandler(start_checklist_from_upsell, pattern="^start_checklist_upsell$"),
            CallbackQueryHandler(start_policy_from_upsell, pattern="^start_policy_upsell$")
        ],
        states={
            # Policy
            POLICY_Q_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, policy_q_contact)],
            POLICY_Q_DATA_COLLECTED: [MessageHandler(filters.TEXT & ~filters.COMMAND, policy_q_data_collected)],
            POLICY_Q_DATA_STORAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, policy_q_data_storage)],
            POLICY_Q_DELETE_MECHANISM: [MessageHandler(filters.TEXT & ~filters.COMMAND, policy_q_delete_mechanism)],
            POLICY_GENERATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, policy_generate)],

            # DPIA
            DPIA_Q_TEAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_team)],
            DPIA_Q_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_goal)],
            DPIA_Q_DATA_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_data_list)],
            DPIA_Q_MINIMIZATION_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_minimization_start)],
            DPIA_Q_MINIMIZATION_REASON: [CallbackQueryHandler(dpia_q_minimization_reason, pattern="^min_(yes|no)$")],
            DPIA_Q_MINIMIZATION_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_minimization_status)],
            DPIA_Q_RETENTION_MECHANISM: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_retention_mechanism)],
            DPIA_Q_STORAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_storage)],
            DPIA_Q_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_risk)],
            DPIA_Q_MITIGATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_q_mitigation)],
            DPIA_GENERATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dpia_generate)],

            # Checklist
            CHECKLIST_Q_PROJECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_q_project_name)],
            C1_S1_NOTE: [CallbackQueryHandler(checklist_c1_s1_note, pattern="^cl_(yes|no)$")],
            C1_S2_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c1_s2_status_from_text),
                CallbackQueryHandler(checklist_c1_s2_status_from_skip, pattern="^cl_skip_note$")
            ],
            C1_S2_NOTE: [CallbackQueryHandler(checklist_c1_s2_note, pattern="^cl_(yes|no)$")],
            C1_S3_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c1_s3_status_from_text),
                CallbackQueryHandler(checklist_c1_s3_status_from_skip, pattern="^cl_skip_note$")
            ],
            C1_S3_NOTE: [CallbackQueryHandler(checklist_c1_s3_note, pattern="^cl_(yes|no)$")],
            C2_S1_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c2_s1_status_from_text),
                CallbackQueryHandler(checklist_c2_s1_status_from_skip, pattern="^cl_skip_note$")
            ],
            C2_S1_NOTE: [CallbackQueryHandler(checklist_c2_s1_note, pattern="^cl_(yes|no)$")],
            C2_S2_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c2_s2_status_from_text),
                CallbackQueryHandler(checklist_c2_s2_status_from_skip, pattern="^cl_skip_note$")
            ],
            C2_S2_NOTE: [CallbackQueryHandler(checklist_c2_s2_note, pattern="^cl_(yes|no)$")],
            C2_S3_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c2_s3_status_from_text),
                CallbackQueryHandler(checklist_c2_s3_status_from_skip, pattern="^cl_skip_note$")
            ],
            C2_S3_NOTE: [CallbackQueryHandler(checklist_c2_s3_note, pattern="^cl_(yes|no)$")],
            C3_S1_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c3_s1_status_from_text),
                CallbackQueryHandler(checklist_c3_s1_status_from_skip, pattern="^cl_skip_note$")
            ],
            C3_S1_NOTE: [CallbackQueryHandler(checklist_c3_s1_note, pattern="^cl_(yes|no)$")],
            C3_S2_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c3_s2_status_from_text),
                CallbackQueryHandler(checklist_c3_s2_status_from_skip, pattern="^cl_skip_note$")
            ],
            C3_S2_NOTE: [CallbackQueryHandler(checklist_c3_s2_note, pattern="^cl_(yes|no)$")],
            C3_S3_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_c3_s3_status_from_text),
                CallbackQueryHandler(checklist_c3_s3_status_from_skip, pattern="^cl_skip_note$")
            ],
            C3_S3_NOTE: [CallbackQueryHandler(checklist_c3_s3_note, pattern="^cl_(yes|no)$")],
            CHECKLIST_GENERATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, checklist_generate_from_text),
                CallbackQueryHandler(checklist_generate_from_skip, pattern="^cl_skip_note$")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(block_workflow_switch, pattern="^start_policy$|^start_dpia$|^start_checklist$"),
            CallbackQueryHandler(cancel_from_block, pattern="^cancel_from_block$"),
            CommandHandler("cancel", cancel)
        ]
    )
    
    application.add_handler(main_conv)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(start, pattern="^start_menu$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^start_menu_post_generation$"))
    application.add_handler(CommandHandler("privacy", show_privacy))
    application.add_handler(CallbackQueryHandler(show_privacy_inline, pattern="^show_privacy$"))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CallbackQueryHandler(show_help_inline, pattern="^show_help$"))
    application.add_handler(CommandHandler("cancel", cancel))

    logger.info("Бот запускається...")
    application.run_polling()

if __name__ == "__main__":
    main()