import logging
from typing import Optional

from app.db.mysql.connection.db_connector import db_connector
import app.db.mysql.queries.streams_sql as queries
from app.core.configs import mysql_config as db_config
from app.core.utils import utils

logger = logging.getLogger(__name__)


class StreamsDBHandler:

    # Note: billing-insert used to live here as `insert_openai_billing_data`.
    # It moved to `app.billing.repository.BillingRepository.insert_billing_data` —
    # billing isn't a Streams-domain query, and keeping it here meant this repository
    # carried OpenAI-specific naming even before this restructure.

    # =====================================================
    # Get streams user chat
    # =====================================================

    async def get_streams_user_chat(
        self,
        request_data,
        start_date=None,
        end_date=None,
        message_count=None,
        unread_messages=False,
    ):
        try:
            sid = request_data.get("sid")
            if not sid:
                logger.error(
                    f"Missing 'sid' in request_data, agentid :: {request_data.get('agentid')}"
                )
                return []

            params = {"param_sid": sid}

            if message_count is not None:
                params["param_message_count"] = int(message_count)
                query = queries.DB_GET_STREAMS_USER_CHAT_BY_MESSAGE_COUNT

            elif unread_messages:
                archiveid = request_data.get("archiveid")
                if not archiveid:
                    logger.error(
                        f"Missing 'archiveid' in request_data for unread messages, agentid :: {request_data.get('agentid')}"
                    )
                    return []
                params["param_archiveid"] = archiveid
                query = queries.DB_GET_STREAMS_USER_CHAT_BY_UNREAD_MESSAGES

            else:
                if not start_date or not end_date:
                    logger.error(
                        f"Missing 'start_date' or 'end_date' for duration chat lookup, agentid :: {request_data.get('agentid')}"
                    )
                    return []
                user_timezone = request_data.get("timezone")
                utc_start = utils.convert_to_utc(start_date, user_timezone)
                utc_end = utils.convert_to_utc(end_date, user_timezone)
                params["param_start_date"] = utc_start
                params["param_end_date"] = utc_end
                query = queries.DB_GET_STREAMS_USER_CHAT_BY_DURATION

            data = await db_connector.execute(
                db_config.DB_STREAMS,
                query,
                params,
            )

            logger.info(
                f"streams chat data count :: {len(data) if data else 0}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return data if data else []

        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            return []

    async def get_streams_parent_messages(self, request_data):
        try:
            sid = request_data.get("sid")
            smsgid = request_data.get("smsgid")
            if not sid or not smsgid:
                logger.error(
                    f"Missing 'sid' or 'smsgid' in request_data, agentid :: {request_data.get('agentid')}"
                )
                return []

            params = {
                "param_sid": sid,
                "param_smsgid": smsgid,
            }
            data = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_STREAMS_PARENT_MESSAGES,
                params,
            )
            logger.info(
                f"streams parent messages data :: {data}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return data[0] if data and len(data) > 0 else {}
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            return {}

    async def get_streams_thread_messages(self, request_data, message_count: Optional[int] = None):
        try:
            smsgid = request_data.get("smsgid")
            if not smsgid:
                logger.error(
                    f"Missing 'smsgid' in request_data, agentid :: {request_data.get('agentid')}"
                )
                return []

            params = {"param_smsgid": smsgid}
            if message_count is not None and int(message_count) > 0:
                params["param_message_count"] = int(message_count)
                query = queries.DB_GET_STREAMS_THREAD_MESSAGES_BY_MESSAGE_COUNT
            else:
                query = queries.DB_GET_STREAMS_THREAD_MESSAGES

            data = await db_connector.execute(
                db_config.DB_STREAMS,
                query,
                params,
            )
            logger.info(
                f"streams thread messages data count :: {len(data) if data else 0}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return data if data else []
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            return []

    async def get_search_allowed_sids(self, archive_id: int) -> list[int]:
        try:
            params = {
                "param_archive_id": archive_id,
            }

            data = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_SEARCH_ALLOWED_SIDS,
                params,
            )

            return [int(row["sid"]) for row in data] if data else []

        except Exception:
            logger.exception(
                "Failed to fetch search-allowed SIDs for archive_id=%s",
                archive_id,
            )
            raise

    async def get_search_contributor_thread_root_ids(
        self,
        *,
        site_id: int,
        archive_id: int,
    ) -> list[int]:
        try:
            params = {
                "param_site_id": site_id,
                "param_archive_id": archive_id,
            }

            data = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_SEARCH_CONTRIBUTOR_THREAD_ROOT_IDS,
                params,
            )

            return [int(row["smsgid"]) for row in data] if data else []

        except Exception:
            logger.exception(
                "Failed to fetch contributor thread roots for "
                "site_id=%s, archive_id=%s",
                site_id,
                archive_id,
            )
            raise

    async def get_messages_for_indexing(
        self,
        site_id: int,
        sids: list[int],
    ) -> list[dict]:
        try:
            if not sids:
                return []

            sid_params = {f"sid_{index}": sid for index, sid in enumerate(sids)}

            sid_placeholders = ", ".join(f":{name}" for name in sid_params)

            query = queries.DB_GET_MESSAGES_FOR_INDEXING.format(
                sid_placeholders=sid_placeholders
            )

            params = {
                "site_id": site_id,
                **sid_params,
            }

            result = await db_connector.execute(
                db_config.DB_STREAMS,
                query,
                params,
            )

            return result
        except Exception as e:
            logger.error(
                f"Error fetching messages for indexing: site_id={site_id}, sids={sids}, error={e}"
            )
            return []

    async def get_messages_for_bulk_indexing(
        self,
        after_messagetime,
        after_smsgid: int,
        batch_size: int,
    ) -> list[dict]:
        try:
            params = {
                "after_messagetime": after_messagetime,
                "after_smsgid": after_smsgid,
                "batch_size": batch_size,
            }

            result = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_MESSAGES_FOR_BULK_INDEXING,
                params,
            )

            if result is None:
                raise RuntimeError(
                    "Database returned None while fetching messages for "
                    "bulk indexing. Check whether the database connection "
                    "pool is initialized."
                )

            return result
        except Exception:
            logger.exception(
                "Error fetching messages for bulk indexing: "
                "after_messagetime=%s, after_smsgid=%s, batch_size=%s",
                after_messagetime,
                after_smsgid,
                batch_size,
            )
            raise


    async def get_streams_thread_messages_by_duration(self, request_data, start_date, end_date):
        """Fetches thread replies occurring within the specified date range."""
        try:
            smsgid = request_data.get("smsgid")
            if not smsgid:
                logger.error(
                    f"Missing 'smsgid' in request_data, agentid :: {request_data.get('agentid')}"
                )
                return []

            if not start_date or not end_date:
                logger.error(
                    f"Missing 'start_date' or 'end_date' for thread duration lookup, agentid :: {request_data.get('agentid')}"
                )
                return []

            user_timezone = request_data.get("timezone")
            utc_start = utils.convert_to_utc(start_date, user_timezone)
            utc_end = utils.convert_to_utc(end_date, user_timezone)

            params = {
                "param_smsgid": smsgid,
                "param_start_date": utc_start,
                "param_end_date": utc_end,
            }
            data = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_STREAMS_THREAD_MESSAGES_BY_DURATION,
                params,
            )
            logger.info(
                f"streams thread messages by duration count :: {len(data) if data else 0}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return data if data else []
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            return []


streams_db_handler = StreamsDBHandler()
