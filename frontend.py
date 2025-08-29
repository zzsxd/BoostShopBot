from telebot import types

class Bot_inline_btns:
    def __init__(self):
        super(Bot_inline_btns, self).__init__()

    def admin_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        btn1 = types.KeyboardButton('/add_product')
        btn2 = types.KeyboardButton('/user_info')
        btn3 = types.KeyboardButton('/set_discount')
        btn4 = types.KeyboardButton('/add_coins')
        btn5 = types.KeyboardButton('/upload_products')
        btn6 = types.KeyboardButton("/admin_stats")
        btn7 = types.KeyboardButton('/export_users')
        btn8 = types.KeyboardButton('/create_post')
        btn9 = types.KeyboardButton('/export_products')
        btn10 = types.KeyboardButton('/order_status')
        markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8, btn9, btn10)
        return markup

    def start_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        btn1 = types.KeyboardButton('👤 Мой профиль')
        btn2 = types.KeyboardButton('🎁 Акции')
        btn3 = types.KeyboardButton('📢 Отзывы')
        btn4 = types.KeyboardButton('🛒 Заказать товар')
        markup.add(btn1, btn2, btn3, btn4)
        return markup

    def profile_buttons(self, user_data):
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton(
            text=f"💎 BS Coin: {user_data['bs_coin']}",
            callback_data="bs_coin_info"
        )
        btn2 = types.InlineKeyboardButton(
            text="🔗 Реферальная ссылка", 
            callback_data="ref_link"
        )
        
        if user_data['bs_coin'] >= 500:
            btn3 = types.InlineKeyboardButton(
                text="🔄 Обменять 500 BS Coin на 5% скидку",
                callback_data="exchange_coin"
            )
            markup.add(btn3)
        
        markup.add(btn1, btn2)
        return markup

    def product_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            if product[9]:
                btn_text = f"{product[1]} - {product[4]} BS Coin"
            else:
                btn_text = f"{product[1]} - {product[3]}₽"
                
            btn = types.InlineKeyboardButton(
                text=btn_text,
                callback_data=f"product_{product[0]}"
            )
            markup.add(btn)
            
        btn_catalog = types.InlineKeyboardButton(
            text="📋 Весь каталог",
            callback_data="full_catalog"
        )
        markup.add(btn_catalog)
        return markup

    def product_detail_buttons(self, product_id, is_exclusive=False, coin_price=0):
        markup = types.InlineKeyboardMarkup()
        
        if is_exclusive:
            btn1 = types.InlineKeyboardButton(
                text=f"💎 Купить за {coin_price} BS Coin",
                callback_data=f"buy_coin_{product_id}"
            )
        else:
            btn1 = types.InlineKeyboardButton(
                text="🛒 Купить сейчас",
                callback_data=f"buy_{product_id}"
            )
        
        btn2 = types.InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_main"
        )
        markup.add(btn1, btn2)
        return markup

    def reviews_buttons(self):
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton(
            text="✍️ Написать отзыв",
            callback_data="start_review"
        )
        btn2 = types.InlineKeyboardButton(
            text="🔥 Все отзывы",
            url="https://t.me/BridgeSide_Featback"
        )
        markup.add(btn1, btn2)
        return markup
    
    def exclusive_products_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            btn = types.InlineKeyboardButton(
                text=f"{product[1]} - {product[4]} BS Coin",
                callback_data=f"product_{product[0]}"
            )
            markup.add(btn)
        return markup
    
    def store_products_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            btn = types.InlineKeyboardButton(
                text=product[1],
                callback_data=f"send_product_{product[0]}"
            )
            markup.add(btn)
        return markup

    def size_selection_buttons(self, variations, is_exclusive=False):
        markup = types.InlineKeyboardMarkup()
        for variation in variations:
            btn_text = f"📏 {variation['size']}"
            if variation['quantity'] > 0:
                btn_text += f" ({variation['quantity']} шт.)"
                
            if is_exclusive:
                callback_data = f"size_coin_{variation['product_id']}_{variation['size']}"
            else:
                callback_data = f"size_{variation['product_id']}_{variation['size']}"
                
            btn = types.InlineKeyboardButton(
                text=btn_text,
                callback_data=callback_data
            )
            markup.add(btn)
        return markup

    def order_now_button(self, product_id, size):
        markup = types.InlineKeyboardMarkup()
        callback_data = f"order_now_{product_id}_{size}"
        print(f"DEBUG: Creating order_now button with callback_data: {callback_data}")
        
        btn = types.InlineKeyboardButton(
            text="🛒 Заказать сейчас",
            callback_data=callback_data
        )
        markup.add(btn)
        return markup

    def review_buttons(self):
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            text="✍️ Написать отзыв",
            callback_data="start_review"
        )
        markup.add(btn)
        return markup
    
    def post_products_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            btn = types.InlineKeyboardButton(
                text=product[1],
                callback_data=f"post_product_{product[0]}"
            )
            markup.add(btn)
        return markup
    
    def create_order_status_buttons(order_id):
        """Создает кнопки для управления статусом заказа"""
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        buttons = [
            types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"order_confirm_{order_id}"),
            types.InlineKeyboardButton("💳 Оплачен", callback_data=f"order_pay_{order_id}"),
            types.InlineKeyboardButton("🚚 Отправить", callback_data=f"order_ship_{order_id}"),
            types.InlineKeyboardButton("📦 Доставлен", callback_data=f"order_deliver_{order_id}"),
            types.InlineKeyboardButton("❌ Отменить", callback_data=f"order_cancel_{order_id}")
        ]
        for i in range(0, len(buttons), 2):
            if i + 1 < len(buttons):
                markup.add(buttons[i], buttons[i + 1])
            else:
                markup.add(buttons[i])
        
        return markup