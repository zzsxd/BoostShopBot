import json
import pandas as pd
from datetime import datetime
import logging
from logging_config import get_logger, log_error, log_info

# Настройка логирования
logger = get_logger('backend')

class DbAct:
    def __init__(self, db, config, path_xlsx):
        self.__db = db
        self.__config = config
        self.__path_xlsx = path_xlsx

    def __normalize_username(self, username):
        if not username:
            return None
        uname = str(username).strip()
        if uname.startswith('@'):
            uname = uname[1:]
        return uname.lower()

    def __is_admin_in_config(self, user_id=None, username=None):
        """Check admin status against config by user_id or username."""
        cfg = self.__config.get_config() or {}
        admins_list = cfg.get('admins', []) or []
        admin_usernames = cfg.get('admin_usernames', []) or []

        # Normalize IDs list: allow strings or ints
        admin_ids_normalized = set()
        for val in admins_list:
            try:
                admin_ids_normalized.add(int(val))
            except Exception:
                # If it's not an int, treat it as a username token
                uname_norm = self.__normalize_username(val)
                if uname_norm:
                    admin_usernames.append(uname_norm)

        # Normalize usernames list
        admin_usernames_normalized = set(self.__normalize_username(u) for u in admin_usernames if self.__normalize_username(u))

        # Check by id
        if user_id is not None:
            try:
                if int(user_id) in admin_ids_normalized:
                    return True
            except Exception:
                pass

        # Check by username
        uname_norm = self.__normalize_username(username)
        if uname_norm and uname_norm in admin_usernames_normalized:
            return True

        return False

    def add_user(self, user_id, first_name, last_name, username):
        if not self.user_exists(user_id):
            referral_code = f"ref_{user_id}"
            is_admin = self.__is_admin_in_config(user_id=user_id, username=username)
            self.__db.db_write(
                '''INSERT INTO users 
                (user_id, first_name, last_name, username, referral_code, is_admin, last_active) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (user_id, first_name, last_name, username, referral_code, is_admin, datetime.now())
            )

    def user_exists(self, user_id):
        data = self.__db.db_read('SELECT COUNT(*) FROM users WHERE user_id = %s', (user_id,))
        return data[0]['COUNT(*)'] > 0 if data else False

    def user_is_admin(self, user_id):
        """Check if user is admin by DB flag or by config (user_id or username)."""
        data = self.__db.db_read('SELECT is_admin, username FROM users WHERE user_id = %s', (user_id,))
        if data:
            row = data[0]
            if bool(row.get('is_admin')):
                return True
            # Fallback to config-based check via username
            return self.__is_admin_in_config(user_id=user_id, username=row.get('username'))
        # If user not in DB, still allow config-based admin by id/username
        return self.__is_admin_in_config(user_id=user_id, username=None)

    def get_user_data(self, user_id):
        data = self.__db.db_read('SELECT * FROM users WHERE user_id = %s', (user_id,))
        if data:
            row = data[0]
            
            # Обработка last_active
            last_active = row.get('last_active')
            if isinstance(last_active, str):
                try:
                    last_active = datetime.strptime(last_active, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    try:
                        last_active = datetime.strptime(last_active, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        last_active = None
            
            # Обработка achievements
            achievements = row.get('achievements', '[]')
            if isinstance(achievements, str):
                try:
                    achievements = json.loads(achievements)
                except json.JSONDecodeError:
                    achievements = []
            
            return {
                'user_id': row.get('user_id'),
                'first_name': row.get('first_name'),
                'last_name': row.get('last_name'),
                'username': row.get('username'),
                'status': row.get('status'),
                'comments': row.get('comments', 0),
                'orders': row.get('orders', 0),
                'bs_coin': row.get('bs_coin', 0),
                'discount': row.get('discount', 0),
                'referral_code': row.get('referral_code'),
                'last_active': last_active,
                'is_admin': bool(row.get('is_admin', False)),
                'achievements': achievements
            }
        return None

    def update_last_active(self, user_id, date):
        self.__db.db_write(
            'UPDATE users SET last_active = %s WHERE user_id = %s',
            (date, user_id)
        )

    def set_discount(self, user_id, discount):
        self.__db.db_write(
            'UPDATE users SET discount = %s WHERE user_id = %s',
            (discount, user_id)
        )

    def add_product(self, name, description, price, price_yuan, photo_id, category, description_full=None, table_id=None, keywords=None):
        try:
            result = self.__db.db_write(
                '''INSERT INTO products (name, description, description_full, table_id, keywords, price, price_yuan, photo_id, category, topic) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                (name, description, description_full, table_id, keywords, price, price_yuan, photo_id, category, "магазин")
            )
            
            if result:
                last_id_data = self.__db.db_read('SELECT LAST_INSERT_ID() as last_id')
                if last_id_data:
                    return last_id_data[0]['last_id']
            return None
            
        except Exception as e:
            log_error(logger, e, "Ошибка добавления товара")
            return None

    def get_products(self, category=None, limit=10):
        if category:
            return self.__db.db_read('SELECT * FROM products WHERE category = %s LIMIT %s', (category, limit))
        return self.__db.db_read('SELECT * FROM products LIMIT %s', (limit,))
    
    def get_product(self, product_id):
        log_info(logger, "DEBUG get_product: product_id = {product_id}")
        data = self.__db.db_read('SELECT * FROM products WHERE product_id = %s', (product_id,))
        return data[0] if data else None

    def get_product_by_table_id(self, table_id):
        """Получить товар по table_id (артикулу)"""
        log_info(logger, f"DEBUG get_product_by_table_id: table_id = {table_id}")
        data = self.__db.db_read('SELECT * FROM products WHERE table_id = %s', (table_id,))
        return data[0] if data else None

    def create_order(self, user_id, product_id, quantity):
        return self.__db.db_write(
            '''INSERT INTO orders (user_id, product_id, quantity) 
            VALUES (%s, %s, %s)''',
            (user_id, product_id, quantity)
        )

    def add_referral(self, referrer_id, referee_id):
        try:
            self.__db.db_write(
                'INSERT INTO referrals (referrer_id, referee_id) VALUES (%s, %s)',
                (referrer_id, referee_id)
            )
            
            self.update_user_stats(referrer_id, 'bs_coin', 100)
            self.update_user_stats(referee_id, 'bs_coin', 50)
            self.update_user_stats(referee_id, 'discount', 5)
            
            return True
        except Exception as e:
            log_error(logger, e, "Ошибка добавления реферала")
            return False

    def get_referral_stats(self, user_id):
        data = self.__db.db_read(
            'SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s', 
            (user_id,)
        )
        return data[0]['count'] if data else 0

    def add_achievement(self, user_id, achievement_code, achievement_data):
        """Добавить ачивку пользователю"""
        try:
            # Проверяем, есть ли уже такая ачивка
            existing = self.__db.db_read(
                'SELECT achievement_id FROM achievements WHERE user_id = %s AND achievement_code = %s',
                (user_id, achievement_code)
            )
            
            if existing:
                return False  # Ачивка уже есть
            
            # Добавляем ачивку
            self.__db.db_write(
                '''INSERT INTO achievements 
                (user_id, achievement_code, achievement_name, achievement_description, 
                 achievement_category, bs_coin_reward, discount_bonus) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (user_id, achievement_code, achievement_data['name'], 
                 achievement_data['description'], achievement_data['category'],
                 achievement_data['bs_coin_reward'], achievement_data['discount_bonus'])
            )
            
            # Начисляем награды
            if achievement_data['bs_coin_reward'] > 0:
                self.update_user_stats(user_id, 'bs_coin', achievement_data['bs_coin_reward'])
            
            if achievement_data['discount_bonus'] > 0:
                self.update_user_stats(user_id, 'discount', achievement_data['discount_bonus'])
            
            return True
            
        except Exception as e:
            log_error(logger, e, f"Ошибка добавления ачивки {achievement_code} пользователю {user_id}")
            return False

    def get_user_achievements(self, user_id):
        """Получить все ачивки пользователя"""
        try:
            achievements = self.__db.db_read(
                '''SELECT achievement_code, achievement_name, achievement_description, 
                   achievement_category, bs_coin_reward, discount_bonus, earned_at 
                   FROM achievements WHERE user_id = %s ORDER BY earned_at DESC''',
                (user_id,)
            )
            return achievements if achievements else []
        except Exception as e:
            log_error(logger, e, f"Ошибка получения ачивок пользователя {user_id}")
            return []

    def get_achievement_by_code(self, user_id, achievement_code):
        """Проверить, есть ли у пользователя конкретная ачивка"""
        try:
            result = self.__db.db_read(
                'SELECT achievement_id FROM achievements WHERE user_id = %s AND achievement_code = %s',
                (user_id, achievement_code)
            )
            return len(result) > 0
        except Exception as e:
            log_error(logger, e, f"Ошибка проверки ачивки {achievement_code} у пользователя {user_id}")
            return False

    def get_achievements_by_category(self, user_id, category):
        """Получить ачивки пользователя по категории"""
        try:
            achievements = self.__db.db_read(
                '''SELECT achievement_code, achievement_name, achievement_description, 
                   bs_coin_reward, discount_bonus, earned_at 
                   FROM achievements WHERE user_id = %s AND achievement_category = %s 
                   ORDER BY earned_at DESC''',
                (user_id, category)
            )
            return achievements if achievements else []
        except Exception as e:
            log_error(logger, e, f"Ошибка получения ачивок категории {category} пользователя {user_id}")
            return []

    def update_user_stats(self, user_id, field, value):
        if field in ['comments', 'orders', 'bs_coin', 'discount']:
            self.__db.db_write(
                f'UPDATE users SET {field} = {field} + %s WHERE user_id = %s',
                (value, user_id)
            )

    def add_review(self, user_id, text, photos_json=None):
        return self.__db.db_write(
            '''INSERT INTO reviews (user_id, text, photo_url) 
            VALUES (%s, %s, %s)''',
            (user_id, text, photos_json)
        )

    def get_reviews(self, limit=5):
        return self.__db.db_read(
            '''SELECT r.*, u.first_name, u.username 
            FROM reviews r JOIN users u ON r.user_id = u.user_id 
            ORDER BY r.created_at DESC LIMIT %s''',
            (limit,)
        )

    
    def get_all_users(self):
        return self.__db.db_read('SELECT user_id, first_name, last_name, username FROM users')
    
    @staticmethod
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

    def import_products_from_excel(self, df):
        success_count = 0
        
        self.clear_all_products()
        
        grouped = df.groupby('Модель')
        
        for model_name, group in grouped:
            try:
                if pd.isna(model_name) or str(model_name).strip() == '':
                    logger.warning(f"Пропущена пустая модель: {model_name}")
                    continue
                    
                first_row = group.iloc[0]
                
                price = self.safe_convert(first_row.get('Цена'), float, 0)
                price_yuan = self.safe_convert(first_row.get('Цена Y'), float, 0)
                
                log_info(logger, "Добавляем товар: {model_name}, цена: {price}, цена Y: {price_yuan}")
                
                product_id = self.add_product(
                    name=str(model_name),
                    description=f"Модель: {model_name}",
                    price=price,
                    price_yuan=price_yuan,
                    photo_id=None,
                    category="general"
                )
                
                if product_id:
                    log_info(logger, "Товар добавлен, ID: {product_id}")
                    variation_count = 0
                    for _, row in group.iterrows():
                        try:
                            quantity = int(self.safe_convert(row.get('Количество'), int, 0))
                            size = str(row.get('Размер', '')).strip()
                            model_id = str(row.get('ID Модели', '')).strip()
                            link = str(row.get('Ссылка', '')).strip()
                            
                            if not size:
                                continue
                            
                            variation_price = self.safe_convert(row.get('Цена'), float, price)
                            variation_price_yuan = self.safe_convert(row.get('Цена Y'), float, price_yuan)
                            
                            self.add_product_variation(
                                product_id=product_id,
                                model_id=model_id,
                                size=size,
                                quantity=quantity,
                                price=variation_price,
                                price_yuan=variation_price_yuan,
                                link=link if link else None
                            )
                            variation_count += 1
                        except Exception as e:
                            log_error(logger, e, "Ошибка импорта вариации: {e}")
                            continue
                    
                    log_info(logger, "Добавлено вариаций: {variation_count} для товара {model_name} (ID: {product_id})")
                    success_count += 1
                else:
                    log_error(logger, e, "Ошибка добавления товара: {model_name}")
                    
            except Exception as e:
                log_error(logger, e, "Ошибка импорта товара {model_name}: {e}")
                continue
                
        log_info(logger, "Импорт завершен. Успешно: {success_count}")
        return success_count

    def import_products_from_excel_new_format(self, economics_df, keys_df):
        """Импорт товаров из новой структуры Excel с двумя листами"""
        success_count = 0
        
        self.clear_all_products()
        
        # Создаем словарь для быстрого поиска описаний по ID модели
        keys_dict = {}
        for _, row in keys_df.iterrows():
            model_id = str(row.get('ID', '')).strip()
            if model_id:
                keys_dict[model_id] = {
                    'description': str(row.get('Краткое описание товара Telegram', '')).strip(),
                    'hashtags': str(row.get('#Хештеги', '')).strip(),
                    'keywords': str(row.get('Топ - 10 ключевый запросов Yandex WordStat', '')).strip()
                }
        
        # Группируем товары по модели
        grouped = economics_df.groupby('Модель')
        
        for model_name, group in grouped:
            try:
                if pd.isna(model_name) or str(model_name).strip() == '':
                    log_info(logger, f"Пропущена пустая модель: {model_name}")
                    continue
                    
                first_row = group.iloc[0]
                model_id = str(first_row.get('ID модели', '')).strip()
                
                # Получаем описание из листа "КЛЮЧИ"
                product_info = keys_dict.get(model_id, {})
                description = product_info.get('description', f"Модель: {model_name}")
                hashtags = product_info.get('hashtags', '')
                keywords = product_info.get('keywords', '')
                
                # Формируем полное описание
                full_description = description
                if hashtags:
                    full_description += f"\n\n{hashtags}"
                
                # Используем цену продажи как основную цену
                price = self.safe_convert(first_row.get('Цена продажи'), float, 0)
                price_yuan = self.safe_convert(first_row.get('Цена Y'), float, 0)
                
                log_info(logger, f"Добавляем товар: {model_name}, цена: {price}, цена Y: {price_yuan}")
                
                product_id = self.add_product(
                    name=str(model_name),
                    description=f"Модель: {model_name}",
                    description_full=full_description,
                    table_id=model_id,
                    keywords=keywords,
                    price=price,
                    price_yuan=price_yuan,
                    photo_id=None,
                    category="general"
                )
                
                if product_id:
                    log_info(logger, f"Товар добавлен, ID: {product_id}")
                    variation_count = 0
                    
                    for _, row in group.iterrows():
                        try:
                            quantity = int(self.safe_convert(row.get('Кол.'), int, 0))
                            size = str(row.get('Размер', '')).strip()
                            color = str(row.get('Цвет', '')).strip()
                            
                            if not size or pd.isna(size):
                                continue
                            
                            # Используем цену продажи для вариации
                            variation_price = self.safe_convert(row.get('Цена продажи'), float, price)
                            variation_price_yuan = self.safe_convert(row.get('Цена Y'), float, price_yuan)
                            
                            # Формируем ссылку
                            link = str(row.get('Ссылки', '')).strip()
                            
                            # Добавляем информацию о цвете в размер
                            size_with_color = f"{size}"
                            if color and color != 'nan':
                                size_with_color += f" ({color})"
                            
                            self.add_product_variation(
                                product_id=product_id,
                                model_id=model_id,
                                size=size_with_color,
                                quantity=quantity,
                                price=variation_price,
                                price_yuan=variation_price_yuan,
                                link=link if link else None
                            )
                            variation_count += 1
                            
                        except Exception as e:
                            log_error(logger, e, f"Ошибка добавления вариации для {model_name}")
                            continue
                    
                    log_info(logger, f"Добавлено {variation_count} вариаций для {model_name}")
                    success_count += 1
                    
            except Exception as e:
                log_error(logger, e, f"Ошибка добавления товара {model_name}")
                continue
        
        log_info(logger, f"Импорт завершен. Успешно: {success_count}")
        return success_count

    def add_product_variation(self, product_id, model_id, size, quantity, price, price_yuan, link):
        try:
            result = self.__db.db_write(
                '''INSERT INTO product_variations 
                (product_id, model_id, size, quantity, price, price_yuan, link) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (product_id, model_id, size, quantity, price, price_yuan, link)
            )
            return bool(result)
        except Exception as e:
            log_error(logger, e, "Ошибка добавления вариации: {e}")
            return False

    def get_product_with_variations(self, product_id):
        product = self.get_product(product_id)
        if not product:
            return None
            
        variations = self.__db.db_read(
            'SELECT * FROM product_variations WHERE product_id = %s',
            (product_id,)
        )
        
        return {
            'product_id': product['product_id'],
            'name': product['name'],
            'description': product['description'],
            'price': product['price'],
            'photo_id': product['photo_id'],
            'is_available': product['is_available'],
            'variations': [
                {
                    'variation_id': v['variation_id'],
                    'model_id': v['model_id'],
                    'size': v['size'],
                    'quantity': v['quantity'],
                    'price': v['price'],
                    'link': v['link']
                } for v in variations
            ]
        }

    def update_variation_quantity(self, variation_id, new_quantity):
        self.__db.db_write(
            'UPDATE product_variations SET quantity = %s WHERE variation_id = %s',
            (new_quantity, variation_id)
        )

    def get_products_count(self):
        data = self.__db.db_read('SELECT COUNT(*) as count FROM products')
        return data[0]['count'] if data else 0

    def get_variations_count(self):
        data = self.__db.db_read('SELECT COUNT(*) as count FROM product_variations')
        return data[0]['count'] if data else 0

    def get_users_count(self):
        data = self.__db.db_read('SELECT COUNT(*) as count FROM users')
        return data[0]['count'] if data else 0

    def get_reviews_count(self):
        data = self.__db.db_read('SELECT COUNT(*) as count FROM reviews')
        return data[0]['count'] if data else 0

    def update_product_exclusive(self, product_id, is_exclusive, coin_price=0):
        try:
            self.__db.db_write(
                'UPDATE products SET is_exclusive = %s, coin_price = %s WHERE product_id = %s',
                (is_exclusive, coin_price, product_id)
            )
            return True
        except Exception as e:
            log_error(logger, e, "Ошибка обновления товара: {e}")
            return False

    def get_all_products(self):
        return self.__db.db_read('SELECT * FROM products')

    def update_product_photo(self, product_id, photo_id):
        return self.__db.db_write(
            'UPDATE products SET photo_id = %s WHERE product_id = %s',
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
                    'product_id': product['product_id'],
                    'name': product['name'],
                    'model_id': product['model_id'] if product['model_id'] else '',
                    'size': product['size'] if product['size'] else '',
                    'price_yuan': product['price_yuan'] if product['price_yuan'] else 0,
                    'quantity': product['quantity'] if product['quantity'] else 0,
                    'price': product['price'] if product['price'] else 0,
                    'link': product['link'] if product['link'] else '',
                    'is_exclusive': bool(product['is_exclusive']),
                    'coin_price': product['coin_price'] if product['coin_price'] else 0
                })
            
            return result
            
        except Exception as e:
            log_error(logger, e, "Ошибка получения данных для экспорта: {e}")
            return []
        
    def clear_all_products(self):
        try:
            # Удаляем в правильном порядке из-за внешних ключей
            self.__db.db_write('DELETE FROM orders_detailed WHERE product_id IN (SELECT product_id FROM products)')
            self.__db.db_write('DELETE FROM orders WHERE product_id IN (SELECT product_id FROM products)')
            self.__db.db_write('DELETE FROM product_variations')
            self.__db.db_write('DELETE FROM products')
            return True
        except Exception as e:
            log_error(logger, e, "Ошибка очистки товаров: {e}")
            return False

    def create_detailed_order(self, user_id, product_id, size, city, address, full_name, phone, delivery_type):
        try:
            log_info(logger, "DEBUG: Создание заказа для user_id: {user_id}, product_id: {product_id}, size: {size}")
            
            variation_data = self.__db.db_read(
                'SELECT variation_id, quantity FROM product_variations WHERE product_id = %s AND size = %s',
                (product_id, str(size))
            )
            
            log_info(logger, "DEBUG: Найдены вариации: {variation_data}")
            
            if not variation_data:
                log_error(logger, e, "❌ Вариация не найдена - product_id: {product_id}, size: {size}")
                return None
                
            variation_id = variation_data[0]['variation_id']
            current_quantity = variation_data[0]['quantity']
            
            # Проверяем достаточность товара
            if current_quantity <= 0:
                log_error(logger, e, "❌ Товара нет в наличии - product_id: {product_id}, size: {size}")
                return None
                
            log_info(logger, "DEBUG: Используем variation_id: {variation_id}, количество: {current_quantity}")
            
            # Создаем запись в базе
            result = self.__db.db_write(
                '''INSERT INTO orders_detailed 
                (user_id, product_id, variation_id, quantity, city, address, full_name, phone, delivery_type) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                (user_id, product_id, variation_id, 1, city, address, full_name, phone, delivery_type)
            )
            
            # Уменьшаем количество товара
            if result:
                success = self.__db.db_write(
                    'UPDATE product_variations SET quantity = quantity - 1 WHERE variation_id = %s AND quantity > 0',
                    (variation_id,)
                )
                log_info(logger, "DEBUG: Уменьшение количества - success: {success}")
                
            log_info(logger, "DEBUG: Результат создания заказа: {result}")
            return result
            
        except Exception as e:
            log_error(logger, e, "Ошибка создания заказа: {e}")
            return None
        
    def save_order_message_id(self, order_id, message_id, topic_id):
        try:
            return self.__db.db_write(
                'UPDATE orders_detailed SET admin_message_id = %s, admin_topic_id = %s WHERE order_id = %s',
                (message_id, topic_id, order_id)
            )
        except Exception as e:
            log_error(logger, e, "Ошибка сохранения message_id: {e}")
            return False

    def get_order_by_id(self, order_id):
        try:
            data = self.__db.db_read(
                '''SELECT order_id, user_id, product_id, city, address, 
                        full_name, phone, delivery_type, status, 
                        admin_message_id, admin_topic_id, created_at
                FROM orders_detailed 
                WHERE order_id = %s 
                ORDER BY created_at DESC LIMIT 1''', 
                (order_id,)
            )
            if data and data[0]:
                row = data[0]
                log_info(logger, "DEBUG get_order_by_id: order_id={order_id}, found_user_id={row['user_id']}")
                return {
                    'order_id': row['order_id'],
                    'user_id': row['user_id'],
                    'product_id': row['product_id'],
                    'city': row['city'],
                    'address': row['address'],
                    'full_name': row['full_name'],
                    'phone': row['phone'],
                    'delivery_type': row['delivery_type'],
                    'status': row['status'],
                    'admin_message_id': row['admin_message_id'],
                    'admin_topic_id': row['admin_topic_id'],
                    'created_at': row['created_at']
                }
            return None
        except Exception as e:
            log_error(logger, e, "Ошибка в get_order_by_id: {e}")
            return None

    def update_order_status(self, order_id, status):
        return self.__db.db_write(
            'UPDATE orders_detailed SET status = %s WHERE order_id = %s',
            (status, order_id)
        )
    
    def decrease_product_quantity(self, product_id, size):
        try:
            size_str = str(size)
            log_info(logger, "DEBUG: Уменьшение количества - product_id: {product_id}, size: {size_str}")
            
            success = self.__db.db_write(
                'UPDATE product_variations SET quantity = quantity - 1 WHERE product_id = %s AND size = %s',
                (product_id, size_str)
            )
            
            log_info(logger, "DEBUG: Уменьшение количества - success: {success}")
            return bool(success)
            
        except Exception as e:
            log_error(logger, e, "Ошибка уменьшения количества: {e}")
            return False

    def get_product_variations(self, product_id):
        try:
            data = self.__db.db_read(
                'SELECT * FROM product_variations WHERE product_id = %s',
                (product_id,)
            )
            variations = []
            for row in data:
                variation = {
                    'variation_id': row['variation_id'],
                    'product_id': row['product_id'],
                    'model_id': row['model_id'],
                    'size': str(row['size']),
                    'quantity': row['quantity'],
                    'price': row['price'],
                    'price_yuan': row['price_yuan'],
                    'link': row['link']
                }
                log_info(logger, "DEBUG Variation: {variation}")
                variations.append(variation)
            return variations
        except Exception as e:
            log_error(logger, e, "Ошибка получения вариаций: {e}")
            return []
    
    def check_size_availability(self, product_id, size):
        """Проверяет доступность размера"""
        try:
            variations = self.get_product_variations(product_id)
            for variation in variations:
                var_size = str(variation['size']).strip()
                input_size = str(size).strip()
                
                if var_size == input_size:
                    return variation['quantity'] > 0
            return False
        except Exception as e:
            log_error(logger, e, "Ошибка проверки размера: {e}")
            return False
        
    def return_product_quantity(self, order_id):
        """Возвращает товар на склад при отмене заказа"""
        try:
            # Получаем информацию о заказе
            order_data = self.get_order_by_id(order_id)
            if not order_data:
                return False
                
            # Получаем variation_id из заказа
            variation_data = self.__db.db_read(
                'SELECT variation_id, quantity FROM orders_detailed WHERE order_id = %s',
                (order_id,)
            )
            
            if not variation_data:
                return False
                
            variation_id = variation_data[0]['variation_id']
            order_quantity = variation_data[0]['quantity']
            
            # Возвращаем товар на склад
            success = self.__db.db_write(
                'UPDATE product_variations SET quantity = quantity + %s WHERE variation_id = %s',
                (order_quantity, variation_id)
            )
            
            log_info(logger, "DEBUG: Возврат товара - order_id: {order_id}, variation_id: {variation_id}, quantity: {order_quantity}, success: {success}")
            return bool(success)
            
        except Exception as e:
            log_error(logger, e, "Ошибка возврата товара: {e}")
            return False