import re
import unicodedata

from app.features.search.domain.exceptions import EmptySearchQuery
from app.features.search.domain.models import ProcessedQuery


class QueryProcessor:

    def process(
        self,
        raw_query: str,
    ) -> ProcessedQuery:

        if raw_query is None:
            raise EmptySearchQuery(
                "Search query cannot be null"
            )

        normalized = unicodedata.normalize(
            "NFKC",
            raw_query,
        )

        normalized = normalized.strip()

        normalized = re.sub(
            r"\s+",
            " ",
            normalized,
        )

        if not normalized:
            raise EmptySearchQuery(
                "Search query cannot be empty"
            )

        return ProcessedQuery(
            raw_query=raw_query,
            normalized_query=normalized,
            lexical_query=normalized,
            embedding_query=normalized,
        )