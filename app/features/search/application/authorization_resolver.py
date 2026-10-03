from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.search.domain.models import SearchAccess


class AuthorizationResolver:

    def __init__(self, db_handler=streams_db_handler):
        self._db_handler = db_handler

    async def resolve(
        self,
        *,
        site_id: int,
        archive_id: int,
    ) -> SearchAccess:
        allowed_sids = await self._db_handler.get_search_allowed_sids(
            archive_id=archive_id,
        )

        contributor_thread_root_ids = (
            await self._db_handler.get_search_contributor_thread_root_ids(
                site_id=site_id,
                archive_id=archive_id,
            )
        )

        return SearchAccess(
            site_id=site_id,
            allowed_sids=frozenset(allowed_sids),
            contributor_thread_root_ids=frozenset(
                contributor_thread_root_ids
            ),
        )