"""Communication tools. Every action runs in the caller's browser app, so each tool is a round trip:
the backend sends a `client_request` over the LiveKit data channel and waits for the matching
`client_response` (see CallContext.client_request). Shared tools (update_task, note_fact,
transfer_to_agent) are handled in call.py.

Guards return plain-language errors so the agent knows which step is missing, and nothing is sent
to the app unless the contact came from a search in this call (or the caller dictated and
confirmed a raw number).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

MAX_RESULTS = 5
MIN_SMS_DIGITS = 7  # SMS needs a full phone number; shorter digit strings are extensions
CLIENT_TIMEOUT = 10.0  # seconds to wait for the app before telling the caller it failed

ClientRequest = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class ClientError(Exception):
    """The app answered with ok=false (or could not be reached)."""


@dataclass
class ToolOutcome:
    text: str  # what the model sees
    end_session: bool = False  # True once a call has been handed to the softphone


@dataclass
class ContactBook:
    """Contacts returned by search_contacts in this call, keyed by contact_id."""

    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)


BOOKS: dict[str, ContactBook] = {}  # call_id -> ContactBook; in memory like the call store


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _digits(text: Any) -> str:
    return re.sub(r"[^\d+*#]", "", str(text))


def _describe(c: dict[str, Any]) -> str:
    nums = [f"{n.get('label', 'number')} {n.get('value', '')}" for n in c.get("numbers", [])]
    if c.get("extension"):
        nums.insert(0, f"extension {c['extension']}")
    extra = f" ({', '.join(nums)})" if nums else ""
    if not c.get("numbers"):
        extra += " [no phone number, cannot receive SMS]"
    chat = "can chat" if c.get("can_chat") else "no chat"
    return f"{c.get('name', 'Unknown')}{extra}, {chat}, contact_id {c.get('contact_id')}"


def _exact_matches(query: str, contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contacts the app marked as an exact match; falls back to a full-name comparison."""
    if any("match" in c for c in contacts):
        return [c for c in contacts if c.get("match") == "exact"]
    return [c for c in contacts if _norm(c.get("name")) == _norm(query)]


def _format_search(query: str, contacts: list[dict[str, Any]]) -> str:
    if not contacts:
        return f"NO MATCH for '{query}' in the contacts."
    shown = contacts[:MAX_RESULTS]
    exact = _exact_matches(query, shown)
    if len(exact) == 1:
        return "EXACT MATCH: " + _describe(exact[0]) + ". No need to ask the caller to confirm."
    if len(shown) == 1:
        return (
            "ONE POSSIBLE MATCH (it sounds similar, not identical), ask the caller to confirm: "
            + _describe(shown[0])
        )
    return (
        f"{len(shown)} POSSIBLE MATCHES, best first (some only sound similar). Read the names and "
        "ask which one: " + "; ".join(f"{i}. {_describe(c)}" for i, c in enumerate(shown, 1))
    )


_INSTRUCTION_START = re.compile(
    r"^\s*(please\s+)?("
    r"say\s+(that|to)\b|"
    r"tell\s+(him|her|them)\b|tell\s+(?!me\b)\w+\s+(that|to|about)\b|"
    r"let\s+(him|her|them|\w+)\s+know\b|"
    r"inform\s+(him|her|them|\w+)\b|"
    r"ask\s+(him|her|them|\w+)\s+(to|if|whether|about)\b|"
    r"(send|write)\s+(a\s+)?(message|text|sms|chat)\b|"
    r"(message|text)\s+(him|her|them)\b"
    r")",
    re.IGNORECASE,
)


def _unframed(message: str) -> str | None:
    """An error for the model when the text is still the caller's instruction, not a message."""
    if _INSTRUCTION_START.match(message):
        return (
            "That text is still the caller's instruction, not a message. Rewrite it as a short "
            "message addressed directly to the recipient in the caller's voice (drop wording like "
            "'say that' or 'tell him', keep all facts and times) and call again."
        )
    return None


def _known_contact(book: ContactBook, args: dict) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (contact, error). contact is None when the caller dictated a raw number."""
    cid = str(args.get("contact_id") or "").strip()
    if cid:
        contact = book.by_id.get(cid)
        if not contact:
            return None, (
                "That contact_id did not come from a search in this call. "
                "Call search_contacts first and use the contact_id it returns."
            )
        return contact, None
    if not args.get("confirmed"):
        return None, (
            "No contact_id given. Search the contacts first, or, if the caller gave you the number "
            "themselves, call again with confirmed true."
        )
    if not _digits(args.get("number", "")):
        return None, "No number given. Ask the caller for the number."
    return None, None


def _key(number: Any) -> str:
    """Digits only, so '+1 555-010-0199' and '15550100199' are the same number."""
    return re.sub(r"\D", "", str(number))


def _phone_numbers(contact: dict[str, Any]) -> dict[str, str]:
    """Phone numbers and DIDs only (SMS cannot go to an extension): digits -> stored value."""
    return {_key(n["value"]): str(n["value"]) for n in contact.get("numbers", []) if _key(n.get("value", ""))}


def _contact_numbers(contact: dict[str, Any]) -> dict[str, str]:
    """Everything dialable: phone numbers plus the extension (digits -> stored value)."""
    values = _phone_numbers(contact)
    if _key(contact.get("extension", "")):
        values[_key(contact["extension"])] = str(contact["extension"])
    return values


async def search_contacts(call_id: str, args: dict, request: ClientRequest) -> ToolOutcome:
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolOutcome("Ask the caller who they mean.")
    data = await request("search_contacts", {"query": query, "limit": MAX_RESULTS})
    contacts = [c for c in data.get("contacts", []) if c.get("contact_id") is not None]
    book = BOOKS.setdefault(call_id, ContactBook())
    contacts = contacts[:MAX_RESULTS]  # the app already ranks best first
    for c in contacts:
        c["contact_id"] = str(c["contact_id"])
        book.by_id[c["contact_id"]] = c
    return ToolOutcome(_format_search(query, contacts))


async def make_call(call_id: str, args: dict, request: ClientRequest) -> ToolOutcome:
    contact, err = _known_contact(BOOKS.setdefault(call_id, ContactBook()), args)
    if err:
        return ToolOutcome(err)
    number = _digits(args.get("number", ""))
    if not number:
        return ToolOutcome("No number to dial. Ask the caller, or pick one from the search result.")
    if contact:
        known = _contact_numbers(contact)
        if _key(number) not in known:
            return ToolOutcome(
                f"{contact.get('name')} has no number {number}. Use one of: " + ", ".join(known.values())
            )
        number = known[_key(number)]
    name = (contact or {}).get("name") or args.get("display_name") or number
    await request(
        "make_call",
        {
            "contact_id": contact["contact_id"] if contact else None,
            "display_name": name,
            "number": number,
            "number_type": args.get("number_type", "phone"),
        },
    )
    return ToolOutcome(
        f"The call to {name} has been started and the session is ending. Say nothing.",
        end_session=True,
    )


async def send_sms(call_id: str, args: dict, request: ClientRequest) -> ToolOutcome:
    contact, err = _known_contact(BOOKS.setdefault(call_id, ContactBook()), args)
    if err:
        return ToolOutcome(err)
    message = str(args.get("message", "")).strip()
    if not message:
        return ToolOutcome("There is no message text yet. Ask the caller what to say.")
    if err := _unframed(message):
        return ToolOutcome(err)
    number = _digits(args.get("number", ""))
    if contact:
        phones = _phone_numbers(contact)
        if not phones:
            return ToolOutcome(
                f"{contact.get('name')} has no phone number or DID, and SMS cannot go to an "
                "extension. Tell the caller and ask who else to message."
            )
        if _key(number) not in phones:
            return ToolOutcome(
                f"{contact.get('name')} has no phone number {number or '(none given)'}. Use one of: "
                + ", ".join(phones.values())
            )
        number = phones[_key(number)]
    elif len(re.sub(r"\D", "", number)) < MIN_SMS_DIGITS:
        return ToolOutcome(
            "That is too short for a phone number (it looks like an extension), and SMS needs a "
            "full phone number. Ask the caller for the full number."
        )
    name = (contact or {}).get("name") or args.get("display_name") or number
    await request(
        "send_sms",
        {
            "contact_id": contact["contact_id"] if contact else None,
            "display_name": name,
            "number": number,
            "message": message,
        },
    )
    return ToolOutcome(f"SMS sent to {name}.")


async def send_chat(call_id: str, args: dict, request: ClientRequest) -> ToolOutcome:
    book = BOOKS.setdefault(call_id, ContactBook())
    contact = book.by_id.get(str(args.get("contact_id") or "").strip())
    if not contact:
        return ToolOutcome(
            "That contact_id did not come from a search in this call. "
            "Call search_contacts first and use the contact_id it returns."
        )
    if not contact.get("can_chat"):
        return ToolOutcome(
            f"{contact.get('name')} cannot receive chat messages. Tell the caller in one sentence; "
            "do not suggest alternatives."
        )
    message = str(args.get("message", "")).strip()
    if not message:
        return ToolOutcome("There is no message text yet. Ask the caller what to say.")
    if err := _unframed(message):
        return ToolOutcome(err)
    await request(
        "send_chat",
        {"contact_id": contact["contact_id"], "display_name": contact["name"], "message": message},
    )
    return ToolOutcome(f"Chat message sent to {contact['name']}.")


CLIENT_TOOLS: dict[str, Callable[[str, dict, ClientRequest], Awaitable[ToolOutcome]]] = {
    fn.__name__: fn for fn in (search_contacts, make_call, send_sms, send_chat)
}


async def run_client_tool(
    call_id: str, name: str, args: dict, request: ClientRequest
) -> ToolOutcome:
    fn = CLIENT_TOOLS.get(name)
    if fn is None:
        return ToolOutcome(f"Unknown tool {name}.")
    return await fn(call_id, args, request)
