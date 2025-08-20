# frontend.py
from telebot import types

class Bot_inline_btns:
    def __init__(self):
        super(Bot_inline_btns, self).__init__()

    def admin_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        btn1 = types.KeyboardButton('/add_product')
        btn2 = types.KeyboardButton('/user_info')
        btn3 = types.KeyboardButton('/set_discount')
        btn4 = types.KeyboardButton('/add_coins')
        markup.add(btn1, btn2, btn3, btn4)
        return markup

    def start_buttons(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        btn1 = types.KeyboardButton('🛍️ Магазин')
        btn2 = types.KeyboardButton('👤 Мой профиль')
        btn3 = types.KeyboardButton('🎁 Акции')
        btn4 = types.KeyboardButton('📢 Отзывы')
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
            # product[9] - is_exclusive, product[4] - coin_price, product[3] - price
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
            text="🔙 Назад в каталог",
            callback_data="back_to_catalog"
        )
        markup.add(btn1, btn2)
        return markup

    def reviews_buttons(self):
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton(
            text="✍️ Написать отзыв",
            callback_data="write_review"
        )
        btn2 = types.InlineKeyboardButton(
            text="🔥 Все отзывы",
            url="https://t.me/c/BridgeSideChannel"
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