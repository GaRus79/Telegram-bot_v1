import telebot
from telebot import types
import sqlite3
import json
import time
import logging
import threading
import os
import signal
import sys
from flask import Flask
 
# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
 
# Получаем токен из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN', '7834676136:AAECptx_K3pZTMcarNPUHbKzCM5YZB3FKBU')
TARGET_CHANNEL = os.getenv('TARGET_CHANNEL', '-1003134337601')
 
bot = telebot.TeleBot(BOT_TOKEN)
 
# Словарь с темами
TOPICS = {
    "💬 ЧАТ": 1,
    "⚡ Сетапы": 38, 
    "😂 Юмор": 21,
    "🔧 Разные полезности": 9,
    "🎫 Ссылки, скидки/промокоды": 7,
    "📰 Полезные новости": 3,
    "📝 Без темы": None
}
 
# Глобальные хранилища
user_posts = {}
user_editor_message_ids = {}
media_groups_cache = {}
processing_timers = {}
 
def init_db():
    conn = sqlite3.connect('/tmp/posts.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_posts
                 (user_id INTEGER PRIMARY KEY, post_data TEXT, topic_id INTEGER)''')
    conn.commit()
    conn.close()
 
init_db()
 
def save_user_post(user_id, post_data, topic_id=None):
    try:
        conn = sqlite3.connect('/tmp/posts.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO user_posts (user_id, post_data, topic_id) 
                     VALUES (?, ?, ?)''', (user_id, json.dumps(post_data), topic_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
 
def get_user_post(user_id):
    try:
        conn = sqlite3.connect('/tmp/posts.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''SELECT post_data, topic_id FROM user_posts WHERE user_id = ?''', (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            return json.loads(result[0]), result[1]
    except Exception as e:
        logger.error(f"Ошибка получения: {e}")
    return None, None
 
def delete_user_post(user_id):
    try:
        conn = sqlite3.connect('/tmp/posts.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''DELETE FROM user_posts WHERE user_id = ?''', (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
 
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(
        message.chat.id,
        "🤖 Бот для создания постов\n\n"
        "📥 Просто перешлите сообщение из любого чата или напишите текст прямо здесь!"
    )
 
@bot.message_handler(commands=['test_topics'])
def test_topics_command(message):
    """Команда для тестирования топиков"""
    user_id = message.from_user.id
    bot.send_message(user_id, "🧪 Тестируем доступные топики...")
    
    for topic_name, topic_id in TOPICS.items():
        if topic_id is not None:
            try:
                send_params = {}
                if topic_id:
                    send_params['message_thread_id'] = topic_id
                
                test_msg = bot.send_message(
                    TARGET_CHANNEL, 
                    f"🧪 Тест темы: {topic_name} (ID: {topic_id})",
                    **send_params
                )
                bot.send_message(user_id, f"✅ Топик '{topic_name}' (ID: {topic_id}) - РАБОТАЕТ")
                try:
                    bot.delete_message(TARGET_CHANNEL, test_msg.message_id)
                except:
                    pass
                    
            except Exception as e:
                bot.send_message(user_id, f"❌ Топик '{topic_name}' (ID: {topic_id}) - ОШИБКА: {e}")
 
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'audio'])
def handle_message(message):
    if message.text and message.text.startswith('/'):
        return
    
    user_id = message.from_user.id
    
    if hasattr(message, 'media_group_id') and message.media_group_id:
        group_id = f"{user_id}_{message.media_group_id}"
    else:
        group_id = f"{user_id}_{int(time.time())}"
 
    logger.info(f"📥 Получено сообщение для группы {group_id}")
 
    if group_id not in media_groups_cache:
        media_groups_cache[group_id] = {
            'user_id': user_id,
            'text': '',
            'media': [],
            'last_update': time.time(),
            'processed': False
        }
        logger.info(f"🆕 Создана новая группа {group_id}")
 
    if message.text:
        media_groups_cache[group_id]['text'] = message.text
        logger.info(f"📝 Добавлен текст: {len(message.text)} символов")
    elif message.photo:
        media_groups_cache[group_id]['media'].append({
            'type': 'photo',
            'file_id': message.photo[-1].file_id,
            'selected': True
        })
        logger.info(f"🖼 Добавлено фото. Всего в группе: {len(media_groups_cache[group_id]['media'])}")
    elif message.video:
        media_groups_cache[group_id]['media'].append({
            'type': 'video', 
            'file_id': message.video.file_id,
            'selected': True
        })
        logger.info(f"🎥 Добавлено видео. Всего в группе: {len(media_groups_cache[group_id]['media'])}")
    elif message.document:
        media_groups_cache[group_id]['media'].append({
            'type': 'document',
            'file_id': message.document.file_id,
            'selected': True
        })
    elif message.audio:
        media_groups_cache[group_id]['media'].append({
            'type': 'audio',
            'file_id': message.audio.file_id,
            'selected': True
        })
 
    media_groups_cache[group_id]['last_update'] = time.time()
    restart_processing_timer(group_id)
 
def restart_processing_timer(group_id):
    if group_id in processing_timers:
        processing_timers[group_id].cancel()
    
    timer = threading.Timer(2.0, process_media_group, [group_id])
    processing_timers[group_id] = timer
    timer.start()
    logger.info(f"⏰ Таймер запущен для группы {group_id}")
 
def process_media_group(group_id):
    if group_id not in media_groups_cache:
        return
        
    group = media_groups_cache[group_id]
    
    if group['processed']:
        return
        
    user_id = group['user_id']
    total_media = len(group['media'])
    
    logger.info(f"🎯 Начинаем обработку группы {group_id}: {total_media} медиафайлов")
    
    group['processed'] = True
    
    try:
        user_posts[user_id] = {
            'text': group['text'],
            'media': group['media'],
            'last_update': time.time()
        }
        
        save_user_post(user_id, user_posts[user_id])
        logger.info(f"💾 Сохранен пост для {user_id}: {total_media} медиафайлов")
        show_post_editor(user_id)
        
        if group_id in processing_timers:
            del processing_timers[group_id]
            
        threading.Timer(10.0, cleanup_media_group, [group_id]).start()
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки группы {group_id}: {e}")
        group['processed'] = False
 
def cleanup_media_group(group_id):
    if group_id in media_groups_cache:
        del media_groups_cache[group_id]
        logger.info(f"🧹 Очищена группа {group_id}")
 
def show_post_editor(user_id):
    try:
        post_data, topic_id = get_user_post(user_id)
        if not post_data:
            logger.error(f"❌ Нет данных поста для пользователя {user_id}")
            return
            
        text = post_data.get('text', '')
        media_items = post_data.get('media', [])
        
        logger.info(f"📊 Показываем редактор для {user_id}: {len(media_items)} медиафайлов")
        
        markup = create_editor_markup(media_items)
        
        selected_count = sum(1 for m in media_items if m['selected'])
        preview = "🎯 <b>РЕДАКТОР ПОСТА</b>\n\n"
        preview += f"📝 <b>Текст:</b>\n{text or '❌ Нет текста'}\n\n"
        
        if media_items:
            preview += f"📦 <b>Медиафайлы:</b> {len(media_items)} шт. (выбрано: {selected_count})\n\n"
            
            for i, media in enumerate(media_items):
                status = "✅" if media['selected'] else "❌"
                emoji = "🖼" if media['type'] == 'photo' else "🎥" if media['type'] == 'video' else "📄" if media['type'] == 'document' else "🎵"
                preview += f"{i+1}. {status} {emoji} {media['type']}\n"
        else:
            preview += "📦 <b>Медиафайлы:</b> нет\n\n"
        
        if topic_id:
            topic_name = get_topic_name(topic_id)
            preview += f"\n🏷 <b>Тема:</b> {topic_name}"
        
        preview += "\n\n👇 <b>Управление:</b>"
 
        if user_id in user_editor_message_ids:
            try:
                bot.edit_message_text(
                    preview,
                    user_id,
                    user_editor_message_ids[user_id],
                    reply_markup=markup,
                    parse_mode='HTML'
                )
                logger.info(f"✅ Обновили редактор для {user_id}")
                return
            except Exception as e:
                logger.warning(f"⚠️ Не удалось редактировать сообщение: {e}")
                del user_editor_message_ids[user_id]
 
        msg = bot.send_message(user_id, preview, reply_markup=markup, parse_mode='HTML')
        user_editor_message_ids[user_id] = msg.message_id
        logger.info(f"✅ Создали новый редактор для {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа редактора: {e}")
 
def create_editor_markup(media_items):
    markup = types.InlineKeyboardMarkup(row_width=4)
    
    if media_items:
        file_buttons = []
        for i, media in enumerate(media_items):
            status = "✅" if media['selected'] else "⚪"
            file_buttons.append(types.InlineKeyboardButton(
                f"{status}{i+1}", 
                callback_data=f"toggle_{i}"
            ))
        
        for i in range(0, len(file_buttons), 4):
            markup.add(*file_buttons[i:i+4])
            
        markup.add(
            types.InlineKeyboardButton("✅ ВЫБРАТЬ ВСЕ", callback_data="select_all"),
            types.InlineKeyboardButton("❌ СНЯТЬ ВСЕ", callback_data="deselect_all")
        )
    
    markup.add(
        types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ ТЕКСТ", callback_data="edit_text")
    )
    
    markup.add(
        types.InlineKeyboardButton("📋 ВЫБРАТЬ ТЕМУ", callback_data="choose_topic"),
        types.InlineKeyboardButton("🚀 ОТПРАВИТЬ", callback_data="send_post")
    )
    
    markup.add(
        types.InlineKeyboardButton("🗑 ОТМЕНИТЬ", callback_data="cancel_post")
    )
    
    return markup
 
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    
    try:
        if call.data == "edit_text":
            bot.answer_callback_query(call.id)
            ask_new_text(user_id)
            
        elif call.data.startswith("toggle_"):
            index = int(call.data.replace("toggle_", ""))
            toggle_media_selection(user_id, index)
            bot.answer_callback_query(call.id)
            
        elif call.data == "select_all":
            set_all_media_selection(user_id, True)
            bot.answer_callback_query(call.id, "✅ Все файлы выбраны")
            
        elif call.data == "deselect_all":
            set_all_media_selection(user_id, False)
            bot.answer_callback_query(call.id, "❌ Выбор снят со всех файлов")
            
        elif call.data == "choose_topic":
            bot.answer_callback_query(call.id)
            show_topic_selection(user_id)
            
        elif call.data == "send_post":
            bot.answer_callback_query(call.id)
            send_post(user_id)
            
        elif call.data == "cancel_post":
            bot.answer_callback_query(call.id)
            cancel_post(user_id)
            
        elif call.data.startswith("topic_"):
            set_topic(user_id, call.data)
            bot.answer_callback_query(call.id, "✅ Тема установлена")
            
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")
 
def ask_new_text(user_id):
    post_data, _ = get_user_post(user_id)
    if not post_data:
        bot.send_message(user_id, "❌ Пост не найден")
        return
        
    current_text = post_data.get('text', '')
    msg = bot.send_message(
        user_id, 
        f"✏️ <b>Редактирование текста</b>\n\n"
        f"Текущий текст:\n{current_text or '❌ Нет текста'}\n\n"
        f"Введите новый текст или отправьте <code>-</code> для пустого текста:",
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, process_new_text, user_id)
 
def process_new_text(message, user_id):
    new_text = '' if message.text.strip() == '-' else message.text
    
    post_data, topic_id = get_user_post(user_id)
    if post_data:
        post_data['text'] = new_text
        save_user_post(user_id, post_data, topic_id)
        show_post_editor(user_id)
 
def toggle_media_selection(user_id, index):
    post_data, topic_id = get_user_post(user_id)
    if not post_data or 'media' not in post_data:
        return
        
    media_items = post_data['media']
    if 0 <= index < len(media_items):
        media_items[index]['selected'] = not media_items[index]['selected']
        save_user_post(user_id, post_data, topic_id)
        show_post_editor(user_id)
 
def set_all_media_selection(user_id, selected):
    post_data, topic_id = get_user_post(user_id)
    if not post_data or 'media' not in post_data:
        return
        
    for media in post_data['media']:
        media['selected'] = selected
        
    save_user_post(user_id, post_data, topic_id)
    show_post_editor(user_id)
 
def show_topic_selection(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for topic_name, topic_id in TOPICS.items():
        callback_data = f"topic_{topic_id}" if topic_id is not None else "topic_none"
        markup.add(types.InlineKeyboardButton(topic_name, callback_data=callback_data))
    
    bot.send_message(user_id, "📋 <b>Выберите тему для поста:</b>", reply_markup=markup, parse_mode='HTML')
 
def set_topic(user_id, callback_data):
    topic_id = None if callback_data == "topic_none" else int(callback_data.replace("topic_", ""))
    
    post_data, _ = get_user_post(user_id)
    if post_data:
        save_user_post(user_id, post_data, topic_id)
        show_post_editor(user_id)
 
def send_post(user_id):
    post_data, topic_id = get_user_post(user_id)
    if not post_data:
        bot.send_message(user_id, "❌ Пост не найден")
        return
        
    text = post_data.get('text', '')
    media_items = [m for m in post_data.get('media', []) if m.get('selected', True)]
    
    logger.info(f"📤 Отправка для {user_id}: {len(media_items)} медиафайлов, тема ID: {topic_id}")
    
    if not text and not media_items:
        bot.send_message(user_id, "❌ Нечего отправлять")
        return
        
    try:
        send_params = {}
        if topic_id is not None:
            try:
                send_params['message_thread_id'] = topic_id
                logger.info(f"📤 Пробуем отправить в топик ID: {topic_id}")
                
                if media_items:
                    media_group = create_media_group(media_items, text)
                    bot.send_media_group(TARGET_CHANNEL, media_group, **send_params)
                else:
                    bot.send_message(TARGET_CHANNEL, text, parse_mode='HTML', **send_params)
                    
            except Exception as topic_error:
                if "message thread not found" in str(topic_error):
                    logger.warning(f"⚠️ Топик {topic_id} не найден, отправляем без топика")
                    if 'message_thread_id' in send_params:
                        del send_params['message_thread_id']
                    
                    if media_items:
                        media_group = create_media_group(media_items, text)
                        bot.send_media_group(TARGET_CHANNEL, media_group, **send_params)
                    else:
                        bot.send_message(TARGET_CHANNEL, text, parse_mode='HTML', **send_params)
                else:
                    raise topic_error
        else:
            if media_items:
                media_group = create_media_group(media_items, text)
                bot.send_media_group(TARGET_CHANNEL, media_group, **send_params)
            else:
                bot.send_message(TARGET_CHANNEL, text, parse_mode='HTML', **send_params)
            
        topic_name = get_topic_name(topic_id)
        bot.send_message(
            user_id, 
            f"✅ <b>Пост успешно отправлен!</b>\n\n"
            f"📊 Статистика:\n"
            f"• Медиафайлов: {len(media_items)}\n"
            f"• Текст: {'есть' if text else 'нет'}\n"
            f"• Тема: {topic_name}",
            parse_mode='HTML'
        )
        
        cleanup_user_data(user_id)
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        bot.send_message(user_id, f"❌ Ошибка отправки: {str(e)}")
 
def create_media_group(media_items, text):
    media_group = []
    for i, media in enumerate(media_items):
        if media['type'] == 'photo':
            media_item = types.InputMediaPhoto(media['file_id'])
        elif media['type'] == 'video':
            media_item = types.InputMediaVideo(media['file_id'])
        elif media['type'] == 'document':
            media_item = types.InputMediaDocument(media['file_id'])
        elif media['type'] == 'audio':
            media_item = types.InputMediaAudio(media['file_id'])
        
        if i == 0 and text:
            media_item.caption = text
            media_item.parse_mode = 'HTML'
            
        media_group.append(media_item)
    
    return media_group
 
def get_topic_name(topic_id):
    if topic_id is None:
        return "Без темы"
    for name, tid in TOPICS.items():
        if tid == topic_id:
            return name
    return f"Тема (ID: {topic_id})"
 
def cancel_post(user_id):
    cleanup_user_data(user_id)
    bot.send_message(user_id, "🗑 <b>Пост отменен</b>", parse_mode='HTML')
 
def cleanup_user_data(user_id):
    delete_user_post(user_id)
    if user_id in user_posts:
        del user_posts[user_id]
    if user_id in user_editor_message_ids:
        del user_editor_message_ids[user_id]
 
# Flask app для health checks
app = Flask(__name__)
 
@app.route('/')
def health_check():
    return "🤖 Бот работает отлично! 🚀", 200
 
@app.route('/health')
def health():
    return {"status": "ok", "bot": "running"}, 200
 
def signal_handler(signum, frame):
    logger.info("Получен сигнал завершения...")
    sys.exit(0)
 
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Запускаем Flask в отдельном потоке
    from threading import Thread
    flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=8080, debug=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    logger.info("🤖 Бот запущен на Railway!")
    logger.info("🌐 Health check доступен по порту 8080")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Ошибка бота: {e}")
        sys.exit(1)
