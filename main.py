import time
import telebot
import os
import traceback
import re
import json
import threading
import platform
import logging
import pandas as pd
import requests
import urllib.parse
import pathlib
import shutil
import time
from threading import Lock
from datetime import datetime, timedelta
from config_parser import ConfigParser
from frontend import Bot_inline_btns
from telebot import types
from backend import DbAct
from db import DB
from logging_config import setup_logging, get_logger, log_error, log_info

# Инициализация логирования
setup_logging()
logger = get_logger('bot')

print("Инициализация бота...")
log_info(logger, "Старт инициализации")

config_name = 'secrets.json'
os_type = platform.system()
work_dir = os.path.dirname(os.path.realpath(__file__))

try:
    config = ConfigParser(f'{work_dir}/{config_name}', os_type)
    config_data = config.get_config()
    mysql_config = config_data.get('mysql', {})
    host_raw = mysql_config.get('host', '127.0.0.1')
    port_raw = mysql_config.get('port', 3306)

    # Если в host передан URL (например, http://localhost:8000/index.php?route=/), распарсим
    parsed_host = urllib.parse.urlparse(host_raw) if isinstance(host_raw, str) and host_raw.startswith('http') else None
    if parsed_host:
        host = parsed_host.hostname or '127.0.0.1'
        # Порт MySQL оставляем стандартный, т.к. 8000 — это, скорее всего, порт веб-интерфейса
        port = 3306
    else:
        host = host_raw
        # Если по ошибке указан 8000, заменим на 3306
        port = 3306 if str(port_raw) == '8000' else port_raw

    db = DB(
        host=host,
        user=mysql_config.get('user', 'root'),
        password=mysql_config.get('password', '12345678'),
        database=mysql_config.get('database', 'bridgeside_bot'),
        port=port,
        lock=Lock()
    )
    db_actions = DbAct(db, config, config_data['xlsx_path'])
    bot = telebot.TeleBot(config.get_config()['tg_api'])
except SystemExit as e:
    # Ошибки из ConfigParser (например, нет secrets.json или пустой tg_api)
    log_error(logger, e, "Ошибка конфигурации при запуске")
    print(f"Ошибка конфигурации: {e}")
    raise
except Exception as e:
    # Любые другие ошибки ранней инициализации (например, БД)
    log_error(logger, e, "Ошибка ранней инициализации")
    print(f"Ошибка инициализации: {e}")
    raise

temp_data = {}
pending_reviews = {}

def clear_temp_data(user_id):
    """Очистить временные данные пользователя"""
    if user_id in temp_data:
        # Если есть фото в процессе создания поста, очистим их
        photos = temp_data[user_id].get('photos', [])
        if photos:
            cleanup_local_files(photos)
        del temp_data[user_id]
channels = [
    '@BridgeSide_Featback',
    '@BridgeSide_LifeStyle', 
    '@BridgeSide_Store'
]

# ============ СИСТЕМА АЧИВОК ============

ACHIEVEMENTS = {
    # Линия "МОСТ" — стиль + технологии
    'pilot_bridge': {
        'name': '🛸 Пилот Моста',
        'description': 'Первая покупка',
        'category': 'МОСТ',
        'bs_coin_reward': 500,
        'discount_bonus': 0,
        'condition': 'first_purchase'
    },
    'style_engineer': {
        'name': '⚙️ Инженер Стиля',
        'description': 'Лук: 3+ вещи разных брендов в одном заказе',
        'category': 'МОСТ',
        'bs_coin_reward': 1000,
        'discount_bonus': 0,
        'condition': 'multi_brand_order'
    },
    
    # Линия "БЕРЕГ" — лояльность
    'pioneer': {
        'name': '💡 Первопроходец',
        'description': 'Первый отзыв с фото',
        'category': 'БЕРЕГ',
        'bs_coin_reward': 100,
        'discount_bonus': 0,
        'condition': 'first_review_with_photo'
    },
    'cornerstone': {
        'name': '🧱 Краеугольный Камень',
        'description': 'Уровень лояльности 5 "Легенда"',
        'category': 'БЕРЕГ',
        'bs_coin_reward': 1000,
        'discount_bonus': 10,
        'condition': 'loyalty_level_5'
    },
    
    # Линия "КОЛЛЕКТИВ" — за приглашения
    'connector': {
        'name': '🔌 Соединяющий',
        'description': 'Привёл 3 зарегистрировавшихся друга по реф-ссылке',
        'category': 'КОЛЛЕКТИВ',
        'bs_coin_reward': 300,
        'discount_bonus': 0,
        'condition': 'three_referrals'
    }
}

def check_achievement_conditions(user_id, condition_type, **kwargs):
    """Проверить условия для получения ачивок"""
    try:
        for achievement_code, achievement_data in ACHIEVEMENTS.items():
            if achievement_data['condition'] == condition_type:
                # Проверяем, есть ли уже эта ачивка
                if db_actions.get_achievement_by_code(user_id, achievement_code):
                    continue
                
                # Проверяем условие
                if check_achievement_condition(user_id, condition_type, achievement_code, **kwargs):
                    # Добавляем ачивку
                    if db_actions.add_achievement(user_id, achievement_code, achievement_data):
                        # Уведомляем пользователя
                        notify_achievement_earned(user_id, achievement_data)
                        return True
    except Exception as e:
        log_error(logger, e, f"Ошибка проверки ачивок для пользователя {user_id}")
    return False

def check_achievement_condition(user_id, condition_type, achievement_code, **kwargs):
    """Проверить конкретное условие ачивки"""
    try:
        if condition_type == 'first_purchase':
            # Первая покупка
            orders = db_actions.get_user_orders(user_id)
            return len(orders) == 1
            
        elif condition_type == 'multi_brand_order':
            # 3+ вещи разных брендов в одном заказе
            # Это нужно будет реализовать при создании заказов
            return False  # Пока не реализовано
            
        elif condition_type == 'first_review_with_photo':
            # Первый отзыв с фото
            reviews = db_actions.get_user_reviews(user_id)
            for review in reviews:
                if review.get('photos') and len(review['photos']) > 0:
                    return True
            return False
            
        elif condition_type == 'loyalty_level_5':
            # Уровень лояльности 5 "Легенда"
            # Это нужно будет реализовать на основе системы лояльности
            return False  # Пока не реализовано
            
        elif condition_type == 'three_referrals':
            # 3 реферала
            referral_count = db_actions.get_referral_stats(user_id)
            return referral_count >= 3
            
    except Exception as e:
        log_error(logger, e, f"Ошибка проверки условия ачивки {achievement_code}")
    return False

def notify_achievement_earned(user_id, achievement_data):
    """Уведомить пользователя о получении ачивки"""
    try:
        message = (
            f"🎉 Поздравляем! Вы получили ачивку!\n\n"
            f"{achievement_data['name']}\n"
            f"{achievement_data['description']}\n\n"
        )
        
        if achievement_data['bs_coin_reward'] > 0:
            message += f"💰 +{achievement_data['bs_coin_reward']} BS Coin\n"
        
        if achievement_data['discount_bonus'] > 0:
            message += f"🎯 +{achievement_data['discount_bonus']}% постоянная скидка\n"
        
        message += f"\n🏆 Категория: {achievement_data['category']}"
        
        bot.send_message(user_id, message)
        
    except Exception as e:
        log_error(logger, e, f"Ошибка уведомления о ачивке для пользователя {user_id}")

# ============ ИНТЕГРАЦИЯ С ЯНДЕКС.ДИСК ============

YANDEX_DISK_BASE_PATH = "BridgeSideBot/Boots"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"

def get_yadisk_tokens():
    """Получить токены Яндекс.Диска с автоматическим обновлением"""
    cfg = config.get_config()
    yadisk_config = cfg.get('yadisk', {})
    
    client_id = yadisk_config.get('client_id')
    client_secret = yadisk_config.get('client_secret')
    access_token = yadisk_config.get('access_token')
    refresh_token = yadisk_config.get('refresh_token')
    expires_at = yadisk_config.get('expires_at', 0)
    
    if not client_id or not client_secret:
        raise RuntimeError("Не настроены client_id и client_secret для Яндекс.Диска")
    
    # Проверяем, не истёк ли токен
    if access_token and expires_at > time.time():
        return access_token
    
    # Если есть refresh_token, обновляем токен
    if refresh_token:
        try:
            new_tokens = refresh_yadisk_token(client_id, client_secret, refresh_token)
            config.update_yadisk_tokens(
                new_tokens['access_token'],
                new_tokens['refresh_token'],
                new_tokens['expires_in']
            )
            return new_tokens['access_token']
        except Exception as e:
            log_error(logger, e, "Ошибка обновления токена Яндекс.Диска")
    
    # Если нет токенов или не удалось обновить
    raise RuntimeError("Требуется авторизация Яндекс.Диска. Используйте /yadisk_auth")

def refresh_yadisk_token(client_id, client_secret, refresh_token):
    """Обновить токен Яндекс.Диска"""
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret
    }
    
    response = requests.post(YANDEX_TOKEN_URL, data=data, timeout=30)
    response.raise_for_status()
    return response.json()

def yadisk_headers(token: str) -> dict:
    return {"Authorization": f"OAuth {token}"}

def yadisk_list_images(product_id: str) -> list:
    token = get_yadisk_tokens()

    # Разрешаем переопределить базовый путь в конфиге: yadisk.base_path
    try:
        cfg = config.get_config()
        base_path = (cfg.get('yadisk', {}) or {}).get('base_path') or YANDEX_DISK_BASE_PATH
    except Exception:
        base_path = YANDEX_DISK_BASE_PATH

    # Пробуем несколько вариантов директорий: base/<id> и base/Boots/<id>
    candidate_folders = [f"{base_path}/{product_id}"]
    if "/Boots" not in base_path:
        candidate_folders.append(f"{base_path}/Boots/{product_id}")
    else:
        # Вариант без "Boots"
        candidate_folders.append(f"{base_path.replace('/Boots', '')}/{product_id}")

    seen = set()
    candidate_folders = [p for p in candidate_folders if not (p in seen or seen.add(p))]

    images_prefixed = []
    images_all = []

    for folder in candidate_folders:
        params = {
            "path": folder,
            "limit": 1000,
            "fields": "name,_embedded.items.name,_embedded.items.path,_embedded.items.type,_embedded.items.media_type",
            "_embedded.limit": 1000,
        }
        resp = requests.get(
            "https://cloud-api.yandex.net/v1/disk/resources",
            headers=yadisk_headers(token), params=params, timeout=30
        )
        if resp.status_code == 404:
            # Путь не найден — пробуем следующий
            continue
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("_embedded") or {}).get("items", [])

        # Сначала собираем строго forbot_*, затем общий список изображений
        for it in items:
            if it.get("type") != "file":
                continue
            if not (it.get("media_type") or "").startswith("image"):
                continue
            name = str(it.get("name", ""))
            path = it.get("path")
            if not path:
                continue
            if name.startswith("forbot_"):
                images_prefixed.append(path)
            images_all.append(path)

        # Если нашли что-то в этой папке — прекращаем искать дальше (приоритет первой найденной)
        if images_prefixed or images_all:
            break

    # Отдаём приоритет файлам с префиксом forbot_, иначе любые изображения
    return images_prefixed if images_prefixed else images_all

def yadisk_get_download_href(file_path: str) -> str:
    token = get_yadisk_tokens()
    r = requests.get("https://cloud-api.yandex.net/v1/disk/resources/download",
                     headers=yadisk_headers(token), params={"path": file_path}, timeout=30)
    r.raise_for_status()
    return r.json()["href"]

def download_photos_from_yadisk(product_id: str) -> list:
    dest_dir = os.path.join("/tmp", "bsbot", str(product_id))
    pathlib.Path(dest_dir).mkdir(parents=True, exist_ok=True)
    local_files: list[str] = []
    
    try:
        image_paths = yadisk_list_images(product_id)
        if not image_paths:
            log_info(logger, f"Нет изображений для товара {product_id}")
            return []
            
        for ypath in image_paths:
            try:
                href = yadisk_get_download_href(ypath)
                with requests.get(href, headers=yadisk_headers(get_yadisk_tokens()), stream=True, timeout=30) as resp:
                    resp.raise_for_status()
                    filename = os.path.basename(urllib.parse.urlparse(ypath).path)
                    local_path = os.path.join(dest_dir, filename)
                    with open(local_path, "wb") as f:
                        shutil.copyfileobj(resp.raw, f)
                    local_files.append(local_path)
                    log_info(logger, f"Скачано фото: {filename}")
            except Exception as e:
                log_error(logger, e, f"Ошибка скачивания фото {ypath}")
                continue
                
    except Exception as e:
        log_error(logger, e, f"Ошибка получения списка изображений для товара {product_id}")
        return []
        
    return local_files

def cleanup_local_files(paths: list) -> None:
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

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

def get_product_price(product):
    """Получить цену продукта"""
    return get_product_field(product, 'price', 0), '₽'

def get_product_name(product):
    """Получить название продукта"""
    return get_product_field(product, 'name', 'Неизвестно')

def show_product(user_id, product_id):
    product = db_actions.get_product(product_id)
    if not product:
        bot.send_message(user_id, "Товар не найден")
        return
    variations = db_actions.get_product_variations(product_id)
    available_sizes = [v for v in variations if v['quantity'] > 0]
    
    buttons = Bot_inline_btns()
    
    # Получаем данные товара
    product_name = get_product_field(product, 'name', 'Неизвестно')
    description_full = get_product_field(product, 'description_full', '')
    description_old = get_product_field(product, 'description', '')
    table_id = get_product_field(product, 'table_id', '')
    # Для отображения артикула используем table_id, а при его отсутствии — model_id из вариаций.
    # Никогда не показываем числовой product_id как артикул.
    first_model_id_for_display = None
    try:
        first_model_id_for_display = next((v.get('model_id') for v in variations if v.get('model_id')), None)
    except Exception:
        pass
    # Также учитываем временно введённый админом артикул при создании поста
    admin_entered_table_id = temp_data.get(user_id, {}).get('table_id') if user_id in temp_data else None
    article_to_show = (str(table_id).strip() if table_id and str(table_id).strip() else None) 
    if not article_to_show and admin_entered_table_id and str(admin_entered_table_id).strip():
        article_to_show = str(admin_entered_table_id).strip()
    if not article_to_show and first_model_id_for_display and str(first_model_id_for_display).strip():
        article_to_show = str(first_model_id_for_display).strip()
    keywords = get_product_field(product, 'keywords', '')
    price = get_product_field(product, 'price', 0)
    
    # Формируем описание
    caption_parts = []
    
    # Название товара
    caption_parts.append(f"🛍️ *{product_name}*")
    
    # Описание товара (приоритет новому полю, если пустое - используем старое)
    description_to_show = description_full if description_full else description_old
    if description_to_show and description_to_show.strip():
        # Убираем хештеги из описания, если они есть
        description_clean = description_to_show
        if '\n' in description_clean:
            lines = description_clean.split('\n')
            # Убираем строки с хештегами из описания
            description_clean = '\n'.join([line for line in lines if not line.strip().startswith('#')]).strip()
        
        if description_clean:
            # Форматируем как blockquote
            quoted_description = '\n'.join([f"> {line}" for line in description_clean.split('\n')])
            caption_parts.append(quoted_description)
    
    # Артикул товара
    if article_to_show:
        caption_parts.append(f"🆔 Артикул: `{article_to_show}`")
    
    # Цена
    if price > 0:
        caption_parts.append(f"💰 Цена: {price}₽")
    else:
        caption_parts.append("💰 Цена: Уточняйте")
    
    # Доступные размеры
    if available_sizes:
        caption_parts.append("📏 Доступные размеры:")
    for variation in available_sizes:
            caption_parts.append(f"• {variation['size']} - {variation['quantity']} шт.")
    
    # Хештеги (извлекаем из описания или используем поле keywords)
    hashtags_to_show = ""
    
    # Сначала пытаемся извлечь хештеги из описания
    if description_to_show and '\n' in description_to_show:
        lines = description_to_show.split('\n')
        hashtag_lines = [line.strip() for line in lines if line.strip().startswith('#')]
        if hashtag_lines:
            hashtags_to_show = ' '.join(hashtag_lines)
    
    # Если хештеги не найдены в описании, используем поле keywords
    if not hashtags_to_show and keywords and keywords.strip():
        hashtags_to_show = keywords.strip()
    
    # Добавляем хештеги в конец
    if hashtags_to_show:
        caption_parts.append(f"\n{hashtags_to_show}")
    
    caption = "\n\n".join(caption_parts)
    
    if available_sizes:
        markup = buttons.size_selection_buttons(available_sizes)
    else:
        markup = None
        
    # Пытаемся получить фотографии с Яндекс.Диска, пробуя несколько идентификаторов в порядке приоритета:
    # 1) table_id (артикул), 2) model_id из вариаций, 3) числовой product_id
    photos = []
    candidate_ids = []
    if article_to_show:
        candidate_ids.append(article_to_show)
    # Берём первый model_id из доступных вариаций, если есть
    try:
        first_model_id = next((v.get('model_id') for v in variations if v.get('model_id')), None)
        if first_model_id and str(first_model_id).strip() not in candidate_ids:
            candidate_ids.append(str(first_model_id).strip())
    except Exception:
        pass
    # Фолбэк на product_id
    if str(product_id) not in candidate_ids:
        candidate_ids.append(str(product_id))

    used_identifier = None
    for candidate in candidate_ids:
        try:
            photos = download_photos_from_yadisk(candidate)
            if photos:
                used_identifier = candidate
                log_info(logger, f"Найдено {len(photos)} фото по идентификатору '{candidate}'")
                break
            else:
                log_info(logger, f"Нет фото по идентификатору '{candidate}'")
        except Exception as e:
            log_error(logger, e, f"Ошибка скачивания фото с Яндекс.Диска для '{candidate}'")
            photos = []
    
    # Если есть фотографии с Яндекс.Диска, отправляем только первую
    if photos:
        first_file = None
        try:
            first_path = photos[0]
            first_file = open(first_path, 'rb')
            bot.send_photo(
                user_id,
                first_file,
                caption=caption,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            log_error(logger, e, f"Ошибка отправки фото для товара {product_id}")
        finally:
            try:
                if first_file:
                    first_file.close()
            except Exception:
                pass
            # Удаляем все временные файлы
            cleanup_local_files(photos)
    
    # Фолбэк: пытаемся использовать photo_id из базы данных
    photo_id = get_product_field(product, 'photo_id')
    if photo_id and photo_id != 'None' and photo_id != 'invalid':
        try:
            bot.send_photo(
                user_id,
                photo_id,
                caption=caption,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            log_error(logger, e, f"Ошибка отправки фото из БД: {e}")
    
    # Если нет фотографий, отправляем только текст
    bot.send_message(
        user_id,
        caption,
        reply_markup=markup,
        parse_mode="Markdown"
    )

def check_and_fix_photos():
    try:
        products = db_actions.get_all_products()
        for product in products:
            product_id, name, _, _, _, photo_id, _, _, _, _, _ = product
            if photo_id:
                try:
                    file_info = bot.get_file(photo_id)
                    log_info(logger, f"Photo {photo_id} для товара {name} доступен")
                except Exception as e:
                    log_error(logger, e, f"Photo {photo_id} для товара {name} недоступен")
                    db_actions.update_product_photo(product_id, None)
        log_info(logger, "Проверка фото завершена")
    except Exception as e:
        log_error(logger, e, "Ошибка при проверке фото")

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


def process_products_file(message):
    user_id = message.from_user.id
    if not message.document:
        bot.send_message(user_id, "Пожалуйста, отправьте Excel файл")
        return
        
    try:
        bot.send_message(user_id, "🔄 Очищаем старые товары...")
        db_actions.clear_all_products()
        
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        filename = f"products_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        with open(filename, 'wb') as f:
            f.write(downloaded_file)
        
        # Проверяем, есть ли листы "ЭКОНОМИКА" и "КЛЮЧИ"
        excel_file = pd.ExcelFile(filename)
        sheet_names = excel_file.sheet_names
        
        if 'ЭКОНОМИКА' in sheet_names and 'КЛЮЧИ' in sheet_names:
            # Новая структура с двумя листами
            bot.send_message(user_id, "📊 Обнаружена новая структура файла с листами 'ЭКОНОМИКА' и 'КЛЮЧИ'")
            
            economics_df = pd.read_excel(filename, sheet_name='ЭКОНОМИКА')
            keys_df = pd.read_excel(filename, sheet_name='КЛЮЧИ')
            
            # Проверяем необходимые колонки в листе "ЭКОНОМИКА"
            required_economics_columns = ['Модель', 'ID модели', 'Размер', 'Цена Y', 'Кол.', 'Цена продажи', 'Цвет', 'Ссылки']
            missing_economics_columns = [col for col in required_economics_columns if col not in economics_df.columns]
            
            if missing_economics_columns:
                bot.send_message(user_id, f"❌ В листе 'ЭКОНОМИКА' отсутствуют колонки: {', '.join(missing_economics_columns)}")
                os.remove(filename)
                return
            
            # Проверяем необходимые колонки в листе "КЛЮЧИ"
            required_keys_columns = ['ID', 'Краткое описание товара Telegram', '#Хештеги']
            missing_keys_columns = [col for col in required_keys_columns if col not in keys_df.columns]
            
            if missing_keys_columns:
                bot.send_message(user_id, f"❌ В листе 'КЛЮЧИ' отсутствуют колонки: {', '.join(missing_keys_columns)}")
                os.remove(filename)
                return
            
            success_count = db_actions.import_products_from_excel_new_format(economics_df, keys_df)
            
            total_products = len(economics_df['Модель'].unique())
            total_variations = len(economics_df)
            zero_quantity = len(economics_df[economics_df['Кол.'].fillna(0) == 0])
            
            stats_msg = (
                f"✅ Успешно импортировано {success_count} товаров\n\n"
                f"📊 Статистика:\n"
                f"• Уникальных моделей: {total_products}\n"
                f"• Всего вариаций: {total_variations}\n"
                f"• С нулевым количеством: {zero_quantity}\n"
                f"• Диапазон цен: {economics_df['Цена продажи'].min():.0f} - {economics_df['Цена продажи'].max():.0f}₽\n\n"
                f"📊 Использована новая структура с описаниями и хештегами"
            )
            
            bot.send_message(user_id, stats_msg)
            
            sample_msg = "📋 Пример первых 5 товаров:\n"
            for i, (_, row) in enumerate(economics_df.head().iterrows()):
                sample_msg += f"{i+1}. {row['Модель']} - {row['Размер']} - {row['Цена продажи']}₽\n"
            
            bot.send_message(user_id, sample_msg)
            return
            
        else:
            # Старая структура с одним листом
            bot.send_message(user_id, "📊 Обнаружена старая структура файла")
        
        df = pd.read_excel(filename)

        # Нормализуем названия колонок для старого формата (частые варианты из файлов)
        def _normalize_column_name(name):
            return str(name).strip().lower()

        column_synonyms = {
            'id модели': 'ID Модели',
            'id модели.': 'ID Модели',
            'кол.': 'Количество',
            'количество': 'Количество',
            'ссылки': 'Ссылка',
            'ссылка': 'Ссылка',
            'цена продажи': 'Цена',
            'цена': 'Цена',
            'цена y': 'Цена Y',
            'модель': 'Модель',
            'размер': 'Размер',
        }

        normalized_columns = {}
        for original_col in list(df.columns):
            key = _normalize_column_name(original_col)
            if key in column_synonyms:
                target = column_synonyms[key]
                if original_col != target and target not in df.columns:
                    df.rename(columns={original_col: target}, inplace=True)
                normalized_columns[target] = True
            else:
                # оставляем исходное имя, если нет синонима
                normalized_columns[original_col] = True

        required_columns = ['Модель', 'ID Модели', 'Размер', 'Цена Y', 'Количество', 'Цена', 'Ссылка']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            bot.send_message(user_id, f"❌ Неверный формат файла. Отсутствуют колонки: {', '.join(missing_columns)}")
            os.remove(filename)
            return
        
        def calculate_price(row):
            try:
                price_yuan = row['Цена Y']
                if pd.isna(price_yuan) or price_yuan == 0:
                    return 0
            
                if isinstance(row['Цена'], (int, float)) and not pd.isna(row['Цена']):
                    return float(row['Цена'])
                
                if isinstance(row['Цена'], str) and row['Цена'].startswith('='):
                    return float(price_yuan) * 12
                
                return float(row['Цена'])
            except:
                return float(price_yuan) * 12 
        
        df['Цена'] = df.apply(calculate_price, axis=1)
        
        df['ID Модели'] = df['ID Модели'].astype(str).apply(lambda x: x.split('.')[0] if '.' in x else x).str.strip()
        
        def safe_convert(value, default=0):
            if value is None or value == '' or pd.isna(value):
                return default
            try:
                if isinstance(value, str):
                    value = value.strip().replace(',', '.')
                    if not value:
                        return default
                return float(value)
            except (ValueError, TypeError):
                return default
        
        df['Количество'] = df['Количество'].apply(lambda x: int(safe_convert(x, 0)))
        df['Цена Y'] = df['Цена Y'].apply(lambda x: safe_convert(x, 0))
        
        df['Модель'] = df['Модель'].fillna('Неизвестно').astype(str)
        df['Размер'] = df['Размер'].fillna('').astype(str)
        df['Ссылка'] = df['Ссылка'].fillna('').astype(str)
        
        if df['Цена'].isnull().all() or (df['Цена'] == 0).all():
            bot.send_message(user_id, "❌ Ошибка: не удалось вычислить цены. Проверьте формат файла.")
            os.remove(filename)
            return
    
        success_count = db_actions.import_products_from_excel(df)
        
        total_products = len(df['Модель'].unique())
        total_variations = len(df)
        zero_quantity = len(df[df['Количество'] == 0])
        
        stats_msg = (
            f"✅ Успешно импортировано {success_count} товаров\n\n"
            f"📊 Статистика:\n"
            f"• Уникальных моделей: {total_products}\n"
            f"• Всего вариаций: {total_variations}\n"
            f"• С нулевым количеством: {zero_quantity}\n"
                f"• Диапазон цен: {df['Цена'].min():.0f} - {df['Цена'].max():.0f}₽\n\n"
                f"📊 Использована старая структура"
        )
        
        bot.send_message(user_id, stats_msg)
        
        sample_msg = "📋 Пример первых 5 товаров:\n"
        for i, (_, row) in enumerate(df.head().iterrows()):
            sample_msg += f"{i+1}. {row['Модель']} - {row['Размер']} - {row['Цена']}₽\n"
        
        bot.send_message(user_id, sample_msg)
        
    except Exception as e:
        error_msg = f"❌ Ошибка при обработке файла: {str(e)}"
        log_error(logger, e, "Ошибка импорта товаров")
        bot.send_message(user_id, error_msg)
    finally:
        if 'filename' in locals() and os.path.exists(filename):
            os.remove(filename)

def create_review_topic(user_data):
    """Создать топик для отзыва в группе админов"""
    try:
        admin_group_id = -1002585832553
        topic_name = f"{user_data['first_name']} {user_data['last_name']} ОТЗЫВ"
        
        # Создаем топик
        result = bot.create_forum_topic(
            chat_id=admin_group_id,
            name=topic_name
        )
        
        if result and result.message_thread_id:
            return result.message_thread_id
        else:
            log_error(logger, None, f"Не удалось создать топик для отзыва: {topic_name}")
            return None
            
    except Exception as e:
        log_error(logger, e, f"Ошибка создания топика для отзыва: {topic_name}")
        return None

def send_review_for_moderation(user_id, review_data):
    try:
        user_data = db_actions.get_user_data(user_id)
        admin_group_id = -1002585832553
        
        # Создаем топик для отзыва
        topic_id = create_review_topic(user_data)
        if not topic_id:
            log_error(logger, None, "Не удалось создать топик для отзыва, отправляем в общий чат")
        
        caption = (
            f"📝 Новый отзыв на модерацию\n\n"
            f"👤 Пользователь: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 @{user_data['username']}\n\n"
            f"📄 Текст: {review_data['text'][:500]}...\n\n"
            f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        markup = types.InlineKeyboardMarkup()
        approve_btn = types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_review_{user_id}")
        reject_btn = types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_review_{user_id}")
        markup.add(approve_btn, reject_btn)
        
        review_id = f"{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        pending_reviews[review_id] = review_data
        
        if review_data.get('photos'):
            # Отправляем медиагруппу с фотографиями
            media = []
            
            # Первое фото с caption и кнопками
            media.append(types.InputMediaPhoto(
                review_data['photos'][0], 
                caption=caption
            ))
            
            # Остальные фото без caption
            for photo in review_data['photos'][1:]:
                media.append(types.InputMediaPhoto(photo))
            
            try:
                send_params = {"chat_id": admin_group_id, "media": media}
                if topic_id:
                    send_params["message_thread_id"] = topic_id
                    
                messages = bot.send_media_group(**send_params)
                # Сохраняем ID первого сообщения для кнопок
                pending_reviews[review_id]['message_id'] = messages[0].message_id
                
                # Отправляем кнопки отдельным сообщением
                button_params = {
                    "chat_id": admin_group_id,
                    "text": "Выберите действие:",
                    "reply_markup": markup
                }
                if topic_id:
                    button_params["message_thread_id"] = topic_id
                    
                bot.send_message(**button_params)
                
            except Exception as e:
                log_error(logger, e, "Ошибка отправки медиагруппы отзыва на модерацию")
                # Fallback: отправляем первое фото с кнопками
                photo_params = {
                    "chat_id": admin_group_id,
                    "photo": review_data['photos'][0],
                    "caption": caption,
                    "reply_markup": markup
                }
                if topic_id:
                    photo_params["message_thread_id"] = topic_id
                msg_one = bot.send_photo(**photo_params)
                pending_reviews[review_id]['message_id'] = msg_one.message_id
                # Остальные фото по отдельности
            for photo in review_data['photos'][1:]:
                    single_photo_params = {
                        "chat_id": admin_group_id,
                        "photo": photo
                    }
                    if topic_id:
                        single_photo_params["message_thread_id"] = topic_id
                    bot.send_photo(**single_photo_params)
        else:
            text_params = {
                "chat_id": admin_group_id,
                "text": caption,
                "reply_markup": markup
            }
            if topic_id:
                text_params["message_thread_id"] = topic_id
                
            message = bot.send_message(**text_params)
            pending_reviews[review_id]['message_id'] = message.message_id
            
    except Exception as e:
        print(f"Ошибка отправки на модерацию: {e}")
        bot.send_message(user_id, "❌ Ошибка при отправке отзыва на модерацию")

def publish_review_to_channel(user_id, review_data):
    try:
        user_data = db_actions.get_user_data(user_id)
        channel_id = "@BridgeSide_Featback"
        
        caption = (
            f"⭐️ Новый отзыв\n\n"
            f"👤 От: {user_data['first_name']} {user_data['last_name']}\n\n"
            f"📝 {review_data['text']}"
        )
        
        if review_data.get('photos'):
            # Отправляем медиагруппу с фотографиями
            media = []
            
            # Первое фото с caption
            media.append(types.InputMediaPhoto(
                review_data['photos'][0], 
                caption=caption
            ))
            
            # Остальные фото без caption
            for photo in review_data['photos'][1:]:
                media.append(types.InputMediaPhoto(photo))
            
            try:
                bot.send_media_group(
                    chat_id=channel_id,
                    media=media
                )
            except Exception as e:
                log_error(logger, e, "Ошибка отправки медиагруппы отзыва в канал")
                # Fallback: отправляем первое фото с caption
                bot.send_photo(
                    chat_id=channel_id,
                    photo=review_data['photos'][0],
                    caption=caption
                )
                # Остальные фото по отдельности
                for photo in review_data['photos'][1:]:
                    bot.send_photo(
                        chat_id=channel_id,
                        photo=photo
                    )
        else:
            bot.send_message(
                chat_id=channel_id,
                text=caption
            )
            
    except Exception as e:
        print(f"Ошибка публикации отзыва: {e}")

def parse_delivery_info(text):
    """Парсит данные доставки из текста"""
    lines = text.strip().split('\n')
    delivery_info = {
        'city': '',
        'address': '',
        'full_name': '',
        'phone': '',
        'delivery_type': ''
    }
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if 'город:' in line.lower():
            delivery_info['city'] = line.split(':', 1)[1].strip() if ':' in line else line
        elif 'адрес:' in line.lower():
            delivery_info['address'] = line.split(':', 1)[1].strip() if ':' in line else line
        elif 'фио:' in line.lower() or 'фИО:' in line.lower():
            delivery_info['full_name'] = line.split(':', 1)[1].strip() if ':' in line else line
        elif 'телефон:' in line.lower():
            delivery_info['phone'] = line.split(':', 1)[1].strip() if ':' in line else line
        elif 'доставка:' in line.lower():
            delivery_info['delivery_type'] = line.split(':', 1)[1].strip() if ':' in line else line
        else:
            # Попробуем определить по формату
            if re.match(r'^\+?[78]?[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}$', line.replace(' ', '')):
                delivery_info['phone'] = line
            elif not delivery_info['city'] and len(line) < 50:
                delivery_info['city'] = line
            elif not delivery_info['address'] and len(line) > 10:
                delivery_info['address'] = line
            elif not delivery_info['full_name'] and len(line.split()) >= 2:
                delivery_info['full_name'] = line
            elif not delivery_info['delivery_type'] and any(x in line.lower() for x in ['почта', 'сдек', 'доставка']):
                delivery_info['delivery_type'] = line
    
    return delivery_info

def notify_admins_about_order(user_id, product, order_data, order_id, payment_photo_id=None):
    try:
        # ДОБАВЬТЕ ПРОВЕРКУ
        print(f"DEBUG notify_admins_about_order - order_data keys: {list(order_data.keys())}")
        
        user_data = db_actions.get_user_data(user_id)
        config_data = config.get_config()
        
        topic_id = create_user_order_topic(user_data)
        
        price, currency = get_product_price(product)
        order_text = (
            f"🛒 НОВЫЙ ЗАКАЗ #{order_id}\n\n"
            f"👤 Клиент: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 {user_data['username']}\n"
            f"🆔 ID: {user_id}\n\n"
            f"🛍️ Товар: {get_product_name(product)}\n"
            f"📏 Размер: {order_data.get('size', 'Не указан')}\n"
            f"💰 Цена: {price} {currency}\n"
            f"🎯 Тип: {'Эксклюзивный (BS Coin)' if get_product_field(product, 'is_exclusive') else 'Обычный'}\n\n"
            f"📦 ДАННЫЕ ДОСТАВКИ:\n"
            f"🏙️ Город: {order_data.get('city', 'Не указан')}\n"
            f"📍 Адрес: {order_data.get('address', 'Не указан')}\n"
            f"👤 ФИО: {order_data.get('full_name', 'Не указан')}\n"
            f"📞 Телефон: {order_data.get('phone', 'Не указан')}\n"
            f"🚚 Способ: {order_data.get('delivery_type', 'Не указан')}\n\n"
            f"💳 ОПЛАТА: {'Приложена ✅' if payment_photo_id else 'Не приложена ❌'}\n\n"
            f"🕒 Время заказа: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"📊 Статус: ⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ"
        )
        
        markup = types.InlineKeyboardMarkup()
        btn_approve = types.InlineKeyboardButton(
            "✅ Подтвердить заказ", 
            callback_data=f"approve_order_{order_id}"
        )
        btn_reject = types.InlineKeyboardButton(
            "❌ Отклонить заказ", 
            callback_data=f"reject_order_{order_id}"
        )
        markup.add(btn_approve, btn_reject)
        
        try:
            if payment_photo_id:
                message = bot.send_photo(
                    chat_id=config_data['admin_group_id'],
                    photo=payment_photo_id,
                    caption=order_text,
                    message_thread_id=topic_id,
                    reply_markup=markup 
                )
            else:
                message = bot.send_message(
                    chat_id=config_data['admin_group_id'],
                    text=order_text,
                    message_thread_id=topic_id,
                    reply_markup=markup
                )
            
            db_actions.save_order_message_id(order_id, message.message_id, topic_id)
                
        except Exception as e:
            print(f"Ошибка отправки в топик: {e}")
                
    except Exception as e:
        print(f"Ошибка в notify_admins_about_order: {e}")
        import traceback
        traceback.print_exc()

def create_user_order_topic(user_data):
    """Создает топик для заказов пользователя и возвращает его ID"""
    try:
        config_data = config.get_config()
        group_id = config_data['admin_group_id']
        
        topic_name = f"{user_data['first_name']} {user_data['last_name']} - ЗАКАЗ"
        
        result = bot.create_forum_topic(
            chat_id=group_id,
            name=topic_name
        )
        
        return result.message_thread_id
        
    except Exception as e:
        print(f"Ошибка создания топика: {e}")
        return config_data['topics'].get('магазин', 1)
    
def close_order_topic(user_data, order_id, status="✅ ВЫПОЛНЕН"):
    """Закрывает топик заказа с указанием статуса"""
    try:
        config_data = config.get_config()
        group_id = config_data['admin_group_id']
        
        topics = bot.get_forum_topics(group_id)
        topic_name = f"{user_data['first_name']} {user_data['last_name']} - ЗАКАЗ"
        
        for topic in topics.topics:
            if topic.name == topic_name:
                close_text = (
                    f"📦 ЗАКАЗ #{order_id} {status}\n"
                    f"👤 {user_data['first_name']} {user_data['last_name']}\n"
                    f"🕒 Завершен: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
                
                bot.send_message(
                    chat_id=group_id,
                    text=close_text,
                    message_thread_id=topic.message_thread_id
                )
                
                try:
                    bot.close_forum_topic(
                        chat_id=group_id,
                        message_thread_id=topic.message_thread_id
                    )
                except:
                    pass
                
                break
                
    except Exception as e:
        print(f"Ошибка закрытия топика: {e}")

# @bot.message_handler(func=lambda message: 
#     message.from_user.id in temp_data and 
#     temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
#     message.text == '✅ Подтвердить заказ')
# def confirm_order_final(message):
#     user_id = message.from_user.id
    
#     try:
#         order_data = temp_data[user_id]['order']
#         product_id = order_data['product_id']
#         product = db_actions.get_product(product_id)
        
#         if not product:
#             bot.send_message(user_id, "❌ Товар не найден")
#             return
        
#         # ДОБАВЬТЕ ОТЛАДОЧНУЮ ПЕЧАТЬ
#         print(f"DEBUG order_data keys: {list(order_data.keys())}")
#         print(f"DEBUG order_data content: {order_data}")
        
#         # Создаем заказ в базе
#         order_id = db_actions.create_detailed_order(
#             user_id=user_id,
#             product_id=product_id,
#             size=order_data.get('size'),
#             city=order_data['city'],
#             address=order_data['address'],
#             full_name=order_data['full_name'], 
#             phone=order_data['phone'],
#             delivery_type=order_data['delivery_type']
#         )
        
#         if order_id:
#             # Уведомляем админов
#             notify_admins_about_order(user_id, product, order_data, order_id, order_data.get('payment_photo'))
            
#             # Убираем клавиатуру
#             remove_markup = types.ReplyKeyboardRemove()
            
#             bot.send_message(
#                 user_id,
#                 f"✅ Заказ #{order_id} оформлен!\n\n"
#                 f"📞 С вами свяжутся в течение 15 минут для подтверждения.\n"
#                 f"💬 Отслеживать статус заказа можно в этом чате.",
#                 reply_markup=remove_markup
#             )
            
#             # Обновляем статистику пользователя
#             db_actions.update_user_stats(user_id, 'orders', 1)
            
#             # Проверяем достижение первого заказа
#             user_data = db_actions.get_user_data(user_id)
#             if user_data and user_data['orders'] == 1:
#                 db_actions.add_achievement(user_id, "first_order")
#                 db_actions.update_user_stats(user_id, 'bs_coin', 50)
#                 bot.send_message(
#                     user_id,
#                     "🎉 Вы получили достижение «Первый заказ» +50 BS Coin!"
#                 )
#         else:
#             bot.send_message(user_id, "❌ Ошибка оформления заказа")
        
#     except Exception as e:
#         print(f"Ошибка подтверждения заказа: {e}")
#         import traceback
#         traceback.print_exc()  # Добавьте эту строку для полной трассировки
#         bot.send_message(user_id, "❌ Ошибка оформления заказа")
#     finally:
#         # Очищаем временные данные
#         if user_id in temp_data and 'order' in temp_data[user_id]:
#             del temp_data[user_id]['order']    

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text.lower() == '❌ отменить заказ')
def cancel_order(message):
    user_id = message.from_user.id
    
    if user_id in temp_data and 'order' in temp_data[user_id]:
        del temp_data[user_id]['order']
    
    remove_markup = types.ReplyKeyboardRemove()
    
    bot.send_message(
        user_id,
        "❌ Заказ отменен.\n\n"
        "Если передумаете - всегда можете оформить новый заказ!",
        reply_markup=remove_markup
    )

# ============ ОБРАБОТЧИКИ КОМАНД ============

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
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
                    db_actions.update_user_stats(referrer_id, 'bs_coin', 100)
                    db_actions.update_user_stats(user_id, 'bs_coin', 50)
                    db_actions.update_user_stats(user_id, 'discount', 5)
                    
                    # Проверяем ачивки для рефералов
                    check_achievement_conditions(referrer_id, 'three_referrals')
                    
                    bot.send_message(
                        referrer_id,
                        f"🎉 Новый реферал! Вам начислено 100 BS Coin. Теперь у вас {db_actions.get_referral_stats(referrer_id)} рефералов."
                    )
                    
                    bot.send_message(
                        user_id,
                        f"🎁 Вы получили бонус за регистрацию по реферальной ссылке!\n"
                        f"💎 +50 BS Coin\n"
                        f"🎯 +5% скидка на все заказы"
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
    
    if handle_daily_bonus(user_id):
        bot.send_message(user_id, "🎉 Ежедневный бонус: 10 BS Coin зашли вам на счет!")
    
    user_data = db_actions.get_user_data(user_id)
    welcome_msg = (
        f"🚀 Добро пожаловать на борт, Друг! 🚀\n"
        f"Рады приветствовать тебя в сообществе BridgeSide — месте, где встречаются твой стиль и уникальные возможности.\n\n"
        f"🌉 Твои мосты в мир BridgeSide: 🌉\n"
        f"🛍️ @BridgeSide_Shop - Прямой каталог наших товары. Здесь ты первым узнаешь о новинках и эксклюзивных дропах.\n"
        f"🌟 @BridgeSide_LifeStyle - Лукбуки, стиль, жизнь сообщества, акции и розыгрыши. Вдохновляйся и участвуй!\n"
        f"📢 @BridgeSide_Featback- Честные отзывы от таких же членов клуба, как и ты. Нам важна твоя оценка.\n\n"
        f"🤖 Как управлять этим кораблем? Проще простого!\n"
        f"Этот бот — твой личный помощник. Не нужно ничего скачивать.Просто вводи команды прямо в эту строку чата:\n"
        f"• /profile — 👤 Твой цифровой пропуск. Здесь твоя история, бонусы и статус в клубе.\n"
        f"• /ref — 📍 Твой реферальный код. Приглашай друзей и получай крутые бонусы за каждого приведенного друга.\n"
        f"• /support — 🛟 Круглосуточная поддержка. Наша команда уже готова помочь 24/7.\n"
        f"🌟Или используй меню в нижней части экрана бота🌟\n\n"
        f"💡 Просто начни с любой команды выше! Бот ждет твоего сигнала. 😉\n"
        f"С уважением, команда BridgeSide. 🌉"
    )
    
    if db_actions.user_is_admin(user_id):
        bot.send_message(user_id, welcome_msg, reply_markup=buttons.admin_buttons())
    else:
        bot.send_message(user_id, welcome_msg, reply_markup=buttons.start_buttons())

@bot.message_handler(commands=['test_button'])
def test_button(message):
    user_id = message.from_user.id
    try:
        markup = types.InlineKeyboardMarkup()
        order_btn = types.InlineKeyboardButton(
            text="🛒 Заказать сейчас",
            callback_data="order_now_36_42.0"
        )
        markup.add(order_btn)
        
        bot.send_message(
            user_id,
            "Тестовая кнопка:",
            reply_markup=markup
        )
        
    except Exception as e:
        bot.send_message(user_id, f"Ошибка теста: {e}")

@bot.message_handler(func=lambda msg: msg.text == '👤 Мой профиль')
def show_profile(message):
    clear_temp_data(message.from_user.id)
    profile(message)

@bot.message_handler(func=lambda msg: msg.text == '🎁 Акции')
def show_promo(message):
    # Кнопка "Акции" заменена на переход в техподдержку
    clear_temp_data(message.from_user.id)
    return support(message)

@bot.message_handler(func=lambda msg: msg.text == '🛟 Тех. Поддержка')
def support_from_button(message):
    clear_temp_data(message.from_user.id)
    return support(message)

@bot.message_handler(func=lambda msg: msg.text == '📢 Отзывы')
def show_reviews(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    buttons = Bot_inline_btns()
    
    text = (
        "📢 Раздел отзывов\n\n"
        "Здесь вы можете оставить свой отзыв или посмотреть все отзывы, нажав на кнопку ниже."
    )
    bot.send_message(user_id, text, reply_markup=buttons.reviews_buttons())

@bot.message_handler(func=lambda msg: msg.text == '🏆 Ачивки')
def show_achievements_menu(message):
    """Показать ачивки через меню"""
    clear_temp_data(message.from_user.id)
    show_achievements(message)

@bot.message_handler(commands=['my_orders'])
def my_orders(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    orders = db_actions.get_user_orders(user_id)
    
    if not orders:
        bot.send_message(user_id, "У вас пока нет заказов")
        return
    
    orders_text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    for order in orders:
        product = db_actions.get_product(order['product_id'])
        orders_text += (
            f"🛒 Заказ #{order['order_id']}\n"
            f"🛍️ Товар: {get_product_name(product) if product else 'Неизвестно'}\n"
            f"📊 Статус: {order['status']}\n"
            f"🕒 Дата: {order['created_at']}\n\n"
        )
    
    bot.send_message(user_id, orders_text)

@bot.message_handler(commands=['support'])
def support(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    temp_data[user_id] = temp_data.get(user_id, {})
    temp_data[user_id]['support_step'] = 'awaiting_description'
    bot.reply_to(message, "🛟 Опишите вашу проблему одним сообщением, и мы свяжемся с вами.")

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('support_step') == 'awaiting_description')
def handle_support_description(message):
    user_id = message.from_user.id
    text = message.text or ''
    temp_data[user_id]['support_step'] = 'submitted'
    temp_data[user_id]['support_text'] = text
    user_data = db_actions.get_user_data(user_id) or {}
    first_name = user_data.get('first_name') or message.from_user.first_name or ''
    last_name = user_data.get('last_name') or message.from_user.last_name or ''
    username = user_data.get('username') or (message.from_user.username or '')

    # Создаем топик в админ-группе
    try:
        cfg = config.get_config() or {}
        admin_group_id = cfg.get('admin_group_id')
        topic_name = f"{first_name} {last_name} ПОДДЕРЖКА".strip()
        topic = bot.create_forum_topic(chat_id=admin_group_id, name=topic_name)
        topic_id = topic.message_thread_id if topic else None
    except Exception as e:
        topic_id = None

    # Кнопки для админа
    markup = types.InlineKeyboardMarkup()
    approve_btn = types.InlineKeyboardButton("✅ Принять", callback_data=f"support_accept_{user_id}")
    reject_btn = types.InlineKeyboardButton("❌ Отклонить", callback_data=f"support_reject_{user_id}")
    markup.add(approve_btn, reject_btn)

    admin_text = (
        f"🆘 Запрос в поддержку\n\n"
        f"👤 Пользователь: {first_name} {last_name}\n"
        f"🔗 @{username}\n\n"
        f"📄 Описание: {text}"
    )
    send_kwargs = {"chat_id": admin_group_id, "text": admin_text, "reply_markup": markup}
    if topic_id:
        send_kwargs["message_thread_id"] = topic_id
    msg = bot.send_message(**send_kwargs)

    # Сохраняем связь
    temp_data[user_id]['support_topic_id'] = topic_id
    temp_data[user_id]['support_chat_id'] = admin_group_id
    temp_data[user_id]['support_message_id'] = msg.message_id
    temp_data[user_id]['support_status'] = 'awaiting'

    bot.send_message(user_id, "✅ Запрос отправлен. Ожидайте ответа оператора.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('support_accept_') or call.data.startswith('support_reject_'))
def handle_support_decision(call):
    admin_id = call.from_user.id
    if not db_actions.user_is_admin(admin_id):
        bot.answer_callback_query(call.id, "⛔ Только для администраторов")
        return
    parts = call.data.split('_')
    action = parts[1]
    user_id = int(parts[2])
    data = temp_data.get(user_id, {})
    topic_id = data.get('support_topic_id')
    chat_id = data.get('support_chat_id')

    if action == 'reject':
        temp_data[user_id]['support_status'] = 'rejected'
        bot.answer_callback_query(call.id, "Отклонено")
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=data.get('support_message_id'), reply_markup=None)
        bot.send_message(user_id, "❌ В поддержке отказано. Попробуйте сформулировать запрос по-другому или позже.")
        return

    if action == 'accept':
        temp_data[user_id]['support_status'] = 'active'
        bot.answer_callback_query(call.id, "Принято")
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=data.get('support_message_id'), reply_markup=None)
        bot.send_message(user_id, "✅ Оператор подключился. Напишите ваше сообщение — мы ответим.")
        # Инструкция только для админа в топике
        try:
            admin_note_kwargs = {"chat_id": chat_id, "text": "Чтобы завершить диалог, напишите /close_support"}
            if topic_id:
                admin_note_kwargs["message_thread_id"] = topic_id
            bot.send_message(**admin_note_kwargs)
        except Exception:
            pass
        # Пометим связку для релея сообщений
        temp_data[user_id]['relay'] = {
            'chat_id': chat_id,
            'topic_id': topic_id
        }

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('support_status') == 'active')
def relay_user_to_admin(message):
    user_id = message.from_user.id
    relay = temp_data.get(user_id, {}).get('relay') or {}
    chat_id = relay.get('chat_id')
    topic_id = relay.get('topic_id')
    try:
        kwargs = {"chat_id": chat_id, "text": f"✉️ От пользователя: {message.text}"}
        if topic_id:
            kwargs["message_thread_id"] = topic_id
        bot.send_message(**kwargs)
    except Exception as _:
        pass

@bot.message_handler(func=lambda m: m.chat.type in ['supergroup', 'group'] and hasattr(m, 'is_topic_message') and m.is_topic_message and str(m.text or '').startswith('/close_support'))
def close_support_from_topic(message):
    admin_id = message.from_user.id
    if not db_actions.user_is_admin(admin_id):
        return
    # Находим пользователя по topic_id
    topic_id = message.message_thread_id
    user_id = None
    for uid, data in temp_data.items():
        if isinstance(data, dict) and data.get('relay', {}).get('topic_id') == topic_id:
            user_id = uid
            break
    if not user_id:
        return
    temp_data[user_id]['support_status'] = 'closed'
    bot.send_message(user_id, "✅ Диалог с поддержкой завершен.")
    # Опционально — закрыть сам топик
    try:
        cfg = config.get_config() or {}
        admin_group_id = cfg.get('admin_group_id')
        bot.close_forum_topic(chat_id=admin_group_id, message_thread_id=topic_id)
    except Exception:
        pass

@bot.message_handler(func=lambda m: m.chat.type in ['supergroup', 'group'] and hasattr(m, 'is_topic_message') and m.is_topic_message)
def relay_admin_to_user(message):
    # Релеим сообщения из топика админгруппы пользователю, если сессия активна
    topic_id = message.message_thread_id
    user_id = None
    for uid, data in temp_data.items():
        if isinstance(data, dict) and data.get('relay', {}).get('topic_id') == topic_id and data.get('support_status') == 'active':
            user_id = uid
            break
    if not user_id:
        return
    try:
        bot.send_message(user_id, f"👨‍💼 Оператор: {message.text}")
    except Exception:
        pass

@bot.message_handler(commands=['ref'])
def ref_command(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        bot.send_message(user_id, "Сначала зарегистрируйтесь с помощью /start")
        return

    ref_count = db_actions.get_referral_stats(user_id)
    ref_link = f"https://t.me/{bot.get_me().username}?start={user_data['referral_code']}"
    
    ref_msg = (
        f"✨ 🪙 ЗАРАБАТЫВАЙ BS COIN ВМЕСТЕ С ДРУЗЬЯМИ! ✨\n\n"
        f'Приглашай друзей и получай крутые бонусы! Это наш способ сказать "спасибо" за твою поддержку.'
        f"🔮 ТВОЯ МАГИЯ ПРИГЛАШЕНИЯ:\n"
        f"Скопируй свою уникальную ссылку и отправь ее друзьям. Только по этой ссылке твой друг получит свой персональный подарок!"
        f"{ref_link}\n"
        f"(Нажми, чтобы скопировать) ✨\n\n"
        f"🎁 ЧТО ТЫ ПОЛУЧАЕШЬ:\n"
        f" +100 BridgeSide Coin 🪙 — зачисляются на твой счет.\n\n"
        f"🎁 ЧТО ПОЛУЧАЕТ ТВОЙ ДРУГ:\n"
        f" Щедрый подарок на первый заказ — СКИДКА 5% 🎯 + +50 BS Coin на свой счет! Отличный повод начать shopping!\n"
        f"🏆 ТОП-5 ПО РЕФЕРАЛАМ ЕЖЕМЕСЯЧНО ПОЛУЧАЮТ ЭКСКЛЮЗИВНЫЙ МЕРЧ!\n"
        f"Чем больше друзей ты приведёшь, тем выше твой шанс оказаться в числе Легенд нашего клуба! Смотри рейтинг в своём профиле (/profile).\n"
        f"🚀Не копи — зарабатывай! Переходи по ссылкам, покупай и приглашай!"
    )
    
    bot.send_message(user_id, ref_msg, parse_mode="HTML")

@bot.message_handler(commands=['set_discount'])
def set_discount(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
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
    clear_temp_data(user_id)
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
    clear_temp_data(user_id)
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

@bot.message_handler(commands=['profile'])
def profile(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
        
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        bot.send_message(user_id, "Сначала зарегистрируйтесь с помощью /start")
        return
    
    buttons = Bot_inline_btns()
    
    # Получаем ачивки пользователя
    user_achievements = db_actions.get_user_achievements(user_id)
    
    achievements_str = ""
    if user_achievements:
        achievements_str = "\n🏆 Ваши ачивки:\n"
        for achievement in user_achievements[:3]:  # Показываем только последние 3
            achievements_str += f"• {achievement['achievement_name']}\n"
        
        if len(user_achievements) > 3:
            achievements_str += f"... и еще {len(user_achievements) - 3} ачивок\n"
    else:
        achievements_str = "\n🏆 Ачивки: Пока нет\n💡 Выполняйте действия в боте для получения ачивок!"
    
    achievements_str += "\n\n📖 <a href='https://telegra.ph/FAQ-Sistema-achivok--Bridge-Side-Collective-09-19'>Подробнее о системе ачивок</a>"
    
    coin_info = ""
    if user_data['bs_coin'] < 100:
        coin_info = "\n\n💡 Как получить BS Coin:\n• /start - ежедневный бонус\n• /ref - реферальная система\n• Активность в канале\n• 🏆 <a href='https://telegra.ph/FAQ-Sistema-achivok--Bridge-Side-Collective-09-19'>Достижения - подробнее</a>"
    
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
        disable_web_page_preview=True,
        reply_markup=buttons.profile_buttons(user_data)
    )

@bot.message_handler(commands=['achievements'])
def show_achievements(message):
    """Показать все ачивки пользователя"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    
    user_data = db_actions.get_user_data(user_id)
    if not user_data:
        bot.send_message(user_id, "Сначала зарегистрируйтесь с помощью /start")
        return
    
    # Получаем ачивки по категориям
    bridge_achievements = db_actions.get_achievements_by_category(user_id, 'МОСТ')
    shore_achievements = db_actions.get_achievements_by_category(user_id, 'БЕРЕГ')
    collective_achievements = db_actions.get_achievements_by_category(user_id, 'КОЛЛЕКТИВ')
    
    # Получаем все доступные ачивки
    all_achievements = db_actions.get_user_achievements(user_id)
    earned_codes = {ach['achievement_code'] for ach in all_achievements}
    
    message_text = "🏆 Система ачивок BridgeSide\n\n"
    message_text += "Добро пожаловать на свой Берег. Здесь мы отмечаем ваш вклад цифровыми ачивками и внутренней валютой — BS Coin.\n\n"
    
    # Линия "МОСТ" — стиль + технологии
    message_text += "— Линия «МОСТ» — стиль + технологии\n"
    for code, data in ACHIEVEMENTS.items():
        if data['category'] == 'МОСТ':
            status = "✅" if code in earned_codes else "⭕"
            message_text += f"{status} {data['name']} — {data['description']} → +{data['bs_coin_reward']} BS Coin\n"
    
    message_text += "\n— Линия «БЕРЕГ» — лояльность\n"
    for code, data in ACHIEVEMENTS.items():
        if data['category'] == 'БЕРЕГ':
            status = "✅" if code in earned_codes else "⭕"
            reward_text = f"+{data['bs_coin_reward']} BS Coin"
            if data['discount_bonus'] > 0:
                reward_text += f" +{data['discount_bonus']}% скидка"
            message_text += f"{status} {data['name']} — {data['description']} → {reward_text}\n"
    
    message_text += "\n— Линия «КОЛЛЕКТИВ» — за приглашения\n"
    for code, data in ACHIEVEMENTS.items():
        if data['category'] == 'КОЛЛЕКТИВ':
            status = "✅" if code in earned_codes else "⭕"
            message_text += f"{status} {data['name']} — {data['description']} → +{data['bs_coin_reward']} BS Coin\n"
    
    message_text += "\n💡 Выполняйте действия в боте и магазине для получения ачивок!"
    
    bot.send_message(user_id, message_text)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
        
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔ Эта команда только для администраторов")
        return
    
    buttons = Bot_inline_btns()
    bot.send_message(
        user_id,
        "🔐 Панель администратора:",
        reply_markup=buttons.admin_buttons()
    )

@bot.message_handler(commands=['admin_stats'])
def admin_stats(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    products_count = db_actions.get_products_count()
    variations_count = db_actions.get_variations_count()
    users_count = db_actions.get_users_count()
    reviews_count = db_actions.get_reviews_count()
    
    stats_msg = (
        f"📊 Статистика магазина:\n\n"
        f"🛍️ Товаров: {products_count}\n"
        f"📦 Вариаций: {variations_count}\n"
        f"👥 Пользователей: {users_count}\n"
        f"📝 Отзывов: {reviews_count}"
    )
    
    bot.send_message(user_id, stats_msg)

@bot.message_handler(commands=['export_products'])
def export_products(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
    
    try:
        products_data = db_actions.get_all_products_for_export()
        
        if not products_data:
            bot.send_message(user_id, "❌ Нет товаров для экспорта")
            return
        
        df_data = []
        for product in products_data:
            df_data.append({
                'Модель': product['name'],
                'ID Модели': product['model_id'],
                'Размер': product['size'],
                'Цена Y': product['price_yuan'],
                'Количество': product['quantity'],
                'Цена': product['price'],
                'Ссылка': product['link']
            })
        
        df = pd.DataFrame(df_data)
        filename = f"products_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)
        
        with open(filename, 'rb') as f:
            bot.send_document(user_id, f, caption="📊 Экспорт товаров")
        
        os.remove(filename)
        
    except Exception as e:
        error_msg = f"❌ Ошибка при экспорте товаров: {str(e)}"
        log_error(logger, e, "Ошибка при экспорте товаров")
        bot.send_message(user_id, error_msg)

@bot.message_handler(commands=['upload_products'])
def upload_products(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    bot.send_message(user_id, "📤 Отправьте Excel файл с товарами")
    bot.register_next_step_handler(message, process_products_file)

@bot.message_handler(commands=['yadisk_auth'])
def yadisk_auth(message):
    """Инициировать авторизацию Яндекс.Диска"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
    
    cfg = config.get_config()
    yadisk_config = cfg.get('yadisk', {})
    client_id = yadisk_config.get('client_id')
    
    if not client_id:
        bot.send_message(user_id, 
            "❌ Не настроен client_id для Яндекс.Диска.\n"
            "Добавьте в secrets.json:\n"
            '```json\n'
            '{\n'
            '  "yadisk": {\n'
            '    "client_id": "ваш_client_id",\n'
            '    "client_secret": "ваш_client_secret"\n'
            '  }\n'
            '}\n'
            '```', parse_mode='Markdown')
        return
    
    # Генерируем state для безопасности
    state = f"yadisk_auth_{user_id}_{int(time.time())}"
    
    # Сохраняем state для проверки
    if user_id not in temp_data:
        temp_data[user_id] = {}
    temp_data[user_id]['yadisk_state'] = state
    
    # Формируем URL авторизации
    auth_url = (
        f"{YANDEX_OAUTH_URL}?"
        f"response_type=code&"
        f"client_id={client_id}&"
        f"state={state}"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔗 Авторизоваться", url=auth_url))
    
    bot.send_message(user_id,
        "🔐 Для авторизации Яндекс.Диска:\n\n"
        "1. Нажмите кнопку ниже\n"
        "2. Войдите в аккаунт Яндекс\n"
        "3. Разрешите доступ приложению\n"
        "4. Скопируйте код из адресной строки\n"
        "5. Отправьте код боту\n\n"
        f"State: `{state}`", 
        reply_markup=markup, parse_mode='Markdown')
    
    temp_data[user_id]['step'] = 'await_yadisk_code'

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'await_yadisk_code')
def handle_yadisk_code(message):
    """Обработать код авторизации от Яндекс.Диска"""
    user_id = message.from_user.id
    data = temp_data.get(user_id, {})
    
    if 'yadisk_state' not in data:
        bot.send_message(user_id, "❌ Сессия неактуальна. Используйте /yadisk_auth")
        return
    
    code = message.text.strip()
    state = data['yadisk_state']
    
    try:
        # Обмениваем код на токены
        cfg = config.get_config()
        yadisk_config = cfg.get('yadisk', {})
        client_id = yadisk_config.get('client_id')
        client_secret = yadisk_config.get('client_secret')
        
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret
        }
        
        response = requests.post(YANDEX_TOKEN_URL, data=token_data, timeout=30)
        response.raise_for_status()
        tokens = response.json()
        
        # Сохраняем токены
        config.update_yadisk_tokens(
            tokens['access_token'],
            tokens.get('refresh_token', ''),
            tokens.get('expires_in', 3600)
        )
        
        bot.send_message(user_id, "✅ Авторизация Яндекс.Диска успешна!")
        
        # Очищаем временные данные
        if user_id in temp_data:
            del temp_data[user_id]
            
    except Exception as e:
        log_error(logger, e, "Ошибка авторизации Яндекс.Диска")
        bot.send_message(user_id, f"❌ Ошибка авторизации: {str(e)}")

@bot.message_handler(commands=['create_post'])
def create_post(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    if user_id in temp_data:
        del temp_data[user_id]
    
    temp_data[user_id] = {'step': 'await_product_id', 'photos': []}
    # Отправляем последнюю xlsx (если есть)
    try:
        xlsx_path = config.get_config().get('xlsx_path')
        if xlsx_path and os.path.exists(os.path.join(work_dir, xlsx_path)):
            with open(os.path.join(work_dir, xlsx_path), 'rb') as f:
                bot.send_document(user_id, f, caption="📄 Актуальная таблица товаров (содержит ID)")
    except Exception as e:
        log_error(logger, e, "Не удалось отправить XLSX")
    bot.send_message(user_id, "✍️ Введите ID товара из таблицы (совпадает с папкой на Яндекс.Диске):")
    bot.register_next_step_handler(message, handle_enter_product_id)

def handle_enter_product_id(message):
    user_id = message.from_user.id
    if user_id not in temp_data or temp_data[user_id].get('step') != 'await_product_id':
        bot.send_message(user_id, "❌ Сессия неактуальна. Нажмите /create_post")
        return
    # Проверяем, что пришёл текст (а не файл/фото)
    if not getattr(message, 'text', None) or not str(message.text).strip():
        bot.send_message(user_id, "✍️ Введите ID товара текстом (как в таблице/папке Я.Диск):")
        bot.register_next_step_handler(message, handle_enter_product_id)
        return
    product_id = message.text.strip()
    # Скачиваем фото
    photos = []
    try:
        photos = download_photos_from_yadisk(product_id)
        if not photos:
            log_info(logger, f"Фото не найдены на Яндекс.Диске для товара {product_id}")
    except Exception as e:
        log_error(logger, e, f"Ошибка при скачивании фото с Яндекс.Диска для товара {product_id}")
        photos = []
    
    temp_data[user_id]['photos'] = photos
    temp_data[user_id]['table_id'] = product_id  # Сохраняем артикул
    
    # Получим товар для описания по table_id (артикулу) или по model_id из вариаций
    product = db_actions.get_product_by_table_id(product_id)
    if not product:
        product = db_actions.get_product_by_model_id(product_id)
    if not product:
        bot.send_message(user_id, f"❌ Товар с артикулом {product_id} не найден в базе данных")
        return
        
    # Получаем данные товара
    product_name = get_product_field(product, 'name', 'Неизвестно')
    description_full = get_product_field(product, 'description_full', '')
    description_old = get_product_field(product, 'description', '')
    table_id_db = get_product_field(product, 'table_id', '')
    keywords = get_product_field(product, 'keywords', '')
    price = get_product_field(product, 'price', 0)
    
    # Получаем доступные размеры
    actual_product_id = get_product_field(product, 'product_id', 0)
    temp_data[user_id]['product_id'] = actual_product_id  # Сохраняем числовой ID для кнопок
    # Вариации по product_id, если пусто — пробуем по model_id (table_id)
    variations = db_actions.get_product_variations(actual_product_id)
    if (not variations) and table_id:
        try:
            variations = db_actions.get_product_variations_by_model_id(table_id)
        except Exception:
            variations = []
    available_sizes = []
    if variations:
        for variation in variations:
            size = get_product_field(variation, 'size', '')
            quantity = variation.get('quantity', None)
            if size and (quantity is None or quantity > 0):
                available_sizes.append(size)
    # Оставляем только числовые размеры и сортируем по возрастанию
    import re
    numeric_sizes = []
    for s in available_sizes:
        ss = str(s).strip()
        m = re.search(r"(\d+(?:[\.,]\d+)?)", ss)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(',', '.'))
            numeric_sizes.append((val, ss))
        except Exception:
            continue
    # Удаляем дубликаты по числовому значению, сортируем и формируем отображаемый список
    seen = set()
    numeric_sizes_sorted = []
    for val, ss in sorted(numeric_sizes, key=lambda x: x[0]):
        if val in seen:
            continue
        seen.add(val)
        # Красиво отображаем: 42.0 -> 42, 42.5 -> 42.5
        disp = str(int(val)) if val.is_integer() else ("{:.1f}".format(val).rstrip('0').rstrip('.') if val % 1 != 0 else str(int(val)))
        numeric_sizes_sorted.append(disp)
    
    # Формируем карточку товара в требуемом формате (HTML):
    # Название, Описание, Артикул, Размеры, Цена, Возврат, Хэштеги, Кнопки-ссылки
    caption_parts = []
    
    # Название товара
    caption_parts.append(f"{product_name}")
    
    # Описание товара (приоритет новому полю, если пустое - используем старое; без строк-хэштегов)
    description_to_show = description_full if description_full else description_old
    description_clean = description_to_show or ""
    if description_clean and '\n' in description_clean:
        lines = description_clean.split('\n')
        description_clean = '\n'.join([line for line in lines if not line.strip().startswith('#')]).strip()
    if description_clean:
        caption_parts.append(f"<blockquote>{description_clean}</blockquote>")
    
    # Определяем артикул для отображения: table_id из БД → введённый админом table_id → первый model_id
    try:
        preview_variations = variations or []
        if (not preview_variations) and table_id_db:
            preview_variations = db_actions.get_product_variations_by_model_id(table_id_db)
    except Exception:
        preview_variations = []
    first_model_id_for_display = None
    try:
        first_model_id_for_display = next((v.get('model_id') for v in preview_variations if v.get('model_id')), None)
    except Exception:
        first_model_id_for_display = None
    admin_table_id = product_id  # ввод админа в этом шаге — это артикул (папка на Я.Диске)
    article_to_show = (str(table_id_db).strip() if table_id_db and str(table_id_db).strip() else None)
    if not article_to_show and admin_table_id and str(admin_table_id).strip():
        article_to_show = str(admin_table_id).strip()
    if not article_to_show and first_model_id_for_display and str(first_model_id_for_display).strip():
        article_to_show = str(first_model_id_for_display).strip()

    # Блок деталей: Артикул, Размеры, Цена (между ними один перевод строки)
    details_lines = []
    if article_to_show:
        details_lines.append(f"<b>Артикул: {article_to_show}</b>")
    if numeric_sizes_sorted:
        sizes_text = ", ".join(numeric_sizes_sorted[:10])
        if len(numeric_sizes_sorted) > 10:
            sizes_text += f" и еще {len(numeric_sizes_sorted) - 10}"
        details_lines.append(f"Размеры: {sizes_text}")
    else:
        # Fallback: показать сырые размеры из БД, если они есть
        if available_sizes:
            uniq_raw = []
            seen_raw = set()
            for s in available_sizes:
                ss = str(s).strip()
                if ss and ss not in seen_raw:
                    seen_raw.add(ss)
                    uniq_raw.append(ss)
            if uniq_raw:
                sizes_text = ", ".join(uniq_raw[:10])
                if len(uniq_raw) > 10:
                    sizes_text += f" и еще {len(uniq_raw) - 10}"
                details_lines.append(f"Размеры: {sizes_text}")
        log_info(logger, f"DEBUG: Sizes not found for preview. product_id={actual_product_id}, table_id={table_id}, variations={len(variations)}, raw_sizes={available_sizes}")
    price_text = f"Цена: {price}₽" if price and price > 0 else "Цена: Уточняйте"
    details_lines.append(price_text)
    if details_lines:
        caption_parts.append("\n".join(details_lines))
    
    # Возврат
    caption_parts.append("Возврат в течение 14 дней")
    
    # Ссылки: Купить и Поддержка (как гиперссылки)
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = ''
    deep_link = f"https://t.me/{bot_username}?start=product_{actual_product_id}" if bot_username else ""
    support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""
    link_chunks = []
    if deep_link:
        link_chunks.append(f"<a href=\"{deep_link}\">🛒 Купить в один клик</a>")
    if support_link:
        link_chunks.append(f"<a href=\"{support_link}\">🆘 Служба поддержки</a>")
    if link_chunks:
        caption_parts.append(" | ".join(link_chunks))
    
    # Хэштеги (из описания или keywords)
    hashtags_to_show = ''
    if description_to_show and '\n' in description_to_show:
        h_lines = [ln.strip() for ln in description_to_show.split('\n') if ln.strip().startswith('#')]
        if h_lines:
            hashtags_to_show = ' '.join(h_lines)
    if not hashtags_to_show and keywords and str(keywords).strip():
        hashtags_to_show = str(keywords).strip()
    if hashtags_to_show:
        caption_parts.append(f"{hashtags_to_show}")
    
    caption = "\n\n".join(caption_parts)
    
    # Превью: отправляем медиа-группу если есть фото, иначе текстовое сообщение (HTML)
    if photos:
        media = []
        for idx, p in enumerate(photos[:10]):
            if idx == 0:
                media.append(types.InputMediaPhoto(open(p, 'rb'), caption=caption, parse_mode="HTML"))
            else:
                media.append(types.InputMediaPhoto(open(p, 'rb')))
        bot.send_media_group(user_id, media)
    else:
        bot.send_message(user_id, caption, parse_mode="HTML")
    
    # Кнопки
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="🚀 Выложить", callback_data=f"post_publish_{actual_product_id}"),
        types.InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"post_edit_{actual_product_id}")
    )
    markup.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"post_cancel_{actual_product_id}"))
    bot.send_message(user_id, "Предпросмотр поста. Что делаем?", reply_markup=markup)
    temp_data[user_id]['step'] = 'preview'

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_cancel_'))
def handle_post_cancel(call):
    user_id = call.from_user.id
    files = temp_data.get(user_id, {}).get('photos', [])
    cleanup_local_files(files)
    if user_id in temp_data:
        del temp_data[user_id]
    bot.answer_callback_query(call.id, "Отменено")
    bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Операция отменена")

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_publish_'))
def handle_post_publish(call):
    user_id = call.from_user.id
    data = temp_data.get(user_id) or {}
    files = data.get('photos', [])
    product_id = data.get('product_id')
    try:
        if not files:
            bot.answer_callback_query(call.id, "Нет файлов для публикации")
            return
        # Формируем подпись из сохранённых значений в требуемом формате (учитывая правки админа)
        product = db_actions.get_product(int(product_id)) if product_id and str(product_id).isdigit() else None
        override_name = data.get('override_name')
        override_description = data.get('override_description')
        override_price = data.get('override_price') if 'override_price' in data else None
        override_tags = data.get('override_tags', '')

        name = override_name or (get_product_name(product) if product else f"Товар {product_id}")
        description_full = get_product_field(product, 'description_full', '') if product else ''
        description_old = get_product_field(product, 'description', '') if product else ''
        table_id = get_product_field(product, 'table_id', '') if product else ''
        admin_table_id = temp_data.get(user_id, {}).get('table_id')
        # Определяем артикул для показа: table_id → admin_table_id → первый model_id
        first_model_id_for_display = None
        try:
            _vars_for_id = db_actions.get_product_variations(int(product_id)) if product else []
            first_model_id_for_display = next((v.get('model_id') for v in _vars_for_id if v.get('model_id')), None)
        except Exception:
            first_model_id_for_display = None
        article_to_show = (str(table_id).strip() if table_id and str(table_id).strip() else None)
        if not article_to_show and admin_table_id and str(admin_table_id).strip():
            article_to_show = str(admin_table_id).strip()
        if not article_to_show and first_model_id_for_display and str(first_model_id_for_display).strip():
            article_to_show = str(first_model_id_for_display).strip()
        keywords = get_product_field(product, 'keywords', '') if product else ''
        price = override_price if override_price is not None else (get_product_field(product, 'price', 0) if product else 0)

        # Описание без строк-хэштегов
        description_to_show = override_description or (description_full if description_full else description_old)
        description_clean = description_to_show
        if description_clean and '\n' in description_clean:
            _lines = description_clean.split('\n')
            description_clean = '\n'.join([ln for ln in _lines if not ln.strip().startswith('#')]).strip()

        # Хэштеги: из исходного описания или из keywords
        hashtags_to_show = ''
        if description_to_show and '\n' in description_to_show:
            h_lines = [ln.strip() for ln in description_to_show.split('\n') if ln.strip().startswith('#')]
            if h_lines:
                hashtags_to_show = ' '.join(h_lines)
        if not hashtags_to_show and override_tags:
            hashtags_to_show = override_tags.strip()
        if not hashtags_to_show and keywords:
            hashtags_to_show = keywords.strip()

        # Формируем подпись в требуемом формате (HTML):
        # Фото (идет отдельно), Название, Описание, Артикул, Размеры, Цена,
        # Возврат, Хэштеги, Кнопки-ссылки
        parts = []
        parts.append(f"{name}")
        if description_clean:
            parts.append(f"<blockquote>{description_clean}</blockquote>")
        if article_to_show:
            parts.append(f"<b>Артикул: {article_to_show}</b>")
        # Размеры
        try:
            variations = db_actions.get_product_variations(int(product_id)) if product else []
            if (not variations) and table_id:
                variations = db_actions.get_product_variations_by_model_id(table_id)
        except Exception:
            variations = []
        available_sizes = []
        if variations:
            for v in variations:
                size = get_product_field(v, 'size', '')
                quantity = v.get('quantity', None)
                if size and (quantity is None or quantity > 0):
                    available_sizes.append(size)
        # Числовая фильтрация и сортировка размеров
        import re
        numeric_sizes = []
        for s in available_sizes:
            ss = str(s).strip()
            m = re.search(r"(\d+(?:[\.,]\d+)?)", ss)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(',', '.'))
                numeric_sizes.append((val, ss))
            except Exception:
                continue
        seen = set()
        numeric_sizes_sorted = []
        for val, ss in sorted(numeric_sizes, key=lambda x: x[0]):
            if val in seen:
                continue
            seen.add(val)
            disp = str(int(val)) if val.is_integer() else ("{:.1f}".format(val).rstrip('0').rstrip('.') if val % 1 != 0 else str(int(val)))
            numeric_sizes_sorted.append(disp)
        if numeric_sizes_sorted:
            sizes_text = ", ".join(numeric_sizes_sorted[:10])
            if len(numeric_sizes_sorted) > 10:
                sizes_text += f" и еще {len(numeric_sizes_sorted) - 10}"
            parts.append(f"Размеры: {sizes_text}")
        else:
            # Fallback на сырые размеры
            if available_sizes:
                uniq_raw = []
                seen_raw = set()
                for s in available_sizes:
                    ss = str(s).strip()
                    if ss and ss not in seen_raw:
                        seen_raw.add(ss)
                        uniq_raw.append(ss)
                if uniq_raw:
                    sizes_text = ", ".join(uniq_raw[:10])
                    if len(uniq_raw) > 10:
                        sizes_text += f" и еще {len(uniq_raw) - 10}"
                    parts.append(f"Размеры: {sizes_text}")
            log_info(logger, f"DEBUG: Sizes not found for publish-from-preview. product_id={product_id}, table_id={table_id}, variations={len(variations)}, raw_sizes={available_sizes}")
        # Цена (как в предпросмотре)
        price_text = f"Цена: {price}₽" if price and price > 0 else "Цена: Уточняйте"
        parts.append(price_text)
        # Кнопки-ссылки
        try:
            bot_username = bot.get_me().username
        except Exception:
            bot_username = ''
        deep_link = f"https://t.me/{bot_username}?start=product_{product_id}" if bot_username else ""
        support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""
        link_chunks = []
        if deep_link:
            link_chunks.append(f"<a href=\"{deep_link}\">🛒 Купить в один клик</a>")
        if support_link:
            link_chunks.append(f"<a href=\"{support_link}\">🆘 Служба поддержки</a>")
        # Возврат выше ссылок
        parts.append("Возврат в течение 14 дней")
        if link_chunks:
            parts.append(" | ".join(link_chunks))
        # Хэштеги
        if hashtags_to_show:
            parts.append(f"{hashtags_to_show}")
        caption = "\n\n".join(parts)
        config_data_local = config.get_config()
        chat_id = config_data_local.get('store_channel_id', '@BridgeSide_Store')
        topic_id = (config_data_local.get('topics') or {}).get('магазин')
        media = []
        for idx, p in enumerate(files[:10]):
            media_input = _resolve_media_input(p)
            if idx == 0:
                media.append(types.InputMediaPhoto(media_input, caption=caption, parse_mode="HTML"))
            else:
                media.append(types.InputMediaPhoto(media_input))
        # Публикация
        bot.send_media_group(chat_id, media, message_thread_id=topic_id)
        bot.answer_callback_query(call.id, "Опубликовано")
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="✅ Пост опубликован")
    except Exception as e:
        log_error(logger, e, "Ошибка публикации поста")
        bot.answer_callback_query(call.id, "Ошибка публикации")
    finally:
        cleanup_local_files(files)
        if user_id in temp_data:
            del temp_data[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_edit_'))
def handle_post_edit(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    # Показать меню редактирования
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(text="🖼 Фото", callback_data="post_editmenu_photos"),
        types.InlineKeyboardButton(text="🏷 Название", callback_data="post_editmenu_name"),
    )
    markup.add(
        types.InlineKeyboardButton(text="📝 Описание", callback_data="post_editmenu_desc"),
        types.InlineKeyboardButton(text="💰 Цена", callback_data="post_editmenu_price"),
    )
    markup.add(
        types.InlineKeyboardButton(text="#️⃣ Хэштеги", callback_data="post_editmenu_tags"),
        types.InlineKeyboardButton(text="🔙 К предпросмотру", callback_data="post_editmenu_back"),
    )
    try:
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id,
                              text="Что хотите отредактировать?", reply_markup=markup)
    except Exception:
        bot.send_message(user_id, "Что хотите отредактировать?", reply_markup=markup)
        temp_data.setdefault(user_id, {})
        temp_data[user_id]['step'] = 'edit_menu'

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_editmenu_'))
def handle_post_edit_menu(call):
    user_id = call.from_user.id
    action = call.data.replace('post_editmenu_', '')
    bot.answer_callback_query(call.id)
    if action == 'back':
        try:
            _render_post_preview(user_id)
        except Exception:
            bot.send_message(user_id, "Не удалось отобразить предпросмотр")
        return
    if action == 'photos':
        temp_data.setdefault(user_id, {})
        # Удаляем локальные файлы, скачанные ранее с Я.Диска, и очищаем список фото
        try:
            existing = temp_data[user_id].get('photos', []) or []
            local_paths = [p for p in existing if isinstance(p, str) and os.path.exists(p)]
            if local_paths:
                cleanup_local_files(local_paths)
        except Exception:
            pass
        temp_data[user_id]['photos'] = []
        temp_data[user_id]['step'] = 'edit_photos_post'
        bot.send_message(user_id, "Отправьте 1–10 фото для поста. Первое фото будет с подписью. Отправьте /done когда закончите.")
    elif action == 'name':
        temp_data.setdefault(user_id, {})
        temp_data[user_id]['step'] = 'edit_name_post'
        bot.send_message(user_id, "Введите новое название модели:")
    elif action == 'desc':
        temp_data.setdefault(user_id, {})
        temp_data[user_id]['step'] = 'edit_desc_post'
        bot.send_message(user_id, "Введите новое описание:")
    elif action == 'price':
        temp_data.setdefault(user_id, {})
        temp_data[user_id]['step'] = 'edit_price_post'
        bot.send_message(user_id, "Введите новую цену (число), либо 0 для 'Уточняйте':")
    elif action == 'tags':
        temp_data.setdefault(user_id, {})
        temp_data[user_id]['step'] = 'edit_tags_post'
        bot.send_message(user_id, "Введите хэштеги (через пробел или перенос строки):")

@bot.message_handler(content_types=['photo'], func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_photos_post')
def handle_edit_photos_post(message):
    user_id = message.from_user.id
    data = temp_data.setdefault(user_id, {})
    files = data.get('photos', [])
    try:
        file_id = message.photo[-1].file_id
        if not isinstance(files, list):
            files = []
        files.append(file_id)
        files = files[-10:]
        data['photos'] = files
        temp_data[user_id] = data
        bot.send_message(user_id, f"Добавлено фото. Текущих фото: {len(files)}. Отправьте ещё или введите /done для предпросмотра.")
    except Exception as e:
        bot.send_message(user_id, f"Не удалось добавить фото: {e}")

@bot.message_handler(commands=['done'])
def handle_done_editing(message):
    user_id = message.from_user.id
    if temp_data.get(user_id, {}).get('step') in ['edit_photos_post', 'edit_name_post', 'edit_desc_post', 'edit_price_post', 'edit_tags_post']:
        _render_post_preview(user_id)

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_name_post')
def handle_edit_name_post(message):
    user_id = message.from_user.id
    temp_data.setdefault(user_id, {})['override_name'] = (message.text or '').strip()
    _render_post_preview(user_id)

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_desc_post')
def handle_edit_desc_post(message):
    user_id = message.from_user.id
    temp_data.setdefault(user_id, {})['override_description'] = (message.text or '').strip()
    _render_post_preview(user_id)

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_price_post')
def handle_edit_price_post(message):
    user_id = message.from_user.id
    txt = (message.text or '').replace(',', '.').strip()
    try:
        value = float(txt)
        temp_data.setdefault(user_id, {})['override_price'] = value
        _render_post_preview(user_id)
    except Exception:
        bot.send_message(user_id, "Введите число, например 4990 или 0")

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_tags_post')
def handle_edit_tags_post(message):
    user_id = message.from_user.id
    temp_data.setdefault(user_id, {})['override_tags'] = (message.text or '').strip()
    _render_post_preview(user_id)

def _resolve_media_input(media_item):
    try:
        if isinstance(media_item, str) and os.path.exists(media_item):
            return open(media_item, 'rb')
        return media_item
    except Exception:
        return media_item

def _render_post_preview(user_id: int):
    data = temp_data.get(user_id) or {}
    files = data.get('photos', [])
    actual_product_id = data.get('product_id')
    table_id_input = data.get('table_id', '')
    product = db_actions.get_product(int(actual_product_id)) if actual_product_id and str(actual_product_id).isdigit() else None
    product_name = data.get('override_name') or (get_product_field(product, 'name', 'Неизвестно') if product else f"Товар {actual_product_id}")
    description_full = get_product_field(product, 'description_full', '') if product else ''
    description_old = get_product_field(product, 'description', '') if product else ''
    table_id_db = get_product_field(product, 'table_id', '') if product else ''
    admin_table_id = table_id_input
    # Определяем артикул для показа: table_id из БД → admin_table_id → первый model_id
    try:
        _vars_for_id = db_actions.get_product_variations(int(actual_product_id)) if product else []
        first_model_id_for_display = next((v.get('model_id') for v in _vars_for_id if v.get('model_id')), None)
    except Exception:
        first_model_id_for_display = None
    article_to_show = (str(table_id_db).strip() if table_id_db and str(table_id_db).strip() else None)
    if not article_to_show and admin_table_id and str(admin_table_id).strip():
        article_to_show = str(admin_table_id).strip()
    if not article_to_show and first_model_id_for_display and str(first_model_id_for_display).strip():
        article_to_show = str(first_model_id_for_display).strip()
    keywords = get_product_field(product, 'keywords', '') if product else ''
    price_value = data.get('override_price') if 'override_price' in data else (get_product_field(product, 'price', 0) if product else 0)
    description_to_show = data.get('override_description') or (description_full if description_full else description_old)
    description_clean = description_to_show or ''
    if description_clean and '\n' in description_clean:
        lines = description_clean.split('\n')
        description_clean = '\n'.join([ln for ln in lines if not ln.strip().startswith('#')]).strip()
    # sizes
    try:
        variations = db_actions.get_product_variations(int(actual_product_id)) if product else []
        if (not variations) and table_id:
            variations = db_actions.get_product_variations_by_model_id(table_id)
    except Exception:
        variations = []
    available_sizes = []
    if variations:
        for v in variations:
            size = get_product_field(v, 'size', '')
            quantity = v.get('quantity', None)
            if size and (quantity is None or quantity > 0):
                available_sizes.append(size)
    import re as _re
    numeric_sizes = []
    for s in available_sizes:
        ss = str(s).strip()
        m = _re.search(r"(\d+(?:[\.,]\d+)?)", ss)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(',', '.'))
            numeric_sizes.append((val, ss))
        except Exception:
            continue
    seen = set()
    numeric_sizes_sorted = []
    for val, ss in sorted(numeric_sizes, key=lambda x: x[0]):
        if val in seen:
            continue
        seen.add(val)
        disp = str(int(val)) if val.is_integer() else ("{:.1f}".format(val).rstrip('0').rstrip('.') if val % 1 != 0 else str(int(val)))
        numeric_sizes_sorted.append(disp)
    # build caption
    parts = []
    parts.append(f"{product_name}")
    if description_clean:
        parts.append(f"<blockquote>{description_clean}</blockquote>")
    if article_to_show:
        parts.append(f"<b>Артикул: {article_to_show}</b>")
    if numeric_sizes_sorted:
        sizes_text = ", ".join(numeric_sizes_sorted[:10])
        if len(numeric_sizes_sorted) > 10:
            sizes_text += f" и еще {len(numeric_sizes_sorted) - 10}"
        parts.append(f"Размеры: {sizes_text}")
    price_text = f"Цена: {price_value}₽" if price_value and price_value > 0 else "Цена: Уточняйте"
    parts.append(price_text)
    parts.append("Возврат в течение 14 дней")
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = ''
    deep_link = f"https://t.me/{bot_username}?start=product_{actual_product_id}" if bot_username else ""
    support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""
    link_chunks = []
    if deep_link:
        link_chunks.append(f"<a href=\"{deep_link}\">🛒 Купить в один клик</a>")
    if support_link:
        link_chunks.append(f"<a href=\"{support_link}\">🆘 Служба поддержки</a>")
    if link_chunks:
        parts.append(" | ".join(link_chunks))
    # hashtags
    hashtags_to_show = data.get('override_tags', '')
    if not hashtags_to_show and description_to_show and '\n' in (description_to_show or ''):
        h_lines = [ln.strip() for ln in description_to_show.split('\n') if ln.strip().startswith('#')]
        if h_lines:
            hashtags_to_show = ' '.join(h_lines)
    if not hashtags_to_show and keywords and str(keywords).strip():
        hashtags_to_show = str(keywords).strip()
    if hashtags_to_show:
        parts.append(f"{hashtags_to_show}")
    caption = "\n\n".join(parts)
    # send preview
    if files:
        media = []
        for idx, p in enumerate(files[:10]):
            media_input = _resolve_media_input(p)
            if idx == 0:
                media.append(types.InputMediaPhoto(media_input, caption=caption, parse_mode="HTML"))
            else:
                media.append(types.InputMediaPhoto(media_input))
        bot.send_media_group(user_id, media)
    else:
        bot.send_message(user_id, caption, parse_mode="HTML")
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="🚀 Выложить", callback_data=f"post_publish_{actual_product_id}"),
        types.InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"post_edit_{actual_product_id}")
    )
    markup.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"post_cancel_{actual_product_id}"))
    bot.send_message(user_id, "Предпросмотр поста. Что делаем?", reply_markup=markup)
    temp_data[user_id]['step'] = 'preview'

@bot.message_handler(func=lambda m: temp_data.get(m.from_user.id, {}).get('step') == 'edit_text')
def handle_new_caption(message):
    user_id = message.from_user.id
    data = temp_data.get(user_id) or {}
    files = data.get('photos', [])
    new_text = message.text or ""
    # Пересобираем подпись по тому же шаблону предпросмотра
    try:
        product_id = data.get('product_id')
        product = db_actions.get_product(int(product_id)) if product_id and str(product_id).isdigit() else None
        product_name = get_product_field(product, 'name', 'Неизвестно') if product else f"Товар {product_id}"
        description_full = get_product_field(product, 'description_full', '') if product else ''
        description_old = get_product_field(product, 'description', '') if product else ''
        table_id = get_product_field(product, 'table_id', '') if product else ''
        keywords = get_product_field(product, 'keywords', '') if product else ''
        price = get_product_field(product, 'price', 0) if product else 0

        # Размеры
        try:
            variations = db_actions.get_product_variations(int(product_id)) if product else []
        except Exception:
            variations = []
        available_sizes = []
        if variations:
            for v in variations:
                size = get_product_field(v, 'size', '')
                quantity = get_product_field(v, 'quantity', 0)
                if quantity > 0 and size:
                    available_sizes.append(size)

        # Ссылки
        try:
            bot_username = bot.get_me().username
        except Exception:
            bot_username = ''
        deep_link = f"https://t.me/{bot_username}?start=product_{product_id}" if bot_username else ""
        support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""

        # Хэштеги: из исходного описания или keywords
        description_to_show = description_full if description_full else description_old
        hashtags_to_show = ''
        if description_to_show and '\n' in description_to_show:
            h_lines = [ln.strip() for ln in description_to_show.split('\n') if ln.strip().startswith('#')]
            if h_lines:
                hashtags_to_show = ' '.join(h_lines)
        if not hashtags_to_show and keywords and keywords.strip():
            hashtags_to_show = keywords.strip()

        # Описание заменяем на новый текст пользователя (без строк-хэштегов)
        description_clean = new_text
        if description_clean and '\n' in description_clean:
            _lines = description_clean.split('\n')
            description_clean = '\n'.join([ln for ln in _lines if not ln.strip().startswith('#')]).strip()

        parts = []
        parts.append(f"{product_name}")
        if description_clean:
            parts.append(f"{description_clean}")
        if table_id:
            parts.append(f"<b>Артикул: {table_id}</b>")
        if available_sizes:
            sizes_text = ", ".join(available_sizes[:10])
            if len(available_sizes) > 10:
                sizes_text += f" и еще {len(available_sizes) - 10}"
            parts.append(f"Размеры: {sizes_text}")
        parts.append(f"Цена: {price}₽")
        link_chunks = []
        if deep_link:
            link_chunks.append(f"<a href=\"{deep_link}\">🛒 Купить в один клик</a>")
        if support_link:
            link_chunks.append(f"<a href=\"{support_link}\">🆘 Служба поддержки</a>")
        if link_chunks:
            parts.append(" | ".join(link_chunks))
        parts.append("Возврат в течение 14 дней")
        if hashtags_to_show:
            parts.append(f"{hashtags_to_show}")
        rebuilt_caption = "\n\n".join(parts)

        media = []
        for idx, p in enumerate(files[:10]):
            if idx == 0:
                media.append(types.InputMediaPhoto(open(p, 'rb'), caption=rebuilt_caption, parse_mode="HTML"))
            else:
                media.append(types.InputMediaPhoto(open(p, 'rb')))
        bot.send_media_group(user_id, media)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(text="🚀 Выложить", callback_data=f"post_publish_{data.get('product_id')}")
        )
        markup.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"post_cancel_{data.get('product_id')}") )
        bot.send_message(user_id, "Готово к публикации", reply_markup=markup)
        temp_data[user_id]['step'] = 'preview'
    except Exception as e:
        log_error(logger, e, "Ошибка предпросмотра после редактирования")
        bot.send_message(user_id, "❌ Ошибка предпросмотра")

@bot.message_handler(commands=['export_users'])
def export_users(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    users = db_actions.get_all_users()
    if not users:
        bot.send_message(user_id, "Нет пользователей для экспорта")
        return
        
    df = pd.DataFrame(users, columns=['user_id', 'first_name', 'last_name', 'username'])
    
    filename = f"users_export_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    df.to_excel(filename, index=False)
    
    with open(filename, 'rb') as f:
        bot.send_document(user_id, f, caption="📊 Экспорт пользователей")
    
    os.remove(filename)


@bot.message_handler(commands=['order_status'])
def order_status_command(message):
    """Изменение статуса заказа"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    args = message.text.split()
    if len(args) < 3:
        bot.send_message(user_id, 
            "Использование: /order_status [order_id] [status]\n\n"
            "Примеры статусов:\n"
            "• НОВЫЙ\n"
            "• ПОДТВЕРЖДЕН\n" 
            "• ОПЛАЧЕН\n"
            "• ОТПРАВЛЕН\n"
            "• ДОСТАВЛЕН\n"
            "• ОТМЕНЕН"
        )
        return
        
    try:
        order_id = int(args[1])
        status = ' '.join(args[2:])
        
        success = db_actions.update_order_status(order_id, status)
        
        if success:
            order_info = db_actions.get_order_by_id(order_id)
            if order_info:
                user_data = db_actions.get_user_data(order_info['user_id'])
                product = db_actions.get_product(order_info['product_id'])
                
                status_texts = {
                    'new': '🆕 НОВЫЙ',
                    'confirmed': '✅ ПОДТВЕРЖДЕН',
                    'paid': '💳 ОПЛАЧЕН', 
                    'shipped': '🚚 ОТПРАВЛЕН',
                    'delivered': '📦 ДОСТАВЛЕН',
                    'cancelled': '❌ ОТМЕНЕН'
                }
                
                status_display = status_texts.get(status.lower(), status.upper())
                
                # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ПОЛЬЗОВАТЕЛЮ
                try:
                    bot.send_message(
                        order_info['user_id'],
                        f"📦 Статус вашего заказа #{order_id} изменен:\n"
                        f"🔄 {status_display}\n\n"
                        f"🛍️ Товар: {get_product_name(product) if product else 'Неизвестно'}\n"
                        f"💰 Сумма: {get_product_field(product, 'price', 0) if product else '0'}₽"
                    )
                except Exception as e:
                    print(f"Ошибка уведомления пользователя: {e}")
            
            bot.send_message(user_id, f"✅ Статус заказа #{order_id} изменен на '{status}'")
        else:
            bot.send_message(user_id, "❌ Заказ не найден")
            
    except ValueError:
        bot.send_message(user_id, "❌ order_id должен быть числом")
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка: {str(e)}")

# ============ ОБРАБОТЧИКИ ТЕКСТОВЫХ КНОПОК АДМИНА ============

@bot.message_handler(func=lambda msg: msg.text == '➕ Добавить товар')
def admin_add_product_text(message):
    """Обработчик кнопки 'Добавить товар'"""
    clear_temp_data(message.from_user.id)
    add_product(message)

@bot.message_handler(func=lambda msg: msg.text == '👤 Информация о пользователе')
def admin_user_info_text(message):
    """Обработчик кнопки 'Информация о пользователе'"""
    clear_temp_data(message.from_user.id)
    user_info(message)

@bot.message_handler(func=lambda msg: msg.text == '🎯 Установить скидку')
def admin_set_discount_text(message):
    """Обработчик кнопки 'Установить скидку'"""
    clear_temp_data(message.from_user.id)
    bot.send_message(message.from_user.id, "Использование: /set_discount [user_id] [%]")

@bot.message_handler(func=lambda msg: msg.text == '💰 Добавить монеты')
def admin_add_coins_text(message):
    """Обработчик кнопки 'Добавить монеты'"""
    clear_temp_data(message.from_user.id)
    bot.send_message(message.from_user.id, "Использование: /add_coins [user_id] [amount]")

@bot.message_handler(func=lambda msg: msg.text == '📤 Загрузить товары')
def admin_upload_products_text(message):
    """Обработчик кнопки 'Загрузить товары'"""
    clear_temp_data(message.from_user.id)
    upload_products(message)

@bot.message_handler(func=lambda msg: msg.text == '📊 Статистика админа')
def admin_stats_text(message):
    """Обработчик кнопки 'Статистика админа'"""
    clear_temp_data(message.from_user.id)
    admin_stats(message)

@bot.message_handler(func=lambda msg: msg.text == '📋 Экспорт пользователей')
def admin_export_users_text(message):
    """Обработчик кнопки 'Экспорт пользователей'"""
    clear_temp_data(message.from_user.id)
    export_users(message)

@bot.message_handler(func=lambda msg: msg.text == '📝 Создать пост')
def admin_create_post_text(message):
    """Обработчик кнопки 'Создать пост'"""
    clear_temp_data(message.from_user.id)
    create_post(message)

@bot.message_handler(func=lambda msg: msg.text == '📦 Экспорт товаров')
def admin_export_products_text(message):
    """Обработчик кнопки 'Экспорт товаров'"""
    clear_temp_data(message.from_user.id)
    export_products(message)

@bot.message_handler(func=lambda msg: msg.text == '📋 Статус заказов')
def admin_order_status_text(message):
    """Обработчик кнопки 'Статус заказов'"""
    clear_temp_data(message.from_user.id)
    bot.send_message(
        message.from_user.id,
        "Использование: /order_status [order_id] [status]\n\n"
        "Примеры статусов:\n"
        "• НОВЫЙ\n"
        "• ПОДТВЕРЖДЕН\n" 
        "• ОПЛАЧЕН\n"
        "• ОТПРАВЛЕН\n"
        "• ДОСТАВЛЕН\n"
        "• ОТМЕНЕН"
    )

@bot.message_handler(commands=['orders'])
def list_orders(message):
    """Показывает список всех заказов"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    status_filter = None
    args = message.text.split()
    if len(args) > 1:
        status_filter = args[1].lower()
    
    orders = db_actions.get_all_orders(status_filter)
    
    if not orders:
        bot.send_message(user_id, "Заказы не найдены")
        return
    
    orders_text = "📦 СПИСОК ЗАКАЗОВ"
    if status_filter:
        orders_text += f" (фильтр: {status_filter})"
    orders_text += "\n\n"
    
    for order in orders:
        product = db_actions.get_product(order['product_id'])
        user_data = db_actions.get_user_data(order['user_id'])
        
        orders_text += (
            f"🛒 Заказ #{order['order_id']}\n"
            f"👤 {user_data['first_name']} {user_data['last_name']}\n"
            f"🛍️ {get_product_name(product) if product else 'Неизвестно'}\n"
            f"📊 Статус: {order['status']}\n"
            f"🕒 {order['created_at']}\n"
            f"🔗 /order_info_{order['order_id']}\n\n"
        )
    
    bot.send_message(user_id, orders_text)

@bot.message_handler(func=lambda message: message.text.startswith('/order_info_'))
def order_info(message):
    user_id = message.from_user.id
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
        
    try:
        order_id = int(message.text.split('_')[2])
        order_info = db_actions.get_order_by_id(order_id)
        
        if not order_info:
            bot.send_message(user_id, "❌ Заказ не найден")
            return
            
        product = db_actions.get_product(order_info['product_id'])
        user_data = db_actions.get_user_data(order_info['user_id'])
        buttons = Bot_inline_btns()
        
        info_text = (
            f"📦 ЗАКАЗ #{order_id}\n\n"
            f"👤 КЛИЕНТ:\n"
            f"• Имя: {user_data['first_name']} {user_data['last_name']}\n"
            f"• @{user_data['username']}\n"
            f"• ID: {user_data['user_id']}\n\n"
            f"🛍️ ТОВАР:\n"
            f"• {get_product_name(product) if product else 'Неизвестно'}\n"
            f"• Цена: {get_product_field(product, 'price', 0) if product else '0'}₽\n\n"
            f"📦 ДОСТАВКА:\n"
            f"• Город: {order_info['city']}\n"
            f"• Адрес: {order_info['address']}\n"
            f"• ФИО: {order_info['full_name']}\n"
            f"• Телефон: {order_info['phone']}\n"
            f"• Способ: {order_info['delivery_type']}\n\n"
            f"📊 СТАТУС: {order_info['status']}\n"
            f"🕒 СОЗДАН: {order_info['created_at']}\n\n"
            f"⚙️ Управление: /order_status {order_id} [статус]"
        )
        
        bot.send_message(user_id, info_text, reply_markup=buttons.create_order_status_buttons(order_id))
        
    except (IndexError, ValueError):
        bot.send_message(user_id, "❌ Неверный формат команды")
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка: {str(e)}")

@bot.message_handler(commands=['add_product'])
def add_product(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
        
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "Эта команда только для администраторов")
        return
        
    bot.send_message(user_id, "Отправьте фото товара")
    bot.register_next_step_handler(message, process_product_photo)

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    """Универсальная команда отмены текущего процесса"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    bot.send_message(user_id, "❌ Текущий процесс отменен")

@bot.message_handler(commands=['check_product'])
def check_product_data(message):
    """Проверить данные товара"""
    user_id = message.from_user.id
    clear_temp_data(user_id)
    
    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return
    
    args = message.text.split()
    if len(args) != 2:
        bot.send_message(user_id, "Использование: /check_product <product_id>")
        return
    
    try:
        product_id = int(args[1])
        product = db_actions.get_product(product_id)
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            return
        
        # Показываем все поля товара
        info = f"🔍 Данные товара ID {product_id}:\n\n"
        for key, value in product.items():
            info += f"• {key}: {value}\n"
        
        bot.send_message(user_id, info)
        
    except ValueError:
        bot.send_message(user_id, "❌ ID товара должен быть числом")

@bot.message_handler(commands=['check_product_by_table_id'])
def check_product_by_table_id(message):
    """Проверить данные товара по table_id (артикулу)"""
    user_id = message.from_user.id
    clear_temp_data(user_id)

    if not db_actions.user_is_admin(user_id):
        bot.send_message(user_id, "⛔️ Недостаточно прав")
        return

    args = message.text.split()
    if len(args) != 2:
        bot.send_message(user_id, "Использование: /check_product_by_table_id <table_id>")
        return

    try:
        table_id = args[1]
        product = db_actions.get_product_by_table_id(table_id)

        if not product:
            bot.send_message(user_id, f"❌ Товар с артикулом {table_id} не найден")
            return

        info = f"🔍 Данные товара с артикулом {table_id}:\n\n"
        for key, value in product.items():
            info += f"• {key}: {repr(value)}\n"

        bot.send_message(user_id, info)

    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка: {str(e)}")

@bot.message_handler(commands=['test_order'])
def test_order(message):
    user_id = message.from_user.id
    clear_temp_data(user_id)
    try:
        # Симулируем процесс заказа
        temp_data[user_id] = {
            'order': {
                'product_id': 36,
                'size': '42.0',
                'step': 'ask_delivery'
            }
        }
        
        delivery_form = (
            "📦 ДЛЯ ОФОРМЛЕНИЯ ЗАКАЗА\n\n"
            "Пожалуйста, заполните данные доставки ОДНИМ сообщением в формате:\n\n"
            "🏙️ Город: Ваш город\n"
            "📍 Адрес: Улица, дом, квартира\n"
            "👤 ФИО: Иванов Иван Иванович\n"
            "📞 Телефон: +79123456789\n"
            "🚚 Доставка: СДЭК\n\n"
            "Пример:\n"
            "Москва\n"
            "ул. Ленина, д. 10, кв. 5\n"
            "Иванов Иван Иванович\n"
            "+79123456789\n"
            "СДЭК"
        )
        
        bot.send_message(user_id, delivery_form)
        
    except Exception as e:
        bot.send_message(user_id, f"Ошибка теста: {e}")

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
    
        user_id = message.from_user.id
        temp_data[user_id] = {
            'name': name,
            'description': desc,
            'price': price,
            'photo_id': photo_id,
            'step': 'ready_to_save'
        }
        
        # Сразу сохраняем товар
        product_id = db_actions.add_product(
            name=name,
            description=desc,
            price=price,
            price_yuan=0,
            photo_id=photo_id,
            category="магазин"
        )
        
        if product_id:
            bot.send_message(
                message.chat.id,
                f"✅ Товар «{name}» успешно добавлен!\n"
                f"💰 Цена: {price}₽\n"
                f"🆔 ID: {product_id}"
            )
        else:
            bot.send_message(message.chat.id, "❌ Ошибка при добавлении товара")
            
        # Очищаем временные данные
        if user_id in temp_data:
            del temp_data[user_id]
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат цены. Используйте только числа")

# ============ ОБРАБОТЧИКИ CALLBACK ============

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
    
    achievements_str += "\n\n📖 [Подробнее о системе ачивок](https://telegra.ph/FAQ-Sistema-achivok--Bridge-Side-Collective-09-19)"
    
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
    markup.add(btn1)
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=f"💎 Способы получения BS Coin:\n\n"
            f"1. 🎁 Ежедневный бонус: +10 BS Coin каждый день (/start)\n"
            f"2. 👥 Реферальная система: +100 BS Coin за каждого приглашенного друга (/ref)\n"
            f"3. 💬 Активность в канале: комментируйте посты и получайте монеты\n"
            f"4. 🏆 Достижения: выполняйте задания и получайте бонусы\n\n"
            f"📖 [Подробнее о системе ачивок](https://telegra.ph/FAQ-Sistema-achivok--Bridge-Side-Collective-09-19)\n\n"
            f"💰 Ваш текущий баланс: {user_data['bs_coin']} BS Coin",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'ref_link')
def ref_link(call):
    user_id = call.message.chat.id
    user_data = db_actions.get_user_data(user_id)
    
    if not user_data:
        bot.answer_callback_query(call.id, "Сначала зарегистрируйтесь с помощью /start")
        return
        
    ref_count = db_actions.get_referral_stats(user_id)
    ref_link = f"https://t.me/{bot.get_me().username}?start={user_data['referral_code']}"
    
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
        f"• Ваш друзья получает 50 BS Coin при первом заказе\n\n"
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


def publish_product_to_channel(product):
    try:
        if not product.get('product_id'):
            log_error(logger, "product_id не определен")
            return None
            
        config_data = config.get_config()
        chat_id = config_data['chat_id']
        topic_id = config_data['topics']['магазин']
        
        # Формируем карточку товара в требуемом формате
        name = product.get('name', 'Неизвестно')
        description = product.get('description', '') or ''
        # Убираем строки-хэштеги из описания
        if '\n' in description:
            _lines = description.split('\n')
            description = '\n'.join([ln for ln in _lines if not ln.strip().startswith('#')]).strip()
        table_id = product.get('table_id') or product.get('article') or ''
        price = product.get('price', 0)
        # Хэштеги: из описания или из поля keywords
        hashtags = ''
        if 'keywords' in product and product.get('keywords'):
            hashtags = product.get('keywords', '').strip()
        else:
            # Попробуем достать строки-хэштеги из исходного описания
            orig_desc = product.get('description', '') or ''
            if '\n' in orig_desc:
                h_lines = [ln.strip() for ln in orig_desc.split('\n') if ln.strip().startswith('#')]
                if h_lines:
                    hashtags = ' '.join(h_lines)
        
        caption_parts = []
        caption_parts.append(f"{name}")
        if description:
            caption_parts.append(f"{description}")
        if table_id:
            caption_parts.append(f"<b>Артикул: {table_id}</b>")
        caption_parts.append(f"Цена: {price}₽")

        # Размеры (если есть в базе)
        try:
            variations = db_actions.get_product_variations(product.get('product_id'))
            available_sizes = [v['size'] for v in variations if v.get('quantity', 0) > 0 and v.get('size')]
        except Exception:
            available_sizes = []
        # Отсортируем и покажем только числовые размеры
        import re
        numeric_sizes = []
        for s in available_sizes:
            ss = str(s).strip()
            m = re.search(r"(\d+(?:[\.,]\d+)?)", ss)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(',', '.'))
                numeric_sizes.append((val, ss))
            except Exception:
                continue
        seen = set()
        numeric_sizes_sorted = []
        for val, ss in sorted(numeric_sizes, key=lambda x: x[0]):
            if val in seen:
                continue
            seen.add(val)
            disp = str(int(val)) if val.is_integer() else ("{:.1f}".format(val).rstrip('0').rstrip('.') if val % 1 != 0 else str(int(val)))
            numeric_sizes_sorted.append(disp)
        if numeric_sizes_sorted:
            sizes_text = ", ".join(numeric_sizes_sorted[:10])
            if len(numeric_sizes_sorted) > 10:
                sizes_text += f" и еще {len(numeric_sizes_sorted) - 10}"
            caption_parts.append(f"Размеры: {sizes_text}")

        # Ссылки: Купить в один клик и Служба поддержки (HTML)
        try:
            bot_username = bot.get_me().username
        except Exception:
            bot_username = ''
        deep_link = f"https://t.me/{bot_username}?start=product_{product['product_id']}" if bot_username else ""
        support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""
        links_line = []
        if deep_link:
            links_line.append(f"<a href=\"{deep_link}\">Купить в один клик</a>")
        if support_link:
            links_line.append(f"<a href=\"{support_link}\">Служба поддержки</a>")
        if links_line:
            caption_parts.append(" | ".join(links_line))

        # Политика возврата
        caption_parts.append("Возврат в течение 14 дней")
        if hashtags:
            caption_parts.append(f"{hashtags}")
        caption = "\n\n".join(caption_parts)
        
        message = bot.send_photo(
            chat_id=chat_id,
            photo=product['photo_id'],
            caption=caption,
            parse_mode="HTML",
            message_thread_id=topic_id
        )
        
        channel_id = str(abs(chat_id))
        return f"https://t.me/c/{channel_id}/{message.message_id}?thread={topic_id}"
    
    except Exception as e:
        print(f"Ошибка публикации: {e}")
        return None

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_product_'))
def select_product_for_post(call):
    user_id = call.from_user.id
    product_id = int(call.data.split('_')[2])
    
    if user_id not in temp_data or temp_data[user_id]['step'] != 'select_product':
        bot.answer_callback_query(call.id, "❌ Ошибка процесса")
        return
        
    product = db_actions.get_product(product_id)
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар не найден")
        return
        
    temp_data[user_id]['product_id'] = product_id
    temp_data[user_id]['step'] = 'add_photos'
    temp_data[user_id]['product_name'] = get_product_name(product)
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=f"📦 Выбран товар: {get_product_name(product)}\n\n"
            f"📸 Теперь отправьте до 6 фотографий товара\n"
            f"📝 После отправки фото напишите текст для поста\n"
            f"❌ Отправьте /cancel для отмены"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data in ['exclusive_yes_post', 'exclusive_no_post'])
def handle_exclusive_post(call):
    user_id = call.from_user.id
    is_exclusive = (call.data == 'exclusive_yes_post')
    
    # Улучшенная проверка состояния
    if user_id not in temp_data or 'product_id' not in temp_data[user_id]:
        try:
            bot.answer_callback_query(call.id, "❌ Процесс создания поста прерван. Начните заново.")
            bot.edit_message_text(
                chat_id=user_id,
                message_id=call.message.message_id,
                text="❌ Процесс прерван. Используйте /create_post для начала заново."
            )
        except:
            bot.send_message(user_id, "❌ Процесс прерван. Используйте /create_post для начала заново.")
        return
        
    product_id = temp_data[user_id]['product_id']
    product = db_actions.get_product(product_id)
    
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар не найден")
        return
    
    try:
        if is_exclusive:
            temp_data[user_id]['step'] = 'ask_coin_price_post'
            bot.edit_message_text(
                chat_id=user_id,
                message_id=call.message.message_id,
                text="💎 Укажите цену в BS Coin (только целое число):\n\n❌ Отправьте /cancel для отмены"
            )
        else:
            # Обновляем статус товара в базе
            success = db_actions.update_product_exclusive(product_id, False, 0)
            if not success:
                bot.answer_callback_query(call.id, "❌ Ошибка обновления товара")
                return
                
            # Публикуем пост
            table_id = temp_data[user_id].get('table_id', '')
            post_success = publish_post_to_channel(
                table_id,
                temp_data[user_id].get('photos', []),
                temp_data[user_id].get('text', ''),
                False,
                0
            )
            
            if post_success:
                bot.answer_callback_query(call.id, "✅ Пост опубликован!")
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=call.message.message_id,
                    text=f"✅ Товар успешно опубликован в @BridgeSide_Store\n\n"
                        f"🛍️ Товар: {temp_data[user_id].get('product_name', 'Неизвестно')}\n"
                        f"🎯 Статус: Обычный (рубли)\n"
                        f"💰 Цена: {get_product_field(product, 'price', 0)}₽"
                )
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка публикации")
                bot.send_message(user_id, "❌ Не удалось опубликовать пост. Проверьте настройки канала.")
            
            # Очищаем временные данные
            if user_id in temp_data:
                del temp_data[user_id]
                
    except Exception as e:
        print(f"Ошибка в handle_exclusive_post: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")
        bot.send_message(user_id, "❌ Произошла ошибка. Попробуйте снова /create_post")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('step') == 'ask_coin_price_post')
def handle_coin_price_input(message):
    user_id = message.from_user.id
    
    if message.text.lower() == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    process_coin_price_post(message)

def process_coin_price_post(message):
    user_id = message.from_user.id
    
    if user_id not in temp_data or temp_data[user_id].get('step') != 'ask_coin_price_post':
        bot.send_message(user_id, "❌ Ошибка процесса. Используйте /create_post для начала заново.")
        return
        
    if message.text.lower() == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    try:
        coin_price = int(message.text)
        if coin_price <= 0:
            raise ValueError("Цена должна быть положительной")
            
        product_id = temp_data[user_id]['product_id']
        product = db_actions.get_product(product_id)
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            if user_id in temp_data:
                del temp_data[user_id]
            return
            
        # Обновляем статус товара
        success = db_actions.update_product_exclusive(product_id, True, coin_price)
        if not success:
            bot.send_message(user_id, "❌ Ошибка обновления товара")
            if user_id in temp_data:
                del temp_data[user_id]
            return
        
        # Публикуем пост
        table_id = temp_data[user_id].get('table_id', '')
        post_success = publish_post_to_channel(
            table_id,
            temp_data[user_id].get('photos', []),
            temp_data[user_id].get('text', ''),
            True,
            coin_price
        )
        
        if post_success:
            bot.send_message(
                user_id,
                f"✅ Товар успешно опубликован в @BridgeSide_Store\n\n"
                f"🛍️ Товар: {temp_data[user_id].get('product_name', 'Неизвестно')}\n"
                f"🎯 Статус: Эксклюзивный\n"
                f"💎 Цена: {coin_price} BS Coin"
            )
        else:
            bot.send_message(user_id, "❌ Ошибка при публикации поста. Проверьте настройки канала.")
        
    except ValueError:
        bot.send_message(user_id, "❌ Неверный формат цены. Используйте только целые положительные числа.")
        bot.send_message(user_id, "💎 Укажите цену в BS Coin:")
        return
    except Exception as e:
        print(f"Ошибка при публикации: {e}")
        bot.send_message(user_id, f"❌ Ошибка при публикации: {str(e)}")
    finally:
        if user_id in temp_data:
            del temp_data[user_id]

def publish_post_to_channel(table_id, photos, text, is_exclusive, coin_price=0):
    try:
        # Получаем товар по table_id (артикулу)
        product = db_actions.get_product_by_table_id(table_id)
        if not product:
            log_error(logger, f"Товар с артикулом {table_id} не найден")
            return False
            
        config_data = config.get_config()
        channel_id = config_data.get('store_channel_id', '@BridgeSide_Store')
        
        if not channel_id:
            log_error(logger, "Не указан channel_id в конфиге")
            return False
        
        actual_product_id = get_product_field(product, 'product_id', 0)
        deep_link = f"https://t.me/{bot.get_me().username}?start=product_{actual_product_id}"
        
        # Получаем данные товара
        product_name = get_product_field(product, 'name', 'Неизвестно')
        description_full = get_product_field(product, 'description_full', '')
        product_table_id = get_product_field(product, 'table_id', '')
        keywords = get_product_field(product, 'keywords', '')
        
        # Отладочная информация
        log_info(logger, f"DEBUG: product_name: {repr(product_name)}")
        log_info(logger, f"DEBUG: description_full: {repr(description_full)}")
        log_info(logger, f"DEBUG: product_table_id: {repr(product_table_id)}")
        log_info(logger, f"DEBUG: keywords: {repr(keywords)}")
        
        # Получаем доступные размеры
        # Вариации по product_id, если пусто — пробуем по model_id (table_id)
        variations = db_actions.get_product_variations(actual_product_id)
        if not variations:
            try:
                model_id = get_product_field(product, 'table_id', '') or get_product_field(product, 'article', '')
                if model_id:
                    variations = db_actions.get_product_variations_by_model_id(model_id)
            except Exception:
                variations = []
        available_sizes = []
        if variations:
            for variation in variations:
                size = get_product_field(variation, 'size', '')
                quantity = get_product_field(variation, 'quantity', 0)
                if quantity > 0 and size:
                    available_sizes.append(size)
        
        # Формируем цену
        if not is_exclusive:
            price_text = f"💰 Цена: {get_product_field(product, 'price', 0)}₽"
        else:
            price_text = f"💎 Цена: {coin_price} BS Coin"
        
        # Формируем карточку товара для группы:
        # Название, Описание, Артикул (жирный), Размеры, Цена, Ссылки, Возврат, Хэштеги
        caption_parts = []
        caption_parts.append(f"{product_name}")
        
        description_old = get_product_field(product, 'description', '')
        description_to_show = description_full if description_full else description_old
        if description_to_show:
            description_clean = description_to_show
            if '\n' in description_clean:
                lines = description_clean.split('\n')
                description_clean = '\n'.join([line for line in lines if not line.strip().startswith('#')]).strip()
            if description_clean:
                caption_parts.append(description_clean)
        
        # Блок деталей: Артикул, Размеры, Цена — одним блоком, одинарные переносы
        details_lines = []
        if product_table_id:
            details_lines.append(f"<b>Артикул: {product_table_id}</b>")
        sizes_text = None
        if available_sizes:
            import re
            numeric_sizes = []
            for s in available_sizes:
                ss = str(s).strip()
                m = re.search(r"(\d+(?:[\.,]\d+)?)", ss)
                if not m:
                    continue
                try:
                    val = float(m.group(1).replace(',', '.'))
                    numeric_sizes.append((val, ss))
                except Exception:
                    continue
            seen = set()
            numeric_sizes_sorted = []
            for val, ss in sorted(numeric_sizes, key=lambda x: x[0]):
                if val in seen:
                    continue
                seen.add(val)
                disp = str(int(val)) if val.is_integer() else ("{:.1f}".format(val).rstrip('0').rstrip('.') if val % 1 != 0 else str(int(val)))
                numeric_sizes_sorted.append(disp)
            if numeric_sizes_sorted:
                sizes_text = ", ".join(numeric_sizes_sorted[:10])
                if len(numeric_sizes_sorted) > 10:
                    sizes_text += f" и еще {len(numeric_sizes_sorted) - 10}"
            else:
                uniq_raw = []
                seen_raw = set()
                for s in available_sizes:
                    ss = str(s).strip()
                    if ss and ss not in seen_raw:
                        seen_raw.add(ss)
                        uniq_raw.append(ss)
                if uniq_raw:
                    sizes_text = ", ".join(uniq_raw[:10])
                    if len(uniq_raw) > 10:
                        sizes_text += f" и еще {len(uniq_raw) - 10}"
        if sizes_text:
            details_lines.append(f"Размеры: {sizes_text}")
        details_lines.append(f"{price_text.replace('💰 ', '').replace('💎 ', '')}")
        if details_lines:
            caption_parts.append("\n".join(details_lines))

        # Ссылки: Купить в один клик и Служба поддержки
        try:
            bot_username = bot.get_me().username
        except Exception:
            bot_username = ''
        support_link = f"https://t.me/{bot_username}?start=support" if bot_username else ""
        links_line = []
        links_line.append(f"<a href=\"{deep_link}\">🛒 Купить в один клик</a>")
        if support_link:
            links_line.append(f"<a href=\"{support_link}\">🆘 Служба поддержки</a>")
        if links_line:
            caption_parts.append(" \n ".join(links_line))

        # Политика возврата
        caption_parts.append("Возврат в течение 14 дней")
        
        hashtags_to_show = ""
        if description_to_show and '\n' in description_to_show:
            lines = description_to_show.split('\n')
            hashtag_lines = [line.strip() for line in lines if line.strip().startswith('#')]
            if hashtag_lines:
                hashtags_to_show = ' '.join(hashtag_lines)
        if not hashtags_to_show and keywords:
            hashtags_to_show = keywords.strip()
        if hashtags_to_show:
            caption_parts.append(f"{hashtags_to_show}")
        
        caption = "\n\n".join(caption_parts)
        
        # Отладочная информация
        log_info(logger, f"DEBUG: Отправляем в канал caption: {repr(caption)}")
        log_info(logger, f"DEBUG: Количество частей caption: {len(caption_parts)}")
        for i, part in enumerate(caption_parts):
            log_info(logger, f"DEBUG: Часть {i}: {repr(part)}")
        
        # Отправляем медиагруппу с фотографиями
        if photos and len(photos) > 0:
            media = []
            
            # Первое фото с caption
            media.append(types.InputMediaPhoto(photos[0], caption=caption, parse_mode="HTML"))

            # Остальные фото без caption
            for photo in photos[1:]:
                media.append(types.InputMediaPhoto(photo))

            try:
                bot.send_media_group(
                    chat_id=channel_id,
                    media=media
                )
                return True
            except Exception as e:
                print(f"Ошибка отправки медиагруппы: {e}")
                # Пробуем отправить текстовое сообщение
                try:
                    bot.send_message(chat_id=channel_id, text=caption, parse_mode="HTML")
                    return True
                except Exception as e2:
                    print(f"Ошибка отправки текста: {e2}")
                    return False
        else:
            # Если нет фото, отправляем текстовое сообщение
            try:
                bot.send_message(chat_id=channel_id, text=caption, parse_mode="HTML")
                return True
            except Exception as e:
                print(f"Ошибка отправки текста: {e}")
                return False
            
    except Exception as e:
        print(f"Ошибка публикации в канал: {e}")
        return False
    
@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('step') in ['add_photos', 'add_text'])
def handle_post_creation(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    try:
        if temp_data[user_id]['step'] == 'add_photos':
            if message.content_type == 'photo':
                if len(temp_data[user_id]['photos']) < 6:
                    temp_data[user_id]['photos'].append(message.photo[-1].file_id)
                    remaining = 6 - len(temp_data[user_id]['photos'])
                    if remaining > 0:
                        bot.send_message(user_id, f"📸 Фото добавлено. Можно добавить еще {remaining} фото")
                    else:
                        bot.send_message(user_id, "✅ Максимум фото достигнут. Теперь отправьте текст поста")
                else:
                    bot.send_message(user_id, "❌ Максимум 6 фотографий. Отправьте текст поста")
            elif message.content_type == 'text':
                temp_data[user_id]['step'] = 'add_text'
                temp_data[user_id]['text'] = message.text
                ask_exclusive_status(user_id)
                
        elif temp_data[user_id]['step'] == 'add_text':
            temp_data[user_id]['text'] = message.text
            ask_exclusive_status(user_id)
            
    except Exception as e:
        print(f"Ошибка создания поста: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки. Попробуйте снова /create_post")
        if user_id in temp_data:
            del temp_data[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith('size_'))
def handle_size_selection(call):
    user_id = call.from_user.id
    try:
        parts = call.data.split('_')
        product_id = int(parts[1])
        size = parts[2]
        
        # Проверяем доступность размера
        if not db_actions.check_size_availability(product_id, size):
            bot.answer_callback_query(call.id, "❌ Этот размер недоступен")
            return
        
        # Сохраняем выбор пользователя
        if user_id not in temp_data:
            temp_data[user_id] = {}
        
        temp_data[user_id]['selected_product'] = product_id
        temp_data[user_id]['selected_size'] = size
        
        # Получаем информацию о товаре
        product = db_actions.get_product(product_id)
        if not product:
            bot.answer_callback_query(call.id, "❌ Товар не найден")
            return
        
        # Создаем кнопку заказа
        markup = types.InlineKeyboardMarkup()
        order_btn = types.InlineKeyboardButton(
            text="🛒 Заказать сейчас",
            callback_data=f"order_{product_id}_{size}"
        )
        markup.add(order_btn)
        
        # Обновляем сообщение
        try:
            if call.message.caption:
                bot.edit_message_caption(
                    chat_id=user_id,
                    message_id=call.message.message_id,
                    caption=call.message.caption,
                    reply_markup=markup
                )
            else:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=call.message.message_id,
                    text=call.message.text,
                    reply_markup=markup
                )
            
            bot.answer_callback_query(call.id, f"✅ Выбран размер: {size}")
            
        except Exception as e:
            log_error(logger, e, "Ошибка редактирования")
            bot.answer_callback_query(call.id, "❌ Ошибка выбора размера")
                
    except Exception as e:
        log_error(logger, e, "Ошибка в handle_size_selection")
        bot.answer_callback_query(call.id, "❌ Ошибка выбора размера")

@bot.callback_query_handler(func=lambda call: call.data.startswith('order_'))
def handle_order(call):
    user_id = call.from_user.id
    try:
        parts = call.data.split('_')
        product_id = int(parts[1])
        size = parts[2] if len(parts) > 2 else None
        
        print(f"DEBUG: Оформление заказа - product_id: {product_id}, size: {size}")
        
        # Сохраняем данные заказа
        if user_id not in temp_data:
            temp_data[user_id] = {}
        
        temp_data[user_id]['order'] = {
            'product_id': product_id,
            'size': size,
            'step': 'ask_city'
        }

        # Начинаем процесс заполнения данных доставки
        bot.send_message(
            user_id,
            "📦 ОФОРМЛЕНИЕ ЗАКАЗА\n\n"
            "Пожалуйста, заполните данные доставки:\n\n"
            "🏙️ Введите ваш город:"
        )
        bot.answer_callback_query(call.id, "📝 Заполните данные доставки")
        
    except Exception as e:
        print(f"Ошибка в handle_order: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка оформления заказа")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_city')
def ask_city(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['city'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_address'
    bot.send_message(user_id, "📍 Введите полный адрес (улица, дом, квартира):")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_address')
def ask_address(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['address'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_full_name'
    bot.send_message(user_id, "👤 Введите ФИО получателя:")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_full_name')
def ask_full_name(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['full_name'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_phone'
    bot.send_message(user_id, "📞 Введите номер телефона:")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_phone')
def ask_phone(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['phone'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_delivery_type'
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("СДЭК"))
    markup.add(types.KeyboardButton("Другое"))
    
    bot.send_message(user_id, "🚚 Выберите способ доставки:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == '🛒 Заказать товар')
def handle_order_button(message):
    bot.send_message(
        message.chat.id,
        "📦 Для заказа товара перейдите в наш канал:\n"
        "👉 @BridgeSide_Store\n\n"
        "Или нажмите на ссылку: https://t.me/BridgeSide_Store",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_delivery_type')
def ask_delivery_type(message):
    user_id = message.from_user.id
    
    # Если выбрано "Другое", переходим к специальной обработке
    if message.text == "Другое":
        handle_other_delivery(message)
        return
    
    # Убираем клавиатуру
    remove_markup = types.ReplyKeyboardRemove()
    
    temp_data[user_id]['order']['delivery_type'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_payment'
    
    # Получаем информацию о товаре
    product_id = temp_data[user_id]['order']['product_id']
    product = db_actions.get_product(product_id)
    
    if product:
        price, currency = get_product_price(product)
        
        # Показываем сводку по заказу
        order_summary = (
            f"✅ Данные доставки получены!\n\n"
            f"📋 Ваш заказ:\n"
            f"🛍️ Товар: {get_product_name(product)}\n"
            f"📏 Размер: {temp_data[user_id]['order'].get('size', 'Не указан')}\n"
            f"💰 Цена: {price} {currency}\n\n"
            f"📦 Доставка:\n"
            f"🏙️ Город: {temp_data[user_id]['order']['city']}\n"
            f"📍 Адрес: {temp_data[user_id]['order']['address']}\n"
            f"👤 ФИО: {temp_data[user_id]['order']['full_name']}\n"
            f"📞 Телефон: {temp_data[user_id]['order']['phone']}\n"
            f"🚚 Способ: {temp_data[user_id]['order']['delivery_type']}\n\n"
            f"💳 Теперь отправьте скриншот чека об оплате\n\n"
            f"РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            f"2200154531899085 \nАльфа-Банк\n\n"
            f"5280413753453047\nТ-банк\n\n"
            f"5228600520272271\nСБЕР\n\n"
            f"8-903-191-98-48 \nСПБ - Яна Ж."
        )
        
        bot.send_message(user_id, order_summary, reply_markup=remove_markup)

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: 
                    message.from_user.id in temp_data and 
                    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_payment')
def process_payment_photo(message):
    user_id = message.from_user.id
    
    try:
        # Сохраняем фото оплаты
        payment_photo_id = message.photo[-1].file_id
        temp_data[user_id]['order']['payment_photo'] = payment_photo_id
        temp_data[user_id]['order']['step'] = 'confirm_order'
        
        # Получаем информацию о товаре
        product_id = temp_data[user_id]['order']['product_id']
        product = db_actions.get_product(product_id)
        
        if product:
            price, currency = get_product_price(product)
            
            order_summary = (
                f"✅ ВСЕ ДАННЫЕ ПОЛУЧЕНЫ!\n\n"
                f"📋 Ваш заказ:\n"
                f"🛍️ Товар: {get_product_name(product)}\n"
                f"📏 Размер: {temp_data[user_id]['order'].get('size', 'Не указан')}\n"
                f"💰 Цена: {price} {currency}\n\n"
                f"📦 Доставка:\n"
                f"🏙️ Город: {temp_data[user_id]['order']['city']}\n"
                f"📍 Адрес: {temp_data[user_id]['order']['address']}\n"
                f"👤 ФИО: {temp_data[user_id]['order']['full_name']}\n"
                f"📞 Телефон: {temp_data[user_id]['order']['phone']}\n"
                f"🚚 Способ: {temp_data[user_id]['order']['delivery_type']}\n\n"
                f"📸 Фото оплаты приложено\n\n"
                f"Выберите действие:"
            )
            
            # Клавиатура подтверждения
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            confirm_btn = types.KeyboardButton("✅ Подтвердить заказ")
            edit_btn = types.KeyboardButton("✏️ Редактировать данные")
            cancel_btn = types.KeyboardButton("❌ Отменить заказ")
            markup.add(confirm_btn, edit_btn, cancel_btn)
            
            # Отправляем фото и описание
            bot.send_photo(user_id, payment_photo_id, caption="📸 Ваше фото оплаты:")
            bot.send_message(user_id, order_summary, reply_markup=markup)
        
    except Exception as e:
        print(f"Ошибка обработки фото: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки фото")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text == '✅ Подтвердить заказ')
def confirm_order_final(message):
    user_id = message.from_user.id
    
    try:
        order_data = temp_data[user_id]['order']
        product_id = order_data['product_id']
        product = db_actions.get_product(product_id)
        
        if not product:
            bot.send_message(user_id, "❌ Товар не найден")
            return
        
        # ОТЛАДОЧНАЯ ИНФОРМАЦИЯ
        print(f"DEBUG: Подтверждение заказа - user_id: {user_id}, product_id: {product_id}")
        print(f"DEBUG: Данные заказа: {order_data}")
        
        # Создаем заказ в базе
        order_id = db_actions.create_detailed_order(
            user_id=user_id,  # Убедитесь что передается правильный user_id
            product_id=product_id,
            size=order_data.get('size'),
            city=order_data['city'],
            address=order_data['address'],
            full_name=order_data['full_name'], 
            phone=order_data['phone'],
            delivery_type=order_data['delivery_type']
        )
        
        print(f"DEBUG: Создан заказ ID: {order_id}")
        
        if order_id:
            # Уведомляем админов
            notify_admins_about_order(user_id, product, order_data, order_id, order_data.get('payment_photo'))
            
            # Убираем клавиатуру
            remove_markup = types.ReplyKeyboardRemove()
            
            bot.send_message(
                user_id,
                f"✅ Заказ #{order_id} оформлен!\n\n"
                f"📞 С вами свяжутся в течение 15 минут для подтверждения.\n"
                f"💬 Отслеживать статус заказа можно в этом чате.",
                reply_markup=remove_markup
            )
            
            # Обновляем статистику пользователя
            db_actions.update_user_stats(user_id, 'orders', 1)
            
            # Проверяем ачивки
            check_achievement_conditions(user_id, 'first_purchase')
            
            # Достижения обрабатываются централизованно в check_achievement_conditions
        else:
            bot.send_message(user_id, "❌ Ошибка оформления заказа")
        
    except Exception as e:
        print(f"Ошибка подтверждения заказа: {e}")
        import traceback
        traceback.print_exc()
        bot.send_message(user_id, "❌ Ошибка оформления заказа")
    finally:
        # Очищаем временные данные
        if user_id in temp_data and 'order' in temp_data[user_id]:
            del temp_data[user_id]['order']

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text.lower() == '✏️ редактировать данные')
def edit_order_data(message):
    user_id = message.from_user.id
    
    # Предлагаем выбрать, какие данные редактировать
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    
    bot.send_message(
        user_id,
        "📝 Что хотите отредактировать?",
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_choice')
def handle_edit_choice(message):
    user_id = message.from_user.id
    choice = message.text
    
    if choice == "✅ Все верно":
        temp_data[user_id]['order']['step'] = 'confirm_order'
        show_order_confirmation(user_id)
        return
    
    if choice == "🏙️ Город":
        temp_data[user_id]['order']['step'] = 'edit_city'
        bot.send_message(user_id, "🏙️ Введите новый город:", reply_markup=types.ReplyKeyboardRemove())
    elif choice == "📍 Адрес":
        temp_data[user_id]['order']['step'] = 'edit_address'
        bot.send_message(user_id, "📍 Введите новый адрес:", reply_markup=types.ReplyKeyboardRemove())
    elif choice == "👤 ФИО":
        temp_data[user_id]['order']['step'] = 'edit_full_name'
        bot.send_message(user_id, "👤 Введите новое ФИО:", reply_markup=types.ReplyKeyboardRemove())
    elif choice == "📞 Телефон":
        temp_data[user_id]['order']['step'] = 'edit_phone'
        bot.send_message(user_id, "📞 Введите новый телефон:", reply_markup=types.ReplyKeyboardRemove())
    elif choice == "🚚 Способ доставки":
        temp_data[user_id]['order']['step'] = 'edit_delivery_type'
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("СДЭК"))
        markup.add(types.KeyboardButton("Другое"))
        bot.send_message(user_id, "🚚 Выберите способ доставки:", reply_markup=markup)
    elif choice == "📸 Фото оплаты":
        temp_data[user_id]['order']['step'] = 'edit_payment'
        bot.send_message(user_id, "📸 Отправьте новое фото оплаты:", reply_markup=types.ReplyKeyboardRemove())

def show_order_confirmation(user_id):
    """Показывает подтверждение заказа с кнопками"""
    product_id = temp_data[user_id]['order']['product_id']
    product = db_actions.get_product(product_id)
    
    if product:
        price, currency = get_product_price(product)
        
        order_summary = (
            f"✅ ВСЕ ДАННЫЕ ПОЛУЧЕНЫ!\n\n"
            f"📋 Ваш заказ:\n"
            f"🛍️ Товар: {get_product_name(product)}\n"
            f"📏 Размер: {temp_data[user_id]['order'].get('size', 'Не указан')}\n"
            f"💰 Цена: {price} {currency}\n\n"
            f"📦 Доставка:\n"
            f"🏙️ Город: {temp_data[user_id]['order']['city']}\n"
            f"📍 Адрес: {temp_data[user_id]['order']['address']}\n"
            f"👤 ФИО: {temp_data[user_id]['order']['full_name']}\n"
            f"📞 Телефон: {temp_data[user_id]['order']['phone']}\n"
            f"🚚 Способ: {temp_data[user_id]['order']['delivery_type']}\n\n"
            f"📸 Фото оплаты: {'Приложено ✅' if temp_data[user_id]['order'].get('payment_photo') else 'Не приложено ❌'}\n\n"
            f"Выберите действие:"
        )
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        confirm_btn = types.KeyboardButton("✅ Подтвердить заказ")
        edit_btn = types.KeyboardButton("✏️ Редактировать данные")
        cancel_btn = types.KeyboardButton("❌ Отменить заказ")
        markup.add(confirm_btn, edit_btn, cancel_btn)
        
        bot.send_message(user_id, order_summary, reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_city')
def edit_city(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['city'] = message.text
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ Город обновлен! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_address')
def edit_address(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['address'] = message.text
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ Адрес обновлен! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_full_name')
def edit_full_name(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['full_name'] = message.text
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ ФИО обновлено! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_phone')
def edit_phone(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['phone'] = message.text
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ Телефон обновлен! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_delivery_type')
def edit_delivery_type(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['delivery_type'] = message.text
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ Способ доставки обновлен! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(content_types=['photo'], 
                    func=lambda message: 
                    message.from_user.id in temp_data and 
                    temp_data[message.from_user.id].get('order', {}).get('step') == 'edit_payment')
def edit_payment(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['payment_photo'] = message.photo[-1].file_id
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🏙️ Город"),
        types.KeyboardButton("📍 Адрес"),
        types.KeyboardButton("👤 ФИО"),
        types.KeyboardButton("📞 Телефон"),
        types.KeyboardButton("🚚 Способ доставки"),
        types.KeyboardButton("📸 Фото оплаты"),
        types.KeyboardButton("✅ Все верно")
    )
    
    temp_data[user_id]['order']['step'] = 'edit_choice'
    bot.send_message(user_id, "✅ Фото оплаты обновлено! Что еще хотите отредактировать?", reply_markup=markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'confirm_order' and
    message.text.lower() == '❌ отменить заказ')
def cancel_order(message):
    user_id = message.from_user.id
    
    if user_id in temp_data and 'order' in temp_data[user_id]:
        del temp_data[user_id]['order']
    
    remove_markup = types.ReplyKeyboardRemove()
    
    bot.send_message(
        user_id,
        "❌ Заказ отменен.\n\n"
        "Если передумаете - всегда можете оформить новый заказ!",
        reply_markup=remove_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_review_', 'reject_review_')))
def handle_review_moderation(call):
    try:
        parts = call.data.split('_')
        action = parts[0]
        user_id = int(parts[2])
        
        review_key = None
        review_data = None
        
        for key in list(pending_reviews.keys()):
            if key.startswith(f"{user_id}_"):
                review_key = key
                review_data = pending_reviews[key]
                break
        
        if not review_data:
            bot.answer_callback_query(call.id, "❌ Данные отзыва не найдены или устарели")
            return
            
        if action == 'approve':
            photos_json = json.dumps(review_data.get('photos', [])) if review_data.get('photos') else None
            db_actions.add_review(
                user_id, 
                review_data['text'], 
                photos_json
            )
            
            # Проверяем ачивки для отзыва
            check_achievement_conditions(user_id, 'first_review_with_photo')
            
            publish_review_to_channel(user_id, review_data)
            
            bot.answer_callback_query(call.id, "✅ Отзыв одобрен")
            bot.send_message(
                user_id,
                "🎉 Ваш отзыв одобрен и опубликован в @BridgeSide_Featback!"
            )
            
        else:
            bot.answer_callback_query(call.id, "❌ Отзыв отклонен")
            bot.send_message(
                user_id,
                "❌ Ваш отзыв не прошел модерацию. Пожалуйста, проверьте его и отправьте еще раз."
            )
            
    except Exception as e:
        print(f"Ошибка в модерации: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при обработке")

@bot.callback_query_handler(func=lambda call: call.data == 'start_review')
def start_review(call):
    user_id = call.from_user.id
    
    if user_id not in temp_data:
        temp_data[user_id] = {}
    
    temp_data[user_id]['step'] = 'writing_review'
    temp_data[user_id]['photos'] = []
    
    bot.send_message(
        user_id,
        "📝 Напишите ваш отзыв. Вы можете:\n"
        "• Написать текст отзыва\n"
        "• Прикрепить до 3 фотографий\n"
        "• Отправить /done для завершения\n"
        "• Отправить /cancel для отмены"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_order_'))
def handle_order_rejection(call):
    try:
        admin_id = call.from_user.id
        if not db_actions.user_is_admin(admin_id):
            bot.answer_callback_query(call.id, "⛔️ Недостаточно прав")
            return
            
        order_id = int(call.data.split('_')[2])
        
        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            bot.answer_callback_query(call.id, "❌ Заказ не найден")
            return
            
        db_actions.return_product_quantity(order_id)
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        temp_data[admin_id] = {
            'reject_order': {
                'order_id': order_id,
                'message_id': call.message.message_id,
                'chat_id': call.message.chat.id,
                'is_photo': call.message.photo is not None,
                'topic_id': call.message.message_thread_id
            }
        }
        
        bot.answer_callback_query(
            call.id, 
            "💬 Ответьте в топике на сообщение с заказом текстом причины отклонения", 
            show_alert=True
        )
            
    except Exception as e:
        print(f"Ошибка обработки отклонения заказа: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")


@bot.message_handler(func=lambda message: message.reply_to_message and message.reply_to_message.text and "ЗАКАЗ #" in message.reply_to_message.text)
def handle_topic_reply(message):
    try:
        admin_id = message.from_user.id
        if not db_actions.user_is_admin(admin_id):
            return
            
        replied_message = message.reply_to_message
        replied_text = replied_message.text if replied_message.text else replied_message.caption

        import re
        order_id_match = re.search(r'ЗАКАЗ #(\d+)', replied_text)
        if not order_id_match:
            return
            
        order_id = int(order_id_match.group(1))
        reason = message.text
        
        db_actions.return_product_quantity(order_id)
        
        db_actions.update_order_status(order_id, f"❌ ОТКЛОНЕН: {reason}")
        
        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            return
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        try:
            if replied_message.caption:
                new_caption = replied_message.caption.replace("⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ", f"❌ ОТКЛОНЕН: {reason}")
                bot.edit_message_caption(
                    chat_id=replied_message.chat.id,
                    message_id=replied_message.message_id,
                    caption=new_caption,
                    message_thread_id=replied_message.message_thread_id,
                    reply_markup=None
                )
            else:
                new_text = replied_message.text.replace("⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ", f"❌ ОТКЛОНЕН: {reason}")
                bot.edit_message_text(
                    chat_id=replied_message.chat.id,
                    message_id=replied_message.message_id,
                    text=new_text,
                    message_thread_id=replied_message.message_thread_id,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        try:
            bot.send_message(
                order_info['user_id'],
                f"❌ Ваш заказ #{order_id} отклонен\n\n"
                f"🛍️ Товар: {get_product_name(product) if product else 'Неизвестно'}\n"
                f"💰 Сумма: {get_product_field(product, 'price', 0) if product else '0'}₽\n\n"
                f"📝 Причина: {reason}\n\n"
                f"💬 Если у вас есть вопросы, обратитесь в поддержку."
            )
            
            if product and get_product_field(product, 'is_exclusive'):
                db_actions.update_user_stats(order_info['user_id'], 'bs_coin', get_product_field(product, 'coin_price', 0))
                bot.send_message(
                    order_info['user_id'],
                    f"💎 Вам возвращено {get_product_field(product, 'coin_price', 0)} BS Coin"
                )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass

            
    except Exception as e:
        print(f"Ошибка обработки ответа в топике: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_order_'))
def handle_order_approval(call):
    try:
        admin_id = call.from_user.id
        if not db_actions.user_is_admin(admin_id):
            bot.answer_callback_query(call.id, "⛔️ Недостаточно прав")
            return
            
        order_id = int(call.data.split('_')[2])
        
        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            bot.answer_callback_query(call.id, "❌ Заказ не найден")
            return

        # Убираем особую обработку совпадения user_id и admin_id — обрабатываем заказ как обычный
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        

        db_actions.update_order_status(order_id, "✅ ПОДТВЕРЖДЕН")


        try:
            if call.message.caption:
                new_caption = call.message.caption.replace("⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ", "✅ ПОДТВЕРЖДЕН")
                bot.edit_message_caption(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    caption=new_caption,
                    reply_markup=None
                )
            else:
                new_text = call.message.text.replace("⏳ ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ", "✅ ПОДТВЕРЖДЕН")
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=new_text,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        try:
            bot.send_message(
                order_info['user_id'],
                f"🎉 Ваш заказ #{order_id} подтвержден!\n\n"
                f"🛍️ Товар: {get_product_name(product) if product else 'Неизвестно'}\n"
                f"💰 Сумма: {get_product_field(product, 'price', 0) if product else '0'}₽\n\n"
                f"📦 Заказ передан в обработку. Ожидайте информацию о доставке."
            )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        bot.answer_callback_query(call.id, "✅ Заказ подтвержден")
        
    except Exception as e:
        print(f"Ошибка обработки подтверждения заказа: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_reason_'))
def ask_reject_reason(call):
    try:
        order_id = int(call.data.split('_')[2])
        
        bot.answer_callback_query(
            call.id, 
            "💬 Ответьте на это сообщение текстом с причиной отклонения", 
            show_alert=True
        )
        
    except Exception as e:
        print(f"Ошибка запроса причины: {e}")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    'reject_reason' in temp_data[message.from_user.id] and
    message.chat.id == temp_data[message.from_user.id]['reject_reason']['chat_id'])
def process_reject_reason_in_topic(message):
    try:
        admin_id = message.from_user.id
        reason_data = temp_data[admin_id]['reject_reason']
        order_id = reason_data['order_id']
        reason = message.text
        
        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            print(f"Заказ {order_id} не найден")
            return
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        if not user_data or not product:
            log_error(logger, "Данные пользователя или товара не найдены")
            return
        

        db_actions.update_order_status(order_id, f"❌ ОТКЛОНЕН: {reason}")
        

        updated_text = (
            f"🛒 ЗАКАЗ #{order_id} ❌ ОТКЛОНЕН\n\n"
            f"👤 Клиент: {user_data['first_name']} {user_data['last_name']}\n"
            f"🔗 @{user_data['username']}\n"
            f"🛍️ Товар: {get_product_name(product)}\n"
            f"💰 Цена: {get_product_field(product, 'price', 0)}₽\n\n"
            f"📦 ДАННЫЕ ДОСТАВКИ:\n"
            f"🏙️ Город: {order_info.get('city', 'Не указан')}\n"
            f"📍 Адрес: {order_info.get('address', 'Не указан')}\n"
            f"👤 ФИО: {order_info.get('full_name', 'Не указан')}\n"
            f"📞 Телефон: {order_info.get('phone', 'Не указан')}\n"
            f"🚚 Способ: {order_info.get('delivery_type', 'Не указан')}\n\n"
            f"📝 Причина отклонения: {reason}\n"
            f"👨‍💼 Отклонил: @{message.from_user.username}\n"
            f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        try:
            if reason_data['is_photo']:
                bot.edit_message_caption(
                    chat_id=reason_data['chat_id'],
                    message_id=reason_data['message_id'],
                    caption=updated_text,
                    reply_markup=None
                )
            else:
                bot.edit_message_text(
                    chat_id=reason_data['chat_id'],
                    message_id=reason_data['message_id'],
                    text=updated_text,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка редактирования сообщения: {e}")
        
        try:
            bot.send_message(
                order_info['user_id'],
                f"❌ Ваш заказ #{order_id} отклонен\n\n"
                f"🛍️ Товар: {get_product_name(product)}\n"
                f"💰 Сумма: {get_product_field(product, 'price', 0)}₽\n\n"
                f"📝 Причина: {reason}\n\n"
                f"💬 Если у вас есть вопросы, обратитесь в поддержку."
            )
            
            if get_product_field(product, 'is_exclusive'):  # is_exclusive
                db_actions.update_user_stats(order_info['user_id'], 'bs_coin', get_product_field(product, 'coin_price', 0))
                bot.send_message(
                    order_info['user_id'],
                    f"💎 Вам возвращено {get_product_field(product, 'coin_price', 0)} BS Coin"
                )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        if admin_id in temp_data and 'reject_reason' in temp_data[admin_id]:
            del temp_data[admin_id]['reject_reason']
        
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
            
    except Exception as e:
        print(f"Ошибка обработки причины отклонения: {e}")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_delivery_type' and
    message.text == "Другое")
def handle_other_delivery(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['step'] = 'ask_custom_delivery'
    bot.send_message(user_id, "🚚 Укажите ваш вариант доставки:")

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_custom_delivery')
def process_custom_delivery(message):
    user_id = message.from_user.id
    temp_data[user_id]['order']['delivery_type'] = message.text
    temp_data[user_id]['order']['step'] = 'ask_payment'
    

    remove_markup = types.ReplyKeyboardRemove()
    

    product_id = temp_data[user_id]['order']['product_id']
    product = db_actions.get_product(product_id)
    
    if product:
        price, currency = get_product_price(product)
        

        order_summary = (
            f"✅ Данные доставки получены!\n\n"
            f"📋 Ваш заказ:\n"
            f"🛍️ Товар: {get_product_name(product)}\n"
            f"📏 Размер: {temp_data[user_id]['order'].get('size', 'Не указан')}\n"
            f"💰 Цена: {price} {currency}\n\n"
            f"📦 Доставка:\n"
            f"🏙️ Город: {temp_data[user_id]['order']['city']}\n"
            f"📍 Адрес: {temp_data[user_id]['order']['address']}\n"
            f"👤 ФИО: {temp_data[user_id]['order']['full_name']}\n"
            f"📞 Телефон: {temp_data[user_id]['order']['phone']}\n"
            f"🚚 Способ: {temp_data[user_id]['order']['delivery_type']}\n\n"
            f"💳 Теперь отправьте скриншот чека об оплате"
        )
        
        bot.send_message(user_id, order_summary, reply_markup=remove_markup)

@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_delivery')
def process_delivery_info(message):
    user_id = message.from_user.id
    
    try:

        temp_data[user_id]['order']['delivery_info'] = message.text
        temp_data[user_id]['order']['step'] = 'ask_payment'
        

        payment_request = (
            "✅ Данные доставки получены!\n\n"
            "Теперь отправьте скриншот чека об оплате\n\n"
            "💳 После оплаты сделайте скриншот и отправьте его сюда\n\n"
            "РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            "2200154531899085 \nАльфа-Банк\n\n"
            "5280413753453047\nТ-банк\n\n"
            "5228600520272271\nСБЕР\n\n"
            "8-903-191-98-48 \nСПБ - Яна Ж."
        )
        
        bot.send_message(user_id, payment_request)
        
    except Exception as e:
        print(f"Ошибка обработки доставки: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки данных")



@bot.message_handler(func=lambda message: 
    message.from_user.id in temp_data and 
    'reject_order' in temp_data[message.from_user.id])
def handle_reject_reason(message):
    try:
        admin_id = message.from_user.id
        order_data = temp_data[admin_id]['reject_order']
        order_id = order_data['order_id']
        reason = message.text
        

        db_actions.return_product_quantity(order_id)
        

        db_actions.update_order_status(order_id, f"❌ ОТКЛОНЕН: {reason}")
        

        order_info = db_actions.get_order_by_id(order_id)
        if not order_info:
            bot.send_message(admin_id, "❌ Заказ не найден")
            return
            
        user_data = db_actions.get_user_data(order_info['user_id'])
        product = db_actions.get_product(order_info['product_id'])
        
        try:
            if order_data['is_photo']:
                bot.edit_message_caption(
                    chat_id=order_data['chat_id'],
                    message_id=order_data['message_id'],
                    caption=f"❌ ЗАКАЗ ОТКЛОНЕН: {reason}",
                    reply_markup=None
                )
            else:
                bot.edit_message_text(
                    chat_id=order_data['chat_id'],
                    message_id=order_data['message_id'],
                    text=f"❌ ЗАКАЗ ОТКЛОНЕН: {reason}",
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        try:

            user_id_from_order = order_info['user_id']
            bot.send_message(
                user_id_from_order,
                f"❌ Ваш заказ #{order_id} отклонен\n\n"
                f"🛍️ Товар: {get_product_name(product) if product else 'Неизвестно'}\n"
                f"💰 Сумма: {get_product_field(product, 'price', 0) if product else '0'}₽\n\n"
                f"📝 Причина: {reason}\n\n"
                f"💬 Если у вас есть вопросы, обратитесь в поддержку."
            )
            

            if product and get_product_field(product, 'is_exclusive'):
                db_actions.update_user_stats(user_id_from_order, 'bs_coin', get_product_field(product, 'coin_price', 0))
                bot.send_message(
                    user_id_from_order,
                    f"💎 Вам возвращено {get_product_field(product, 'coin_price', 0)} BS Coin"
                )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        bot.send_message(admin_id, "✅ Заказ отклонен, пользователь уведомлен")
        

        del temp_data[admin_id]['reject_order']
        
    except Exception as e:
        print(f"Ошибка обработки причины отклонения: {e}")
        bot.send_message(admin_id, "❌ Ошибка обработки")



@bot.message_handler(content_types=['photo'], 
                    func=lambda message: 
                    message.from_user.id in temp_data and 
                    temp_data[message.from_user.id].get('order', {}).get('step') == 'ask_payment')
def process_payment_photo(message):
    user_id = message.from_user.id
    
    try:

        payment_photo_id = message.photo[-1].file_id
        temp_data[user_id]['order']['payment_photo'] = payment_photo_id
        temp_data[user_id]['order']['step'] = 'confirm_order'

        product_id = temp_data[user_id]['order']['product_id']
        product = db_actions.get_product(product_id)
        
        if product:
            order_summary = (
                f"✅ ВСЕ ДАННЫЕ ПОЛУЧЕНЫ!\n\n"
                f"📋 Ваш заказ:\n"
                f"🛍️ Товар: {get_product_name(product)}\n"
                f"📏 Размер: {temp_data[user_id]['order'].get('size', 'Не указан')}\n"
                f"💰 Цена: {get_product_field(product, 'price', 0)}₽\n\n"
                f"📦 Доставка:\n{temp_data[user_id]['order']['delivery_info']}\n\n"
                f"📸 Фото оплаты приложено\n\n"
                f"Выберите действие:"
            )
            

            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            confirm_btn = types.KeyboardButton("✅ Подтвердить заказ")
            edit_btn = types.KeyboardButton("✏️ Редактировать данные")
            cancel_btn = types.KeyboardButton("❌ Отменить заказ")
            markup.add(confirm_btn, edit_btn, cancel_btn)
            

            bot.send_photo(user_id, payment_photo_id, caption="📸 Ваше фото оплаты:")
            bot.send_message(user_id, order_summary, reply_markup=markup)
        
    except Exception as e:
        print(f"Ошибка обработки фото: {e}")
        bot.send_message(user_id, "❌ Ошибка обработки фото")


@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_review_', 'reject_review_')))
def handle_review_moderation(call):
    try:
        parts = call.data.split('_')
        action = parts[0]
        user_id = int(parts[2])
        
        review_key = None
        review_data = None
        
        for key in list(pending_reviews.keys()):
            if key.startswith(f"{user_id}_"):
                review_key = key
                review_data = pending_reviews[key]
                break
        
        if not review_data:
            bot.answer_callback_query(call.id, "❌ Данные отзыва не найдены или устарели")
            return
            
        if action == 'approve':
            photos_json = json.dumps(review_data.get('photos', [])) if review_data.get('photos') else None
            db_actions.add_review(
                user_id, 
                review_data['text'], 
                photos_json
            )
            
            # Проверяем ачивки для отзыва
            check_achievement_conditions(user_id, 'first_review_with_photo')
            
            publish_review_to_channel(user_id, review_data)
            
            bot.answer_callback_query(call.id, "✅ Отзыв одобрен")
            bot.send_message(
                user_id,
                "🎉 Ваш отзыв одобрен и опубликован в @BridgeSide_Featback!"
            )
            
        else:
            bot.answer_callback_query(call.id, "❌ Отзыв отклонен")
            bot.send_message(
                user_id,
                "❌ Ваш отзыв не прошел модерацию. Пожалуйста, проверьте его и отправьте еще раз."
            )
            
    except Exception as e:
        print(f"Ошибка в модерации: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при обработке")

@bot.callback_query_handler(func=lambda call: call.data == 'start_review')
def start_review(call):
    user_id = call.from_user.id
    
    if user_id not in temp_data:
        temp_data[user_id] = {}
    
    temp_data[user_id]['step'] = 'writing_review'
    temp_data[user_id]['photos'] = []
    
    bot.send_message(
        user_id,
        "📝 Напишите ваш отзыв. Вы можете:\n"
        "• Написать текст отзыва\n"
        "• Прикрепить до 3 фотографий\n"
        "• Отправить /done для завершения\n"
        "• Отправить /cancel для отмены"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_size_'))
def select_size(call):
    user_id = call.from_user.id
    product_id = int(call.data.split('_')[2])
    
    product = db_actions.get_product_with_variations(product_id)
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return
        
    buttons = Bot_inline_btns()
    markup = buttons.size_selection_buttons(product['variations'])
    
    bot.edit_message_reply_markup(
        chat_id=user_id,
        message_id=call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith(('size_', 'size_coin_')))
def handle_size_selection(call):
    user_id = call.from_user.id
    try:
        parts = call.data.split('_')
        
        is_exclusive = parts[0] == 'size_coin'
        
        if is_exclusive:
            product_id = int(parts[2])
            size = parts[3]
        else:
            product_id = int(parts[1])
            size = parts[2]
        
        print(f"DEBUG: Выбран размер - product_id: {product_id}, size: '{size}', exclusive: {is_exclusive}")
        
        if not db_actions.check_size_availability(product_id, size):
            bot.answer_callback_query(call.id, "❌ Этот размер недоступен")
            return
        
        if user_id not in temp_data:
            temp_data[user_id] = {}
        
        temp_data[user_id]['selected_product'] = product_id
        temp_data[user_id]['selected_size'] = size
        temp_data[user_id]['is_exclusive'] = is_exclusive
        
        product = db_actions.get_product(product_id)
        if not product:
            bot.answer_callback_query(call.id, "❌ Товар не найден")
            return
        
        markup = types.InlineKeyboardMarkup()
        
        if is_exclusive:
            buy_btn = types.InlineKeyboardButton(
                text=f"💎 Купить за {get_product_field(product, 'coin_price', 0)} BS Coin",
                callback_data=f"buy_coin_{product_id}_{size}"
            )
            markup.add(buy_btn)
        else:
            order_btn = types.InlineKeyboardButton(
                text="🛒 Заказать сейчас",
                callback_data=f"order_{product_id}_{size}"
            )
            markup.add(order_btn)
        
        try:
            if call.message.caption:
                bot.edit_message_caption(
                    chat_id=user_id,
                    message_id=call.message.message_id,
                    caption=call.message.caption,
                    reply_markup=markup
                )
            else:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=call.message.message_id,
                    text=call.message.text,
                    reply_markup=markup
                )
            
            bot.answer_callback_query(call.id, f"✅ Выбран размер: {size}")
            
        except Exception as e:
            print(f"Ошибка редактирования: {e}")
            bot.answer_callback_query(call.id, "❌ Ошибка выбора размера")
                
    except Exception as e:
        print(f"Ошибка в handle_size_selection: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка выбора размера")


# ============ ОБРАБОТЧИКИ СООБЩЕНИЙ ============

@bot.message_handler(content_types=['text', 'photo'])
def handle_messages(message):
    user_id = message.from_user.id
    
    if user_id in temp_data and temp_data[user_id].get('step') in ['add_photos', 'add_text']:
        handle_post_creation(message)
        return
        
    if user_id in temp_data and temp_data[user_id].get('step') == 'writing_review':
        handle_review(message)
        return
        

def handle_post_creation(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        if user_id in temp_data:
            del temp_data[user_id]
        bot.send_message(user_id, "❌ Создание поста отменено")
        return
        
    if temp_data[user_id]['step'] == 'add_photos':
        if message.content_type == 'photo':
            if len(temp_data[user_id]['photos']) < 6:
                temp_data[user_id]['photos'].append(message.photo[-1].file_id)
                remaining = 6 - len(temp_data[user_id]['photos'])
                bot.send_message(user_id, f"📸 Фото добавлено. Осталось: {remaining}")
            else:
                bot.send_message(user_id, "❌ Максимум 6 фотографий. Отправьте текст поста")
        elif message.content_type == 'text':
            temp_data[user_id]['step'] = 'add_text'
            temp_data[user_id]['text'] = message.text
            ask_exclusive_status(user_id)
            
    elif temp_data[user_id]['step'] == 'add_text':
        temp_data[user_id]['text'] = message.text
        ask_exclusive_status(user_id)

def handle_review(message):
    user_id = message.from_user.id
    
    review_data = temp_data[user_id]
    
    if message.content_type == 'photo':
        if 'photos' not in review_data:
            review_data['photos'] = []
            
        if len(review_data['photos']) < 3:
            review_data['photos'].append(message.photo[-1].file_id)
            remaining = 3 - len(review_data['photos'])
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Завершить", callback_data="review_done"))
            bot.send_message(user_id, f"📸 Фото добавлено. Можно добавить еще {remaining} фото или нажмите 'Завершить' для отправки", reply_markup=markup)
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Завершить", callback_data="review_done"))
            bot.send_message(user_id, "❌ Можно прикрепить не более 3 фотографий. Нажмите 'Завершить' для отправки", reply_markup=markup)
            
    elif message.content_type == 'text':
        text = message.text.strip()
        
        if text.lower() == '/done':
            if 'text' not in review_data or not review_data['text']:
                bot.send_message(user_id, "❌ Сначала напишите текст отзыва")
                return
                
            send_review_for_moderation(user_id, review_data)
            
            if user_id in temp_data:
                del temp_data[user_id]
                
            bot.send_message(user_id, "✅ Отзыв отправлен на модерацию! Ожидайте решения администратора.")
            
        elif text.lower() == '/cancel':
            if user_id in temp_data:
                del temp_data[user_id]
            bot.send_message(user_id, "❌ Создание отзыва отменено")
            
        else:
            review_data['text'] = text
            photos_count = len(review_data.get('photos', []))
            remaining_photos = 3 - photos_count
            
            if photos_count > 0:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("✅ Завершить", callback_data="review_done"))
                bot.send_message(
                    user_id, 
                    f"✅ Текст отзыва сохранен. Прикреплено фото: {photos_count}/3. "
                    f"Можете добавить еще {remaining_photos} фото или нажмите 'Завершить' для отправки",
                    reply_markup=markup
                )
            else:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("✅ Завершить", callback_data="review_done"))
                bot.send_message(
                    user_id, 
                    f"✅ Текст отзыва сохранен. Можете прикрепить до {remaining_photos} фото или нажмите 'Завершить' для отправки",
                    reply_markup=markup
                )

@bot.callback_query_handler(func=lambda call: call.data == 'review_done')
def handle_review_done(call):
    try:
        user_id = call.from_user.id
        if temp_data.get(user_id, {}).get('step') != 'writing_review':
            bot.answer_callback_query(call.id, "Нет активного отзыва")
            return
        review_data = temp_data.get(user_id) or {}
        # Проверяем, что есть текст
        if not review_data.get('text'):
            bot.answer_callback_query(call.id, "Сначала напишите текст отзыва")
            bot.send_message(user_id, "❌ Сначала напишите текст отзыва")
            return
        # Отправляем на модерацию
        send_review_for_moderation(user_id, review_data)
        if user_id in temp_data:
            del temp_data[user_id]
        bot.answer_callback_query(call.id, "Отправлено на модерацию")
        bot.send_message(user_id, "✅ Отзыв отправлен на модерацию! Ожидайте решения администратора.")
    except Exception as e:
        log_error(logger, e, "Ошибка завершения отзыва")
        try:
            bot.answer_callback_query(call.id, "Ошибка")
        except Exception:
            pass


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

# ============ ЗАПУСК БОТА ============

if __name__ == '__main__':
    log_info(logger, "Бот запущен...")
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        log_error(logger, e, "Ошибка при запуске бота")
        traceback.print_exc()