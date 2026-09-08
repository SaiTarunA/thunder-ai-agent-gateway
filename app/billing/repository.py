"""Billing DB access. Moved out of `db/mysql/repositories/streams_repo.py`'s
`insert_openai_billing_data` — billing isn't a Streams-domain query, and keeping it
there meant the repository knew about OpenAI naming even before this refactor."""

import logging

import app.db.mysql.queries.streams_sql as queries
from app.core.configs import mysql_config as db_config
from app.db.mysql.connection.db_connector import db_connector

logger = logging.getLogger(__name__)


class BillingRepository:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def insert_billing_data(self, bill_data: dict, agentid=None):
        try:
            logger.info(
                "============= bill_data :: %s, agentid :: %s",
                bill_data,
                agentid,
            )

            if not bill_data:
                logger.error("NO bill_data found ------ ")
                return []

            query = queries.DB_GENERATE_AI_BILLING
            data = await db_connector.execute_statement(db_config.DB_STREAMS, query, bill_data)
            return data if data else []

        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {agentid}")
            return []


billing_repository = BillingRepository()
