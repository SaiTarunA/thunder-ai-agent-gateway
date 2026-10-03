from datetime import datetime
from typing import Any

from app.features.search.indexing.models import SearchMessage

from app.features.search.domain.enums import ChannelType


TEAM_STREAM_TYPE_MAP = {
    0: ChannelType.DM.value,
    1: ChannelType.DISPLAY_CHANNEL.value,
    2: ChannelType.PRIVATE_CHANNEL.value,
    3: ChannelType.COMPANY_CHANNEL.value,
    4: ChannelType.PUBLIC_CHANNEL.value,
    5: ChannelType.TEMPORARY_CHANNEL.value,
}


def _normalize_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        return datetime.fromisoformat(value)

    raise ValueError(f"Unsupported datetime value: {value!r}")


def _is_zero_datetime(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return value.startswith("0000-00-00")

    if isinstance(value, datetime):
        return value.year == 1 and value.month == 1 and value.day == 1

    return False


def map_message_row(row: dict[str, Any]) -> SearchMessage:
    msgtype = int(row["msgtype"])
    message_id = int(row["smsgid"])
    commentvia = int(row.get("commentvia") or 0)

    is_thread_reply = msgtype == 20

    thread_root_id = (
        commentvia if is_thread_reply and commentvia else message_id
    )

    parent_message_id = (
        commentvia if is_thread_reply and commentvia else None
    )

    created_at = _normalize_datetime(row["messagetime"])

    editedon = row.get("editedon")
    is_edited = not _is_zero_datetime(editedon)

    updated_at = (
        _normalize_datetime(editedon)
        if is_edited
        else created_at
    )

    teamstreamtype = row.get("teamstreamtype")

    channel_type = (
        TEAM_STREAM_TYPE_MAP.get(int(teamstreamtype))
        if teamstreamtype is not None
        else None
    )
    first_name = (row.get("firstname") or "").strip()
    last_name = (row.get("lastname") or "").strip()

    author_name = " ".join(
        part for part in (first_name, last_name) if part
    ) or None

    return SearchMessage(
        site_id=int(row["siteid"]),
        sid=int(row["sid"]),
        channel_type=channel_type,
        channel_name=row.get("channel_name"),
        message_id=message_id,
        parent_message_id=parent_message_id,
        thread_root_id=thread_root_id,
        text=row.get("message") or "",
        message_type=str(msgtype),
        author_archive_id=int(row["archiveid"]),
        author_username=row.get("username") or "",
        author_name=author_name,
        created_at=created_at,
        updated_at=updated_at,
        is_thread_reply=is_thread_reply,
        is_deleted=bool(row["isdeleted"]),
        is_edited=is_edited,
        is_pinned=bool(row.get("pinstatus", 0)),
    )