import json
import logging
from datetime import datetime

from app.ai import ai_constants
import app.db.mysql.queries.opensips_sql as queries
from app.db.mysql.connection.db_connector import db_connector
from app.core.configs import mysql_config as db_config
from app.core.utils import utils

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
        self, start_date, end_date, request_data, source_type: str = ai_constants.OPERATION_CHAT_SUMMARY, ref_id: int | None = None
    ):
        try:
            target_ref_id = ref_id if ref_id is not None else int(
                request_data.get("smsgid") if source_type == ai_constants.OPERATION_PROCESS_THREAD else (request_data.get("sid") or 0)
            )
            logger.info(
                f"============= fetching {source_type} summary ======== "
                f"ref_id :: {target_ref_id}"
            )
            if not start_date or not end_date:
                raise Exception(
                    "Missing 'start_date' or 'end_date' for duration chat lookup"
                )
            user_timezone = request_data.get("timezone")
            utc_start = utils.convert_to_utc(start_date, user_timezone)
            utc_end = utils.convert_to_utc(end_date, user_timezone)

            query = queries.DB_SELECT_CHAT_SUMMARY
            params = {
                "source_type": source_type,
                "ref_id": target_ref_id,
                "start_date": utc_start,
                "end_date": utc_end,
            }
            data = await db_connector.execute(
                db_config.DB_LOCAL, query, params
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
        self, summary, start_date, end_date, request_data, extra_data: dict | None = None, source_type: str = ai_constants.OPERATION_CHAT_SUMMARY, ref_id: int | None = None
    ):
        try:
            logger.info(
                f"============= inserting {source_type} summary ======== "
                f"agentid :: {request_data.get('agentid')}"
            )
            if not start_date or not end_date:
                raise Exception(
                    "Missing 'start_date' or 'end_date' for duration chat lookup"
                )
            user_timezone = request_data.get("timezone")
            utc_start = utils.convert_to_utc(start_date, user_timezone)
            utc_end = utils.convert_to_utc(end_date, user_timezone)

            merged_extra = {"agentid": request_data.get("agentid")}
            if extra_data:
                merged_extra.update(extra_data)

            archive_id = int(request_data.get("archiveid") or request_data.get("archive_id") or 0)
            site_id = int(request_data.get("siteid") or request_data.get("site_id") or 0)
            target_ref_id = ref_id if ref_id is not None else int(
                request_data.get("smsgid") if source_type == ai_constants.OPERATION_PROCESS_THREAD else (request_data.get("sid") or 0)
            )

            query = queries.DB_INSERT_CHAT_SUMMARY
            params = {
                "archive_id": archive_id,
                "source_type": source_type,
                "ref_id": target_ref_id,
                "site_id": site_id,
                "start_date": utc_start,
                "end_date": utc_end,
                "summary": summary,
                "extra_data": json.dumps(merged_extra),
            }
            data = await db_connector.execute_statement(
                db_config.DB_LOCAL, query, params
            )
            logger.info(f"chat summary inserted :: {data}")
        except Exception as e:
            if "Duplicate entry" in str(e):
                logger.warning(f"Duplicate entry on insert, falling back to update: {e}")
                return await self.update_chat_summary_into_db(
                    summary, start_date, end_date, request_data, extra_data=extra_data, source_type=source_type, ref_id=ref_id
                )
            logger.error(f"Error :: {e}")
            raise

    # =====================================================
    # Update chat summary
    # =====================================================

    async def update_chat_summary_into_db(
        self, summary, start_date, end_date, request_data, extra_data: dict | None = None, source_type: str = ai_constants.OPERATION_CHAT_SUMMARY, ref_id: int | None = None
    ):
        try:
            logger.info(
                f"============= updating {source_type} summary ======== "
                f"agentid :: {request_data.get('agentid')}"
            )
            if not start_date or not end_date:
                raise Exception(
                    "Missing 'start_date' or 'end_date' for duration chat lookup"
                )
            user_timezone = request_data.get("timezone")
            utc_start = utils.convert_to_utc(start_date, user_timezone)
            utc_end = utils.convert_to_utc(end_date, user_timezone)

            merged_extra = {"agentid": request_data.get("agentid")}
            if extra_data:
                merged_extra.update(extra_data)

            target_ref_id = ref_id if ref_id is not None else int(
                request_data.get("smsgid") if source_type == ai_constants.OPERATION_PROCESS_THREAD else (request_data.get("sid") or 0)
            )

            query = queries.DB_UPDATE_CHAT_SUMMARY
            params = {
                "source_type": source_type,
                "ref_id": target_ref_id,
                "start_date": utc_start,
                "end_date": utc_end,
                "summary": summary,
                "extra_data": json.dumps(merged_extra),
            }
            data = await db_connector.execute_statement(
                db_config.DB_LOCAL, query, params
            )
            logger.info(f"chat summary updated :: {data}")
        except Exception as e:
            logger.error(f"Error :: {e}")
            raise

    async def update_chat_summary_by_id(
        self, summary_id: int, summary: str, end_date: datetime | str, request_data: dict, extra_data: dict | None = None
    ):
        """Updates an existing summary row directly by primary key id, extending its end_date and summary text."""
        try:
            logger.info(
                f"============= updating summary by id {summary_id} ======== "
                f"agentid :: {request_data.get('agentid')}"
            )
            user_timezone = request_data.get("timezone")
            utc_end = utils.convert_to_utc(end_date, user_timezone)

            merged_extra = {"agentid": request_data.get("agentid")}
            if extra_data:
                merged_extra.update(extra_data)

            query = queries.DB_UPDATE_CHAT_SUMMARY_BY_ID
            params = {
                "id": summary_id,
                "summary": summary,
                "end_date": utc_end,
                "extra_data": json.dumps(merged_extra),
            }
            data = await db_connector.execute_statement(
                db_config.DB_LOCAL, query, params
            )
            logger.info(f"chat summary updated by id :: {data}")
            return data
        except Exception as e:
            logger.error(f"Error updating summary by id :: {e}")
            raise



opensips_db_handler = OpenSIPsDBHandler()
