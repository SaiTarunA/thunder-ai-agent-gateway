from datetime import datetime, timezone

from app.features.search.domain.models import (
    SearchAccess,
    SearchContextRequest,
    SearchFilters,
    SearchModifiers,
)


class FilterResolver:

    def _extract_modifiers(
        self,
        modifiers: str,
    ) -> SearchModifiers:
        author_usernames = set()
        channel_names = set()

        for part in modifiers.split():
            if part.startswith("from:"):
                username = part[len("from:"):].strip()

                if username:
                    author_usernames.add(username)

            elif part.startswith("in:"):
                channel_name = part[len("in:"):].strip()

                if channel_name:
                    channel_names.add(channel_name)

        # TODO:
        # Resolve author_usernames -> archive IDs
        # Resolve channel_names -> SIDs

        author_ids: set[int] = set()
        sids: set[int] = set()

        return SearchModifiers(
            author_ids=(
                frozenset(author_ids)
                if author_ids
                else None
            ),
            sids=(
                frozenset(sids)
                if sids
                else None
            ),
        )

    @staticmethod
    def _to_datetime(
        timestamp: float | None,
    ) -> datetime | None:
        if timestamp is None:
            return None

        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )

    def resolve(
        self,
        access: SearchAccess,
        context_request: SearchContextRequest,
    ) -> SearchFilters:
        channel_types = (
            context_request.channel_types
            if context_request.channel_types
            else ()
        )

        modifiers = None

        if context_request.modifiers:
            modifiers = self._extract_modifiers(
                context_request.modifiers
            )

        return SearchFilters(
            access=access,
            channel_types=tuple(channel_types),
            after=self._to_datetime(
                context_request.after,
            ),
            before=self._to_datetime(
                context_request.before,
            ),
            modifiers=modifiers,
        )