from app.features.search.providers.retriever.opensearch.indexes.base import (
    OpenSearchIndexDefinition,
)


class BackfillCheckpointIndex(OpenSearchIndexDefinition):

    @property
    def name(self) -> str:
        return "streams-search-backfill-checkpoints"

    @property
    def settings(self) -> dict:
        return {
            "number_of_shards": 1,
            "number_of_replicas": 1,
        }

    @property
    def mappings(self) -> dict:
        return {
            "dynamic": "strict",
            "properties": {
                "job_name": {
                    "type": "keyword",
                },
                "since": {
                    "type": "date",
                },
                "after_messagetime": {
                    "type": "date",
                },
                "after_smsgid": {
                    "type": "long",
                },
                "total_fetched": {
                    "type": "long",
                },
                "total_indexed": {
                    "type": "long",
                },
                "total_failed": {
                    "type": "long",
                },
                "status": {
                    "type": "keyword",
                },
                "updated_at": {
                    "type": "date",
                },
            },
        }
