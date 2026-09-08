import json
import logging

import app.db.mysql.queries.opensips_sql as queries
from app.db.mysql.connection.db_connector import db_connector
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class OpenSIPsDBHandler:

    # =====================================================
    # Get AI system settings (pure DB read — no file I/O)
    # =====================================================
    # Previously named `load_ai_system_settings_into_memory`, this method also wrote
    # the result to a local JSON cache file, mixing filesystem concerns into a
    # database repository. That file-cache behavior now lives in
    # `app.ai.settings_cache.AISettingsCache`, which calls this method for its DB read.

    async def get_ai_system_settings(self, feature_name) -> dict:
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

            return json_obj

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
