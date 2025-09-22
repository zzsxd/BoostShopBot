import logging
import os
from datetime import datetime

def setup_logging():
    """Настройка системы логирования"""
    
    # Создаем директорию для логов, если её нет
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Настройка форматирования
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Настройка корневого логгера
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Очищаем существующие обработчики
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Обработчик для файла ошибок
    error_handler = logging.FileHandler(
        os.path.join(log_dir, f"errors_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    
    # Обработчик для файла общих логов
    info_handler = logging.FileHandler(
        os.path.join(log_dir, f"bot_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding='utf-8'
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)
    
    # Обработчик для консоли (только ошибки)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчики
    root_logger.addHandler(error_handler)
    root_logger.addHandler(info_handler)
    root_logger.addHandler(console_handler)
    
    # Настройка логгеров для конкретных модулей
    setup_module_loggers()
    
    return root_logger

def setup_module_loggers():
    """Настройка логгеров для конкретных модулей"""
    
    # Логгер для базы данных
    db_logger = logging.getLogger('db')
    db_logger.setLevel(logging.DEBUG)
    
    # Логгер для бэкенда
    backend_logger = logging.getLogger('backend')
    backend_logger.setLevel(logging.DEBUG)
    
    # Логгер для основного бота
    bot_logger = logging.getLogger('bot')
    bot_logger.setLevel(logging.DEBUG)
    
    # Логгер для Telegram API
    telebot_logger = logging.getLogger('telebot')
    telebot_logger.setLevel(logging.WARNING)  # Уменьшаем количество логов от telebot

def get_logger(name):
    """Получить логгер для конкретного модуля"""
    return logging.getLogger(name)

def log_error(logger, error, context=""):
    """Удобная функция для логирования ошибок"""
    if context:
        logger.error(f"{context}: {error}", exc_info=True)
    else:
        logger.error(f"Ошибка: {error}", exc_info=True)

def log_info(logger, message, context=""):
    """Удобная функция для логирования информации"""
    if context:
        logger.info(f"{context}: {message}")
    else:
        logger.info(message)
