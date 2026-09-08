import logging

from sqlalchemy import text, bindparam

from app.db.mysql.connection.db_pool import DBPool
from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class DBConnector:
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super(DBConnector, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        logger.info("Initializing DBConnector Object")
        self.live_db_pool = None
        self.warehouse_db_pool = None
        self.db_pool = DBPool()

    # =====================================================
    # SELECT Operations
    # =====================================================

    async def execute(
        self,
        db_type,
        db_query,
        params=None,
    ):
        result = None

        try:
            engines = self.db_pool.get_engines(
                db_type,
                db_config.DB_SELECT_OPERQATION,
            )

            logger.info(f"pool_objs ---> {engines}")

            if not engines:
                logger.error(
                    f"No pool objects for {db_type}"
                )
                return None

            for i, engine in enumerate(engines):
                host = engine.url.host
                logger.info(
                    f"host :: {host} "
                    f"type :: {db_type} "
                    f"index :: {i}"
                )

                retry = db_config.DB_FAILOVER_RETRY_COUNT

                while retry:
                    try:
                        logger.info(
                            f"Trying DB: {db_type}, "
                            f"Host: {host}, "
                            f"retry count: {retry}"
                        )
                        async with engine.connect() as conn:
                            t = text(db_query)
                            if params:
                                for k, v in params.items():
                                    if isinstance(v, (list, tuple, set)):
                                        t = t.bindparams(bindparam(k, expanding=True))
                            result_proxy = await conn.execute(
                                t,
                                params or {},
                            )
                            result = result_proxy.mappings().all()
                            await conn.commit()
                        break

                    except Exception as err:
                        if retry == 1:
                            logger.error(
                                f"Max retries reached -> {err}"
                            )
                            result = None
                            break
                        retry -= 1
                        logger.error(
                            f"DB failed -> {err}"
                        )

                if result is not None:
                    break

            if result is None:
                logger.error(
                    "All DB connections failed"
                )

        except Exception as e:
            logger.exception(
                f"Query failed: {e}"
            )

        return result

    # =====================================================
    # INSERT / UPDATE / DELETE Operations
    # =====================================================

    async def execute_statement(
        self,
        db_type,
        db_query,
        params=None,
    ):
        result = None

        try:
            engines = self.db_pool.get_engines(
                db_type,
                db_config.DB_MODIFY_OPERQATION,
            )

            if not engines:
                logger.error(
                    f"No pool objects for {db_type}"
                )
                return None

            for i, engine in enumerate(engines):
                host = engine.url.host
                logger.info(
                    f"host :: {host} "
                    f"type :: {db_type} "
                    f"index :: {i}"
                )

                retry = db_config.DB_FAILOVER_RETRY_COUNT

                while retry:
                    try:
                        logger.info(
                            f"Trying DB TYPE :: {db_type}, "
                            f"DB_IP :: {host}, "
                            f"retry :: {retry}"
                        )
                        async with engine.connect() as conn:
                            t = text(db_query)
                            if params:
                                for k, v in params.items():
                                    if isinstance(v, (list, tuple, set)):
                                        t = t.bindparams(bindparam(k, expanding=True))
                            result_proxy = await conn.execute(
                                t,
                                params or {},
                            )

                            if result_proxy.returns_rows:
                                result = result_proxy.fetchall()
                                logger.info(
                                    f"DB operation success, "
                                    f"output: {result}"
                                )
                            else:
                                result = result_proxy.rowcount
                                logger.info(
                                    f"DB operation success, "
                                    f"affected rows: {result}"
                                )

                            await conn.commit()
                        break

                    except Exception as err:
                        if retry == 1:
                            logger.error(
                                f"Max retries reached :: {err}"
                            )
                            result = None
                            break
                        retry -= 1
                        logger.error(
                            f"DB connection failed :: "
                            f"{host} :: {err}"
                        )
                        if "SQL syntax" in str(err):
                            result = None
                            break

                if result is not None:
                    break

            if result is None:
                logger.error(
                    "All database connections failed"
                )

        except Exception as e:
            logger.exception(
                f"Query failed :: {e}"
            )

        return result

db_connector = DBConnector()
