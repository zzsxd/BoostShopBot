import time
import telebot
import os
import traceback
import re
import json
import threading
import platform
import logging
import pandas as pd
from threading import Lock
from datetime import datetime, timedelta
from config_parser import ConfigParser
from frontend import Bot_inline_btns
from telebot import types
from backend import DbAct
from db import DB

config_name = 'secrets.json'
os_type = platform.system()
work_dir = os.path.dirname(os.path.realpath(__file__))
config = ConfigParser(f'{work_dir}/{config_name}', os_type)
db = DB(config.get_config()['db_file_name'], Lock())
db_actions = DbAct(db, config, config.get_config()['xlsx_path'])
bot = telebot.TeleBot(config.get_config()['tg_api'])

temp_data = {}
pending_reviews = {}

config_data = config.get_config()
channels = [
    '@BridgeSide_Featback',
    '@BridgeSide_LifeStyle', 
    '@BridgeSide_Store'
]

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def is_subscribed(user_id):
    try:
        for channel in channels:
            member = bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        return True
    except Exception as e:
        print(f"Ошибка проверки подписки: {e}")
        return False

def show_subscription_request(user_id):
    buttons = Bot_inline_btns()
    markup = types.InlineKeyboardMarkup()
    
    for i, channel in enumerate(channels):
        channel_btn = types.InlineKeyboardButton(f"📢 Канал {i+1}", url=f"https://t.me/{channel[1:]}")
        markup.add(channel_btn)
    
    check_btn = types.InlineKeyboardButton("✅ Я ПОДПИСАЛСЯ", callback_data="check_subscription")
    markup.add(check_btn)
    
    bot.send_message(
        user_id,
        "📢 Для использования бота, подпишитесь на наши каналы:",
        reply_markup=markup
    )

def show_product(user_id, product_id):
    product = db_actions.get_product(product_id)
    if not product:
        bot.send_message(user_id, "Товар не найден")
        return
    variations = db_actions.get_product_variations(product_id)
    available_sizes = [v for v in variations if v['quantity'] > 0]
    
    buttons = Bot_inline_btns()
    
    is_exclusive = product[10] == 1
    
    if is_exclusive:
        caption = (
            f"🎯 ЭКСКЛЮЗИВНЫЙ ТОВАР\n\n"
            f"🛍️ {product[1]}\n\n"
            f"📝 {product[2]}\n"
            f"💎 Цена: {product[4]} BS Coin\n\n"
            f"📏 Доступные размеры:"
        )
    else:
        caption = (
            f"🛍️ {product[1]}\n\n"
            f"📝 {product[2]}\n"
            f"💰 Цена: {product[3]}₽\n\n"
            f"📏 Доступные размеры:"
        )
    
    for variation in available_sizes:
        caption += f"\n• {variation['size']} - {variation['quantity']} шт."
    
    if available_sizes:
        markup = buttons.size_selection_buttons(available_sizes, is_exclusive)
    else:
        markup = None
        
    if product[6] and product[6] != 'None' and product[6] != 'invalid':
        try:
            bot.send_photo(
                user_id,
                product[6],
                caption=caption,
                reply_markup=markup
            )
            return
        except Exception as e:
            print(f"Ошибка отправки фото: {e}")
    
    bot.send_message(
        user_id,
        caption,
        reply_markup=markup
    )

def check_and_fix_photos():
    try:
        products = db_actions.get_all_products()
        for product in products:
            product_id, name, _, _, _, photo_id, _, _, _, _, _ = product
            if photo_id:
                try:
                    file_info = bot.get_file(photo_id)
                    print(f"✅ Photo {photo_id} для товара {name} доступен")
                except Exception as e:
                    print(f"❌ Photo {photo_id} для товара {name} недоступен: {e}")
                    db_actions.update_product_photo(product_id, None)
        print("Проверка фото завершена")
    except Exception as e:
        print(f"Ошибка при проверке фото: {e}")

def handle_daily_bonus(user_id):
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        return False
    
    last_active = user_data.get('last_active')
    now = datetime.now()
    
    if isinstance(last_active, str):
        try:
            last_active = datetime.strptime(last_active, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            last_active = None
    
    if not last_active or (now - last_active) > timedelta(hours=24):
        db_actions.update_user_stats(user_id, 'bs_coin', 10)
        db_actions.update_last_active(user_id, now)
        return True
    return False

def check_comment_achievement(user_id):
    user_data = db_actions.get_user_data(user_id)
    if user_data and user_data['comments'] >= 10:
        if "active_commentator" not in user_data['achievements']:
            db_actions.add_achievement(user_id, "active_commentator")
            db_actions.update_user_stats(user_id, 'discount', 1)
            bot.send_message(
                user_id,
                "🏆 Достижение «Активный комментатор»! Ваша скидка увеличена на 1%"
            )

def ask_exclusive_status(user_id):
    markup = types.InlineKeyboardMarkup()
    btn_yes = types.InlineKeyboardButton("✅ Да", callback_data="exclusive_yes_post")
    btn_no = types.InlineKeyboardButton("❌ Нет", callback_data="exclusive_no_post")
    markup.add(btn_yes, btn_no)
    
    bot.send_message(
        user_id,
        "🎯 Это эксклюзивный товар (только за BS Coin)?",
        reply_markup=markup
    )


def process_products_file(message):
    user_id = message.from_user.id
    if not message.document:
        bot.send_message(user_id, "Пожалуйста, отправьте Excel файл")
        return
        
    try:
        bot.send_message(user_id, "🔄 Очищаем старые товары...")
        db_actions.clear_all_products()
        
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        filename = f"products_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        with open(filename, 'wb') as f:
            f.write(downloaded_file)
        
        import pandas as pd
        
        df = pd.read_excel(filename)
        
        required_columns = ['Модель', 'ID Модели', 'Размер', 'Цена Y', 'Количество', 'Цена', 'Ссылка']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            bot.send_message(user_id, f"❌ Неверный формат файла. Отсутствуют колонки: {', '.join(missing_columns)}")
            os.remove(filename)
            return
        
        def calculate_price(row):
            try:
                price_yuan = row['Цена Y']
                if pd.isna(price_yuan) or price_yuan == 0:
                    return 0
            
                if isinstance(row['Цена'], (int, float)) and not pd.isna(row['Цена']):
                    return float(row['Цена'])
                
                if isinstance(row['Цена'], str) and row['Цена'].startswith('='):
                    return float(price_yuan) * 12
                
                return float(row['Цена'])
            except:
                return float(price_yuan) * 12 
        
        df['Цена'] = df.apply(calculate_price, axis=1)
        
        df['ID Модели'] = df['ID Модели'].astype(str).apply(lambda x: x.split('.')[0] if '.' in x else x).str.strip()
        
        def safe_convert(value, default=0):
            if value is None or value == '' or pd.isna(value):
                return default
            try:
                if isinstance(value, str):
                    value = value.strip().replace(',', '.')
                    if not value:
                        return default
                return float(value)
            except (ValueError, TypeError):
                return default
        
        df['Количество'] = df['Количество'].apply(lambda x: int(safe_convert(x, 0)))
        df['Цена Y'] = df['Цена Y'].apply(lambda x: safe_convert(x, 0))
        
        df['Модель'] = df['Модель'].fillna('Неизвестно').astype(str)
        df['Размер'] = df['Размер'].fillna('').astype(str)
        df['Ссылка'] = df['Ссылка'].fillna('').astype(str)
        
        if df['Цена'].isnull().all() or (df['Цена'] == 0).all():
            bot.send_message(user_id, "❌ Ошибка: не удалось вычислить цены. Проверьте формат файла.")
            os.remove(filename)
            return
    
        
        success_count = db_actions.import_products_from_excel(df)
        
        total_products = len(df['Модель'].unique())
        total_variations = len(df)
        zero_quantity = len(df[df['Количество'] == 0])
        
        stats_msg = (
            f"✅ Успешно импортировано {success_count} товаров\n\n"
            f"📊 Статистика:\n"
            f"• Уникальных моделей: {total_products}\n"
            f"• Всего вариаций: {total_variations}\n"
            f"• С нулевым количеством: {zero_quantity}\n"
            f"• Диапазон цен: {df['Цена'].min():.0f} - {df['Цена'].max():.0f}₽"
        )
        
        bot.send_message(user_id, stats_msg)
        
        sample_msg = "📋 Пример первых 5 товаров:\n"
        for i, (_, row) in enumerate(df.head().iterrows()):
            sample_msg += f"{i+1}. {row['Модель']} - {row['Размер']} - {row['Цена']}₽\n"
        
        bot.send_message(user_id, sample_msg)
        
    except Exception as e:
        error_msg = f"❌ Ошибка при обработке файла: {str(e)}"
        print(f"Ошибка импорта: {e}")
        import traceback
        print(traceback.format_exc())
        bot.send_message(user_id, error_msg)
    finally:
        if os.path.exists(filename):
            os.remove(filename)

def send_review_for_moderation(user_id, review_data):
    try:
        user_data = db_actions.get_user_data(user_id)
        admin_group_id = -1002585832553
        
        caption = (
            f"📝 Новый отзыв на модерацию\n\n"
            f"👤 Пользователь: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 @{user_data['username']}\n\n"
            f"📄 Текст: {review_data['text'][:500]}...\n\n"
            f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        markup = types.InlineKeyboardMarkup()
        approve_btn = types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_review_{user_id}")
        reject_btn = types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_review_{user_id}")
        markup.add(approve_btn, reject_btn)
        
        review_id = f"{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        pending_reviews[review_id] = review_data
        
        if review_data.get('photos'):
            message = bot.send_photo(
                chat_id=admin_group_id,
                photo=review_data['photos'][0],
                caption=caption,
                reply_markup=markup
            )
            
            pending_reviews[review_id]['message_id'] = message.message_id
            
            for photo in review_data['photos'][1:]:
                bot.send_photo(
                    chat_id=admin_group_id,
                    photo=photo
                )
        else:
            message = bot.send_message(
                chat_id=admin_group_id,
                text=caption,
                reply_markup=markup
            )
            pending_reviews[review_id]['message_id'] = message.message_id
            
    except Exception as e:
        print(f"Ошибка отправки на модерацию: {e}")
        bot.send_message(user_id, "❌ Ошибка при отправке отзыва на модерацию")

def publish_review_to_channel(user_id, review_data):
    try:
        user_data = db_actions.get_user_data(user_id)
        channel_id = "@BridgeSide_Featback"
        
        caption = (
            f"⭐️ Новый отзыв\n\n"
            f"👤 От: {user_data['first_name']} {user_data['last_name']}\n\n"
            f"📝 {review_data['text']}\n\n"
            f"💬 Присоединяйтесь к обсуждению!"
        )
        
        if review_data.get('photos'):
            bot.send_photo(
                chat_id=channel_id,
                photo=review_data['photos'][0],
                caption=caption
            )
        else:
            bot.send_message(
                chat_id=channel_id,
                text=caption
            )
            
    except Exception as e:
        print(f"Ошибка публикации отзыва: {e}")

def send_order_to_admin(user_id, delivery_info):
    user_data = db_actions.get_user_data(user_id)
    product_data = temp_data[user_id]
    product = db_actions.get_product(product_data['selected_product'])
    
    order_text = (
        f"🛒 Новый заказ\n\n"
        f"👤 Пользователь: {user_data['first_name']} {user_data['last_name']}\n"
        f"🔗 {user_data['username']}\n\n"
        f"🛍️ Товар: {product[1]}\n"
        f"📏 Размер: {product_data['selected_size']}\n"
        f"💰 Цена: {product[3]}₽\n\n"
        f"📦 Данные доставки:\n{delivery_info}\n\n"
        f"🕒 Время заказа: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    for admin_id in config.get_config()['admins']:
        try:
            bot.send_message(admin_id, order_text)
        except:
            pass

def notify_admins_about_order(user_id, product, order_data, order_id, payment_photo_id=None):
    try:
        user_data = db_actions.get_user_data(user_id)
        config_data = config.get_config()
        
        topic_id = create_user_order_topic(user_data)
        
        order_text = (
            f"🛒 НОВЫЙ ЗАКАЗ #{order_id}\n\n"
            f"👤 Клиент: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 @{user_data['username']}\n"
            f"🆔 ID: {user_id}\n\n"
            f"🛍️ Товар: {product[1]}\n"
            f"💰 Цена: {product[3]}₽\n"
            f"🎯 Тип: {'Эксклюзивный (BS Coin)' if product[10] else 'Обычный'}\n\n"
            f"📦 ДАННЫЕ ДОСТАВКИ:\n"
            f"🏙️ Город: {order_data['city']}\n"
            f"📍 Адрес: {order_data['address']}\n"
            f"👤 ФИО: {order_data['full_name']}\n"
            f"📞 Телефон: {order_data['phone']}\n"
            f"🚚 Способ: {order_data['delivery_type']}\n\n"
            f"💳 ОПЛАТА: {'Приложена ✅' if payment_photo_id else 'Не приложена ❌'}\n\n"
            f"🕒 Время заказа: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"📊 Статус: ⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ"
        )
        
        markup = types.InlineKeyboardMarkup()
        btn_approve = types.InlineKeyboardButton(
            "✅ Подтвердить заказ", 
            callback_data=f"approve_order_{order_id}"
        )
        btn_reject = types.InlineKeyboardButton(
            "❌ Отклонить заказ", 
            callback_data=f"reject_order_{order_id}"
        )
        markup.add(btn_approve, btn_reject)
        
        try:
            if payment_photo_id:
                message = bot.send_photo(
                    chat_id=config_data['admin_group_id'],
                    photo=payment_photo_id,
                    caption=order_text,
                    message_thread_id=topic_id,
                    reply_markup=markup 
                )
            else:
                message = bot.send_message(
                    chat_id=config_data['admin_group_id'],
                    text=order_text,
                    message_thread_id=topic_id,
                    reply_markup=markup
                )
            
            db_actions.save_order_message_id(order_id, message.message_id, topic_id)
                
        except Exception as e:
            print(f"Ошибка отправки в топик: {e}")
                
    except Exception as e:
        print(f"Ошибка в notify_admins_about_order: {e}")

def create_user_order_topic(user_data):
    """Создает топик для заказов пользователя и возвращает его ID"""
    try:
        config_data = config.get_config()
        group_id = config_data['admin_group_id']
        
        topic_name = f"{user_data['first_name']} {user_data['last_name']} - ЗАКАЗ"
        
        result = bot.create_forum_topic(
            chat_id=group_id,
            name=topic_name
        )
        
        return result.message_thread_id
        
    except Exception as e:
        print(f"Ошибка создания топика: {e}")
        return config_data['topics'].get('магазин', 1)
    
def close_order_topic(user_data, order_id, status="✅ ВЫПОЛНЕН"):
    """Закрывает топик заказа с указанием статуса"""
    try:
        config_data = config.get_config()
        group_id = config_data['admin_group_id']
        
        topics = bot.get_forum_topics(group_id)
        topic_name = f"{user_data['first_name']} {user_data['last_name']} - ЗАКАЗ"
        
        for topic in topics.topics:
            if topic.name == topic_name:
                close_text = (
                    f"📦 ЗАКАЗ #{order_id} {status}\n"
                    f"👤 {user_data['first_name']} {user_data['last_name']}\n"
                    f"🕒 Завершен: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
                
                bot.send_message(
                    chat_id=group_id,
                    text=close_text,
                    message_thread_id=topic.message_thread_id
                )
                
                try:
                    bot.close_forum_topic(
                        chat_id=group_id,
                        message_thread_id=topic.message_thread_id
                    )
                except:
                    pass
                
                break
                
    except Exception as e:
        print(f"Ошибка закрытия топика: {e}")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text.lower() == '✅ подтвердить')
def confirm_order(message):
    user_id = message.from_user.id
    
    try:
        order_data = temp_data[user_id]['order']
        product_id = order_data['product_id']
        size = order_data.get('size')
        product = db_actions.get_product(product_id)
        payment_photo_id = order_data.get('payment_photo')
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            return
        
        if size:
            variations = db_actions.get_product_variations(product_id)
            available = False
            for variation in variations:
                if variation['size'] == size and variation['quantity'] > 0:
                    available = True
                    break
            
            if not available:
                bot.send_message(user_id, "❌ Этот размер больше недоступен")
                return
                
            success = db_actions.decrease_product_quantity(product_id, size)
            if not success:
                bot.send_message(user_id, "❌ Этот размер больше недоступен")
                return
        
        order_id = db_actions.create_detailed_order(
            user_id=user_id,
            product_id=product_id,
            size=size,
            city=order_data['city'],
            address=order_data['address'],
            full_name=order_data['full_name'],
            phone=order_data['phone'],
            delivery_type=order_data['delivery_type']
        )
        
        if order_id:
            if product[10]:
                db_actions.update_user_stats(user_id, 'bs_coin', -product[4])
            
            db_actions.update_user_stats(user_id, 'orders', 1)
            
            notify_admins_about_order(user_id, product, order_data, order_id, payment_photo_id)
            
            bot.send_message(
                user_id,
                f"✅ Заказ #{order_id} оформлен!\n\n"
                f"📞 С вами свяжутся в течение 15 минут для подтверждения заказа.\n"
                f"💬 Отслеживать статус заказа можно в этом чате."
            )
        else:
            bot.send_message(user_id, "❌ Ошибка оформления заказа")
        
    except Exception as e:
        print(f"Ошибка подтверждения заказа: {e}")
        bot.send_message(user_id, "❌ Ошибка оформления заказа")
    finally:
        if user_id in temp_data and 'order' in temp_data[user_id]:
            del temp_data[user_id]['order']

# ============ ОБРАБОТЧИКИ КОМАНД ============

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
    
    buttons = Bot_inline_btns()
    
    is_new_user = not db_actions.user_exists(user_id)
    if is_new_user:
        first_name = message.from_user.first_name or ""
        last_name = message.from_user.last_name or ""
        username = f"@{message.from_user.username}" if message.from_user.username else ""
        db_actions.add_user(user_id, first_name, last_name, username)

    command_parts = message.text.split()
    if len(command_parts) > 1:
        param = command_parts[1]
        
        if param.startswith('ref_'):
            try:
                referrer_id = int(param.split('_')[1])
                if is_new_user and db_actions.user_exists(referrer_id) and referrer_id != user_id:
                    db_actions.add_referral(referrer_id, user_id)
                    db_actions.update_user_stats(referrer_id, 'bs_coin', 100)
                    db_actions.update_user_stats(user_id, 'bs_coin', 50)
                    db_actions.update_user_stats(user_id, 'discount', 5)
                    
                    bot.send_message(
                        referrer_id,
                        f"🎉 Новый реферал! Вам начислено 100 BS Coin. Теперь у вас {db_actions.get_referral_stats(referrer_id)} рефералов."
                    )
                    
                    bot.send_message(
                        user_id,
                        f"🎁 Вы получили бонус за регистрацию по реферальной ссылке!\n"
                        f"💎 +50 BS Coin\n"
                        f"🎯 +5% скидка на все заказы"
                    )
            except (ValueError, IndexError):
                pass
            
        elif param.startswith('product_'):
            try:
                product_id_str = param.split('_')[1]
                if product_id_str.lower() == 'none':
                    raise ValueError("Неверный ID товара")
                    
                product_id = int(product_id_str)
                show_product(user_id, product_id)
                return
            except (ValueError, IndexError):
                bot.send_message(user_id, "❌ Неверная ссылка на товар")
    
    if handle_daily_bonus(user_id):
        bot.send_message(user_id, "🎉 Ежедневный бонус: 10 BS Coin зашли вам на счет!")
    
    user_data = db_actions.get_user_data(user_id)
    welcome_msg = (
        f"🚀 Добро пожаловать на борт, Друг! 🚀\n"
        f"Рады приветствовать тебя в сообществе BridgeSide — месте, где встречаются твой стиль и уникальные возможности.\n\n"
        f"🌉 Твои мосты в мир BridgeSide: 🌉\n"
        f"🛍️ @BridgeSide_Shop - Прямой каталог наших товаров. Здесь ты первым узнаешь о новинках и эксклюзивных дропах.\n"
        f"🌟 @BridgeSide_LifeStyle - Лукбуки, стиль, жизнь сообщества, акции и розыгрыши. Вдохновляйся и участвуй!\n"
        f"📢 @BridgeSide_Featback- Честные отзывы от таких же членов клуба, как и ты. Нам важна твоя оценка.\n\n"
        f"🤖 Как управлять этим кораблем? Проще простого!\n"
        f"Этот бот — твой личный помощник. Не нужно ничего скачивать.Просто вводи команды прямо в эту строку чата:\n"
        f"• /profile — 👤 Твой цифровой пропуск. Здесь твоя история, бонусы и статус в клубе.\n"
        f"• /ref — 📍 Твой реферальный код. Приглашай друзей и получай крутые бонусы за каждого приведенного друга.\n"
        f"• /support — 🛟 Круглосуточная поддержка. Наша команда уже готова помочь 24/7.\n"
        f"🌟Или используй меню в нижней части экрана бота🌟\n\n"
        f"💡 Просто начни с любой команды выше! Бот ждет твоего сигнала. 😉\n"
        f"С уважением, команда BridgeSide. 🌉"
    )
    
    if db_actions.user_is_admin(user_id):
        bot.send_message(user_id, welcome_msg, reply_markup=buttons.admin_buttons())
    else:
        bot.send_message(user_id, welcome_msg, reply_markup=buttons.start_buttons())

@bot.message_handler(func=lambda msg: msg.text == '👤 Мой профиль')
def show_profile(message):
    profile(message)

@bot.message_handler(func=lambda msg: msg.text == '🎁 Акции')
def show_promo(message):
    bot.send_message(message.chat.id, "🔥 Горячие акции")

@bot.message_handler(func=lambda msg: msg.text == '📢 Отзывы')
def show_reviews(message):
    user_id = message.from_user.id
    reviews = db_actions.get_reviews()
    buttons = Bot_inline_btns()
    
    if not reviews:
        bot.send_message(user_id, "Пока нет отзывов. Будьте первым!", reply_markup=buttons.reviews_buttons())
        return
        
    reviews_msg = "🔥 Последние отзывы:\n\n"
    for review in reviews[:3]:
        reviews_msg += f"⭐️ {review[2]}\n— {review[5] or review[6]}\n\n"
    
    bot.send_message(
        user_id,
        reviews_msg,
        reply_markup=buttons.reviews_buttons()
    )

@bot.message_handler(commands=['my_orders'])
def my_orders(message):
    user_id = message.from_user.id
    orders = db_actions.get_user_orders(user_id)
    
    if not orders:
        bot.send_message(user_id, "У вас пока нет заказов")
        return
    
    orders_text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    for order in orders:
        product = db_actions.get_product(order['product_id'])
        orders_text += (
            f"🛒 Заказ #{order['order_id']}\n"
            f"🛍️ Товар: {product[1] if product else 'Неизвестно'}\n"
            f"📊 Статус: {order['status']}\n"
            f"🕒 Дата: {order['created_at']}\n\n"
        )
    
    bot.send_message(user_id, orders_text)

@bot.message_handler(commands=['support'])
def support(message):
    bot.reply_to(message, "🛠️ Наша служба поддержки работает для вас!\n\n"
                          "Если у вас возникли вопросы или проблемы:\n"
                          "• Напишите нам: @support_username\n"
                          "• Время работы: 10:00-22:00 (МСК)\n\n"
                          "Мы ответим вам в течение 15 минут!")

@bot.message_handler(commands=['ref'])
def ref_command(message):
    user_id = message.from_user.id
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        bot.send_message(user_id, "Сначала зарегистрируйтесь с помощью /start")
        return

    ref_count = db_actions.get_referral_stats(user_id)
    ref_link = f"https://t.me/{bot.get_me().username}?start={user_data['referral_code']}"
    
    ref_msg = (
        f"✨ 🪙 ЗАРАБАТЫВАЙ BS COIN ВМЕСТЕ С ДРУЗЬЯМИ! ✨\n\n"
        f'Приглашай друзей и получай крутые бонусы! Это наш способ сказать "спасибо" за твою поддержку.'
        f"🔮 ТВОЯ МАГИЯ ПРИГЛАШЕНИЯ:\n"
        f"Скопируй свою уникальную ссылку и отправь ее друзьям. Только по этой ссылке твой друг получит свой персональный подарок!"
        f"{ref_link}\n"
        f"(Нажми, чтобы скопировать) ✨\n\n"
        f"🎁 ЧТО ТЫ ПОЛУЧАЕШЬ:\n"
        f" +100 BridgeSide Coin 🪙 — зачисляются на твой счет.\n\n"
        f"🎁 ЧТО ПОЛУЧАЕТ ТВОЙ ДРУГ:\n"
        f" Щедрый подарок на первый заказ — СКИДКА 5% 🎯 + +50 BS Coin на свой счет! Отличный повод начать shopping!\n"
        f"🏆 ТОП-5 ПО РЕФЕРАЛАМ ЕЖЕМЕСЯЧНО ПОЛУЧАЮТ ЭКСКЛЮЗИВНЫЙ МЕРЧ!\n"
        f"Чем больше друзей ты приведёшь, тем выше твой шанс оказаться в числе Легенд нашего клуба! Смотри рейтинг в своём профиле (/profile).\n"
        f"🚀Не копи — зарабатывай! Переходи по ссылкам, покупай и приглашай!"
    )
    
    bot.send_message(user_id, ref_msg, parse_mode="HTML")

@bot.message_handler(commands=['set_discount'])
def set_discount(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return

    args = message.text.split()
    if len(args) != 3:
        bot.send_message(user_id, "Использование: /set_discount [user_id] [%]")
        return

    try:
        target_user_id = int(args[1])
        discount = int(args[2])
        
        if discount < 0 or discount > 50:
            bot.send_message(user_id, "Скидка должна быть от 0 до 50%")
            return
            
        db_actions.set_discount(target_user_id, discount)
        bot.send_message(user_id, f"✅ Скидка для пользователя {target_user_id} установлена: {discount}%")
        bot.send_message(target_user_id, f"🎉 Вам установлена скидка: {discount}%")
    except ValueError:
        bot.send_message(user_id, "Ошибка формата. user_id и % должны быть числами")

@bot.message_handler(commands=['add_coins'])
def add_coins(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return

    args = message.text.split()
    if len(args) != 3:
        bot.send_message(user_id, "Использование: /add_coins [user_id] [amount]")
        return

    try:
        target_user_id = int(args[1])
        amount = int(args[2])
        db_actions.update_user_stats(target_user_id, 'bs_coin', amount)
        bot.send_message(user_id, f"✅ Пользователю {target_user_id} начислено {amount} BS Coin")
        bot.send_message(target_user_id, f"🎉 Вам начислено {amount} BS Coin!")
    except ValueError:
        bot.send_message(user_id, "Ошибка формата. user_id и amount должны быть числами")

@bot.message_handler(commands=['user_info'])
def user_info(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return

    args = message.text.split()
    if len(args) != 2:
        bot.send_message(user_id, "Использование: /user_info [user_id]")
        return

    try:
        target_user_id = int(args[1])
        user_data = db_actions.get_user_data(target_user_id)
        if not user_data:
            bot.send_message(user_id, "Пользователь не найден")
            return

        info = (
            f"👤 Информация о пользователе:\n"
            f"🆔 ID: {user_data['user_id']}\n"
            f"👤 Имя: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 Username: {user_data['username']}\n"
            f"⭐️ Статус: {user_data['status']}\n"
            f"💬 Комментарии: {user_data['comments']}\n"
            f"📦 Заказы: {user_data['orders']}\n"
            f"🪙 BS Coin: {user_data['bs_coin']}\n"
            f"🎁 Скидка: {user_data['discount']}%\n"
            f"👥 Рефералов: {db_actions.get_referral_stats(target_user_id)}"
        )
        bot.send_message(user_id, info)
    except ValueError:
        bot.send_message(user_id, "user_id должен быть числом")

@bot.message_handler(commands=['profile'])
def profile(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        bot.send_message(user_id, "Сначала зарегистрируйтесь с помощью /start")
        return
    
    buttons = Bot_inline_btns()
    achievements_str = ""
    if user_data['achievements']:
        icons = {"first_order": "🚀", "active_commentator": "💬", "referral_king": "👑"}
        achievements_str = "\n🏆 Достижения: " + " ".join(
            [icons.get(a, "🌟") for a in user_data['achievements']]
        )
    
    coin_info = ""
    if user_data['bs_coin'] < 100:
        coin_info = "\n\n💡 Как получить BS Coin:\n• /start - ежедневный бонус\n• /ref - реферальная система\n• Активность в канале"
    
    profile_msg = (
        f"👤 Ваш профиль:\n\n"
        f"🆔 ID: <code>{user_data['user_id']}</code>\n"
        f"🌟 Статус: {user_data['status']}\n"
        f"💬 Комментарии: {user_data['comments']}\n"
        f"📦 Заказы: {user_data['orders']}\n"
        f"🪙 BS Coin: {user_data['bs_coin']}{coin_info}\n"
        f"🎁 Скидка: {user_data['discount']}%\n"
        f"{achievements_str}"
    )
    
    bot.send_message(
        user_id,
        profile_msg,
        parse_mode="HTML",
        reply_markup=buttons.profile_buttons(user_data)
    )

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔ Эта команда только для администраторов")
        return
    
    buttons = Bot_inline_btns()
    bot.send_message(
        user_id,
        "🔐 Панель администратора:",
        reply_markup=buttons.admin_buttons()
    )

@bot.message_handler(commands=['admin_stats'])
def admin_stats(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    products_count = db_actions.get_products_count()
    variations_count = db_actions.get_variations_count()
    users_count = db_actions.get_users_count()
    reviews_count = db_actions.get_reviews_count()
    
    stats_msg = (
        f"📊 Статистика магазина:\n\n"
        f"🛍️ Товаров: {products_count}\n"
        f"📦 Вариаций: {variations_count}\n"
        f"👥 Пользователей: {users_count}\n"
        f"📝 Отзывов: {reviews_count}"
    )
    
    bot.send_message(user_id, stats_msg)

@bot.message_handler(commands=['export_products'])
def export_products(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
    
    try:
        products_data = db_actions.get_all_products_for_export()
        
        if not products_data:
            bot.send_message(user_id, "❌ Нет товаров для экспорта")
            return
        
        df_data = []
        for product in products_data:
            df_data.append({
                'Модель': product['name'],
                'ID Модели': product['model_id'],
                'Размер': product['size'],
                'Цена Y': product['price_yuan'],
                'Количество': product['quantity'],
                'Цена': product['price'],
                'Ссылка': product['link']
            })
        
        df = pd.DataFrame(df_data)
        filename = f"products_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)
        
        with open(filename, 'rb') as f:
            bot.send_document(user_id, f, caption="📊 Экспорт товаров")
        
        os.remove(filename)
        
    except Exception as e:
        error_msg = f"❌ Ошибка при экспорте товаров: {str(e)}"
        print(error_msg)
        bot.send_message(user_id, error_msg)

@bot.message_handler(commands=['upload_products'])
def upload_products(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    bot.send_message(user_id, "📤 Отправьте Excel файл с товарами")
    bot.register_next_step_handler(message, process_products_file)

@bot.message_handler(commands=['create_post'])
def create_post(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    if user_id in temp_data:
        del temp_data[user_id]
    
    temp_data[user_id] = {
        'step': 'select_product',
        'photos': []
    }
    
    products = db_actions.get_products(limit=50)
    buttons = Bot_inline_btns()
    
    if not products:
        bot.send_message(user_id, "❌ Нет товаров для публикации")
        return
        
    bot.send_message(
        user_id,
        "📦 Выберите товар для публикации в канал:",
        reply_markup=buttons.post_products_buttons(products)
    )

@bot.message_handler(commands=['export_users'])
def export_users(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    users = db_actions.get_all_users()
    if not users:
        bot.send_message(user_id, "Нет пользователей для экспорта")
        return
        
    df = pd.DataFrame(users, columns=['user_id', 'first_name', 'last_name', 'username'])
    
    filename = f"users_export_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    df.to_excel(filename, index=False)
    
    with open(filename, 'rb') as f:
        bot.send_document(user_id, f, caption="📊 Экспорт пользователей")
    
    os.remove(filename)

@bot.message_handler(commands=['exclusive'])
def exclusive_products(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    products = db_actions.get_exclusive_products(limit=10)
    buttons = Bot_inline_btns()
    
    if not products:
        bot.send_message(user_id, "Эксклюзивных товаров пока нет")
        return
        
    products_msg = "🎯 Эксклюзивные товары (только за BS Coin):\n\n"
    for product in products:
        products_msg += f"{product[1]} - {product[4]} BS Coin\n"
    
    bot.send_message(
        user_id,
        products_msg,
        reply_markup=buttons.exclusive_products_buttons(products)
    )

@bot.message_handler(commands=['order_status'])
def order_status_command(message):
    """Изменение статуса заказа"""
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    args = message.text.split()
    if len(args) < 3:
        bot.send_message(user_id, 
            "Использование: /order_status [order_id] [status]\n\n"
            "Примеры статусов:\n"
            "• new - Новый\n"
            "• confirmed - Подтвержден\n" 
            "• paid - Оплачен\n"
            "• shipped - Отправлен\n"
            "• delivered - Доставлен\n"
            "• cancelled - Отменен"
        )
        return
        
    try:
        order_id = int(args[1])
        status = ' '.join(args[2:])
        
        success = db_actions.update_order_status(order_id, status)
        
        if success:
            order_info = db_actions.get_order_by_id(order_id)
            if order_info:
                user_data = db_actions.get_user_data(order_info['user_id'])
                product = db_actions.get_product(order_info['product_id'])
                
                status_texts = {
                    'new': '🆕 НОВЫЙ',
                    'confirmed': '✅ ПОДТВЕРЖДЕН',
                    'paid': '💳 ОПЛАЧЕН', 
                    'shipped': '🚚 ОТПРАВЛЕН',
                    'delivered': '📦 ДОСТАВЛЕН',
                    'cancelled': '❌ ОТМЕНЕН'
                }
                
                status_display = status_texts.get(status.lower(), status.upper())
                
                bot.send_message(
                    order_info['user_id'],
                    f"📦 Статус вашего заказа #{order_id} изменен:\n"
                    f"🔄 {status_display}\n\n"
                    f"🛍️ Товар: {product[1] if product else 'Неизвестно'}\n"
                    f"💰 Сумма: {product[3] if product else '0'}₽"
                )
            
            bot.send_message(user_id, f"✅ Статус заказа #{order_id} изменен на '{status}'")
        else:
            bot.send_message(user_id, "❌ Заказ не найден")
            
    except ValueError:
        bot.send_message(user_id, "❌ order_id должен быть числом")
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка: {str(e)}")


@bot.message_handler(commands=['orders'])
def list_orders(message):
    """Показывает список всех заказов"""
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    status_filter = None
    args = message.text.split()
    if len(args) > 1:
        status_filter = args[1].lower()
    
    orders = db_actions.get_all_orders(status_filter)
    
    if not orders:
        bot.send_message(user_id, "Заказы не найдены")
        return
    
    orders_text = "📦 СПИСОК ЗАКАЗОВ"
    if status_filter:
        orders_text += f" (фильтр: {status_filter})"
    orders_text += "\n\n"
    
    for order in orders:
        product = db_actions.get_product(order['product_id'])
        user_data = db_actions.get_user_data(order['user_id'])
        
        orders_text += (
            f"🛒 Заказ #{order['order_id']}\n"
            f"👤 {user_data['first_name']} {user_data['last_name']}\n"
            f"🛍️ {product[1] if product else 'Неизвестно'}\n"
            f"📊 Статус: {order['status']}\n"
            f"🕒 {order['created_at']}\n"
            f"🔗 /order_info_{order['order_id']}\n\n"
        )
    
    bot.send_message(user_id, orders_text)


@bot.callback_query_handler(func=lambda call: call.data.startswith('order_now_'))
def handle_order_now(call):
    try:
        print(f"DEBUG: Order now callback: {call.data}")
        
        user_id = call.from_user.id
        parts = call.data.split('_')
        
        if len(parts) < 4:
            print(f"DEBUG: Неправильный формат - {call.data}")
            bot.answer_callback_query(call.id, "❌ Ошибка формата")
            return
            
        product_id = int(parts[2])
        size = parts[3]
        
        print(f"DEBUG: Order now - product_id: {product_id}, size: '{size}' (type: {type(size)})")
        
        if not db_actions.check_size_availability(product_id, size):
            bot.answer_callback_query(call.id, "❌ Этот размер недоступен")
            return
        
        if user_id not in temp_data:
            temp_data[user_id] = {}
        
        temp_data[user_id]['order'] = {
            'product_id': product_id,
            'size': size,
            'step': 'ask_delivery'
        }
        
        delivery_form = (
            "📦 ДЛЯ ОФОРМЛЕНИЯ ЗАКАЗА\n\n"
            "Пожалуйста, заполните данные доставки ОДНИМ сообщением в формате:\n\n"
            "🏙️ Город: Ваш город\n"
            "📍 Адрес: Улица, дом, квартира\n"
            "👤 ФИО: Иванов Иван Иванович\n"
            "📞 Телефон: +79123456789\n"
            "🚚 Доставка: Почта России\n\n"
            "Пример:\n"
            "Москва\n"
            "ул. Ленина, д. 10, кв. 5\n"
            "Иванов Иван Иванович\n"
            "+79123456789\n"
            "Почта России"
        )
        
        bot.send_message(user_id, delivery_form)
        bot.answer_callback_query(call.id, "📝 Заполните данные доставки")
        
    except Exception as e:
        print(f"Ошибка в order_now: {e}")
        import traceback
        traceback.print_exc()
        bot.answer_callback_query(call.id, "❌ Ошибка оформления заказа")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text == "✅ Подтвердить заказ")
def confirm_order(message):
    user_id = message.from_user.id
    
    try:
        order_data = temp_data[user_id]['order']
        product_id = order_data['product_id']
        size = order_data.get('size')
        product = db_actions.get_product(product_id)
        payment_photo_id = order_data.get('payment_photo')
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            return
        
        if size:
            success = db_actions.decrease_product_quantity(product_id, size)
            if not success:
                bot.send_message(user_id, "❌ Этот размер больше недоступен")
                return
        
        order_id = db_actions.create_detailed_order(
            user_id=user_id,
            product_id=product_id,
            size=size,
            city=order_data['city'],
            address=order_data['address'],
            full_name=order_data['full_name'],
            phone=order_data['phone'],
            delivery_type=order_data['delivery_type']
        )
        
        if order_id:
            if product[10]:  # is_exclusive
                db_actions.update_user_stats(user_id, 'bs_coin', -product[4])  # coin_price
            
            db_actions.update_user_stats(user_id, 'orders', 1)
            
            notify_admins_about_order(user_id, product, order_data, order_id, payment_photo_id)
            
            remove_markup = types.ReplyKeyboardRemove()
            
            bot.send_message(
                user_id,
                f"✅ Заказ #{order_id} оформлен!\n\n"
                f"📞 С вами свяжутся в течение 15 минут для подтверждения заказа.\n"
                f"💬 Отслеживать статус заказа можно в этом чате.",
                reply_markup=remove_markup
            )
        else:
            bot.send_message(user_id, "❌ Ошибка оформления заказа")
        
    except Exception as e:
        print(f"Ошибка подтверждения заказа: {e}")
        bot.send_message(user_id, "❌ Ошибка оформления заказа")
    finally:
        if user_id in temp_data and 'order' in temp_data[user_id]:
            del temp_data[user_id]['order']

@bot.message_handler(func=lambda message: message.text.startswith('/order_info_'))
def order_info(message):
    """Показывает детальную информацию о заказе"""
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    try:
        order_id = int(message.text.split('_')[2])
        order_info = db_actions.get_order_by_id(order_id)
        
        if not order_info:
            bot.send_message(user_id, "❌ Заказ не найден")
            return
            
        product = db_actions.get_product(order_info['product_id'])
        user_data = db_actions.get_user_data(order_info['user_id'])
        buttons = Bot_inline_btns()
        
        info_text = (
            f"📦 ЗАКАЗ #{order_id}\n\n"
            f"👤 КЛИЕНТ:\n"
            f"• Имя: {user_data['first_name']} {user_data['last_name']}\n"
            f"• @{user_data['username']}\n"
            f"• ID: {user_data['user_id']}\n\n"
            f"🛍️ ТОВАР:\n"
            f"• {product[1] if product else 'Неизвестно'}\n"
            f"• Цена: {product[3] if product else '0'}₽\n\n"
            f"📦 ДОСТАВКА:\n"
            f"• Город: {order_info['city']}\n"
            f"• Адрес: {order_info['address']}\n"
            f"• ФИО: {order_info['full_name']}\n"
            f"• Телефон: {order_info['phone']}\n"
            f"• Способ: {order_info['delivery_type']}\n\n"
            f"📊 СТАТУС: {order_info['status']}\n"
            f"🕒 СОЗДАН: {order_info['created_at']}\n\n"
            f"⚙️ Управление: /order_status {order_id} [статус]"
        )
        
        bot.send_message(user_id, info_text, reply_markup=buttons.create_order_status_buttons(order_id))
        
    except (IndexError, ValueError):
        bot.send_message(user_id, "❌ Неверный формат команды")
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('order_'))
def handle_order_status_buttons(call):
    """Обработка кнопок изменения статуса заказа"""
    buttons = Bot_inline_btns()
    try:
        admin_id = call.from_user.id
        if not db_actions.user_is_admin(admin_id):
            bot.answer_callback_query(call.id, "⛔️ Недостаточно прав")
            return
            
        parts = call.data.split('_')
        action = parts[1]
        order_id = int(parts[2])
        
        status_mapping = {
            'confirm': 'confirmed',
            'pay': 'paid',
            'ship': 'shipped', 
            'deliver': 'delivered',
            'cancel': 'cancelled'
        }
        
        if action in status_mapping:
            new_status = status_mapping[action]
            success = db_actions.update_order_status(order_id, new_status)
            
            if success:
                order_info = db_actions.get_order_by_id(order_id)
                if order_info:
                    user_data = db_actions.get_user_data(order_info['user_id'])
                    product = db_actions.get_product(order_info['product_id'])
                    
                    status_messages = {
                        'confirmed': '✅ Ваш заказ подтвержден!',
                        'paid': '💳 Заказ оплачен. Готовим к отправке!',
                        'shipped': '🚚 Заказ отправлен! Трек-номер будет отправлен отдельно.',
                        'delivered': '🎉 Заказ доставлен! Спасибо за покупку!',
                        'cancelled': '❌ Заказ отменен. Если это ошибка, свяжитесь с поддержкой.'
                    }
                    
                    bot.send_message(
                        order_info['user_id'],
                        f"{status_messages.get(new_status, 'Статус заказа изменен')}\n\n"
                        f"🛒 Заказ #{order_id}\n"
                        f"🛍️ {product[1] if product else 'Товар'}"
                    )
                
                try:
                    current_text = call.message.caption if call.message.caption else call.message.text
                    updated_text = current_text.replace(
                        "📊 Статус: ⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ",
                        f"📊 Статус: {new_status.upper()}"
                    )
                    
                    if call.message.photo:
                        bot.edit_message_caption(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            caption=updated_text,
                            reply_markup=buttons.create_order_status_buttons(order_id)
                        )
                    else:
                        bot.edit_message_text(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            text=updated_text,
                            reply_markup=buttons.create_order_status_buttons(order_id)
                        )
                except Exception as e:
                    print(f"Ошибка обновления сообщения: {e}")
                
                bot.answer_callback_query(call.id, f"✅ Статус изменен на {new_status}")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка обновления статуса")
                
    except Exception as e:
        print(f"Ошибка обработки кнопки статуса: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

@bot.message_handler(commands=['add_product'])
def add_product(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "Эта команда только для администраторов")
        return
        
    bot.send_message(user_id, "Отправьте фото товара")
    bot.register_next_step_handler(message, process_product_photo)

def process_product_photo(message):
    if not message.photo:
        bot.send_message(message.chat.id, "Пожалуйста, отправьте фото товара")
        return
        
    photo_id = message.photo[-1].file_id
    bot.send_message(message.chat.id, "Теперь отправьте название товара")
    bot.register_next_step_handler(
        message, 
        lambda m: process_product_name(m, photo_id)
    )

def process_product_name(message, photo_id):
    name = message.text
    bot.send_message(message.chat.id, "Теперь отправьте описание товара")
    bot.register_next_step_handler(
        message, 
        lambda m: process_product_description(m, photo_id, name)
    )

def process_product_description(message, photo_id, name):
    description = message.text
    bot.send_message(message.chat.id, "Теперь отправьте цену товара (только число)")
    bot.register_next_step_handler(
        message, 
        lambda m: process_product_price(m, photo_id, name, description)
    )

def process_product_price(message, photo_id, name, desc):
    try:
        price = float(message.text)
        
        markup = types.InlineKeyboardMarkup()
        btn_yes = types.InlineKeyboardButton("Да", callback_data="exclusive_yes")
        btn_no = types.InlineKeyboardButton("Нет", callback_data="exclusive_no")
        markup.add(btn_yes, btn_no)
        
        msg = bot.send_message(
            message.chat.id,
            "🎯 Это эксклюзивный товар (только за BS Coin)?",
            reply_markup=markup
        )
    
        user_id = message.from_user.id
        temp_data[user_id] = {
            'name': name,
            'description': desc,
            'price': price,
            'photo_id': photo_id,
            'step': 'ask_exclusive'
        }
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат цены. Используйте только числа")

# ============ ОБРАБОТЧИКИ CALLBACK ============

@bot.callback_query_handler(func=lambda call: call.data == 'check_subscription')
def check_subscription(call):
    user_id = call.from_user.id
    if is_subscribed(user_id):
        bot.delete_message(user_id, call.message.message_id)
        start(call.message)
    else:
        bot.answer_callback_query(call.id, "Вы ещё не подписались на канал")

@bot.callback_query_handler(func=lambda call: call.data == 'exchange_coin')
def exchange_coin(call):
    user_id = call.from_user.id
    user_data = db_actions.get_user_data(user_id)
    
    if not user_data:
        bot.answer_callback_query(call.id, "Ошибка: пользователь не найден")
        return
        
    if user_data['bs_coin'] < 500:
        bot.answer_callback_query(call.id, "Недостаточно BS Coin! Нужно минимум 500")
        return
        
    db_actions.update_user_stats(user_id, 'bs_coin', -500)
    db_actions.update_user_stats(user_id, 'discount', 5)
    
    bot.answer_callback_query(call.id, "✅ Успешно! 500 BS Coin обменяны на 5% скидки")
    
    user_data = db_actions.get_user_data(user_id)
    buttons = Bot_inline_btns()
    
    achievements_str = ""
    if user_data['achievements']:
        icons = {"first_order": "🚀", "active_commentator": "💬", "referral_king": "👑"}
        achievements_str = "\n🏆 Достижения: " + " ".join(
            [icons.get(a, "🌟") for a in user_data['achievements']]
        )
    
    profile_msg = (
        f"👤 Ваш профиль:\n\n"
        f"🆔 ID: <code>{user_data['user_id']}</code>\n"
        f"🌟 Статус: {user_data['status']}\n"
        f"💬 Комментарии: {user_data['comments']}\n"
        f"📦 Заказы: {user_data['orders']}\n"
        f"🪙 BS Coin: {user_data['bs_coin']}\n"
        f"🎁 Скидка: {user_data['discount']}%\n"
        f"{achievements_str}"
    )
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=profile_msg,
        parse_mode="HTML",
        reply_markup=buttons.profile_buttons(user_data)
    )
    
    bot.send_message(user_id, "🎉 Поздравляем! Вы обменяли 500 BS Coin на 5% скидки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_coin_'))
def buy_product_with_coins(call):
    user_id = call.from_user.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    try:
        product_id = int(call.data.split('_')[2])
    except (IndexError, ValueError):
        bot.answer_callback_query(call.id, "❌ Ошибка: неверный формат запроса")
        return
        
    product = db_actions.get_product(product_id)
    user_data = db_actions.get_user_data(user_id)
    
    if not user_data:
        bot.answer_callback_query(call.id, "Сначала зарегистрируйтесь с помощью /start")
        return
    
    if not product or not product[10]:
        bot.answer_callback_query(call.id, "Товар не найден или не является эксклюзивным")
        return
    
    coin_price = product[4]
    
    if user_data['bs_coin'] < coin_price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно BS Coin!")
        
        buttons = Bot_inline_btns()
        markup = types.InlineKeyboardMarkup()
        
        btn1 = types.InlineKeyboardButton(
            text="💎 Получить BS Coin",
            callback_data="how_to_get_coins"
        )
        btn2 = types.InlineKeyboardButton(
            text="🔙 Назад к товару",
            callback_data=f"product_{product_id}"
        )
        markup.add(btn1, btn2)
        
        bot.send_message(
            user_id,
            f"❌ Недостаточно BS Coin для покупки!\n\n"
            f"💎 Нужно: {coin_price} BS Coin\n"
            f"💰 У вас: {user_data['bs_coin']} BS Coin\n"
            f"📊 Не хватает: {coin_price - user_data['bs_coin']} BS Coin\n\n"
            f"💡 Вы можете получить BS Coin через:\n"
            f"• Ежедневный бонус (/start)\n"
            f"• Реферальную систему (/ref)\n"
            f"• Активность в канале",
            reply_markup=markup
        )
        return
    
    db_actions.create_order(user_id, product_id, 1)
    db_actions.update_user_stats(user_id, 'bs_coin', -coin_price)
    db_actions.update_user_stats(user_id, 'orders', 1)
    
    if user_data['orders'] == 0:
        db_actions.add_achievement(user_id, "first_order")
        db_actions.update_user_stats(user_id, 'bs_coin', 50)
        bot.send_message(
            user_id,
            "🎉 Вы получили достижение «Первый заказ» +50 BS Coin!"
        )
    
    bot.answer_callback_query(call.id, "Заказ оформлен!")
    bot.send_message(
        user_id,
        f"✅ Ваш заказ оформлен! Списано {coin_price} BS Coin. Ожидайте информацию о доставке."
    )

@bot.callback_query_handler(func=lambda call: call.data == 'how_to_get_coins')
def how_to_get_coins(call):
    user_id = call.from_user.id
    user_data = db_actions.get_user_data(user_id)
    
    if not user_data:
        bot.answer_callback_query(call.id, "Сначала зарегистрируйтесь с помощью /start")
        return
    
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton(
        text="👥 Реферальная система",
        callback_data="ref_link"
    )
    markup.add(btn1)
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=f"💎 Способы получения BS Coin:\n\n"
            f"1. 🎁 Ежедневный бонус: +10 BS Coin каждый день (/start)\n"
            f"2. 👥 Реферальная система: +100 BS Coin за каждого приглашенного друга (/ref)\n"
            f"3. 💬 Активность в канале: комментируйте посты и получайте монеты\n"
            f"4. 🏆 Достижения: выполняйте задания и получайте бонусы\n\n"
            f"💰 Ваш текущий баланс: {user_data['bs_coin']} BS Coin",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'ref_link')
def ref_link(call):
    user_id = call.message.chat.id
    if not is_subscribed(user_id):
        show_subscription_request(user_id)
        return
        
    user_data = db_actions.get_user_data(user_id)
    
    if not user_data:
        bot.answer_callback_query(call.id, "Сначала зарегистрируйтесь с помощью /start")
        return
        
    ref_count = db_actions.get_referral_stats(user_id)
    ref_link = f"https://t.me/{bot.get_me().username}?start={user_data['referral_code']}"
    
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton(
        text="💎 Вернуться к товарам",
        callback_data="back_to_catalog"
    )
    markup.add(btn1)
    
    ref_msg = (
        f"👥 Реферальная система\n\n"
        f"Приглашайте друзей и получайте бонусы!\n\n"
        f"🔗 Ваша реферальная ссылка:\n{ref_link}\n\n"
        f"• За каждого приглашенного друга вы получаете 100 BS Coin\n"
        f"• Ваш друг получает 50 BS Coin при первом заказе\n\n"
        f"🚀 Приглашено друзей: {ref_count}\n"
        f"💰 Заработано: {ref_count * 100} BS Coin"
    )
    
    try:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=call.message.message_id,
            text=ref_msg,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            user_id,
            ref_msg,
            parse_mode="HTML",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data in ['exclusive_yes', 'exclusive_no'])
def process_exclusive(call):
    user_id = call.from_user.id
    is_exclusive = (call.data == 'exclusive_yes')
    
    if user_id not in temp_data or temp_data[user_id]['step'] != 'ask_exclusive':
        bot.answer_callback_query(call.id, "Ошибка процесса. Начните заново.")
        return
    
    temp_data[user_id]['is_exclusive'] = is_exclusive
    temp_data[user_id]['step'] = 'ask_coin_price' if is_exclusive else 'ready_to_save'
    
    if is_exclusive:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=call.message.message_id,
            text="💎 Укажите цену в BS Coin:"
        )
        bot.register_next_step_handler(call.message, process_coin_price)
    else:
        product_data = temp_data[user_id]
        product_id = db_actions.add_product(
            name=product_data['name'],
            description=product_data['description'],
            price=product_data['price'],
            photo_id=product_data['photo_id'],
            is_exclusive=False,
            coin_price=0
        )
        
        product_data['product_id'] = product_id
        post_link = publish_product_to_channel(product_data)
        
        if post_link:
            bot.send_message(
                user_id,
                f"✅ Товар успешно добавлен и опубликован!\nСсылка: {post_link}",
                disable_web_page_preview=True
            )
        else:
            bot.send_message(user_id, "✅ Товар добавлен, но не опубликован")
        
        if user_id in temp_data:
            del temp_data[user_id]

def process_coin_price(message):
    user_id = message.from_user.id
    
    if user_id not in temp_data or temp_data[user_id]['step'] != 'ask_coin_price':
        bot.send_message(user_id, "Ошибка процесса. Начните заново.")
        return
    
    try:
        coin_price = int(message.text)
        if coin_price <= 0:
            raise ValueError("Цена должна быть положительной")
        
        product_data = temp_data[user_id]
        product_id = db_actions.add_product(
            name=product_data['name'],
            description=product_data['description'],
            price=product_data['price'],
            photo_id=product_data['photo_id'],
            is_exclusive=True,
            coin_price=coin_price
        )
        
        if not product_id:
            raise Exception("Не удалось добавить товар в базу данных")
        
        product_data['product_id'] = product_id
        product_data['coin_price'] = coin_price
        product_data['is_exclusive'] = True
        post_link = publish_product_to_channel(product_data)
        
        if post_link:
            bot.send_message(
                user_id,
                f"✅ Эксклюзивный товар успешно добавлен и опубликован!\nСсылка: {post_link}",
                disable_web_page_preview=True
            )
        else:
            bot.send_message(user_id, "✅ Товар добавлен, но не опубликован")
        
        if user_id in temp_data:
            del temp_data[user_id]
            
    except ValueError:
        bot.send_message(user_id, "❌ Неверный формат цены. Используйте только целые числа.")
        msg = bot.send_message(user_id, "💎 Укажите цену в BS Coin:")
        bot.register_next_step_handler(msg, process_coin_price)
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка при добавлении товара: {str(e)}")
        if user_id in temp_data:
            del temp_data[user_id]

def publish_product_to_channel(product):
    try:
        if not product.get('product_id'):
            print("Ошибка: product_id не определен")
            return None
            
        config_data = config.get_config()
        chat_id = config_data['chat_id']
        topic_id = config_data['topics']['магазин']
        
        deep_link = f"https://t.me/{bot.get_me().username}?start=product_{product['product_id']}"
        
        markup = types.InlineKeyboardMarkup()
        buy_btn = types.InlineKeyboardButton(text="🛒 Купить", url=deep_link)
        markup.add(buy_btn)
        
        if product.get('is_exclusive'):
            caption = (
                f"🎯 ЭКСКЛЮЗИВНЫЙ ТОВАР\n\n"
                f"🛍️ {product['name']}\n\n"
                f"📝 {product['description']}\n"
                f"💎 Цена: {product['coin_price']} BS Coin\n\n"
                f"👉 Нажмите «🛒 Купить» для заказа через бота"
            )
        else:
            caption = (
                f"🛍️ {product['name']}\n\n"
                f"📝 {product['description']}\n"
                f"💰 Цена: {product['price']}₽\n\n"
                f"👉 Нажмите «🛒 Купить» для заказа через бота"
            )
        
        message = bot.send_photo(
            chat_id=chat_id,
            photo=product['photo_id'],
            caption=caption,
            reply_markup=markup,
            message_thread_id=topic_id
        )
        
        channel_id = str(abs(chat_id))
        return f"https://t.me/c/{channel_id}/{message.message_id}?thread={topic_id}"
    
    except Exception as e:
        print(f"Ошибка публикации: {e}")
        return None

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_product_'))
def select_product_for_post(call):
    user_id = call.from_user.id
    product_id = int(call.data.split('_')[2])
    
    if user_id not in temp_data or temp_data[user_id]['step'] != 'select_product':
        bot.answer_callback_query(call.id, "❌ Ошибка процесса")
        return
        
    product = db_actions.get_product(product_id)
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар не найден")
        return
        
    temp_data[user_id]['product_id'] = product_id
    temp_data[user_id]['step'] = 'add_photos'
    temp_data[user_id]['product_name'] = product[1]
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=f"📦 Выбран товар: {product[1]}\n\n"
            f"📸 Теперь отправьте до 6 фотографий товара\n"
            f"📝 После отправки фото напишите текст для поста\n"
            f"❌ Отправьте /cancel для отмены"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data in ['exclusive_yes_post', 'exclusive_no_post'])
def handle_exclusive_post(call):
    user_id = call.from_user.id
    is_exclusive = (call.data == 'exclusive_yes_post')
    
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Ошибка процесса")
        return
        
    product_id = temp_data[user_id]['product_id']
    product = db_actions.get_product(product_id)
    
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар не найден")
        return
    
    if is_exclusive:
        temp_data[user_id]['step'] = 'ask_coin_price_post'
        bot.edit_message_text(
            chat_id=user_id,
            message_id=call.message.message_id,
            text="💎 Укажите цену в BS Coin:"
        )
    else:
        db_actions.update_product_exclusive(product_id, False, 0)
        success = publish_post_to_channel(
            product_id,
            temp_data[user_id]['photos'],
            temp_data[user_id]['text'],
            False,
            0
        )
        
        if success:
            bot.answer_callback_query(call.id, "✅ Пост опубликован!")
            bot.edit_message_text(
                chat_id=user_id,
                message_id=call.message.message_id,
                text=f"✅ Товар успешно опубликован в @BridgeSide_Store\n\n"
                    f"🛍️ Товар: {temp_data[user_id]['product_name']}\n"
                    f"🎯 Статус: Обычный (рубли)\n"
                    f"💰 Цена: {product[3]}₽"
            )
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка публикации")
            
        if user_id in temp_data:
            del temp_data[user_id]

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('step') == 'ask_coin_price_post')


def handle_coin_price_input(message):
    user_id = message.from_user.id
    
    if message.text.lower() == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    process_coin_price_post(message)

def process_coin_price_post(message):
    user_id = message.from_user.id
    
    if user_id not in temp_data or temp_data[user_id]['step'] != 'ask_coin_price_post':
        bot.send_message(user_id, "❌ Ошибка процесса. Начните заново.")
        return
        
    if message.text.lower() == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    try:
        coin_price = int(message.text)
        if coin_price <= 0:
            raise ValueError("Цена должна быть положительной")
            
        product_id = temp_data[user_id]['product_id']
        product = db_actions.get_product(product_id)
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            return
            
        db_actions.update_product_exclusive(product_id, True, coin_price)
        
        success = publish_post_to_channel(
            product_id,
            temp_data[user_id]['photos'],
            temp_data[user_id]['text'],
            True,
            coin_price
        )
        
        if success:
            bot.send_message(
                user_id,
                f"✅ Товар успешно опубликован в @BridgeSide_Store\n\n"
                f"🛍️ Товар: {temp_data[user_id]['product_name']}\n"
                f"🎯 Статус: Эксклюзивный\n"
                f"💎 Цена: {coin_price} BS Coin"
            )
        else:
            bot.send_message(user_id, "❌ Ошибка при публикации поста")
        
    except ValueError:
        bot.send_message(user_id, "❌ Неверный формат цены. Используйте только целые числа.")
        bot.send_message(user_id, "💎 Укажите цену в BS Coin:")
        return
    except Exception as e:
        print(f"Ошибка при публикации: {e}")
        bot.send_message(user_id, f"❌ Ошибка при публикации: {str(e)}")
    finally:
        if user_id in temp_data:
            del temp_data[user_id]

def publish_post_to_channel(product_id, photos, text, is_exclusive, coin_price=0):
    try:
        product = db_actions.get_product(product_id)
        if not product:
            print("❌ Товар не найден")
            return False
            
        config_data = config.get_config()
        channel_id = config_data.get('store_channel_id', '@BridgeSide_Store')
        
        if not channel_id:
            print("❌ Не указан channel_id в конфиге")
            return False
        
        deep_link = f"https://t.me/{bot.get_me().username}?start=product_{product_id}"
        

        markup = types.InlineKeyboardMarkup()
        buy_btn = types.InlineKeyboardButton("🛒 Купить", url=deep_link)
        markup.add(buy_btn)
        

        if not is_exclusive:
            price_text = f"💰 {product[3]}₽"
        else:
            price_text = f"💎 {coin_price} BS Coin"
        
        caption = (
            f"{text}\n\n"
            f"{price_text}\n\n"
            f"👉 Нажмите «🛒 Купить» для заказа через бота"
        )
        
        if photos and len(photos) > 0:
            bot.send_photo(
                chat_id=channel_id,
                photo=photos[0],
                caption=caption,
                reply_markup=markup
            )
            
            for photo in photos[1:]:
                bot.send_photo(
                    chat_id=channel_id,
                    photo=photo
                )
        else:
            bot.send_message(
                chat_id=channel_id,
                text=caption,
                reply_markup=markup
            )
            
        return True
        
    except Exception as e:
        print(f"Ошибка публикации в канал: {e}")
        return False
    
@bot.callback_query_handler(func=lambda call: call.data.startswith(('buy_', 'order_now_')))
def handle_buy(call):
    user_id = call.from_user.id
    
    try:
        if call.data.startswith('buy_coin_'):
            parts = call.data.split('_')
            product_id = int(parts[2])
        else:
            parts = call.data.split('_')
            product_id = int(parts[1])
            size = parts[3] if len(parts) > 3 else None
        

        if user_id not in temp_data:
            temp_data[user_id] = {}
        
        temp_data[user_id]['order'] = {
            'product_id': product_id,
            'step': 'ask_delivery'
        }
        

        delivery_form = (
            "📦 ДЛЯ ОФОРМЛЕНИЯ ЗАКАЗА\n\n"
            "Пожалуйста, заполните данные доставки:\n\n"
            "🏙️ Город:\n"
            "📍 Полный адрес (улица, дом, квартира):\n"
            "👤 ФИО получателя:\n"
            "📞 Номер телефона:\n"
            "🚚 Предпочтительный способ доставки (Почта России/СДЭК):\n\n"
            "Отправьте все данные ОДНИМ сообщением в указанном порядке."
        )
        
        bot.send_message(user_id, delivery_form)
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        print(f"Ошибка обработки покупки: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка оформления заказа")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_order_', 'reject_order_')))
def handle_order_approval(call):
    try:
        admin_id = call.from_user.id
        if not db_actions.user_is_admin(admin_id):
            bot.answer_callback_query(call.id, "⛔️ Недостаточно прав")
            return
            
        action = call.data.split('_')[0]
        order_id = int(call.data.split('_')[2])
        
        order_data = db_actions.get_order_by_id(order_id)
        if not order_data:
            bot.answer_callback_query(call.id, "❌ Заказ не найден")
            return
            
        user_data = db_actions.get_user_data(order_data['user_id'])
        product = db_actions.get_product(order_data['product_id'])
        
        if action == 'approve':

            db_actions.update_order_status(order_id, "✅ ПОДТВЕРЖДЕН")
            

            current_text = call.message.caption if call.message.caption else call.message.text
            updated_text = f"{current_text}\n\n📊 Статус: ✅ ПОДТВЕРЖДЕН\n👨‍💼 Подтвердил: @{call.from_user.username}"
            
            try:
                if call.message.photo:
                    bot.edit_message_caption(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        caption=updated_text,
                        reply_markup=None
                    )
                else:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=updated_text,
                        reply_markup=None
                    )
            except Exception as e:
                print(f"Ошибка редактирования сообщения: {e}")
            

            bot.send_message(
                order_data['user_id'],
                f"🎉 Ваш заказ #{order_id} подтвержден!\n\n"
                f"🛍️ Товар: {product[1]}\n"
                f"💰 Сумма: {product[3]}₽\n\n"
                f"📦 Заказ передан в обработку. Ожидайте информацию о доставке."
            )
            
            bot.answer_callback_query(call.id, "✅ Заказ подтвержден")
            
        else:
        
            markup = types.InlineKeyboardMarkup()
            reason_btn = types.InlineKeyboardButton(
                "📝 Указать причину отклонения", 
                callback_data=f"reject_reason_{order_id}"
            )
            markup.add(reason_btn)
            

            current_text = call.message.caption if call.message.caption else call.message.text
            updated_text = f"{current_text}\n\n📊 Статус: ❌ ОТКЛОНЕН (укажите причину)\n👨‍💼 Отклонил: @{call.from_user.username}"
            
            try:
                if call.message.photo:
                    bot.edit_message_caption(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        caption=updated_text,
                        reply_markup=markup
                    )
                else:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=updated_text,
                        reply_markup=markup
                    )
            except Exception as e:
                print(f"Ошибка редактирования сообщения: {e}")
            
            bot.answer_callback_query(call.id, "📝 Укажите причину в топике")
            
    except Exception as e:
        print(f"Ошибка обработки подтверждения заказа: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_reason_'))
def ask_reject_reason(call):
    """Запрос причины отклонения в топике"""
    try:
        order_id = int(call.data.split('_')[2])
        
        bot.answer_callback_query(
            call.id, 
            "💬 Ответьте на это сообщение текстом с причиной отклонения", 
            show_alert=True
        )
        
    except Exception as e:
        print(f"Ошибка запроса причины: {e}")

@bot.message_handler(func=lambda message: message.reply_to_message)
def handle_reply_in_topic(message):

    try:
        replied_message = message.reply_to_message
        

        replied_text = ""
        if replied_message.caption:
            replied_text = replied_message.caption
        elif replied_message.text:
            replied_text = replied_message.text
        else:

            return
        

        if replied_text and "ЗАКАЗ #" in replied_text and "❌ ОТКЛОНЕН" in replied_text:

            import re
            order_id_match = re.search(r'ЗАКАЗ #(\d+)', replied_text)
            if order_id_match:
                order_id = int(order_id_match.group(1))
                reason = message.text
                

                db_actions.update_order_status(order_id, f"❌ ОТКЛОНЕН: {reason}")
                

                order_info = db_actions.get_order_by_id(order_id)
                if order_info:
                    user_data = db_actions.get_user_data(order_info['user_id'])
                    product = db_actions.get_product(order_info['product_id'])
                    

                    if user_data and product:
                        try:
                            bot.send_message(
                                order_info['user_id'],
                                f"❌ Ваш заказ #{order_id} отклонен\n\n"
                                f"🛍️ Товар: {product[1]}\n"
                                f"💰 Сумма: {product[3]}₽\n\n"
                                f"📝 Причина: {reason}\n\n"
                                f"💬 Если у вас есть вопросы, обратитесь в поддержку."
                            )
                            

                            if product[10]:
                                db_actions.update_user_stats(order_info['user_id'], 'bs_coin', product[4])
                                bot.send_message(
                                    order_info['user_id'],
                                    f"💎 Вам возвращено {product[4]} BS Coin"
                                )
                        except Exception as e:
                            print(f"Ошибка уведомления пользователя: {e}")
                

                try:
                    bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass
                

                try:
                    bot.send_message(
                        message.chat.id,
                        f"✅ Причина отклонения заказа #{order_id} сохранена",
                        reply_to_message_id=replied_message.message_id
                    )
                except:
                    pass
                
    except Exception as e:
        print(f"Ошибка обработки ответа: {e}")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    'reject_reason' in temp_data[message.from_user.id] and
    message.chat.id == temp_data[message.from_user.id]['reject_reason']['chat_id'])
def process_reject_reason_in_topic(message):
    """Обработка причины отклонения в топике"""
    try:
        admin_id = message.from_user.id
        reason_data = temp_data[admin_id]['reject_reason']
        order_id = reason_data['order_id']
        reason = message.text
        
        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            print(f"Заказ {order_id} не найден")
            return
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        if not user_data or not product:
            print("Данные пользователя или товара не найдены")
            return
        

        db_actions.update_order_status(order_id, f"❌ ОТКЛОНЕН: {reason}")
        

        updated_text = (
            f"🛒 ЗАКАЗ #{order_id} ❌ ОТКЛОНЕН\n\n"
            f"👤 Клиент: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 @{user_data['username']}\n"
            f"🛍️ Товар: {product[1]}\n"
            f"💰 Цена: {product[3]}₽\n\n"
            f"📦 ДАННЫЕ ДОСТАВКИ:\n"
            f"🏙️ Город: {order_info.get('city', 'Не указан')}\n"
            f"📍 Адрес: {order_info.get('address', 'Не указан')}\n"
            f"👤 ФИО: {order_info.get('full_name', 'Не указан')}\n"
            f"📞 Телефон: {order_info.get('phone', 'Не указан')}\n"
            f"🚚 Способ: {order_info.get('delivery_type', 'Не указан')}\n\n"
            f"📝 Причина отклонения: {reason}\n"
            f"👨‍💼 Отклонил: @{message.from_user.username}\n"
            f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        try:
            if reason_data['is_photo']:
                bot.edit_message_caption(
                    chat_id=reason_data['chat_id'],
                    message_id=reason_data['message_id'],
                    caption=updated_text,
                    reply_markup=None
                )
            else:
                bot.edit_message_text(
                    chat_id=reason_data['chat_id'],
                    message_id=reason_data['message_id'],
                    text=updated_text,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка редактирования сообщения: {e}")
        
        try:
            bot.send_message(
                order_info['user_id'],
                f"❌ Ваш заказ #{order_id} отклонен\n\n"
                f"🛍️ Товар: {product[1]}\n"
                f"💰 Сумма: {product[3]}₽\n\n"
                f"📝 Причина: {reason}\n\n"
                f"💬 Если у вас есть вопросы, обратитесь в поддержку."
            )
            
            if product[10]:  # is_exclusive
                db_actions.update_user_stats(order_info['user_id'], 'bs_coin', product[4])
                bot.send_message(
                    order_info['user_id'],
                    f"💎 Вам возвращено {product[4]} BS Coin"
                )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        if admin_id in temp_data and 'reject_reason' in temp_data[admin_id]:
            del temp_data[admin_id]['reject_reason']
        
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
            
    except Exception as e:
        print(f"Ошибка обработки причины отклонения: {e}")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    'reject_order' in temp_data[message.from_user.id])
def handle_reject_reason(message):
    """Обработка причины отклонения заказа"""
    try:
        admin_id = message.from_user.id
        order_data = temp_data[admin_id]['reject_order']
        order_id = order_data['order_id']
        
        reason = message.text
        order_info = db_actions.get_order_by_id(order_id)
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        try:
            if order_data['is_photo']:
                msg_info = bot.get_message(order_data['chat_id'], order_data['message_id'])
                current_text = msg_info.caption
            else:
                msg_info = bot.get_message(order_data['chat_id'], order_data['message_id'])
                current_text = msg_info.text
        except:
            current_text = "Информация о заказе"
        
        updated_text = f"{current_text}\n\n📊 Статус: ❌ ОТКЛОНЕН\n📝 Причина: {reason}\n👨‍💼 Отклонил: @{message.from_user.username}"
        
        try:
            if order_data['is_photo']:
                bot.edit_message_caption(
                    chat_id=order_data['chat_id'],
                    message_id=order_data['message_id'],
                    caption=updated_text,
                    reply_markup=None
                )
            else:
                bot.edit_message_text(
                    chat_id=order_data['chat_id'],
                    message_id=order_data['message_id'],
                    text=updated_text,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка редактирования сообщения: {e}")
        
        bot.send_message(
            order_info['user_id'],
            f"❌ Ваш заказ #{order_id} отклонен\n\n"
            f"🛍️ Товар: {product[1]}\n"
            f"💰 Сумма: {product[3]}₽\n\n"
            f"📝 Причина: {reason}\n\n"
            f"💬 Если у вас есть вопросы, обратитесь в поддержку."
        )

        if product[10]:  # is_exclusive
            db_actions.update_user_stats(order_info['user_id'], 'bs_coin', product[4])
            bot.send_message(
                order_info['user_id'],
                f"💎 Вам возвращено {product[4]} BS Coin"
            )
        
        bot.send_message(admin_id, "✅ Заказ отклонен, пользователь уведомлен")
        
        del temp_data[admin_id]['reject_order']
        
    except Exception as e:
        print(f"Ошибка обработки причины отклонения: {e}")
        bot.send_message(admin_id, "❌ Ошибка обработки")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_delivery')
def process_delivery_info(message):
    user_id = message.from_user.id
    product_id = temp_data[user_id]['order']['product_id']
    product = db_actions.get_product(product_id)
    
    try:
        delivery_data = message.text.strip().split('\n')
        
        if len(delivery_data) < 5:
            bot.send_message(user_id, "❌ Пожалуйста, заполните ВСЕ поля полностью")
            return
        
        city = delivery_data[0].replace('Город:', '').replace('город:', '').strip()
        address = delivery_data[1].replace('Адрес:', '').replace('адрес:', '').strip()
        full_name = delivery_data[2].replace('ФИО:', '').replace('фио:', '').strip()
        phone = delivery_data[3].replace('Телефон:', '').replace('номер:', '').strip()
        delivery_type = delivery_data[4].replace('Способ доставки:', '').replace('доставка:', '').strip()
        
        temp_data[user_id]['order'].update({
            'city': city,
            'address': address,
            'full_name': full_name,
            'phone': phone,
            'delivery_type': delivery_type,
            'step': 'ask_payment'
        })
        
        bot.send_message(
            user_id,
            "✅ Данные доставки получены!\n\n"
            f"Необходимо произвести оплату на сумму {product[3]}₽ по номеру - +79123456789\n"
            "📸 Теперь пришлите фото подтверждения оплаты\n"
            "(скриншот перевода или чека)"
        )
        
    except Exception as e:
        print(f"Ошибка обработки данных доставки: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки данных. Попробуйте еще раз.")

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: 
                    message.from_user.id in temp_data and 
                    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_payment')
def process_payment_photo(message):
    user_id = message.from_user.id
    
    try:
        payment_photo_id = message.photo[-1].file_id
        temp_data[user_id]['order']['payment_photo'] = payment_photo_id
        temp_data[user_id]['order']['step'] = 'confirm_order'
        
        product_id = temp_data[user_id]['order']['product_id']
        product = db_actions.get_product(product_id)
        
        if product:
            order_summary = (
                f"✅ ВСЕ ДАННЫЕ ПОЛУЧЕНЫ!\n\n"
                f"📋 Ваш заказ:\n"
                f"🛍️ Товар: {product[1]}\n"
                f"💰 Цена: {product[3]}₽\n\n"
                f"📦 Доставка:\n"
                f"🏙️ Город: {temp_data[user_id]['order']['city']}\n"
                f"📍 Адрес: {temp_data[user_id]['order']['address']}\n"
                f"👤 ФИО: {temp_data[user_id]['order']['full_name']}\n"
                f"📞 Телефон: {temp_data[user_id]['order']['phone']}\n"
                f"🚚 Способ: {temp_data[user_id]['order']['delivery_type']}\n\n"
                f"📸 Фото оплаты приложено\n\n"
                f"Нажмите кнопку ниже для подтверждения заказа 👇"
            )
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            confirm_btn = types.KeyboardButton("✅ Подтвердить заказ")
            markup.add(confirm_btn)
            
            bot.send_photo(user_id, payment_photo_id, caption="📸 Ваше фото оплаты:")
            bot.send_message(user_id, order_summary, reply_markup=markup)
        
    except Exception as e:
        print(f"Ошибка обработки фото оплаты: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки фото. Попробуйте еще раз.")


@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_delivery')
def process_delivery_info(message):
    user_id = message.from_user.id
    
    try:
        delivery_data = message.text.strip().split('\n')
        
        if len(delivery_data) < 5:
            bot.send_message(user_id, "❌ Пожалуйста, заполните все поля полностью")
            return
        
        city = delivery_data[0].strip()
        address = delivery_data[1].strip()
        full_name = delivery_data[2].strip()
        phone = delivery_data[3].strip()
        delivery_type = delivery_data[4].strip()
        
        valid_delivery_types = ['почта россии', 'сдэк', 'почта', 'сдек']
        if delivery_type.lower() not in valid_delivery_types:
            bot.send_message(user_id, "❌ Укажите 'Почта России' или 'СДЭК' как способ доставки")
            return
        
        if delivery_type.lower() in ['почта россии', 'почта']:
            delivery_type = 'Почта России'
        else:
            delivery_type = 'СДЭК'
        
        temp_data[user_id]['order'].update({
            'city': city,
            'address': address,
            'full_name': full_name,
            'phone': phone,
            'delivery_type': delivery_type,
            'step': 'confirm_order'
        })
        
        product_id = temp_data[user_id]['order']['product_id']
        product = db_actions.get_product(product_id)
        
        if product:
            price = product[4] if product[10] else product[3]
            currency = 'BS Coin' if product[10] else '₽'
            
            order_summary = (
                f"✅ Данные получены! Проверьте заказ:\n\n"
                f"🛍️ Товар: {product[1]}\n"
                f"📏 Размер: {temp_data[user_id]['order']['size']}\n"
                f"💰 Цена: {price} {currency}\n\n"
                f"📦 Доставка:\n"
                f"🏙️ Город: {city}\n"
                f"📍 Адрес: {address}\n"
                f"👤 ФИО: {full_name}\n"
                f"📞 Телефон: {phone}\n"
                f"🚚 Способ: {delivery_type}\n\n"
                f"Для подтверждения отправьте '✅ Подтвердить'"
            )
            
            bot.send_message(user_id, order_summary)
        
    except Exception as e:
        print(f"Ошибка обработки данных доставки: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки данных. Попробуйте еще раз.")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_review_', 'reject_review_')))
def handle_review_moderation(call):
    try:
        parts = call.data.split('_')
        action = parts[0]
        user_id = int(parts[2])
        
        review_key = None
        review_data = None
        
        for key in list(pending_reviews.keys()):
            if key.startswith(f"{user_id}_"):
                review_key = key
                review_data = pending_reviews[key]
                break
        
        if not review_data:
            bot.answer_callback_query(call.id, "❌ Данные отзыва не найдены или устарели")
            return
            
        if action == 'approve':
            photos_json = json.dumps(review_data.get('photos', [])) if review_data.get('photos') else None
            db_actions.add_review(
                user_id, 
                review_data['text'], 
                photos_json
            )
            
            publish_review_to_channel(user_id, review_data)
            
            bot.answer_callback_query(call.id, "✅ Отзыв одобрен")
            bot.send_message(
                user_id,
                "🎉 Ваш отзыв одобрен и опубликован в @BridgeSide_Featback!"
            )
            
        else:
            bot.answer_callback_query(call.id, "❌ Отзыв отклонен")
            bot.send_message(
                user_id,
                "❌ Ваш отзыв не прошел модерацию. Пожалуйста, проверьте его и отправьте еще раз."
            )
            
    except Exception as e:
        print(f"Ошибка в модерации: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при обработке")

@bot.callback_query_handler(func=lambda call: call.data == 'start_review')
def start_review(call):
    user_id = call.from_user.id
    
    if user_id not in temp_data:
        temp_data[user_id] = {}
    
    temp_data[user_id]['step'] = 'writing_review'
    temp_data[user_id]['photos'] = []
    
    bot.send_message(
        user_id,
        "📝 Напишите ваш отзыв. Вы можете:\n"
        "• Написать текст отзыва\n"
        "• Прикрепить до 3 фотографий\n"
        "• Отправить /done для завершения\n"
        "• Отправить /cancel для отмены"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_size_'))
def select_size(call):
    user_id = call.from_user.id
    product_id = int(call.data.split('_')[2])
    
    product = db_actions.get_product_with_variations(product_id)
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return
        
    buttons = Bot_inline_btns()
    markup = buttons.size_selection_buttons(product['variations'])
    
    bot.edit_message_reply_markup(
        chat_id=user_id,
        message_id=call.message.message_id,
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith(('size_', 'size_coin_')))
def handle_size_selection(call):
    user_id = call.from_user.id
    parts = call.data.split('_')
    
    is_exclusive = parts[0] == 'size_coin'
    
    if is_exclusive:
        product_id = int(parts[2])
        size = parts[3]
    else:
        product_id = int(parts[1])
        size = parts[2]
    
    print(f"DEBUG: Выбран размер - product_id: {product_id}, size: '{size}', exclusive: {is_exclusive}")
    
    if not db_actions.check_size_availability(product_id, size):
        bot.answer_callback_query(call.id, "❌ Этот размер недоступен")
        return
    
    if user_id not in temp_data:
        temp_data[user_id] = {}
    
    temp_data[user_id]['selected_product'] = product_id
    temp_data[user_id]['selected_size'] = size
    temp_data[user_id]['is_exclusive'] = is_exclusive
    
    product = db_actions.get_product(product_id)
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар не найден")
        return
    
    buttons = Bot_inline_btns()
    
    if is_exclusive:
        markup = types.InlineKeyboardMarkup()
        buy_btn = types.InlineKeyboardButton(
            text=f"💎 Купить за {product[4]} BS Coin",
            callback_data=f"buy_coin_{product_id}_{size}"
        )
        markup.add(buy_btn)
    else:
        markup = buttons.order_now_button(product_id, size)
        print(f"DEBUG: Created order_now button for product {product_id}, size {size}")
    
    try:
        if call.message.caption:
            bot.edit_message_caption(
                chat_id=user_id,
                message_id=call.message.message_id,
                caption=call.message.caption,
                reply_markup=markup
            )
        else:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=call.message.message_id,
                text=call.message.text,
                reply_markup=markup
            )
        
        bot.answer_callback_query(call.id, f"✅ Выбран размер: {size}")
        
    except Exception as e:
        print(f"Ошибка редактирования сообщения: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка выбора размера")

@bot.callback_query_handler(func=lambda call: True)
def debug_all_callbacks(call):
    print(f"DEBUG CALLBACK RECEIVED: {call.data} from user {call.from_user.id}")

# ============ ОБРАБОТЧИКИ СООБЩЕНИЙ ============

@bot.message_handler(content_types=['text', 'photo'])
def handle_messages(message):
    user_id = message.from_user.id
    
    if user_id in temp_data and temp_data[user_id].get('step') in ['add_photos', 'add_text']:
        handle_post_creation(message)
        return
        
    if user_id in temp_data and temp_data[user_id].get('step') == 'writing_review':
        handle_review(message)
        return
        
    if user_id in temp_data and temp_data[user_id].get('step') == 'waiting_delivery_info':
        process_delivery_info(message)
        return

def handle_post_creation(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    if temp_data[user_id]['step'] == 'add_photos':
        if message.content_type == 'photo':
            if len(temp_data[user_id]['photos']) < 6:
                temp_data[user_id]['photos'].append(message.photo[-1].file_id)
                remaining = 6 - len(temp_data[user_id]['photos'])
                bot.send_message(user_id, f"📸 Фото добавлено. Осталось: {remaining}")
            else:
                bot.send_message(user_id, "❌ Максимум 6 фотографий. Отправьте текст поста")
        elif message.content_type == 'text':
            temp_data[user_id]['step'] = 'add_text'
            temp_data[user_id]['text'] = message.text
            ask_exclusive_status(user_id)
            
    elif temp_data[user_id]['step'] == 'add_text':
        temp_data[user_id]['text'] = message.text
        ask_exclusive_status(user_id)

def handle_review(message):
    user_id = message.from_user.id
    
    review_data = temp_data[user_id]
    
    if message.content_type == 'photo':
        if 'photos' not in review_data:
            review_data['photos'] = []
            
        if len(review_data['photos']) < 3:
            review_data['photos'].append(message.photo[-1].file_id)
            remaining = 3 - len(review_data['photos'])
            bot.send_message(user_id, f"📸 Фото добавлено. Можно добавить еще {remaining} фото или отправьте /done для завершения")
        else:
            bot.send_message(user_id, "❌ Можно прикрепить не более 3 фотографий. Отправьте /done для завершения")
            
    elif message.content_type == 'text':
        text = message.text.strip()
        
        if text.lower() == '/done':
            if 'text' not in review_data or not review_data['text']:
                bot.send_message(user_id, "❌ Сначала напишите текст отзыва")
                return
                
            send_review_for_moderation(user_id, review_data)
            
            if user_id in temp_data:
                del temp_data[user_id]
                
            bot.send_message(user_id, "✅ Отзыв отправлен на модерацию! Ожидайте решения администратора.")
            
        elif text.lower() == '/cancel':
            if user_id in temp_data:
                del temp_data[user_id]
            bot.send_message(user_id, "❌ Создание отзыва отменено")
            
        else:
            review_data['text'] = text
            photos_count = len(review_data.get('photos', []))
            remaining_photos = 3 - photos_count
            
            if photos_count > 0:
                bot.send_message(
                    user_id, 
                    f"✅ Текст отзыва сохранен. Прикреплено фото: {photos_count}/3. "
                    f"Можете добавить еще {remaining_photos} фото или отправьте /done для завершения"
                )
            else:
                bot.send_message(
                    user_id, 
                    f"✅ Текст отзыва сохранен. Можете прикрепить до {remaining_photos} фото или отправьте /done для завершения"
                )

def process_delivery_info(message):
    user_id = message.from_user.id
    delivery_info = message.text
    
    send_order_to_admin(user_id, delivery_info)
    
    bot.send_message(
        user_id,
        "✅ Ваш заказ принят! Ожидайте подтверждения."
    )
    
    if user_id in temp_data:
        del temp_data[user_id]

@bot.channel_post_handler(content_types=['text'])
def handle_channel_post(message):
    if message.reply_to_message:
        user_id = message.from_user.id
        
        db_actions.update_user_stats(user_id, 'comments', 1)
        
        check_comment_achievement(user_id)

@bot.message_handler(content_types=['text'], func=lambda message: message.is_topic_message)
def handle_topic_messages(message):
    user_id = message.from_user.id
    
    db_actions.update_user_stats(user_id, 'comments', 1)
    
    check_comment_achievement(user_id)

# ============ ЗАПУСК БОТА ============

if __name__ == '__main__':
    print("Бот запущен...")
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")
        print("Трассировка ошибки:")
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
        traceback.print_exc()