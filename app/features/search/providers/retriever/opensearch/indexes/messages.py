from app.features.search.providers.retriever.opensearch.indexes.base import (
    OpenSearchIndexDefinition,
)


class MessagesIndex(OpenSearchIndexDefinition):

    @property
    def name(self) -> str:
        return "streams-messages-v1"

    @property
    def alias(self) -> str:
        return "streams-messages"

    @property
    def settings(self) -> dict:
        return {
            "index.knn": True,
            "number_of_shards": 1,
            "number_of_replicas": 1,
        }

    @property
    def mappings(self) -> dict:
        return {
            "dynamic": "strict",
            "properties": {
                "site_id": {
                    "type": "keyword",
                },
                "site_name": {
                    "type": "keyword",
                },

                "message_id": {
                    "type": "long",
                },
                "sid": {
                    "type": "long",
                },

                "conversation_type": {
                    "type": "keyword",
                },
                "conversation_name": {
                    "type": "text",
                    "fields": {
                        "keyword": {
                            "type": "keyword",
                        }
                    },
                },

                "message_type": {
                    "type": "keyword",
                },

                "text": {
                    "type": "text",
                    "analyzer": "standard",
                },

                "author_archive_id": {
                    "type": "long",
                },
                "author_account_id": {
                    "type": "long",
                },
                "author_username": {
                    "type": "text",
                    "fields": {
                        "keyword": {
                            "type": "keyword",
                        }
                    },
                },
                "author_name": {
                    "type": "text",
                    "fields": {
                        "keyword": {
                            "type": "keyword",
                        }
                    },
                },

                "created_at": {
                    "type": "date",
                },
                "updated_at": {
                    "type": "date",
                },

                "thread_root_id": {
                    "type": "long",
                },
                "parent_message_id": {
                    "type": "long",
                },
                "is_thread_reply": {
                    "type": "boolean",
                },

                "is_deleted": {
                    "type": "boolean",
                },
                "is_edited": {
                    "type": "boolean",
                },
                "is_pinned": {
                    "type": "boolean",
                },

                "embedding": {
                    "type": "knn_vector",
                    "dimension": 384,
                    "space_type": "cosinesimil",
                    "method": {
                        "name": "hnsw",
                        "engine": "lucene",
                    },
                },

                "embedding_version": {
                    "type": "keyword",
                },
            },
        }