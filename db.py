# db.py
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
            
            self.__db.commit()
        else:
            self.__db = sqlite3.connect(self.__db_path, check_same_thread=False)
            self.__cursor = self.__db.cursor()
        
        try:
            self.__cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in self.__cursor.fetchall()]
            if 'last_active' not in columns:
                self.__cursor.execute("ALTER TABLE users ADD COLUMN last_active TIMESTAMP")
                self.__db.commit()
        except Exception as e:
            print(f"Ошибка при проверке столбца last_active: {e}")

    def db_write(self, query, args=()):
        self.set_lock()
        try:
            self.__cursor.execute(query, args)
            status = self.__cursor.lastrowid
            self.__db.commit()
            return status
        except Exception as e:
            print(f"Ошибка записи в БД: {e}")
            print(f"Запрос: {query}")
            print(f"Аргументы: {args}")
            self.__db.rollback()
            return None
        finally:
            self.realise_lock()

    def db_read(self, query, args=()):
        self.set_lock()
        try:
            self.__cursor.execute(query, args)
            return self.__cursor.fetchall()
        except Exception as e:
            print(f"Ошибка чтения из БД: {e}")
            return []
        finally:
            self.realise_lock()

    def set_lock(self):
        self.__lock.acquire(True)

    def realise_lock(self):
        self.__lock.release()