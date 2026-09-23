from datetime import datetime
from typing import Any

from app.features.search.domain.enums import (
    ChannelType,
    ContentType,
    RetrievalMethodType,
    RetrievalStatus,
)
from app.features.search.domain.models import (
    FilesData,
    MessagesData,
    RetrievalCandidate,
    RetrievalResult,
)
from app.features.search.providers.interfaces import ResponseMapper


class OpenSearchResponseMapper(ResponseMapper):

    def map_response(
        self,
        response: Any,
        methods: tuple[RetrievalMethodType, ...],
        latency_ms: int,
    ) -> RetrievalResult:
        candidates = []

        hits = response.get("hits", {}).get("hits", [])

        for rank, hit in enumerate(hits, start=1):
            candidates.append(
                self._map_hit(
                    hit=hit,
                    rank=rank,
                )
            )

        return RetrievalResult(
            methods=methods,
            status=RetrievalStatus.SUCCESS,
            candidates=candidates,
            latency_ms=latency_ms,
        )

    def _map_hit(
        self,
        *,
        hit: dict[str, Any],
        rank: int,
    ) -> RetrievalCandidate:
        source = hit.get("_source", {})

        content_type = ContentType(
            source.get(
                "content_type",
                ContentType.MESSAGES.value,
            )
        )

        if content_type == ContentType.MESSAGES:
            data = self._map_message(source)
        elif content_type == ContentType.FILES:
            data = self._map_file(source)
        else:
            data = None

        return RetrievalCandidate(
            document_id=str(hit["_id"]),
            content_type=content_type,
            data=data,
            rank=rank,
            raw_score=hit.get("_score"),
            sort_values=tuple(hit.get("sort", [])),
        )

    @staticmethod
    def _map_message(
        source: dict[str, Any],
    ) -> MessagesData:
        return MessagesData(
            message_id=int(source["message_id"]),
            sid=int(source["sid"]),
            text=source.get("text", ""),
            author_archive_id=int(source["author_archive_id"]),
            author_name=source.get("author_name"),
            author_username=source.get("author_username"),
            conversation_name=source.get("conversation_name"),
            conversation_type=(
                ChannelType(source["conversation_type"])
                if source.get("conversation_type")
                else None
            ),
            created_at=(
                datetime.fromisoformat(source["created_at"])
                if source.get("created_at")
                else None
            ),
            updated_at=(
                datetime.fromisoformat(source["updated_at"])
                if source.get("updated_at")
                else None
            ),
            thread_root_id=(
                int(source["thread_root_id"])
                if source.get("thread_root_id") is not None
                else None
            ),
            parent_message_id=(
                int(source["parent_message_id"])
                if source.get("parent_message_id") is not None
                else None
            ),
            is_thread_reply=source.get("is_thread_reply", False),
            is_edited=source.get("is_edited", False),
            is_pinned=source.get("is_pinned", False),
        )

    @staticmethod
    def _map_file(
        source: dict[str, Any],
    ) -> FilesData:
        return FilesData()