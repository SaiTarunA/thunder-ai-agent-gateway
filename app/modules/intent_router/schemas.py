from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class LunaRequest(BaseModel):
    agentid: str = Field(..., description="The ID/Email of the agent making the request")
    siteid: str | int = Field(..., description="The ID of the site making the request")
    sitename: str = Field(..., description="The name of the site making the request")
    user_name: Optional[str] = Field(None, description="Name of the user")
    archiveid: Optional[str] = Field(None, description="The archive ID of the user")
    sid: Optional[str] = Field(None, description="The chat ID of the conversation")
    smsgid: Optional[str] = Field(None, description="The message ID of the chat")
    user_query: str = Field(..., description="The user query")
    previous_response_id: Optional[str] = Field(None, description="The previous response ID")
    conversation_id: Optional[str] = Field(None, description="The conversation ID")
