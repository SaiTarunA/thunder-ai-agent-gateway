import logging

from app.db.mysql.connection.db_connector import db_connector
import app.db.mysql.queries.warehouse_sql as queries
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class LivePBXDBHandler:


    async def verify_admin_login_token(self, token, refresh_token):
        try:
            logger.info(
                f"collecting details for admin login token :: {token}"
            )
            query = queries.DB_SELECT_ADMIN_LOGIN_TOKENS
            params = {"token": token, "refresh_token": refresh_token}
            data = await db_connector.execute(
                db_config.DB_LIVEPBX, query, params
            )
            if data and len(data) > 0:
                logger.info(
                    f"details found for admin login token :: {data}"
                )
                return data
            else:
                logger.warning(
                    f"No details found for admin login token :: {token}"
                )
                return None
        except Exception as e:
            logger.error(f"Error : {e}, token :: {token}")
            return None


livepbx_db_handler = LivePBXDBHandler()
