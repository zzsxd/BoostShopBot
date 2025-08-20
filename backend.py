# backend.py
import json
from datetime import datetime

class DbAct:
    def __init__(self, db, config, path_xlsx):
        self.__db = db
        self.__config = config
        self.__path_xlsx = path_xlsx

    def add_user(self, user_id, first_name, last_name, username):
        if not self.user_exists(user_id):
            referral_code = f"ref_{user_id}"
            is_admin = user_id in self.__config.get_config()['admins']
            self.__db.db_write(
                '''INSERT INTO users 
                (user_id, first_name, last_name, username, referral_code, is_admin, last_active) 
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (user_id, first_name, last_name, username, referral_code, is_admin, datetime.now())
            )

    def user_exists(self, user_id):
        data = self.__db.db_read('SELECT COUNT(*) FROM users WHERE user_id = ?', (user_id,))
        return data[0][0] > 0

    def user_is_admin(self, user_id):
        data = self.__db.db_read('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        return data[0][0] if data else False

    def get_user_data(self, user_id):
        data = self.__db.db_read('SELECT * FROM users WHERE user_id = ?', (user_id,))
        if data:
            row = data[0]
            last_active = row[10]
            
            if isinstance(last_active, str):
                try:
                    last_active = datetime.strptime(last_active, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    last_active = None
            
            return {
                'user_id': row[0],
                'first_name': row[1],
                'last_name': row[2],
                'username': row[3],
                'status': row[4],
                'comments': row[5],
                'orders': row[6],
                'bs_coin': row[7],
                'discount': row[8],
                'referral_code': row[9],
                'last_active': last_active,
                'is_admin': row[11],
                'achievements': json.loads(row[12]) if row[12] else []
            }
        return None

    def update_last_active(self, user_id, date):
        self.__db.db_write(
            'UPDATE users SET last_active = ? WHERE user_id = ?',
            (date, user_id)
        )

    def set_discount(self, user_id, discount):
        self.__db.db_write(
            'UPDATE users SET discount = ? WHERE user_id = ?',
            (discount, user_id)
        )

    def add_product(self, name, description, price, photo_id, category="general", 
                    topic="магазин", is_exclusive=False, coin_price=0):
        return self.__db.db_write(
            '''INSERT INTO products (name, description, price, coin_price, photo_id, category, topic, is_exclusive) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (name, description, price, coin_price, photo_id, category, topic, is_exclusive)
        )

    def get_products(self, category=None, limit=10):
        if category:
            return self.__db.db_read('SELECT * FROM products WHERE category = ? LIMIT ?', (category, limit))
        return self.__db.db_read('SELECT * FROM products LIMIT ?', (limit,))
    
    def get_product(self, product_id):
        data = self.__db.db_read('SELECT * FROM products WHERE product_id = ?', (product_id,))
        if data:
            # 0: product_id, 1: name, 2: description, 3: price, 4: coin_price,
            # 5: photo_id, 6: category, 7: topic, 8: is_available, 9: is_exclusive, 10: created_at
            return data[0]
        return None

    def create_order(self, user_id, product_id, quantity):
        return self.__db.db_write(
            '''INSERT INTO orders (user_id, product_id, quantity) 
            VALUES (?, ?, ?)''',
            (user_id, product_id, quantity)
        )
    def add_referral(self, referrer_id, referee_id):
        self.__db.db_write(
            'INSERT INTO referrals (referrer_id, referee_id) VALUES (?, ?)',
            (referrer_id, referee_id)
        )
        self.update_user_stats(referrer_id, 'bs_coin', 100)
        self.update_user_stats(referrer_id, 'referrals', 1)

    def get_referral_stats(self, user_id):
        return self.__db.db_read(
            'SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', 
            (user_id,)
        )[0][0]

    def add_achievement(self, user_id, achievement):
        user = self.get_user_data(user_id)
        if user and achievement not in user['achievements']:
            achievements = user['achievements']
            achievements.append(achievement)
            self.__db.db_write(
                'UPDATE users SET achievements = ? WHERE user_id = ?',
                (json.dumps(achievements), user_id))
            return True
        return False

    def update_user_stats(self, user_id, field, value):
        if field in ['comments', 'orders', 'bs_coin', 'discount', 'referrals']:
            self.__db.db_write(
                f'UPDATE users SET {field} = {field} + ? WHERE user_id = ?',
                (value, user_id)
            )

    def add_review(self, user_id, text, photo_url=None):
        return self.__db.db_write(
            '''INSERT INTO reviews (user_id, text, photo_url) 
            VALUES (?, ?, ?)''',
            (user_id, text, photo_url)
        )

    def get_reviews(self, limit=5):
        return self.__db.db_read(
            '''SELECT r.*, u.first_name, u.username 
            FROM reviews r JOIN users u ON r.user_id = u.user_id 
            ORDER BY r.created_at DESC LIMIT ?''',
            (limit,)
        )
    def get_exclusive_products(self, limit=10):
        return self.__db.db_read(
            'SELECT * FROM products WHERE is_exclusive = TRUE LIMIT ?', 
            (limit,)
        )