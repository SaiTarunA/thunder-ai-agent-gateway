import json
import logging

from app.db.mysql.connection.db_connector import db_connector
import app.db.mysql.queries.warehouse_sql as queries
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class WarehouseDBHandler:

    async def getUUID(self):
        try:
            logger.info("============= getUUID ========")
            query = queries.DB_GET_UNIQUE_ID
            data = await db_connector.execute(db_config.DB_WAREHOUSEPBX, query, [])
            return data[0]["uniqueid"] if data else None
        except Exception as e:
            logger.exception(f"Error :: {e}")
            return None

    async def verifyAuthToken(self, auth_token):
        try:
            logger.info(
                f"============= verify_authkey ======== auth_token :: {auth_token}"
            )
            query = queries.DB_SELECT_UNIQUE_ID
            params = {"uniqueid": auth_token}
            data = await db_connector.execute_statement(
                db_config.DB_WAREHOUSEPBX, query, params
            )
            logger.info(f"auth data : {data}, auth_token :: {auth_token}")
            return data if data else []
        except Exception as e:
            logger.exception(f"Error :: {e}, auth_token :: {auth_token}")
            return []

    async def insertAuthToken(self, unique_id, agentid, siteid):
        try:
            logger.info(
                f"============= insert_authkey ======== agentid :: {agentid}"
            )
            query = queries.DB_INSERT_UNIQUE_ID
            params = {"uniqueid": unique_id, "agentid": agentid, "siteid": siteid}
            data = await db_connector.execute_statement(
                db_config.DB_WAREHOUSEPBX, query, params
            )
            return data if data else []
        except Exception as e:
            logger.exception(f"Error :: {e}, agentid :: {agentid}")
            return []

    async def deleteAuthToken(self, auth_token):
        try:
            logger.info(
                f"============= delete_authkey ======== auth_token :: {auth_token}"
            )
            query = queries.DB_DELETE_UUINQUQ_ID
            params = {"uniqueid": auth_token}
            data = await db_connector.execute_statement(
                db_config.DB_WAREHOUSEPBX, query, params
            )
            return data if data else []
        except Exception as e:
            logger.exception(f"Error :: {e}, auth_token :: {auth_token}")
            return []

    async def insertResponseBody(self, response, unique_id, agentid):
        try:
            logger.info(
                f"============= insertResponseBody ======== agentid :: {agentid}"
            )
            res_body = json.dumps(response, indent=2)
            query = queries.DB_UPDATE_RES_BODY
            params = {"res_body": res_body, "id": unique_id}
            data = await db_connector.execute_statement(
                db_config.DB_WAREHOUSEPBX, query, params
            )
            logger.info(
                f"insertResponseBody response :: {data}, agentid :: {agentid}"
            )
            return data if data else None
        except Exception as e:
            logger.exception(f"Error :: {e}, agentid :: {agentid}")
            return {"Error": str(e)}


warehouse_db_handler = WarehouseDBHandler()
