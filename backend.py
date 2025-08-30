import json
import pandas as pd
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

    def add_product(self, name, description, price, price_yuan, photo_id, category, is_exclusive=False, coin_price=0):
        try:
            result = self.__db.db_write(
                '''INSERT INTO products (name, description, price, price_yuan, coin_price, photo_id, category, topic, is_exclusive) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (name, description, price, price_yuan, coin_price, photo_id, category, "магазин", is_exclusive)
            )
            
            if result:
                # Получаем ID последней вставленной записи
                last_id_data = self.__db.db_read('SELECT last_insert_rowid()')
                if last_id_data:
                    return last_id_data[0][0]
            return None
            
        except Exception as e:
            print(f"Ошибка добавления товара: {e}")
            return None

    def get_products(self, category=None, limit=10):
        if category:
            return self.__db.db_read('SELECT * FROM products WHERE category = ? LIMIT ?', (category, limit))
        return self.__db.db_read('SELECT * FROM products LIMIT ?', (limit,))
    
    def get_product(self, product_id):
        print(f"DEBUG get_product: product_id = {product_id}")
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
        try:
            self.__db.db_write(
                'INSERT INTO referrals (referrer_id, referee_id) VALUES (?, ?)',
                (referrer_id, referee_id)
            )
            
            self.update_user_stats(referrer_id, 'bs_coin', 100)
            
            self.update_user_stats(referee_id, 'bs_coin', 50)
            self.update_user_stats(referee_id, 'discount', 5)
            
            return True
        except Exception as e:
            print(f"Ошибка добавления реферала: {e}")
            return False

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

    def add_review(self, user_id, text, photos_json=None):
        return self.__db.db_write(
            '''INSERT INTO reviews (user_id, text, photo_url) 
            VALUES (?, ?, ?)''',
            (user_id, text, photos_json)
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
    
    def get_all_users(self):
        return self.__db.db_read('SELECT user_id, first_name, last_name, username FROM users')
    
    def import_products_from_excel(self, df):
        success_count = 0
        
        # Сначала очищаем старые товары
        self.clear_all_products()
        
        grouped = df.groupby('Модель')
        
        for model_name, group in grouped:
            try:
                if pd.isna(model_name) or str(model_name).strip() == '':
                    print(f"Пропущена пустая модель: {model_name}")
                    continue
                    
                first_row = group.iloc[0]
                
                def get_safe_value(row, column, default=0):
                    value = row.get(column)
                    if pd.isna(value) or value == '':
                        return default
                    try:
                        if isinstance(value, str):
                            return float(value.replace(',', '.'))
                        return float(value)
                    except (ValueError, TypeError):
                        return default
                
                price = get_safe_value(first_row, 'Цена', 0)
                price_yuan = get_safe_value(first_row, 'Цена Y', 0)
                
                print(f"Добавляем товар: {model_name}, цена: {price}, цена Y: {price_yuan}")
                
                # Добавляем товар в таблицу products
                product_id = self.add_product(
                    name=str(model_name),
                    description=f"Модель: {model_name}",
                    price=price,
                    price_yuan=price_yuan,
                    photo_id=None,
                    category="general",
                    is_exclusive=False,
                    coin_price=0
                )
                
                if product_id:
                    print(f"Товар добавлен, ID: {product_id}")
                    variation_count = 0
                    for _, row in group.iterrows():
                        try:
                            quantity = int(get_safe_value(row, 'Количество', 0))
                            size = str(row.get('Размер', '')).strip()
                            model_id = str(row.get('ID Модели', '')).strip()
                            link = str(row.get('Ссылка', '')).strip()
                            
                            if not size:
                                continue
                                
                            # Для каждой вариации используем правильный product_id
                            variation_price = get_safe_value(row, 'Цена', price)
                            variation_price_yuan = get_safe_value(row, 'Цена Y', price_yuan)
                            
                            self.add_product_variation(
                                product_id=product_id,  # ← Используем текущий product_id
                                model_id=model_id,
                                size=size,
                                quantity=quantity,
                                price=variation_price,
                                price_yuan=variation_price_yuan,
                                link=link if link else None
                            )
                            variation_count += 1
                        except Exception as e:
                            print(f"Ошибка импорта вариации: {e}")
                            continue
                    
                    print(f"Добавлено вариаций: {variation_count} для товара {model_name} (ID: {product_id})")
                    success_count += 1
                else:
                    print(f"Ошибка добавления товара: {model_name}")
                    
            except Exception as e:
                print(f"Ошибка импорта товара {model_name}: {e}")
                import traceback
                print(traceback.format_exc())
                continue
                
        print(f"Импорт завершен. Успешно: {success_count}")
        return success_count

    def add_product_variation(self, product_id, model_id, size, quantity, price, price_yuan, link):
        try:
            result = self.__db.db_write(
                '''INSERT INTO product_variations 
                (product_id, model_id, size, quantity, price, price_yuan, link) 
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (product_id, model_id, size, quantity, price, price_yuan, link)
            )
            return bool(result)
        except Exception as e:
            print(f"Ошибка добавления вариации: {e}")
            return False

    def get_product_with_variations(self, product_id):
        product = self.get_product(product_id)
        if not product:
            return None
            
        variations = self.__db.db_read(
            'SELECT * FROM product_variations WHERE product_id = ?',
            (product_id,)
        )
        
        return {
            'product_id': product[0],
            'name': product[1],
            'description': product[2],
            'price': product[3],
            'photo_id': product[5],
            'is_available': product[8],
            'variations': [
                {
                    'variation_id': v[0],
                    'model_id': v[2],
                    'size': v[3],
                    'quantity': v[4],
                    'price': v[5],
                    'link': v[6]
                } for v in variations
            ]
        }

    def update_variation_quantity(self, variation_id, new_quantity):
        self.__db.db_write(
            'UPDATE product_variations SET quantity = ? WHERE variation_id = ?',
            (new_quantity, variation_id)
        )

    def get_products_count(self):
        data = self.__db.db_read('SELECT COUNT(*) FROM products')
        return data[0][0] if data else 0

    def get_variations_count(self):
        data = self.__db.db_read('SELECT COUNT(*) FROM product_variations')
        return data[0][0] if data else 0

    def get_users_count(self):
        data = self.__db.db_read('SELECT COUNT(*) FROM users')
        return data[0][0] if data else 0

    def get_reviews_count(self):
        data = self.__db.db_read('SELECT COUNT(*) FROM reviews')
        return data[0][0] if data else 0
    

    def safe_convert(value, to_type=float, default=0):
        if value is None or value == '' or pd.isna(value):
            return default
            
        try:
            if to_type == int:
                return int(float(value))
            elif to_type == float:
                return float(value)
            elif to_type == str:
                return str(value).strip()
            else:
                return default
        except (ValueError, TypeError):
            return default
        
    def update_product_exclusive(self, product_id, is_exclusive, coin_price=0):
        try:
            if is_exclusive:
                self.__db.db_write(
                    'UPDATE products SET is_exclusive = ?, coin_price = ? WHERE product_id = ?',
                    (True, coin_price, product_id)
                )
            else:
                self.__db.db_write(
                    'UPDATE products SET is_exclusive = ?, coin_price = ? WHERE product_id = ?',
                    (False, 0, product_id)
                )
            return True
        except Exception as e:
            print(f"Ошибка обновления товара: {e}")
            return False
        

    def get_all_products(self):
        return self.__db.db_read('SELECT * FROM products')

    def update_product_photo(self, product_id, photo_id):
        return self.__db.db_write(
            'UPDATE products SET photo_id = ? WHERE product_id = ?',
            (photo_id, product_id)
        )
    
    def get_all_products_for_export(self):
        try:
            products = self.__db.db_read('''
                SELECT p.product_id, p.name, pv.model_id, pv.size, pv.price_yuan, 
                    pv.quantity, pv.price, pv.link, p.is_exclusive, p.coin_price
                FROM products p
                LEFT JOIN product_variations pv ON p.product_id = pv.product_id
                ORDER BY p.name, pv.size
            ''')
            
            result = []
            for product in products:
                result.append({
                    'product_id': product[0],
                    'name': product[1],
                    'model_id': product[2] if product[2] else '',
                    'size': product[3] if product[3] else '',
                    'price_yuan': product[4] if product[4] else 0,
                    'quantity': product[5] if product[5] else 0,
                    'price': product[6] if product[6] else 0,
                    'link': product[7] if product[7] else '',
                    'is_exclusive': bool(product[8]),
                    'coin_price': product[9] if product[9] else 0
                })
            
            return result
            
        except Exception as e:
            print(f"Ошибка получения данных для экспорта: {e}")
            return []
        
    def clear_all_products(self):
        try:
            self.__db.db_write('DELETE FROM orders WHERE product_id IN (SELECT product_id FROM products)')
            self.__db.db_write('DELETE FROM product_variations')
            self.__db.db_write('DELETE FROM products')
            self.__db.db_write('DELETE FROM sqlite_sequence WHERE name IN ("products", "product_variations", "orders")')
            return True
        except Exception as e:
            print(f"Ошибка очистки товаров: {e}")
            return False


    def create_detailed_order(self, user_id, product_id, size, city, address, full_name, phone, delivery_type):
        try:
            print(f"DEBUG: Создание заказа - user_id: {user_id}, product_id: {product_id}, size: {size}")
            
            variation_data = self.__db.db_read(
                'SELECT variation_id FROM product_variations WHERE product_id = ? AND size = ?',
                (product_id, str(size))
            )
            
            print(f"DEBUG: Найдены вариации: {variation_data}")
            
            if not variation_data:
                print(f"❌ Вариация не найдена - product_id: {product_id}, size: {size}")
                return None
                
            variation_id = variation_data[0][0]
            print(f"DEBUG: Используем variation_id: {variation_id}")
            
            result = self.__db.db_write(
                '''INSERT INTO orders_detailed 
                (user_id, product_id, variation_id, quantity, city, address, full_name, phone, delivery_type) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, product_id, variation_id, 1, city, address, full_name, phone, delivery_type)
            )
            
            print(f"DEBUG: Результат создания заказа: {result}")
            return result
            
        except Exception as e:
            print(f"Ошибка создания заказа: {e}")
            import traceback
            traceback.print_exc()
            return None
        
    # В класс DbAct добавьте:
    def save_order_message_id(self, order_id, message_id, topic_id):
        try:
            columns = self.__db.db_read("PRAGMA table_info(orders_detailed)")
            column_names = [col[1] for col in columns]
            
            if 'admin_message_id' not in column_names or 'admin_topic_id' not in column_names:
                print("Предупреждение: Колонки admin_message_id или admin_topic_id не существуют")
                return False
                
            return self.__db.db_write(
                'UPDATE orders_detailed SET admin_message_id = ?, admin_topic_id = ? WHERE order_id = ?',
                (message_id, topic_id, order_id)
            )
        except Exception as e:
            print(f"Ошибка сохранения message_id: {e}")
            return False

    def get_order_by_id(self, order_id):
        data = self.__db.db_read(
            '''SELECT order_id, user_id, product_id, city, address, 
                    full_name, phone, delivery_type, status, 
                    admin_message_id, admin_topic_id, created_at
            FROM orders_detailed WHERE order_id = ?''', 
            (order_id,)
        )
        if data and data[0]:
            row = data[0]
            return {
                'order_id': row[0],
                'user_id': row[1],
                'product_id': row[2],
                'city': row[3],
                'address': row[4],
                'full_name': row[5],
                'phone': row[6],
                'delivery_type': row[7],
                'status': row[8],
                'admin_message_id': row[9],
                'admin_topic_id': row[10],
                'created_at': row[11]
            }
        return None

    def update_order_status(self, order_id, status):
        return self.__db.db_write(
            'UPDATE orders_detailed SET status = ? WHERE order_id = ?',
            (status, order_id)
        )
    
    def decrease_product_quantity(self, product_id, size):
        try:
            size_str = str(size)
            print(f"DEBUG: Уменьшение количества - product_id: {product_id}, size: {size_str}")
            
            success = self.__db.db_write(
                'UPDATE product_variations SET quantity = quantity - 1 WHERE product_id = ? AND size = ?',
                (product_id, size_str)
            )
            
            print(f"DEBUG: Уменьшение количества - success: {success}")
            return bool(success)
            
        except Exception as e:
            print(f"Ошибка уменьшения количества: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_product_variations(self, product_id):
        try:
            data = self.__db.db_read(
                'SELECT * FROM product_variations WHERE product_id = ?',
                (product_id,)
            )
            variations = []
            for row in data:
                variation = {
                    'variation_id': row[0],
                    'product_id': row[1],
                    'model_id': row[2],
                    'size': str(row[3]),
                    'quantity': row[4],
                    'price': row[5],
                    'price_yuan': row[6],
                    'link': row[7]
                }
                print(f"DEBUG Variation: {variation}")
                variations.append(variation)
            return variations
        except Exception as e:
            print(f"Ошибка получения вариаций: {e}")
            return []
    
    def check_size_availability(self, product_id, size):
        try:
            size_str = str(size)
            
            data = self.__db.db_read(
                'SELECT quantity, size FROM product_variations WHERE product_id = ?',
                (product_id,)
            )
            
            if not data:
                return False
            
            for row in data:
                db_size = str(row[1])
                db_quantity = row[0]
                
                if db_size == size_str:
                    return db_quantity > 0
                    
            return False
            
        except Exception as e:
            print(f"Ошибка проверки доступности размера: {e}")
            return False