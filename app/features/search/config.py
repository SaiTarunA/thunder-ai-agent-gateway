from dataclasses import dataclass


@dataclass(frozen=True)
class SearchConfig:
    max_page_limit: int = 100

    hybrid_pagination_depth_multiplier: int = 5

    retrieval_timeout_seconds: float = 5.0
    embedding_timeout_seconds: float = 1.0

    hybrid_search_pipeline: str = "streams-hybrid-pipeline"