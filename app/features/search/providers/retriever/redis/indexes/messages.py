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
            # TAG, not NUMERIC: these ids are ~1.8e18 (19-digit snowflake-
            # style), far past a double's exact-integer range (~9e15) -
            # NUMERIC would silently round them. TAG compares as exact
            # strings, same as sid/author_archive_id/thread_root_id below.
            TagField("message_id"),
            TagField("sid"),
            TagField("channel_type"),
            TagField("message_type"),
            TextField("text"),
            TagField("author_archive_id"),
            NumericField("created_at", sortable=True),
            NumericField("updated_at", sortable=True),
            TagField("thread_root_id"),
            TagField("parent_message_id"),
            TagField("is_thread_reply"),
            TagField("is_deleted"),
            TagField("is_edited"),
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
