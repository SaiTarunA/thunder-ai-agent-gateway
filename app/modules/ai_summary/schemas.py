from pydantic import BaseModel
from datetime import datetime

class SummaryNLPExtractedData(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    message_count: int | None = None
    unread_messages: bool | None = False
    is_resummarization_request: bool = False
    context: str | None = None
    summary_type: str | None = None
    tone: str | None = None
    buddy_name: str | None = None
    topic_name: str | None = None
    response_text: str | None = None


class StreamsUserChatData(BaseModel):
    message: str
    messagetime: datetime
    archiveid: int
    sid: int
    accountid: int
    username: str
    siteid: int
    firstname: str
    lastname: str


class StoredChatSummaryData(BaseModel):
    summary: str
    start_date: datetime
    end_date: datetime
    sid: int
    extra_data: dict | str | None = None

