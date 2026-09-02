import json
import os
import logging
from pathlib import Path

import aiofiles

import app.db.mysql.queries.opensips_sql as queries
from app.db.mysql.connection.db_connector import db_connector
from app.core.configs import mysql_config as db_config
from app.core import constants

logger = logging.getLogger(__name__)


class OpenSIPsDBHandler:

    # =====================================================
    # Load system settings (DB → File)
    # =====================================================

    async def load_ai_system_settings_into_memory(
        self,
        file_name,
        feature_name,
    ):
        try:
            logger.info(
                "============= collecting ai system settings from db ========"
            )
            query = queries.DB_SELECT_AI_SYSTEM_SETTINGS
            params = {"feature_name": feature_name}
            data = await db_connector.execute(
                db_config.DB_OPENSIPS, query, params
            )

            if not data:
                raise Exception(
                    f"No data available for feature: {feature_name}"
                )

            json_obj = json.loads(data[0].get("configuration", "{}"))
            logger.info(
                f"ai system settings for '{params}' :: \n{json_obj}"
            )

            file_path = os.path.join(self.cache_path, file_name)
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(json_obj, indent=4, ensure_ascii=False))

            logger.info(
                f"updated ai system settings file path :: {file_path}"
            )

        except Exception as e:
            logger.error(f"Error :: {e}")
            raise

    # =====================================================
    # Get chat summary
    # =====================================================

    async def get_chat_summary_from_db(
        self, start_date, end_date, request_data
    ):
        try:
            logger.info(
                "============= fetching chat summary ======== "
                f"sid :: {request_data.get('sid')}"
            )
            query = queries.DB_SELECT_CHAT_SUMMARY
            params = {
                "sid": request_data.get("sid"),
                "start_date": start_date,
                "end_date": end_date,
            }
            data = await db_connector.execute(
                db_config.DB_OPENSIPS, query, params
            )
            logger.info(f"chat summary fetched :: {data}")
            return data if data else []
        except Exception as e:
            logger.error(
                f"Error :: {e}, sid :: {request_data.get('sid')}"
            )
            return []

    # =====================================================
    # Insert chat summary
    # =====================================================

    async def insert_chat_summary_into_db(
        self, summary, start_date, end_date, request_data
    ):
        try:
            logger.info(
                "============= inserting chat summary ======== "
                f"agentid :: {request_data.get('agentid')}"
            )
            query = queries.DB_INSERT_CHAT_SUMMARY
            params = {
                "sid": request_data.get("sid"),
                "start_date": start_date,
                "end_date": end_date,
                "summary": summary,
                "extra_data": json.dumps({"agentid": request_data.get("agentid")}),
            }
            data = await db_connector.execute_statement(
                db_config.DB_OPENSIPS, query, params
            )
            logger.info(f"chat summary inserted :: {data}")
        except Exception as e:
            logger.error(f"Error :: {e}")
            raise

    # =====================================================
    # Update chat summary
    # =====================================================

    async def update_chat_summary_into_db(
        self, summary, start_date, end_date, request_data
    ):
        try:
            logger.info(
                "============= updating chat summary ======== "
                f"agentid :: {request_data.get('agentid')}"
            )
            query = queries.DB_UPDATE_CHAT_SUMMARY
            params = {
                "summary": summary,
                "sid": request_data.get("sid"),
                "start_date": start_date,
                "end_date": end_date,
            }
            data = await db_connector.execute_statement(
                db_config.DB_OPENSIPS, query, params
            )
            logger.info(f"chat summary updated :: {data}")
        except Exception as e:
            logger.error(f"Error :: {e}")
            raise


opensips_db_handler = OpenSIPsDBHandler()
