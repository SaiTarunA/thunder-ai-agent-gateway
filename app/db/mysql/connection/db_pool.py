import logging
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

from app.core.configs import mysql_config as db_config

logger = logging.getLogger(__name__)


class DBPool:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DBPool, cls).__new__(cls)
            cls._instance.engines = {}
        return cls._instance

    async def init_db_pool(self):
        """
        Initialize async database connection pools for all
        configured masters and slaves.
        """
        try:
            logger.info("=============")

            for db_name, dbcfg in db_config.DB_CONFIGS.items():

                self.engines[db_name] = {
                    "master": [],
                    "slave": []
                }

                logger.info(
                    f"Creating master pool for database: {db_name}"
                )

                for master_ip in list(dict.fromkeys(dbcfg.get("master", []))):
                    engine = await self.create_engine(dbcfg, master_ip)
                    if engine:
                        self.engines[db_name]["master"].append(engine)

                logger.info(
                    f"Creating slave pool for database: {db_name}"
                )

                for slave_ip in list(dict.fromkeys(dbcfg.get("slave", []))):
                    engine = await self.create_engine(dbcfg, slave_ip)
                    if engine:
                        self.engines[db_name]["slave"].append(engine)

        except Exception as e:
            logger.exception(
                f"Error initializing pools: {e}"
            )

    async def create_engine(
        self,
        dbconfig: dict,
        host: str
    ) -> AsyncEngine | None:
        """
        Create Async SQLAlchemy Engine.
        """
        try:
            logger.info(
                f"host={host}, "
                f"user={dbconfig['user']}, "
                f"database={dbconfig['database']}, "
                f"connectionLimit={dbconfig['connectionLimit']}, "
                f"connectTimeout={dbconfig['connectTimeout']}"
            )

            url = (
                f"mysql+aiomysql://"
                f"{dbconfig['user']}:{dbconfig['password']}"
                f"@{host}/{dbconfig['database']}"
            )

            pool_size = dbconfig.get("connectionLimit", 10)
            if pool_size < 50:
                pool_size = 50
            max_overflow = 20

            engine = create_async_engine(
                url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=dbconfig.get("connectTimeout", 30),
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False,
            )

            from sqlalchemy import text
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            logger.info(f"Successfully created pool for {host}")
            return engine

        except SQLAlchemyError as e:
            logger.error(
                f"Error creating async engine: {e}"
            )
            return None

    def get_engines(
        self,
        db_name: str,
        role: str = "master"
    ) -> list[AsyncEngine] | None:
        """
        Return engines for database role.
        """
        try:
            engines = self.engines.get(db_name, {}).get(role, [])
            if not engines:
                logger.error(
                    f"No engines found "
                    f"for {db_name} ({role})"
                )
                return None
            return engines
        except Exception as e:
            logger.error(f"Error getting engines: {e}")
            return None

    async def close_all(self):
        """
        Gracefully close all connection pools.
        """
        for db_name, roles in self.engines.items():
            for role in ("master", "slave"):
                for engine in roles.get(role, []):
                    try:
                        await engine.dispose()
                        logger.info(f"Closed pool: {db_name}/{role}")
                    except Exception as e:
                        logger.error(f"Error closing pool: {e}")
