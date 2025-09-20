import copy
import json
import os
import sys
import time



class ConfigParser:
    def __init__(self, file_path, os_type):
        super(ConfigParser, self).__init__()
        self.__file_path = file_path
        self.__default_pathes = {'Windows': 'C:\\', 'Linux': '/'}
        self.__default = {
            'tg_api': '', 
            'admins': [], 
            'db_file_name': 'db.sqlite3', 
            "xlsx_path": "report.xlsx",
            'mysql': {
                'host': '127.0.0.1',
                'user': 'root',
                'password': '12345678',
                'database': 'bridgeside_bot',
                'port': 8000
            },
            'yadisk': {
                'client_id': '',
                'client_secret': '',
                'access_token': '',
                'refresh_token': '',
                'expires_at': 0
            }
        }
        self.__current_config = None
        self.load_conf()

    def load_conf(self):
        if os.path.exists(self.__file_path):
            with open(self.__file_path, 'r', encoding='utf-8') as file:
                self.__current_config = json.loads(file.read())
            if len(self.__current_config['tg_api']) == 0:
                sys.exit('config is invalid')
        else:
            self.create_conf(self.__default)
            sys.exit('config is not existed')

    def create_conf(self, config):
        with open(self.__file_path, 'w', encoding='utf-8') as file:
            file.write(json.dumps(config, sort_keys=True, indent=4))

    def get_config(self):
        return self.__current_config
    
    def save_config(self):
        """Сохранить текущую конфигурацию в файл"""
        with open(self.__file_path, 'w', encoding='utf-8') as file:
            file.write(json.dumps(self.__current_config, sort_keys=True, indent=4))
    
    def update_yadisk_tokens(self, access_token, refresh_token, expires_in):
        """Обновить токены Яндекс.Диска в конфигурации"""
        if 'yadisk' not in self.__current_config:
            self.__current_config['yadisk'] = {}
        
        self.__current_config['yadisk']['access_token'] = access_token
        self.__current_config['yadisk']['refresh_token'] = refresh_token
        self.__current_config['yadisk']['expires_at'] = int(time.time()) + expires_in
        
        self.save_config()