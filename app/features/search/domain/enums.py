from enum import Enum

class ContentType(str, Enum):
    MESSAGES = "messages"

    # Future Content Types
    SMS = "sms"
    FILES = "files"
    CHANNELS = "channels"
    USERS = "users"

class ChannelType(str, Enum):
    DM = "dm"
    PRIVATE_CHANNEL = "private_channel"

    # Future Channel Types
    TEMPORARY_CHANNEL = "temporary_channel"
    DISPLAY_CHANNEL = "display_channel"
    COMPANY_CHANNEL = "company_channel"
    PUBLIC_CHANNEL = "public_channel"


class SortType(str, Enum):
    SCORE = "score"
    TIMESTAMP = "timestamp"

class SortDirectionType(str, Enum):
    ASC = "asc"
    DESC = "desc"

class RetrievalMethodType(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"

class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"