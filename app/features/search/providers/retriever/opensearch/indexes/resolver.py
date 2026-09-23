from app.features.search.domain.enums import ContentType


class OpenSearchIndexResolver:

    _INDEX_ALIASES = {
        ContentType.MESSAGES: "streams-messages",
        ContentType.FILES: "streams-files",
        ContentType.CHANNELS: "streams-channels",
        ContentType.USERS: "streams-users",
        ContentType.SMS: "streams-sms",
    }

    def resolve(
        self,
        content_type: ContentType,
    ) -> str:
        try:
            return self._INDEX_ALIASES[content_type]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported content type: {content_type}"
            ) from exc