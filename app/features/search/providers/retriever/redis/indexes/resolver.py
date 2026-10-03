from app.features.search.domain.enums import ContentType


class RedisIndexResolver:

    _INDEX_NAMES = {
        ContentType.MESSAGES: "streams-messages-v1",
    }

    def resolve(
        self,
        content_type: ContentType,
    ) -> str:
        try:
            return self._INDEX_NAMES[content_type]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported content type: {content_type}"
            ) from exc
