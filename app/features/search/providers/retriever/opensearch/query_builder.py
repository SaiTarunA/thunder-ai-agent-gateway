from __future__ import annotations

from datetime import datetime
from typing import Any

from app.features.search.domain.enums import (
    SortType,
)
from app.features.search.domain.models import (
    RetrievalRequest,
    SearchAccess,
    SearchFilters,
    SearchModifiers,
)
from app.features.search.providers.interfaces import SearchQueryBuilder


class OpenSearchQueryBuilder(SearchQueryBuilder):

    def build(
        self,
        request: RetrievalRequest,
    ) -> dict[str, Any]:

        if request.is_hybrid:
            query = self._build_hybrid_query(request)

        elif request.is_lexical:
            query = self._build_lexical_query(request)

        elif request.is_semantic:
            query = self._build_semantic_query(request)

        else:
            raise ValueError(
                f"Unsupported retrieval methods: {request.methods}"
            )

        body: dict[str, Any] = {
            "size": request.limit,
            "query": query,
            "sort": self._build_sort(request),
        }

        if request.search_after is not None:
            body["search_after"] = list(
                request.search_after
            )

        return body

    def _build_lexical_query(
        self,
        request: RetrievalRequest,
    ) -> dict[str, Any]:

        return {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": request.query,
                            "fields": [
                                "text^5",
                                "conversation_name^2",
                                "author_name^1.5",
                                "author_username",
                            ],
                            "type": "best_fields",
                        }
                    }
                ],
                "filter": self._build_filters(
                    request.filters,
                ),
            }
        }

    def _build_semantic_query(
        self,
        request: RetrievalRequest,
    ) -> dict[str, Any]:

        if request.query_vector is None:
            raise ValueError(
                "query_vector is required for semantic retrieval"
            )

        return {
            "bool": {
                "must": [
                    {
                        "knn": {
                            "embedding": {
                                "vector": request.query_vector,
                                "k": request.pagination_depth,
                            }
                        }
                    }
                ],
                "filter": self._build_filters(
                    request.filters,
                ),
            }
        }

    def _build_hybrid_query(
        self,
        request: RetrievalRequest,
    ) -> dict[str, Any]:

        if request.query_vector is None:
            raise ValueError(
                "query_vector is required for hybrid retrieval"
            )

        return {
            "hybrid": {
                "queries": [
                    {
                        "multi_match": {
                            "query": request.query,
                            "fields": [
                                "text^5",
                                "conversation_name^2",
                                "author_name^1.5",
                                "author_username",
                            ],
                            "type": "best_fields",
                        }
                    },
                    {
                        "knn": {
                            "embedding": {
                                "vector": request.query_vector,
                                "k": request.pagination_depth,
                            }
                        }
                    },
                ],
                "filter": {
                    "bool": {
                        "filter": self._build_filters(
                            request.filters,
                        ),
                    }
                },
                "pagination_depth": request.pagination_depth,
            }
        }

    @staticmethod
    def _build_filters(
        filters: SearchFilters,
    ) -> list[dict[str, Any]]:

        result = [
            {
                "term": {
                    "site_id": filters.access.site_id,
                }
            },
            {
                "term": {
                    "is_deleted": False,
                }
            },
            OpenSearchQueryBuilder._build_authorization_filter(
                filters.access,
            ),
        ]

        if filters.channel_types:
            result.append(
                {
                    "terms": {
                        "conversation_type": [
                            channel_type.value
                            for channel_type in filters.channel_types
                        ]
                    }
                }
            )

        if filters.modifiers:
            result.extend(
                OpenSearchQueryBuilder._build_modifier_filters(
                    filters.modifiers,
                )
            )

        date_filter = OpenSearchQueryBuilder._build_date_filter(
            after=filters.after,
            before=filters.before,
        )

        if date_filter:
            result.append(date_filter)

        return result

    @staticmethod
    def _build_authorization_filter(
        access: SearchAccess,
    ) -> dict[str, Any]:

        access_conditions: list[dict[str, Any]] = []

        if access.allowed_sids:
            access_conditions.append(
                {
                    "terms": {
                        "sid": list(access.allowed_sids),
                    }
                }
            )

        if access.contributor_thread_root_ids:
            access_conditions.append(
                {
                    "terms": {
                        "thread_root_id": list(
                            access.contributor_thread_root_ids
                        ),
                    }
                }
            )

        if not access_conditions:
            return {
                "match_none": {},
            }

        return {
            "bool": {
                "should": access_conditions,
                "minimum_should_match": 1,
            }
        }

    @staticmethod
    def _build_modifier_filters(
        modifiers: SearchModifiers,
    ) -> list[dict[str, Any]]:

        result: list[dict[str, Any]] = []

        if modifiers.sids:
            result.append(
                {
                    "terms": {
                        "sid": list(modifiers.sids),
                    }
                }
            )

        if modifiers.author_ids:
            result.append(
                {
                    "terms": {
                        "author_archive_id": list(
                            modifiers.author_ids
                        ),
                    }
                }
            )

        return result

    @staticmethod
    def _build_date_filter(
        *,
        after: datetime | None,
        before: datetime | None,
    ) -> dict[str, Any] | None:

        if after is None and before is None:
            return None

        range_query: dict[str, Any] = {}

        if after is not None:
            range_query["gte"] = after.isoformat()

        if before is not None:
            range_query["lt"] = before.isoformat()

        return {
            "range": {
                "created_at": range_query,
            }
        }

    @staticmethod
    def _build_sort(
        request: RetrievalRequest,
    ) -> list[dict[str, Any]]:

        direction = request.sort_direction.value

        if request.sort == SortType.SCORE:
            return [
                {
                    "_score": {
                        "order": direction,
                    }
                },
                {
                    "message_id": {
                        "order": "asc",
                    }
                },
            ]

        if request.sort == SortType.TIMESTAMP:
            return [
                {
                    "created_at": {
                        "order": direction,
                    }
                },
                {
                    "message_id": {
                        "order": "asc",
                    }
                },
            ]

        raise ValueError(
            f"Unsupported sort type: {request.sort}"
        )