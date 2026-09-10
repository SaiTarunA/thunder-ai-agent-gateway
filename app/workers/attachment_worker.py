import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp

from app.core import constants
from app.core.message_cleaner import filter_conversations
from app.core.utils import Utils
from app.services.gatekeeper_service import FileData, fetch_and_save_data

logger = logging.getLogger(__name__)

TEMP_ATTACHMENT_DIR = Path(__file__).resolve().parents[2] / "temp_attachment_dir"

NON_READABLE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".ico", ".svg",
    ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".dmg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}

READABLE_TEXT_EXTENSIONS = {
    ".txt", ".log", ".csv", ".json", ".xml", ".yaml", ".yml", ".md", ".rst",
    ".html", ".htm", ".py", ".js", ".ts", ".c", ".cpp", ".h", ".sh", ".sql",
    ".properties", ".ini", ".cfg", ".conf", ".env",
}

BINARY_MAGIC_BYTES = (
    b"\x00\x00\x00 ftyp",
    b"\x00\x00\x00\x14ftyp",
    b"\x00\x00\x00\x18ftyp",
    b"\x00\x00\x00\x1cftyp",
    b"\x00\x00\x00\x20ftyp",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"PK\x03\x04",
    b"\x7fELF",
    b"MZ",
)


def get_attachment_details(item: Any) -> Dict[str, Any]:
    """
    Extracts and normalizes attachment details from an item's message.

    Args:
        item: A dictionary or object containing a 'message' field (JSON string, dict, or list).

    Returns:
        dict:
            attachments (list): List of valid attachment dictionaries.
            has_attachments (bool): Pythonic snake_case alias.
    """
    try:
        message = None
        if isinstance(item, dict):
            message = item.get("message")
        elif item is not None:
            message = getattr(item, "message", None)

        parsed = Utils.safe_parse_json(message)
        attachments: List[Any] = []

        if isinstance(parsed, list):
            attachments.extend(parsed)
        elif isinstance(parsed, dict):
            msg = parsed.get("msg")
            if isinstance(msg, list):
                attachments.extend(msg)
            elif isinstance(msg, dict):
                attachments.append(msg)
            else:
                attachments.append(parsed)

        valid_attachments: List[Dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue

            att = dict(attachment)
            if not att.get("link"):
                file_id_as_link = str(att.get("fileid") or att.get("fileId") or "")
                if file_id_as_link:
                    att["link"] = file_id_as_link

            link_str = str(att.get("link") or "")
            if link_str:
                valid_attachments.append(att)

        has_attachments = len(valid_attachments) > 0

        return {
            "attachments": valid_attachments,
            "has_attachments": has_attachments,
        }
    except Exception as e:
        logger.exception(f"An error occurred while getting attachment details: {e}")
        return {
            "attachments": [],
            "has_attachments": False,
        }


def is_readable_text_file(file_path: Path) -> Optional[str]:
    """
    Checks if a file is a plain text or general readable file, and returns its content.
    Returns None if binary, non-readable, or empty.
    """
    try:
        if not file_path.exists() or not file_path.is_file():
            return None

        if file_path.stat().st_size == 0:
            return None

        ext = file_path.suffix.lower()

        # Ignore known binary media and document extensions
        if ext in NON_READABLE_EXTENSIONS:
            return None

        with open(file_path, "rb") as f:
            header = f.read(64)
            f.seek(0)
            raw_bytes = f.read(1_000_000)

        # Check for explicit binary magic signatures (e.g. MP4, PNG, JPEG, ZIP, PDF)
        if any(header.startswith(sig) or sig in header[:32] for sig in BINARY_MAGIC_BYTES):
            return None

        # Attempt decodings with error tolerance
        decoded_text = None
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1", "cp1252"):
            try:
                decoded_text = raw_bytes.decode(encoding, errors="ignore")
                if decoded_text:
                    break
            except Exception:
                continue

        if not decoded_text:
            decoded_text = raw_bytes.decode("latin-1", errors="ignore")

        # Strip null bytes and retain printable text and standard whitespace
        cleaned = "".join(c for c in decoded_text if c != "\x00" and (c.isprintable() or c in "\n\r\t"))
        trimmed = cleaned.strip()

        if trimmed and len(trimmed) > 5:
            return trimmed

        return None

    except Exception as e:
        logger.warning(f"Error reading attachment file {file_path}: {e}")
        return None


async def process_messages_attachments(
    user_chats: List[Any],
    request_data: dict,
    target_dir: Path,
    max_concurrent: int = 5,
) -> List[dict]:
    """
    Processes a list of chat messages, identifying attachments, downloading them in parallel
    batches using a Semaphore, and extracting readable text content.

    Returns:
        List[dict]: Formatted conversation items ready for filter_conversations.
    """
    if not user_chats:
        return []

    agentid = str(request_data.get("agentid") or "").strip()
    xauthtoken = str(request_data.get("x_auth_token") or "")
    clienttype = str(request_data.get("clienttype") or constants.WS_CLIENT_TYPE)
    authkey = request_data.get("authkey")

    prepared_items: List[dict] = []
    download_tasks: List[dict] = []

    for chat in user_chats:
        username = getattr(chat, "username", "")
        firstname = getattr(chat, "firstname", "") or ""
        lastname = getattr(chat, "lastname", "") or ""
        messagetime = getattr(chat, "messagetime", None)

        user_display = f"{firstname} {lastname}".strip() or Utils.extract_user_name(username)
        timestamp_str = messagetime.strftime("%Y-%m-%d %H:%M:%S") if messagetime else ""

        message_val = getattr(chat, "message", None)
        parsed_json = Utils.safe_parse_json(message_val)
        is_json_message = isinstance(parsed_json, (dict, list))

        if is_json_message:
            attachment_details = get_attachment_details(chat)
            attachments = attachment_details.get("attachments", [])
            has_attachments = attachment_details.get("has_attachments", False)

            if has_attachments and attachments:
                att_items = []
                for att in attachments:
                    link = str(att.get("link") or "").strip()
                    raw_fname = str(
                        att.get("filename")
                        or att.get("fileName")
                        or att.get("name")
                        or f"file_{link}"
                    ).strip()
                    filename = os.path.basename(raw_fname)

                    if not link or not filename:
                        continue

                    att_item = {
                        "filename": filename,
                        "link": link,
                        "download_key": f"{filename}_{link}",
                    }
                    att_items.append(att_item)
                    download_tasks.append(att_item)

                prepared_items.append({
                    "type": "attachment",
                    "user": user_display,
                    "timestamp": timestamp_str,
                    "attachments": att_items,
                    "username": username,
                })
            else:
                text_val = None
                if isinstance(parsed_json, dict):
                    for k in ("text", "msg", "message"):
                        if isinstance(parsed_json.get(k), str) and parsed_json[k].strip():
                            text_val = parsed_json[k].strip()
                            break

                final_text = text_val or (str(message_val) if message_val else None)
                if final_text:
                    prepared_items.append({
                        "type": "text",
                        "user": user_display,
                        "timestamp": timestamp_str,
                        "message": final_text,
                    })
        else:
            if message_val:
                prepared_items.append({
                    "type": "text",
                    "user": user_display,
                    "timestamp": timestamp_str,
                    "message": str(message_val),
                })

    download_results: Dict[str, Optional[str]] = {}

    if download_tasks:
        semaphore = asyncio.Semaphore(max_concurrent)

        async with aiohttp.ClientSession() as session:
            async def download_one(att_task: dict):
                async with semaphore:
                    filename = att_task["filename"]
                    link = att_task["link"]
                    key = att_task["download_key"]

                    file_data = FileData(filename=filename, public_link=link)
                    saved = await fetch_and_save_data(
                        file_data=file_data,
                        xauthtoken=xauthtoken,
                        session=session,
                        target_dir=target_dir,
                        agent_id=agentid,
                        clienttype=clienttype,
                        authkey=authkey,
                    )

                    if saved:
                        downloaded_file = target_dir / filename
                        content = is_readable_text_file(downloaded_file)
                        if content:
                            download_results[key] = content
                        else:
                            logger.info(
                                f"Attachment '{filename}' is not a readable text file, ignoring. agentid : {agentid}"
                            )
                            download_results[key] = None
                    else:
                        logger.warning(
                            f"Failed to fetch attachment '{filename}' from gatekeeper. agentid : {agentid}"
                        )
                        download_results[key] = None

            await asyncio.gather(*(download_one(task) for task in download_tasks))

    conversations: List[dict] = []

    for item in prepared_items:
        if item["type"] == "text":
            conversations.append({
                "timestamp": item["timestamp"],
                "user": item["user"],
                "message": item["message"],
            })
        elif item["type"] == "attachment":
            readable_texts: List[str] = []
            for att in item["attachments"]:
                content = download_results.get(att["download_key"])
                if content:
                    readable_texts.append(content)

            if readable_texts:
                conversations.append({
                    "timestamp": item["timestamp"],
                    "user": item["user"],
                    "message": "\n\n".join(readable_texts),
                })
            else:
                logger.info(
                    f"All attachments ignored for chat entry from {item['username']}, agentid : {agentid}"
                )

    return filter_conversations(conversations)
