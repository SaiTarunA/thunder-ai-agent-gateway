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
    ):
        try:
            params = {"sid": request_data.get("sid")}

            if message_count is not None:
                params["message_count"] = message_count
                query = queries.DB_GET_STREAMS_USER_CHAT_BY_MESSAGE_COUNT
            else:
                params["start_date"] = start_date
                params["end_date"] = end_date
                query = queries.DB_GET_STREAMS_USER_CHAT_BY_DURATION

            data = await db_connector.execute(
                db_config.DB_STREAMS,
                query,
                params,
            )

            logger.info(
                f"streams chat data :: {data}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return data if data else []

        except Exception as e:
            logger.error(
                f"Error :: {e}, agentid :: {request_data.get('agentid')}"
            )
            return []


streams_db_handler = StreamsDBHandler()
