import logging

from app.db.mysql.connection.db_connector import db_connector
import app.db.mysql.queries.streams_sql as queries
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class StreamsDBHandler:

    # =====================================================
    # Insert OpenAI billing data
    # =====================================================

    async def insert_openai_billing_data(self, bill_data, agentid):
        try:
            logger.info(
                "============= insertOpenAIBillingData =========="
                f" bill_data :: {bill_data}, agentid :: {agentid}"
            )

            if not bill_data:
                logger.error("NO bill_data found ------ ")
                return []

            query = queries.DB_GENERATE_AI_BILLING
            params = bill_data
            data = await db_connector.execute_statement(
                db_config.DB_STREAMS,
                query,
                params,
            )
            return data if data else []

        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {agentid}")
            return []

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
                params["param_start_date"] = start_date
                params["param_end_date"] = end_date
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
            logger.error(
                f"Error :: {e}, agentid :: {request_data.get('agentid')}"
            )
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

    async def get_streams_thread_messages(self, request_data):
        try:
            smsgid = request_data.get("smsgid")
            if not smsgid:
                logger.error(
                    f"Missing 'smsgid' in request_data, agentid :: {request_data.get('agentid')}"
                )
                return []

            params = {"param_smsgid": smsgid}
            data = await db_connector.execute(
                db_config.DB_STREAMS,
                queries.DB_GET_STREAMS_THREAD_MESSAGES,
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


streams_db_handler = StreamsDBHandler()
