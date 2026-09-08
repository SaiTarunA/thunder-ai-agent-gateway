"""AI system settings file cache.

Moved out of `db/mysql/repositories/opensips_repo.py`, which used to do a DB read
*and* write a local JSON cache file in the same repository method — a repository
mixing filesystem I/O with data access. The DB read now lives purely in
`opensips_repo.OpenSIPsDBHandler.get_ai_system_settings`; this module owns the
file-cache behavior around it.
"""

import json
import logging
import os
from pathlib import Path

import aiofiles

from app.db.mysql.repositories.opensips_repo import opensips_db_handler

logger = logging.getLogger(__name__)

CACHE_FOLDER_PATH = "cache/"


class AISettingsCache:

    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.cache_path = Path().resolve() / CACHE_FOLDER_PATH
        return cls._instance

    async def load(self, file_name: str, feature_name: str) -> dict:
        try:
            file_path = os.path.join(self.cache_path, file_name)

            if not os.path.exists(file_path):
                configuration = await opensips_db_handler.get_ai_system_settings(feature_name)
                self.cache_path.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(configuration, indent=4, ensure_ascii=False))
                logger.info(f"updated ai system settings file path :: {file_path}")

            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            return json.loads(content)

        except Exception as e:
            logger.error(f"Error :: {e}")
            return {}


ai_settings_cache = AISettingsCache()
