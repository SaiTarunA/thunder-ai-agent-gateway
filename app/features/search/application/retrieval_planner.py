from app.features.search.config import SearchConfig
from app.features.search.domain.enums import RetrievalMethodType
from app.features.search.domain.models import (
    RetrievalPlan,
    SearchContextRequest,
)


class RetrievalPlanner:
    def __init__(self, config: SearchConfig):
        self._config = config

    def plan(
        self,
        context_request: SearchContextRequest,
    ) -> RetrievalPlan:
        methods = tuple(
            context_request.retrieval_methods or ()
        )

        if not methods:
            raise ValueError(
                "At least one retrieval method is required"
            )

        methods = tuple(
            method
            for method in (
                RetrievalMethodType.LEXICAL,
                RetrievalMethodType.SEMANTIC,
            )
            if method in methods
        )

        limit = (
            context_request.limit
            if context_request.limit is not None
            else self._config.max_page_limit
        )

        pagination_depth = (
            limit
            * self._config.hybrid_pagination_depth_multiplier
        )

        return RetrievalPlan(
            methods=methods,
            limit=limit,
            pagination_depth=pagination_depth,
        )