from __future__ import annotations

from charset_normalizer import from_bytes


def decode_text(data: bytes) -> str:
    """Decode raw bytes to text for native, non-binary formats.

    UTF-8 is tried first, since it's the common case and doesn't
    require pulling in a detection library. For anything else,
    charset-normalizer identifies the actual encoding rather than
    guessing or failing outright.
    """

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    match = from_bytes(data).best()

    if match is None:
        raise ValueError("Unable to determine the text encoding.")

    return str(match)
