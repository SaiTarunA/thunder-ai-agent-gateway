import logging

from app.db.mysql.connection.db_connector import db_connector
import app.db.mysql.queries.opensips_sql as queries
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class CloudDBHandler:


    async def fetch_query_for_authorization(
        self,
        authkey: str,
        username: str,
    ):
        try:
            logger.info(
                f"============= authkey :: {authkey}, "
                f"agentid :: {username}"
            )
            query = queries.DB_SELECT_QUERY_FOR_AUTHORIZATION
            params = {"authkey": authkey, "username": username}
            data = await db_connector.execute_statement(
                db_config.DB_CLOUD, query, params
            )
            return data if data else []
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {username}")
            return []


cloud_db_handler = CloudDBHandler()
