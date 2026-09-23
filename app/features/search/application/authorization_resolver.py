from app.features.search.domain.models import SearchAccess


class AuthorizationResolver:

    def resolve(
        self,
        *,
        site_id: int,
        archive_id: int,
    ) -> SearchAccess:
        # TODO:
        # Resolve from the database:
        #
        # 1. allowed_sids:
        #    Conversations the user can normally access.
        #
        # 2. contributor_thread_root_ids:
        #    Thread roots where the user has contributor access.
        #
        # Contributor access must NOT grant access to the
        # entire conversation/SID.

        allowed_sids = frozenset()
        contributor_thread_root_ids = frozenset()

        return SearchAccess(
            site_id=site_id,
            allowed_sids=allowed_sids,
            contributor_thread_root_ids=contributor_thread_root_ids,
        )