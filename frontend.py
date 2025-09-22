from telebot import types

def get_product_field(product, field_name, default=None):
    """Получить поле продукта по имени для совместимости с MySQL"""
    if isinstance(product, dict):
        return product.get(field_name, default)
    elif isinstance(product, (list, tuple)):
        # Маппинг полей для обратной совместимости
        field_mapping = {
            'product_id': 0,
            'name': 1,
            'description': 2,
            'price': 3,
            'price_yuan': 4,
            'coin_price': 5,
            'photo_id': 6,
            'category': 7,
            'topic': 8,
            'is_available': 9,
            'is_exclusive': 10
        }
        index = field_mapping.get(field_name, -1)
        return product[index] if 0 <= index < len(product) else default
    return default

class Bot_inline_btns:
    def __init__(self):
        super(Bot_inline_btns, self).__init__()

    def admin_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        btn1 = types.KeyboardButton('➕ Добавить товар')
        btn2 = types.KeyboardButton('👤 Информация о пользователе')
        btn3 = types.KeyboardButton('🎯 Установить скидку')
        btn4 = types.KeyboardButton('💰 Добавить монеты')
        btn5 = types.KeyboardButton('📤 Загрузить товары')
        btn6 = types.KeyboardButton('📊 Статистика админа')
        btn7 = types.KeyboardButton('📋 Экспорт пользователей')
        btn8 = types.KeyboardButton('📝 Создать пост')
        btn9 = types.KeyboardButton('📦 Экспорт товаров')
        btn10 = types.KeyboardButton('📋 Статус заказов')
        markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8, btn9, btn10)
        return markup

    def start_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        btn1 = types.KeyboardButton('👤 Мой профиль')
        btn2 = types.KeyboardButton('🎁 Акции')
        btn3 = types.KeyboardButton('📢 Отзывы')
        btn4 = types.KeyboardButton('🛒 Заказать товар')
        btn5 = types.KeyboardButton('🏆 Ачивки')
        markup.add(btn1, btn2, btn3, btn4, btn5)
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
            btn_text = f"{get_product_field(product, 'name', 'Неизвестно')} - {get_product_field(product, 'price', 0)}₽"
                
            btn = types.InlineKeyboardButton(
                text=btn_text,
                callback_data=f"product_{get_product_field(product, 'product_id', 0)}"
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
    
    
    def store_products_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            btn = types.InlineKeyboardButton(
                text=get_product_field(product, 'name', 'Неизвестно'),
                callback_data=f"send_product_{get_product_field(product, 'product_id', 0)}"
            )
            markup.add(btn)
        return markup

    def size_selection_buttons(self, variations):
        markup = types.InlineKeyboardMarkup()
        for variation in variations:
            btn_text = f"📏 {variation['size']}"
            if variation['quantity'] > 0:
                btn_text += f" ({variation['quantity']} шт.)"
                
            callback_data = f"size_{variation['product_id']}_{variation['size']}"
                
            btn = types.InlineKeyboardButton(
                text=btn_text,
                callback_data=callback_data
            )
            markup.add(btn)
        return markup

    def order_now_button(self, product_id, size):
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            text="🛒 Заказать сейчас",
            callback_data=f"order_{product_id}_{size}"
        )
        markup.add(btn)
        return markup
    
    def post_products_buttons(self, products):
        markup = types.InlineKeyboardMarkup()
        for product in products:
            btn = types.InlineKeyboardButton(
                text=get_product_field(product, 'name', 'Неизвестно'),
                callback_data=f"post_product_{get_product_field(product, 'product_id', 0)}"
            )
            markup.add(btn)
        return markup
    
    def create_order_status_buttons(order_id):
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