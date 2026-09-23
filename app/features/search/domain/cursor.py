import base64
import json
import hashlib

from app.features.search.domain.enums import (
    ContentType,
    SortDirectionType,
    SortType,
)
from app.features.search.domain.models import SearchContextRequest, SearchCursor


class SearchCursorCodec:

    def encode(
        self,
        cursor: SearchCursor,
    ) -> str:
        payload = {
            "q": cursor.query_hash,
            "s": cursor.sort.value,
            "d": cursor.sort_direction.value,
            "pd": cursor.pagination_depth,
            "sa": {
                content_type.value: (
                    list(search_after)
                    if search_after is not None
                    else None
                )
                for content_type, search_after
                in cursor.search_after.items()
            },
        }

        raw = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return base64.urlsafe_b64encode(
            raw,
        ).decode("ascii")

    def decode(
        self,
        value: str,
    ) -> SearchCursor:
        try:
            raw = base64.urlsafe_b64decode(
                value.encode("ascii"),
            )

            payload = json.loads(
                raw.decode("utf-8"),
            )

            search_after = {
                ContentType(content_type): (
                    tuple(values)
                    if values is not None
                    else None
                )
                for content_type, values
                in payload["sa"].items()
            }

            return SearchCursor(
                query_hash=payload["q"],
                sort=SortType(payload["s"]),
                sort_direction=SortDirectionType(payload["d"]),
                pagination_depth=int(payload["pd"]),
                search_after=search_after,
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise ValueError(
                "Invalid search cursor",
            ) from exc


class SearchQueryHasher:

    def hash(
        self,
        request: SearchContextRequest,
        *,
        normalized_query: str,
    ) -> str:
        payload = {
            "query": normalized_query,
            "content_types": [value.value for value in (request.content_types or [])],
            "channel_types": [value.value for value in (request.channel_types or [])],
            "before": request.before,
            "after": request.after,
            "include_context_messages": (request.include_context_messages),
            "modifiers": request.modifiers,
            "retrieval_methods": [
                value.value for value in (request.retrieval_methods or [])
            ],
            "sort": request.sort.value,
            "sort_direction": request.sort_direction.value,
        }

        raw = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return hashlib.sha256(raw).hexdigest()
