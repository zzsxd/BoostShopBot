# main.py
import time
import telebot
import os
import re
import json
import threading
import platform
from threading import Lock
from datetime import datetime, timedelta
from config_parser import ConfigParser
from frontend import Bot_inline_btns
from telebot import types
from backend import DbAct
from db import DB

temp_data = {}
config_name = 'secrets.json'

def main():
    config_data = config.get_config()
    channel_username = config_data.get('channel_username', 'BoostShop_Community')
    
    def is_subscribed(user_id):
        try:
            member = bot.get_chat_member(chat_id=f"@{channel_username}", user_id=user_id)
            return member.status in ['member', 'administrator', 'creator']
        except Exception as e:
            print(f"Ошибка проверки подписки: {e}")
            return False
    
    def show_subscription_request(user_id):
        buttons = Bot_inline_btns()
        markup = types.InlineKeyboardMarkup()
        channel_btn = types.InlineKeyboardButton("🔥 ПОДПИСАТЬСЯ", url=f"https://t.me/+JrjbQb9-HtcxOWUy")
        check_btn = types.InlineKeyboardButton("✅ Я ПОДПИСАЛСЯ", callback_data="check_subscription")
        markup.add(channel_btn, check_btn)
        bot.send_message(
            user_id,
            "📢 Для использования бота, подпишитесь на наш канал:",
            reply_markup=markup
        )

    def show_product(user_id, product_id):
        product = db_actions.get_product(product_id)
        if not product:
            bot.send_message(user_id, "Товар не найден")
            return
        
        buttons = Bot_inline_btns()
        
        if product[9]:  # is_exclusive
            caption = (
                f"🎯 ЭКСКЛЮЗИВНЫЙ ТОВАР\n\n"
                f"🛍️ {product[1]}\n\n"
                f"📝 {product[2]}\n"
                f"💎 Цена: {product[4]} BS Coin"
            )
            markup = buttons.product_detail_buttons(product_id, True, product[4])
        else:
            caption = (
                f"🛍️ {product[1]}\n\n"
                f"📝 {product[2]}\n"
                f"💰 Цена: {product[3]}₽"
            )
            markup = buttons.product_detail_buttons(product_id, False)
        
        bot.send_photo(
            user_id,
            product[5],  # photo_id
            caption=caption,
            reply_markup=markup
        )
    
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
        
        # пользователь еще не получал бонус
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

    @bot.message_handler(commands=['start'])
    def start(message):
        user_id = message.from_user.id
        
        # Проверка подписки на канал
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
                        bot.send_message(
                            referrer_id,
                            f"🎉 Новый реферал! Вам начислено 100 BS Coin. Теперь у вас {db_actions.get_referral_stats(referrer_id)} рефералов."
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
        
        # ежедневный бонус
        if handle_daily_bonus(user_id):
            bot.send_message(user_id, "🎉 Ежедневный бонус: 10 BS Coin зашли вам на счет!")
        
        user_data = db_actions.get_user_data(user_id)
        welcome_msg = (
            f"🛍️ Добро пожаловать в BrandName!\n\n"
            f"• Заказывайте товары в канале #магазин\n"
            f"• Читайте новости в #новости\n"
            f"• Ваш профиль: /profile\n\n"
            f"Команды:\n"
            f"🔍 /catalog - Товары\n"
            f"👥 /ref - Реферальная система\n"
            f"🛠️ /support - Поддержка"
        )
        
        if db_actions.user_is_admin(user_id):
            bot.send_message(user_id, welcome_msg, reply_markup=buttons.admin_buttons())
        else:
            bot.send_message(user_id, welcome_msg, reply_markup=buttons.start_buttons())

    @bot.message_handler(func=lambda msg: msg.text == '🛍️ Магазин')
    def show_shop(message):
        catalog(message)
    
    @bot.message_handler(func=lambda msg: msg.text == '👤 Мой профиль')
    def show_profile(message):
        profile(message)
    
    @bot.message_handler(func=lambda msg: msg.text == '🎁 Акции')
    def show_promo(message):
        bot.send_message(message.chat.id, "🔥 Горячие акции")
    
    @bot.message_handler(func=lambda msg: msg.text == '📢 Отзывы')
    def show_сдуфкs(message):
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
            f"👥 Реферальная система\n\n"
            f"Приглашайте друзей и получайте бонусы!\n\n"
            f"🔗 Ваша реферальная ссылка:\n{ref_link}\n\n"
            f"• За каждого приглашенного друга вы получаете 100 BS Coin\n"
            f"• Ваш друг получает 50 BS Coin при первом заказе\n\n"
            f"🚀 Приглашено друзей: {ref_count}"
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


    @bot.message_handler(commands=['catalog'])
    def catalog(message_or_call):
        if isinstance(message_or_call, types.CallbackQuery):
            user_id = message_or_call.message.chat.id
            message = message_or_call.message
        else:
            user_id = message_or_call.chat.id
            message = message_or_call
                
        if not is_subscribed(user_id):
            show_subscription_request(user_id)
            return
                
        products = db_actions.get_products(limit=5)
        buttons = Bot_inline_btns()
        
        if not products:
            bot.send_message(user_id, "Каталог пока пуст")
            return
            
        products_msg = "🔥 Популярные товары:\n\n"
        for product in products:
            # product[9] - is_exclusive, product[4] - coin_price, product[3] - price
            if product[9]:
                products_msg += f"{product[1]} - {product[4]} BS Coin\n"
            else:
                products_msg += f"{product[1]} - {product[3]}₽\n"
        
        if isinstance(message_or_call, types.CallbackQuery):
            try:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message.message_id,
                    text=products_msg,
                    reply_markup=buttons.product_buttons(products)
                )
            except:
                bot.send_message(
                    user_id,
                    products_msg,
                    reply_markup=buttons.product_buttons(products)
                )
        else:
            bot.send_message(
                user_id,
                products_msg,
                reply_markup=buttons.product_buttons(products)
            )

    @bot.callback_query_handler(func=lambda call: call.data.startswith('product_'))
    def product_detail(call):
        user_id = call.message.chat.id
        if not is_subscribed(user_id):
            show_subscription_request(user_id)
            return
            
        product_id = int(call.data.split('_')[1])
        product = db_actions.get_product(product_id)
        buttons = Bot_inline_btns()
        
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return
        
        if product[9]:  # is_exclusive
            product_msg = (
                f"🎯 ЭКСКЛЮЗИВНЫЙ ТОВАР\n\n"
                f"🛍️ {product[1]}\n\n"
                f"📝 Описание: {product[2]}\n"
                f"💎 Цена: {product[4]} BS Coin\n"
                f"📦 В наличии: {'Да' if product[8] else 'Нет'}"
            )
            markup = buttons.product_detail_buttons(product_id, True, product[4])
        else:
            product_msg = (
                f"🛍️ {product[1]}\n\n"
                f"📝 Описание: {product[2]}\n"
                f"💰 Цена: {product[3]}₽\n"
                f"📦 В наличии: {'Да' if product[8] else 'Нет'}"
            )
            markup = buttons.product_detail_buttons(product_id, False)
        
        try:
            bot.edit_message_caption(
                chat_id=user_id,
                message_id=call.message.message_id,
                caption=product_msg,
                reply_markup=markup
            )
        except:
            if product[5]:  # if photo_id exists
                bot.send_photo(
                    user_id,
                    product[5],
                    caption=product_msg,
                    reply_markup=markup
                )
            else:
                bot.send_message(
                    user_id,
                    product_msg,
                    reply_markup=markup
                )
        bot.answer_callback_query(call.id)  

    @bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
    def buy_product(call):
        if call.data.startswith('buy_coin_'):
            return
            
        user_id = call.message.chat.id
        if not is_subscribed(user_id):
            show_subscription_request(user_id)
            return
            
        try:
            product_id = int(call.data.split('_')[1])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id, "❌ Ошибка: неверный формат запроса")
            return
            
        user_data = db_actions.get_user_data(user_id)
        
        if not user_data:
            bot.answer_callback_query(call.id, "Сначала зарегистрируйтесь с помощью /start")
            return
            
        db_actions.create_order(user_id, product_id, 1)
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
            "✅ Ваш заказ оформлен! Ожидайте информацию о доставке."
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith('buy_coin_'))
    def buy_product_with_coins(call):
        print(123123)
        user_id = call.message.chat.id
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
        
        if not product or not product[9]:  # is_exclusive
            bot.answer_callback_query(call.id, "Товар не найден или не является эксклюзивным")
            return
        
        coin_price = product[4]  # coin_price
        
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
        btn2 = types.InlineKeyboardButton(
            text="🔙 Назад в каталог",
            callback_data="back_to_catalog"
        )
        markup.add(btn1, btn2)
        
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
        
        # Создаем кнопки для навигации
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


    @bot.callback_query_handler(func=lambda call: call.data == 'back_to_catalog')
    def back_to_catalog(call):
        user_id = call.message.chat.id
        if not is_subscribed(user_id):
            show_subscription_request(user_id)
            return
            
        try:
            bot.delete_message(user_id, call.message.message_id)
        except:
            pass
            
        catalog(call.message)

if __name__ == '__main__':
    os_type = platform.system()
    work_dir = os.path.dirname(os.path.realpath(__file__))
    config = ConfigParser(f'{work_dir}/{config_name}', os_type)
    db = DB(config.get_config()['db_file_name'], Lock())
    db_actions = DbAct(db, config, config.get_config()['xlsx_path'])
    bot = telebot.TeleBot(config.get_config()['tg_api'])
    main()
    bot.polling(none_stop=True)