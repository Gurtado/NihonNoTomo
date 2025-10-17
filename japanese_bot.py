import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

#import pandas as pd
#from openpyxl import load_workbook
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import random
from telegram.ext import CallbackQueryHandler
import time
from datetime import datetime
import sqlite3

# логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальный кэш (или в context)
word_stats_cache = None
cache_timestamp = 0

import os
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8060505381:AAF7Q7k2yKN5kDV3JymXrPM78QS1mSj2YuY')

#________________________________________________________________________________#
#____________________________РАБОТА С БДЩЕЙ___________________________________#
#________________________________________________________________________________#

# подрубанькаем SQlite
def get_cells(db_file='日本語_bot.db'):
    try:
        if not os.path.exists(db_file):
            raise FileNotFoundError(f"Украли, блин, этот ваш {db_file}!")

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        cursor.execute('''
        SELECT 
            w.id, 
            w.type, 
            w.japanese_text, 
            w.reading, 
            w.translation,
            ws_k.level as kanji_level,      
            ws_k.count as kanji_count,      
            ws_k.last_studied as kanji_date,
            ws_r.level as reading_level,    
            ws_r.count as reading_count,     
            ws_r.last_studied as reading_date
        FROM words w
        LEFT JOIN word_stats ws_k ON w.id = ws_k.word_id AND ws_k.stat_type = 'kanji'
        LEFT JOIN word_stats ws_r ON w.id = ws_r.word_id AND ws_r.stat_type = 'reading'
        ''')

        all_words = []

        for row in cursor.fetchall():
            #распакунькали кортеж каждого слова с бдшки:
            try:
                word_id = row[0]
                word_type = row[1]
                japanese = row[2]
                reading = row[3]
                translation = row[4]
                kanji_level = row[5]
                kanji_count = row[6]
                kanji_date = row[7]
                reading_level = row[8]
                reading_count = row[9]
                reading_date = row[10]
        
                #запихали в статистику SRS-уровень кандзи или чтения (или A, если в 1 раз)
                if word_type in ['kanji', 'vocab_kanji']:
                    stats = {
                        'level': str(kanji_level or 'A'),
                        'count': int(kanji_count or 0),
                        'date': kanji_date
                    }
                else:
                    stats = {
                        'level': str(reading_level or 'A'), 
                        'count': int(reading_count or 0),
                        'date': reading_date
                    }
                #запихали в словарь слова всё остальное, и статистику
                item = {
                    'id': word_id,
                    'type': word_type,
                    'value': japanese or reading or '',
                    'translation': translation or 'Нет перевода',
                    'reading': reading,
                    'stats': stats
                }

                all_words.append(item)                
                
            except Exception as e:
                print(f"Слово посеяли, u know?!: {e}")
                
        conn.close()
        return {'words': all_words, 'total': len(all_words)}
        
    except Exception as e:
        print(f"БД камень не даёт: {e}")
        return {'words': [], 'total': 0}

def add_word_to_db(word_type, japanese_text, reading, translation):
    """Добавляет слово в базу данных"""
    try:
        conn = sqlite3.connect('日本語_bot.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO words (type, japanese_text, reading, translation)
        VALUES (?, ?, ?, ?)
        ''', (word_type, japanese_text, reading, translation))
        
        word_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return word_id
        
    except Exception as e:
        print(f"❌ Ошибка добавления слова: {e}")
        return None



#________________________________________________________________________________#
#___________________________РАБОТА СО СТАТИСТИКОЙ________________________________#
#________________________________________________________________________________#
 
#SRS статистика слова, перезапись локально
def update_word_statistics(word, is_correct, training_mode='text'):
    try:
        #тыкаемся в ячейку статистики слова:
        current_stats = word['stats']
        current_date = datetime.now().strftime("%Y-%m-%d")  #для даты в статистике

        if word['type'] in ['kanji', 'vocab_kanji']:
            stat_type = 'kanji'
        else:
            stat_type = 'reading'

        #меняем старую дату на дату ответа
        current_stats['date'] = current_date
        if is_correct:
            #добавляем балл (0-4). Если больше 4, поднимаем уровень
            if training_mode == 'text':
                # Текстовый режим - полный балл
                points = 1
            else:
                # Режим с кнопками - 0.2 балла
                points = 0.2
                current_stats['count'] += points
            if current_stats['count'] > 4:
                current_stats.update({
                    'count': 0, 
                    'level': upgrade_level(current_stats['level'])
                })
        else:
            #отнимаем балл (0-4). Если меньше 0, понижаем уровень,
            #но баллов ставим 4, чтобы сл верный ответ поднял назад
            current_stats['count'] -= 1
            if current_stats['count'] < 0:
                current_stats.update({
                    'count': 4, 
                    'level': downgrade_level(current_stats['level'])
                })
        return current_stats
    
    except Exception as e:
        print(f"❌ Пизда статистике: {e}")
        return None


def upgrade_level(current_level):
    levels = {'A': 'G', 'G': 'M', 'M': 'E', 'E': 'MASTERED'}
    return levels.get(current_level, 'A')

def downgrade_level(current_level):
    levels = {'G': 'A', 'M': 'G', 'E': 'M', 'MASTERED': 'E'}
    return levels.get(current_level, 'A')

def save_all_stats_to_db(stats_updates):
    if not stats_updates:
        return

    try:
        conn = sqlite3.connect('日本語_bot.db')
        cursor = conn.cursor()
        current_date = datetime.now().strftime("%Y-%m-%d")

        for update in stats_updates:
            word_id = update['word_id']
            word_type = update['word_type']
            current_stats = update['updated_stats']

            stat_type = 'kanji' if word_type in ['kanji', 'vocab_kanji'] else 'reading'
            updated_stats = update['updated_stats']

            # Удаляем старую и вставляем новую
            cursor.execute('''DELETE
                           FROM word_stats WHERE
                           word_id = ? AND stat_type = ?''', (word_id, stat_type))
            cursor.execute('''INSERT INTO word_stats
                            (word_id, stat_type, level, count, last_studied)
                            VALUES (?, ?, ?, ?, ?)''', 
                  (word_id, stat_type, updated_stats['level'], updated_stats['count'], current_date))

        conn.commit()
        conn.close()
        print(f"Сохранено {len(stats_updates)} обновлений статистики")
        
    except Exception as e:
        print(f"Ошибка сохранения статистики: {e}")

def calculate_priority(word):
    """Рассчитывает приоритет слова для тренировки"""
    priority = 0
    current_stats = word['stats']
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Базовый приоритет по уровню
    level_weights = {
        'A': 100,  # Самый высокий приоритет
        'G': 70,
        'M': 40, 
        'E': 20,
        'MASTERED': 5  # Очень низкий приоритет
    }
    
    priority += level_weights.get(current_stats['level'], 50)

    studied_today = (current_stats.get('date') == current_date)
    
    # Уменьшаем приоритет если слово уже изучалось сегодня
    if studied_today:
        priority -= 80
    
    # Учитываем счетчик (слова с низким count важнее)
    if current_stats['level'] != 'MASTERED':
        priority += (4 - min(current_stats['count'], 3)) * 10
    
    # Случайный элемент чтобы не было всегда одинаково
    priority += random.randint(0, 20)
    
    return max(priority, 1)  # Минимальный приоритет 1

#SRS-pool
def get_smart_word_pool(dic, session_size=20):
    all_words = []

    for word in dic['words']:
        word['priority'] = calculate_priority(word)
        
        all_words.append(word)

    all_words.sort(key=lambda x: x['priority'], reverse=True)
    all_words = all_words[:session_size]
    random.shuffle(all_words) #вшик-вшик-вшик

    return all_words

    
            
#________________________________________________________________________________#
#____________________________РАБОТА С ТЕЛЕГОЙ___________________________________#
#________________________________________________________________________________#
async def start_button_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тренировка с кнопками"""
    await start_training(update, context, training_mode='button')

async def start_text_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовая тренировка"""
    await start_training(update, context, training_mode='text')

#начало тренировок с установкой таймера, пулом слов, загрузкой локалки
async def start_training(update: Update, context: ContextTypes.DEFAULT_TYPE, training_mode='button'):
    try:
        dic = get_cells()
        
        #пул слов
        session_words = get_smart_word_pool(dic, session_size=20)

        #локальный сейв сессии:
        context.user_data['training_words'] = session_words
        context.user_data['current_index'] = 0
        context.user_data['score'] = 0
        context.user_data['session_end_time'] = time.time() + 300
        context.user_data['stats_updates'] = []  # массив для статистики за всю игру
        context.user_data['training_mode'] = training_mode

        await ask_question(update, context)
    except Exception as e:
        await update.message.reply_text(f"Сейвы рушатся, мозги крутятся: {str(e)}")

#функция вопросов с кнопками ответов
async def ask_question(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    try:
        # ПРАВИЛЬНОЕ определение message в зависимости от типа update
        if isinstance(update_or_query, Update) and update_or_query.message:
            # Это обычное сообщение (команда)
            message = update_or_query.message
        elif isinstance(update_or_query, Update) and update_or_query.callback_query:
            # Это callback от кнопки
            message = update_or_query.callback_query.message
        elif hasattr(update_or_query, 'message'):
            # Это что-то с атрибутом message
            message = update_or_query.message
        else:
            # Fallback - используем оригинальный объект
            message = update_or_query
            
        
        words = context.user_data.get('training_words', [])
        if not words:
            await message.reply_text("❌ Ошибка: нет слов для тренировки")
            return
            
        index = context.user_data.get('current_index', 0)
        time_left = context.user_data.get('session_end_time', 0) - time.time()

       
        if (index >= len(words)) or (time_left <= 0):
            #запись статистики за сессию
            if context.user_data.get('stats_updates'):
                save_all_stats_to_db(context.user_data['stats_updates'])
            
            reason = "🌚 Время вышло!" if (time_left <= 0) else "😭 Слова закончились"
            await message.reply_text(
                f"🎊 Всё, сдаёмся! ({reason})\n"
                f"🌟 Правильных ответов: {context.user_data.get('score', 0)}/{len(words)}"
            )
            await help_command(update_or_query, context)
            return
        
        #вывод таймера:
        minutes = int(time_left // 60)
        seconds = int(time_left % 60)
        timer_display = f"⏰ {minutes:02d}:{seconds:02d}"
        
        current_word = words[index]
        
        var_translation = random.randint(0, 1)  #R->IA or IA->R

        if var_translation == 0:
            #IA->R
            question_text = current_word['value']
            correct_answer = current_word['translation']
            
            other_words = [w for w in words if w['translation'] != correct_answer]
            wrong_answers = random.sample(other_words, min(3, len(other_words)))
            wrong_answers = [w['translation'] for w in wrong_answers]
        else:
            #R->IA
            question_text = current_word['translation']  
            correct_answer = current_word['value']

            other_words = [w for w in words if w['value'] != correct_answer]
            wrong_answers = random.sample(other_words, min(3, len(other_words)))
            wrong_answers = [w['value'] for w in wrong_answers]



        context.user_data['current_correct_answer'] = correct_answer
        context.user_data['awaiting_confirmation'] = False
        
        #режим тренировки
        training_mode = context.user_data.get('training_mode', 'button')

        if training_mode == 'text':
            # Текстовый режим
            await message.reply_text(
                f"{timer_display} | 🎯 {context.user_data.get('score', 0)} | 📊 {index+1}/{len(words)}\n"
                f"Слово: {question_text}\n"
                f"Напишите ответ:"
            )
        else:
            # Режим с кнопками
            all_answers = [correct_answer] + wrong_answers
            random.shuffle(all_answers)

           

            #кнопки
            keyboard = []
            for answer in all_answers:
                keyboard.append([InlineKeyboardButton(answer, callback_data=answer)])
           
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(
                f"{timer_display} | 🎯 {context.user_data.get('score', 0)} | 📊 {index+1}/{len(words)}\n"
                f"Слово: {question_text}\n"
                f"Выбери перевод:",
                reply_markup=reply_markup
            )

            
    except Exception as e:
        logger.error(f"Ошибка в ask_question: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            if isinstance(update_or_query, Update) and update_or_query.message:
                await update_or_query.message.reply_text(f"❌ Ошибка при показе вопроса: {str(e)}")
        except:
            pass
    

async def add_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет слово через быстрый ввод"""
    if not context.args:
        help_text = """
    Добавление слов:

        🔤 **Полный формат** (3 части):
        `/addword 学|がく|учеба`
        `/addword りゅう学|りゅうがく|иностранное обучение`

        🎌 **Только кандзи** (2 части):
        `/addword 学|учеба`

        📖 **Только чтение** (2 части):  
        `/addword こんにちは|привет`

        Разделители: | / \ ｜
        """
        await update.message.reply_text(help_text)
        return

    input_text = ' '.join(context.args)
    word_data = parse_word_input(input_text)

    if not word_data:
        await update.message.reply_text("Не то! Используй: текст|чтение|перевод")
        return

    # Добавляем в базу
    word_id = add_word_to_db(
        word_data['type'],
        word_data['japanese_text'],
        word_data['reading'],
        word_data['translation']
    )

    if word_id:
        # Красивое сообщение в зависимости от типа
        if word_data['type'] == 'vocab':
            message = f"✅ Добавлено: {word_data['japanese_text']} ({word_data['reading']}) - {word_data['translation']}"
        elif word_data['type'] == 'kanji':
            message = f"🎌 Добавлен кандзи: {word_data['japanese_text']} - {word_data['translation']}"
        else:
            message = f"📖 Добавлено чтение: {word_data['reading']} - {word_data['translation']}"
        
        await update.message.reply_text(message)
    else:
        await update.message.reply_text("Ошибка при добавлении слова в базу")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_answer = query.data

    if user_answer in ["confirm_synonym", "deny_synonym"]:
        return
    
    correct_answer = context.user_data['current_correct_answer']  #БЕЗ этого не проверим!

    is_correct = (user_answer == correct_answer) #bool

    #текущее слово для обновления статистики
    words = context.user_data['training_words']
    index = context.user_data['current_index']
    current_word = words[index]

    training_mode = context.user_data.get('training_mode', 'button')
    stats_result = update_word_statistics(current_word, is_correct, training_mode)

    stats_update = {
        'word_id': current_word['id'],
        'word_type': current_word['type'], 
        'updated_stats': stats_result
    }
    context.user_data['stats_updates'].append(stats_update)

    reading_info = ""
    if current_word.get('reading') and current_word['reading'].strip():  # ← проверка на непустое чтение
        reading_info = f"\n📖 Чтение: {current_word['reading']}"
    
    if is_correct:
        context.user_data['score'] += 1
        message_text=f"🌟 Правильно! Ответ: {correct_answer}{reading_info}"
    else:
        message_text=f"❌ Неправильно! Правильно: {correct_answer}{reading_info}\nТы выбрал: {user_answer}"

    await query.edit_message_text(text=message_text)

    context.user_data['current_index'] += 1
    await ask_question(query, context)

async def text_training_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_answer = update.message.text.strip()
        correct_answer = context.user_data.get('current_correct_answer')
        
        if not correct_answer:
            await update.message.reply_text("❌ Ошибка: нет активного вопроса")
            return

        if user_answer.lower() == correct_answer.lower():
            is_correct = True
            await update.message.reply_text(f"🌟 Правильно! Ответ: {correct_answer}")
            await process_correct_answer(update, context, is_correct) #сразу кидаем в статистику

        else:
            #уточнение
            context.user_data['user_answer'] = user_answer
            context.user_data['awaiting_confirmation'] = True
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_synonym")],
                [InlineKeyboardButton("Нет", callback_data="deny_synonym")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"❌ Неправильно! Правильно: {correct_answer}\n"
                f"Вы написали: {user_answer}\n"
                f"Написали ли Вы то же другими словами?",
                reply_markup=reply_markup
            )
    except Exception as e:
        await update.message.reply_text(f"Ошибка текст_бандерлог: {str(e)}")

async def synonym_confirmation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    training_mode = context.user_data.get('training_mode', 'button')
    user_answer = context.user_data.get('user_answer')
    correct_answer = context.user_data.get('current_correct_answer')

    #Получаем текущее слово для показа чтения
    words = context.user_data['training_words']
    index = context.user_data['current_index']
    current_word = words[index]
    
    reading_info = ""
    if current_word.get('reading') and current_word['reading'].strip():  # ← проверка на непустое чтение
        reading_info = f"\n📖 Чтение: {current_word['reading']}"
    
    if query.data == "confirm_synonym":
        is_correct = True
        await query.edit_message_text(
            f"✅ Принято как правильный ответ!\n"
            f"Ваш вариант: {user_answer}\n"
            f"Ожидалось: {correct_answer}{reading_info}"
        )
    else:
        is_correct = False
        await query.edit_message_text(
            f"❌ Неправильный ответ\n"
            f"Вы написали: {user_answer}\n"
            f"Правильно: {correct_answer}{reading_info}"
        )

    #флаг ожидания в жопу
    context.user_data['awaiting_confirmation'] = False
    context.user_data['user_answer'] = None
    #статистику не в жопу
    await process_correct_answer(update, context, is_correct)

async def handle_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых ответов в режиме тренировки"""
    # Проверяем, находимся ли мы в режиме текстовой тренировки и ожидаем ответ
    if (context.user_data.get('training_mode') == 'text' and 
        not context.user_data.get('awaiting_confirmation', False)):
        await text_training_handler(update, context)
    else:
        await handle_message(update, context)

        
async def process_correct_answer(update, context, is_correct):
    words = context.user_data['training_words']
    index = context.user_data['current_index']
    current_word = words[index]

    #в статистику
    training_mode = context.user_data.get('training_mode', 'button')
    updated_stats = update_word_statistics(current_word, is_correct, training_mode)
    # Показываем чтение для текстового режима
    if context.user_data.get('training_mode') == 'text':
        reading_info = ""
        if current_word.get('reading') and current_word['reading'].strip():  # ← проверка на непустое чтение
            reading_info = f"\n📖 Чтение: {current_word['reading']}"

    # Сохраняем для batch-записи
    stats_update = {
        'word_id': current_word['id'],
        'word_type': current_word['type'], 
        'updated_stats': updated_stats
    }
    context.user_data['stats_updates'].append(stats_update)
    
    if is_correct:
        context.user_data['score'] += 1
    
    # Переходим к следующему вопросу
    context.user_data['current_index'] += 1
    await ask_question(update, context)

#парс ввода новых слов    
def parse_word_input(text):
    normalized = text.replace('\\', '|').replace('/', '|').replace('｜', '|').replace('＼', '|')
    parts = [part.strip() for part in normalized.split('|') if part.strip()]

    if len(parts) == 3:
        #кандзи|чтение|перевод
        return {
            'type': 'vocab',
            'japanese_text': parts[0],
            'reading': parts[1], 
            'translation': parts[2]
        }
    elif len(parts) == 2:
        #текст|перевод (кандзи, либо хирагана)
        if any('\u4e00' <= char <= '\u9fff' for char in parts[0]):  # Проверка на кандзи
            return {
                'type': 'kanji',
                'japanese_text': parts[0],
                'reading': None,
                'translation': parts[1]
            }
        else:
            return {
                'type': 'reading', 
                'japanese_text': '',
                'reading': parts[0],
                'translation': parts[1]
            }
    else:
        return None
    
                              
#вывод кол-ва слов
async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: #траим попытку впихать всё в словарь и работать через него
        dic = get_cells()

        kanji_count = 0
        vocab_kanji_count = 0
        reading_count = 0
        other_count = 0
        vocab_count = 0
        
        for word in dic['words']:
            if word['type'] == 'kanji':
                kanji_count += 1
            elif word['type'] == 'vocab_kanji':
                vocab_kanji_count += 1
            elif word['type'] == 'vocab': 
                vocab_count += 1
            elif word['type'] == 'reading':
                reading_count += 1
            else:
                other_count += 1
        
        message = (
            f"🎌 Кандзи: {kanji_count}\n"
            f"📚 Слова с кандзи: {vocab_kanji_count}\n"  
            f"📖 Слова с чтением: {reading_count+vocab_count}\n"
            f"📊 Всего слов: {dic['total']}"
        )

        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"ХЗ {str(e)}")

#________________________________________________________________________________#
#____________________________РАБОТА С ОСНОВОЙ___________________________________#
#________________________________________________________________________________#
        
# /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"こんにちは, {user_name}!\n"
        "Это бот для лазанья по моему словарю!\n"
        "Используй /help для списка команд"
    )

# Команда /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
Что тут можно:
/start - начать работу
/help - заloopить
/count - посчитать словечки
/train - потренить
/addword - добавить слово
/texttrain - жёстко потренить
    """
    await update.message.reply_text(help_text)

# Обработка обычных сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text(f"Вы написали: {text}")

# Ошибки
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

def main():
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("count", count_command))
    application.add_handler(CommandHandler("train", start_button_training))
    application.add_handler(CommandHandler("texttrain", start_text_training))
    application.add_handler(CommandHandler("addword", add_word_command))
    # Сначала специфичные паттерны, потом общий обработчик
    application.add_handler(CallbackQueryHandler(synonym_confirmation_handler, pattern="^(confirm_synonym|deny_synonym)$"))
    application.add_handler(CallbackQueryHandler(button_handler))  # общий обработчик для остальных кнопок
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_answer))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()


