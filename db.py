import os
import pymysql
import time
from threading import Lock
from datetime import datetime
import logging
from logging_config import get_logger, log_error, log_info

# Настройка логирования
logger = get_logger('db')

class DB:
    def __init__(self, host, user, password, database, port=3306, lock=None):
        super(DB, self).__init__()
        self.__lock = lock or Lock()
        self.__host = host
        self.__user = user
        self.__password = password
        self.__database = database
        self.__port = port
        self.__cursor = None
        self.__db = None
        self.init()

    def init(self):
        try:
            # Убеждаемся, что база данных существует
            self.__ensure_database()

            self.__db = self._connect()
            self.__cursor = self.__db.cursor()
            
            # Создание таблиц
            self.create_tables()
            self.migrate_tables()
            
        except pymysql.Error as e:
            log_error(logger, e, "Ошибка подключения к MySQL")
            raise

    def _connect(self):
        """Создает новое соединение с БД."""
        return pymysql.connect(
            host=self.__host,
            user=self.__user,
            password=self.__password,
            database=self.__database,
            port=self.__port,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=10,
            write_timeout=10,
            autocommit=False,
        )

    def ensure_connection(self):
        """Проверяет соединение и восстанавливает при необходимости."""
        try:
            if self.__db is None:
                self.__db = self._connect()
                self.__cursor = self.__db.cursor()
                return
            # ping с авто-реконнектом
            self.__db.ping(reconnect=True)
            # если курсор закрыт — пересоздаем
            try:
                self.__cursor.execute("SELECT 1")
            except Exception:
                self.__cursor = self.__db.cursor()
        except pymysql.Error as _:
            # жесткий реконнект
            try:
                if self.__cursor:
                    try:
                        self.__cursor.close()
                    except Exception:
                        pass
                if self.__db:
                    try:
                        self.__db.close()
                    except Exception:
                        pass
            finally:
                self.__db = self._connect()
                self.__cursor = self.__db.cursor()

    def __ensure_database(self):
        """Проверяет доступность целевой БД; при отсутствии — создаёт с ретраями."""
        # 1) Сначала пробуем подключиться напрямую к целевой базе — если уже есть, выходим
        try:
            test_conn = pymysql.connect(
                host=self.__host,
                user=self.__user,
                password=self.__password,
                database=self.__database,
                port=self.__port,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
            )
            try:
                with test_conn.cursor() as cur:
                    cur.execute("SELECT 1")
                test_conn.commit()
            finally:
                test_conn.close()
            return
        except pymysql.OperationalError as e:
            # 1049: Unknown database — нужно создать
            if not (e.args and e.args[0] == 1049):
                # Иные ошибки — пробрасываем (таймауты/аутентификация и т.п.)
                log_error(logger, e, "Ошибка проверки существования базы данных")
                raise

        # 2) Создаём базу с ретраями, если её нет
        attempts = 5
        base_delay_seconds = 2
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                tmp_conn = pymysql.connect(
                    host=self.__host,
                    user=self.__user,
                    password=self.__password,
                    port=self.__port,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=10,
                    write_timeout=10
                )
                try:
                    with tmp_conn.cursor() as cur:
                        cur.execute(
                            f"CREATE DATABASE IF NOT EXISTS `{self.__database}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                        )
                    tmp_conn.commit()
                finally:
                    tmp_conn.close()
                return
            except pymysql.OperationalError as e:
                last_err = e
                if e.args and e.args[0] in (2006, 2013, 2014, 2017, 2055):
                    delay_seconds = base_delay_seconds * (2 ** (attempt - 1))
                    log_info(logger, f"Повторная попытка создания БД ({attempt}/{attempts}) через {delay_seconds}s из-за: {e}")
                    time.sleep(delay_seconds)
                    continue
                else:
                    log_error(logger, e, "Ошибка создания базы данных")
                    raise
            except pymysql.Error as e:
                log_error(logger, e, "Ошибка создания базы данных")
                raise
        if last_err:
            log_error(logger, last_err, "Ошибка создания базы данных после ретраев")
            raise last_err

    def create_tables(self):
        try:
            # Таблица users
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    username VARCHAR(255),
                    status VARCHAR(50) DEFAULT 'Новый',
                    comments INT DEFAULT 0,
                    orders INT DEFAULT 0,
                    bs_coin INT DEFAULT 0,
                    discount INT DEFAULT 0,
                    referral_code VARCHAR(255),
                    last_active TIMESTAMP NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    achievements TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица products
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    product_id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255),
                    description TEXT,
                    description_full TEXT,
                    table_id VARCHAR(100),
                    keywords TEXT,
                    price DECIMAL(10, 2),
                    price_yuan DECIMAL(10, 2),
                    coin_price INT DEFAULT 0,
                    photo_id VARCHAR(255),
                    category VARCHAR(100),
                    topic VARCHAR(100),
                    is_available BOOLEAN DEFAULT TRUE,
                    is_exclusive BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица orders
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    product_id INT,
                    quantity INT,
                    status VARCHAR(50) DEFAULT 'Новый',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица referrals
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referral_id INT AUTO_INCREMENT PRIMARY KEY,
                    referrer_id BIGINT,
                    referee_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (referee_id) REFERENCES users(user_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица reviews
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    text TEXT,
                    photo_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица product_variations
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS product_variations (
                    variation_id INT AUTO_INCREMENT PRIMARY KEY,
                    product_id INT,
                    model_id VARCHAR(255),
                    size VARCHAR(50),
                    quantity INT,
                    price DECIMAL(10, 2),
                    price_yuan DECIMAL(10, 2),
                    link TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')    
            
            # Таблица pending_reviews
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    review_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    text TEXT,
                    photos TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица orders_detailed
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders_detailed (
                    order_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    product_id INT,
                    variation_id INT,
                    quantity INT,
                    city VARCHAR(255),
                    address TEXT,
                    full_name VARCHAR(255),
                    phone VARCHAR(50),
                    delivery_type VARCHAR(100),
                    status VARCHAR(50) DEFAULT 'new',
                    admin_message_id BIGINT,
                    admin_topic_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                    FOREIGN KEY (variation_id) REFERENCES product_variations(variation_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # Таблица achievements
            self.__cursor.execute('''
                CREATE TABLE IF NOT EXISTS achievements (
                    achievement_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    achievement_code VARCHAR(100),
                    achievement_name VARCHAR(255),
                    achievement_description TEXT,
                    achievement_category VARCHAR(50),
                    bs_coin_reward INT DEFAULT 0,
                    discount_bonus INT DEFAULT 0,
                    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    UNIQUE KEY unique_user_achievement (user_id, achievement_code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            self.__db.commit()
            log_info(logger, "Таблицы успешно созданы/проверены")
            
        except pymysql.Error as e:
            log_error(logger, e, "Ошибка создания таблиц")
            self.__db.rollback()
            raise

    def migrate_tables(self):
        self.migrate_orders_detailed_table()
        self.migrate_users_table()
        self.migrate_products_table()

    def migrate_orders_detailed_table(self):
        try:
            # Проверяем существование колонок
            self.__cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'orders_detailed' 
                AND TABLE_SCHEMA = DATABASE()
            """)
            columns = [row['COLUMN_NAME'] for row in self.__cursor.fetchall()]
            
            if 'admin_message_id' not in columns:
                self.__cursor.execute("ALTER TABLE orders_detailed ADD COLUMN admin_message_id BIGINT")
                log_info(logger, "✅ Добавлена колонка admin_message_id")
            
            if 'admin_topic_id' not in columns:
                self.__cursor.execute("ALTER TABLE orders_detailed ADD COLUMN admin_topic_id BIGINT")
                log_info(logger, "✅ Добавлена колонка admin_topic_id")
            
            self.__db.commit()
            
        except pymysql.Error as e:
            log_error(logger, e, "❌ Ошибка миграции orders_detailed")
            self.__db.rollback()

    def migrate_users_table(self):
        try:
            self.__cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'users' 
                AND TABLE_SCHEMA = DATABASE()
            """)
            columns = [row['COLUMN_NAME'] for row in self.__cursor.fetchall()]
            
            if 'last_active' not in columns:
                self.__cursor.execute("ALTER TABLE users ADD COLUMN last_active TIMESTAMP NULL")
                self.__db.commit()
                log_info(logger, "✅ Добавлена колонка last_active в users")
            
            # Убираем default у TEXT поля achievements, если он есть
            try:
                self.__cursor.execute("ALTER TABLE users MODIFY achievements TEXT NULL")
                self.__db.commit()
                log_info(logger, "✅ Обновлена колонка achievements (TEXT без DEFAULT)")
            except Exception as _:
                pass
                
        except pymysql.Error as e:
            log_error(logger, e, "❌ Ошибка миграции users")

    def migrate_products_table(self):
        try:
            # Проверяем существование колонок
            self.__cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'products' 
                AND TABLE_SCHEMA = DATABASE()
            """)
            columns = [row['COLUMN_NAME'] for row in self.__cursor.fetchall()]
            
            # Добавляем новые колонки, если их нет
            if 'description_full' not in columns:
                self.__cursor.execute("ALTER TABLE products ADD COLUMN description_full TEXT NULL")
                self.__db.commit()
                log_info(logger, "✅ Добавлена колонка description_full в products")
            
            if 'table_id' not in columns:
                self.__cursor.execute("ALTER TABLE products ADD COLUMN table_id VARCHAR(100) NULL")
                self.__db.commit()
                log_info(logger, "✅ Добавлена колонка table_id в products")
            
            if 'keywords' not in columns:
                self.__cursor.execute("ALTER TABLE products ADD COLUMN keywords TEXT NULL")
                self.__db.commit()
                log_info(logger, "✅ Добавлена колонка keywords в products")
                
        except pymysql.Error as e:
            log_error(logger, e, "❌ Ошибка миграции products")

    def db_write(self, query, params=None):
        self.set_lock()
        try:
            self.ensure_connection()
            cursor = self.__db.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                self.__db.commit()
                rows_affected = cursor.rowcount
                logger.debug(f"DB_WRITE: query='{query}', params={params}, rows_affected={rows_affected}")
                return rows_affected
            except pymysql.OperationalError as e:
                # Ошибки потери соединения: 2006 (MySQL server has gone away), 2013 (Lost connection during query)
                if e.args and e.args[0] in (2006, 2013, 2014, 2017, 2055):
                    log_info(logger, "Обнаружен разрыв соединения, выполняю реконнект и повтор" )
                    self.ensure_connection()
                    cursor = self.__db.cursor()
                    if params:
                        cursor.execute(query, params)
                    else:
                        cursor.execute(query)
                    self.__db.commit()
                    return cursor.rowcount
                raise
        except pymysql.Error as e:
            log_error(logger, e, f"Ошибка записи в БД. Query: {query}, Params: {params}")
            try:
                self.__db.rollback()
            except Exception:
                pass
            return 0
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            self.realise_lock()

    def db_read(self, query, args=()):
        self.set_lock()
        try:
            self.ensure_connection()
            try:
                self.__cursor.execute(query, args)
                return self.__cursor.fetchall()
            except pymysql.OperationalError as e:
                if e.args and e.args[0] in (2006, 2013, 2014, 2017, 2055):
                    log_info(logger, "Разрыв соединения при чтении, реконнект и повтор")
                    self.ensure_connection()
                    self.__cursor.execute(query, args)
                    return self.__cursor.fetchall()
                raise
        except pymysql.Error as e:
            log_error(logger, e, "❌ Ошибка чтения из БД")
            return []
        finally:
            self.realise_lock()

    def set_lock(self):
        self.__lock.acquire(True)

    def realise_lock(self):
        self.__lock.release()

    def close(self):
        """Закрытие соединения с БД"""
        if self.__cursor:
            self.__cursor.close()
        if self.__db:
            self.__db.close()

    def __del__(self):
        self.close()