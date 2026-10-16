"""Google Drive search request and response shapes, and the structured
replies the search models are held to (sent as strict tool schemas via
`app.ai.tools.pydantic_model_to_openai_tool`)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------


class DriveSearchArgs(BaseModel):
    """The drive_search intent's arguments, as the intent router fills them."""

    query: str = Field(
        ...,
        description=(
            "What the user is looking for, in their own words as typed (for a follow-up, the original request), "
            "without greetings or the instruction to search Drive."
        ),
    )
    confirm_source: bool = Field(
        ...,
        description=(
            "true when the user named no place for the file (no Drive, no chat or channel) and a Drive is "
            "connected; the backend then asks whether to search Drive or chats. Otherwise false."
        ),
    )
    source_question: str = Field(
        ...,
        description=(
            "When confirm_source is true: one short question in the user's language asking whether to look in "
            "their Google Drive or in their chats. Otherwise an empty string."
        ),
    )


class DriveSearchParams(BaseModel):
    """A Drive search the caller already knows it wants: Luna's reqtype
    "drive_search", or the follow-up to a file picker."""

    query: str = Field("", description="The question being answered.")
    chosen_file_id: Optional[str] = Field(
        None, description="A file picked from a previous result's alternatives; skips discovery."
    )
    typed_filename: Optional[str] = Field(
        None, description="A filename typed instead of picking one; resolved by the filename ladder."
    )


class DriveSearchRequest(DriveSearchParams):
    """Body of POST /api/v1/gdrive/search. The billing fields default to the
    caller's JWT subject when absent."""

    siteid: Optional[str | int] = Field(None, description="The ID of the site making the request")
    sitename: Optional[str] = Field(None, description="The name of the site making the request")
    agentid: Optional[str] = Field(None, description="The ID/Email of the agent making the request")
    timezone: Optional[str] = Field(None, description="The user's IANA timezone, for dates in the question")


# ---------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------


class FileOption(BaseModel):
    """One file: a returned document, or an option to pick from."""

    file_id: str = Field(..., description="The Drive file ID")
    file_name: str = Field(..., description="The file's name in Drive")
    folder: str = Field("", description="The file's folder path, when known")
    mime_type: str = Field("", description="The file's MIME type")
    modified_time: str = Field("", description="When the file last changed (RFC 3339)")
    web_view_link: str = Field("", description="Where to open the file in Drive")
    score: float = Field(0.0, description="Relevance, 0-10, when the file was scored")
    excerpt: str = Field("", description="The passage the reranker read: where the question's words cluster")
    matched_in: str = Field(
        "", description='"name" or "contents" when a search on the words as typed matched this file, else ""'
    )


class Citation(BaseModel):
    """A document the answer cites."""

    n: int = Field(..., description="The number the answer cites it by, [n]: its position in documents, from 1")
    file_id: str = Field(..., description="The Drive file ID")
    file_name: str = Field(..., description="The file's name in Drive")
    folder: str = Field("", description="The file's folder path, when known")
    web_view_link: str = Field("", description="Where to open the file in Drive")


class DriveSearchResult(BaseModel):
    query: str = Field(..., description="The question that was searched")
    verdict: Literal["confident", "uncertain", "lost", "none"] = Field(
        "none",
        description=(
            "confident: the documents answer the question; uncertain: they are a best guess; lost: nothing "
            "clearly matched, so alternatives are the closest files to pick from (or a filename to type); "
            "none: nothing in the Drive matched"
        ),
    )
    documents: list[FileOption] = Field(default_factory=list, description="The matching files, best first")
    answer: str = Field(
        "",
        description=(
            "The reply to show: the question answered from the documents, each fact cited as [n], the n-th "
            "document; or, with no documents to answer from, a question back (which of the alternatives, or "
            "what the file was called or held). Empty only when the answer call failed"
        ),
    )
    citations: list[Citation] = Field(
        default_factory=list, description="The documents the answer cites, in order of first citation"
    )
    alternatives: list[FileOption] = Field(default_factory=list, description="Files to pick from when lost")
    exact_matches: list[FileOption] = Field(
        default_factory=list,
        description="Files containing the words exactly as typed that the ranked documents do not already show",
    )
    resolution_tier: str = Field(
        "",
        description="For a typed filename, the tier that matched (exact, contains, fuzzy, semantic)",
    )
    error: str = Field("", description="Why nothing could be returned, when that is known")


# ---------------------------------------------------------------------
# Structured model replies
#
# These are sent to the model, so changing a field or its description
# changes the prompt.
# ---------------------------------------------------------------------


class QueryUnderstanding(BaseModel):
    term_groups: list[list[str]] = Field(..., description="2-4 groups; each is ANDed together.")
    intent: Literal["entity", "topic", "corpus"]
    file_type: Literal["any", "document", "spreadsheet", "presentation", "pdf", "image"]
    date_from: str = Field(..., description="YYYY-MM-DD or empty.")
    date_to: str = Field(..., description="YYYY-MM-DD or empty.")
    date_field: Literal["modified", "created", "viewed"]
    ownership: Literal["any", "mine", "shared"]
    person: str
    folder: str
    starred: bool
    order: Literal["none", "newest", "oldest", "largest"]


class QueryUnderstandingWithKeywords(QueryUnderstanding):
    keywords: list[str] = Field(..., description="Up to 4 words from the question, exactly as typed.")


class FilenameMatch(BaseModel):
    index: int
    confidence: Literal["high", "medium", "low"]


class FilenameMatches(BaseModel):
    items: list[FilenameMatch]


class DriveAnswer(BaseModel):
    """The answer stage's reply."""

    answer: str = Field(..., description="The reply to the person, each fact cited as [n]")
    found: bool = Field(
        ...,
        description=(
            "true when the files hold the answer, or are the file or list the question asks for; false when "
            "they do not, and the reply says so"
        ),
    )
