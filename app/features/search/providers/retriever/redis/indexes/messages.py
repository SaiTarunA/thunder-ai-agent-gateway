from redis.commands.search.field import (
    Field,
    NumericField,
    TagField,
    TextField,
    VectorField,
)

from app.features.search.providers.retriever.redis.indexes.base import (
    RedisIndexDefinition,
)


class MessagesIndex(RedisIndexDefinition):

    @property
    def name(self) -> str:
        return "streams-messages-v1"

    @property
    def key_prefix(self) -> str:
        return "search:messages:"

    @property
    def fields(self) -> list[Field]:
        return [
            TagField("site_id"),
            NumericField("message_id", sortable=True),
            TagField("sid"),
            TagField("channel_type"),
            TextField("channel_name"),
            TagField("message_type"),
            TextField("text"),
            TagField("author_archive_id"),
            TextField("author_username"),
            TextField("author_name"),
            NumericField("created_at", sortable=True),
            NumericField("updated_at", sortable=True),
            TagField("thread_root_id"),
            TagField("parent_message_id"),
            TagField("is_thread_reply"),
            TagField("is_deleted"),
            TagField("is_edited"),
            TagField("is_pinned"),
            TagField("embedding_version"),
            VectorField(
                "embedding",
                "HNSW",
                {
                    "TYPE": "FLOAT32",
                    "DIM": 384,
                    "DISTANCE_METRIC": "COSINE",
                },
            ),
        ]
