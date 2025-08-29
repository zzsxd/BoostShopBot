import os
import sqlite3
from threading import Lock
from datetime import datetime

class DB:
    def __init__(self, path, lock):
        super(DB, self).__init__()
        self.__lock = lock
        self.__db_path = path
        self.__cursor = None
        self.__db = None
        self.init()

    def init(self):
        if not os.path.exists(self.__db_path):
            self.__db = sqlite3.connect(self.__db_path, check_same_thread=False)
            self.__cursor = self.__db.cursor()
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    status TEXT DEFAULT 'Новый',
                    comments INTEGER DEFAULT 0,
                    orders INTEGER DEFAULT 0,
                    bs_coin INTEGER DEFAULT 0,
                    discount INTEGER DEFAULT 0,
                    referral_code TEXT,
                    last_active TIMESTAMP,
                    is_admin BOOLEAN DEFAULT FALSE,
                    achievements TEXT DEFAULT '[]'
                )
            ''')
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    description TEXT,
                    price REAL,
                    price_yuan REAL,
                    coin_price INTEGER DEFAULT 0,
                    photo_id TEXT,
                    category TEXT,
                    topic TEXT,
                    is_available BOOLEAN DEFAULT TRUE,
                    is_exclusive BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_id INTEGER,
                    quantity INTEGER,
                    status TEXT DEFAULT 'Новый',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id),
                    FOREIGN KEY(product_id) REFERENCES products(product_id)
                )
            ''')
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referee_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(referrer_id) REFERENCES users(user_id),
                    FOREIGN KEY(referee_id) REFERENCES users(user_id)
                )
            ''')
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    text TEXT,
                    photo_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')

            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS product_variations (
                    variation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    model_id TEXT,
                    size TEXT,
                    quantity INTEGER,
                    price REAL,
                    price_yuan REAL,
                    link TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(product_id) REFERENCES products(product_id)
                )
            ''')

            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    text TEXT,
                    photos TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')
            
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders_detailed (
                    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_id INTEGER,
                    variation_id INTEGER,
                    quantity INTEGER,
                    city TEXT,
                    address TEXT,
                    full_name TEXT,
                    phone TEXT,
                    delivery_type TEXT,
                    status TEXT DEFAULT 'new',
                    admin_message_id INTEGER,
                    admin_topic_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id),
                    FOREIGN KEY(product_id) REFERENCES products(product_id),
                    FOREIGN KEY(variation_id) REFERENCES product_variations(variation_id)
                )
            ''')
            
            self.__db.commit()
        else:
            self.__db = sqlite3.connect(self.__db_path, check_same_thread=False)
            self.__cursor = self.__db.cursor()
            self.migrate_tables()
        self.migrate_users_table()

    def migrate_tables(self):
        self.migrate_orders_detailed_table()

    def migrate_orders_detailed_table(self):
        try:
            self.__cursor.execute("PRAGMA table_info(orders_detailed)")
            columns = [col[1] for col in self.__cursor.fetchall()]
            
            if 'admin_message_id' not in columns:
                self.__cursor.execute("ALTER TABLE orders_detailed ADD COLUMN admin_message_id INTEGER")
                print("✅ Добавлена колонка admin_message_id")
            
            if 'admin_topic_id' not in columns:
                self.__cursor.execute("ALTER TABLE orders_detailed ADD COLUMN admin_topic_id INTEGER")
                print("✅ Добавлена колонка admin_topic_id")
            
            self.__db.commit()
            
        except Exception as e:
            print(f"❌ Ошибка миграции orders_detailed: {e}")
            self.__db.rollback()

    def migrate_users_table(self):
        try:
            self.__cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in self.__cursor.fetchall()]
            if 'last_active' not in columns:
                self.__cursor.execute("ALTER TABLE users ADD COLUMN last_active TIMESTAMP")
                self.__db.commit()
                print("✅ Добавлена колонка last_active в users")
        except Exception as e:
            print(f"❌ Ошибка миграции users: {e}")

    def db_write(self, query, params=None):
        try:
            self.set_lock()
            cursor = self.__db.cursor()
            
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
                
            self.__db.commit()
            
            rows_affected = cursor.rowcount
            print(f"DEBUG DB_WRITE: query='{query}', params={params}, rows_affected={rows_affected}")
            
            return rows_affected
            
        except sqlite3.Error as e:
            print(f"Ошибка записи в БД: {e}")
            print(f"Query: {query}")
            print(f"Params: {params}")
            return 0
        finally:
            self.realise_lock()

    def db_read(self, query, args=()):
        self.set_lock()
        try:
            self.__cursor.execute(query, args)
            return self.__cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка чтения из БД: {e}")
            return []
        finally:
            self.realise_lock()

    def set_lock(self):
        self.__lock.acquire(True)

    def realise_lock(self):
        self.__lock.release()