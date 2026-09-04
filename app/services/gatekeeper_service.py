import asyncio
import logging
import aiofiles
import aiohttp
from pathlib import Path
from pydantic import BaseModel

import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

GATEKEEPER_API_URL = os.getenv("GATEKEEPER_API_URL")
GATEKEEPER_API_FALL_BACK_URL = os.getenv("GATEKEEPER_API_FALL_BACK_URL")
GATEKEEPER_AUTH_TOKEN = os.getenv("GATEKEEPER_AUTH_TOKEN")
TYPE_GREETING = "GREETING"

DEFAULT_GATEKEEPER_HEADERS = {
    "accept-charset": "UTF-8",
    "res": 720,
    "Connection": "Keep-Alive",
    "Content-Type": "text/plain",
    "Transfer-Encoding": "chunked",
    "CLIENTTYPE": "Web",
}


class FileData(BaseModel):
    filename: str
    public_link: str


async def fetch_with_retry(func, *args, **kwargs):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as e:
            if attempt == max_retries - 1:
                raise e
            wait = (attempt + 1) * 2
            logger.warning(f"Attempt {attempt + 1} failed, retrying in {wait}s: {e}")
            await asyncio.sleep(wait)


async def fetch_and_save_data(
    file_data: FileData, xauthtoken: str, session: aiohttp.ClientSession, target_dir: Path
) -> bool:
    headers = {
        **DEFAULT_GATEKEEPER_HEADERS,
        "X-Auth-Token": xauthtoken,
        "filename": file_data.filename,
        "PUBLICLINK": file_data.public_link,
    }

    # Internal function to allow retry logic on the GET request
    async def _get(url: str):
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30000)
        ) as response:
            if response.status != 200:
                raise aiohttp.ClientResponseError(
                    response.request_info, response.history, status=response.status
                )
            return await response.read()

    try:
        logger.info(f"Gatekeeper URL: {GATEKEEPER_API_URL}")
        file_content = await fetch_with_retry(_get, GATEKEEPER_API_URL)
    except Exception as primary_err:
        logger.warning(
            f"Primary Gatekeeper URL failed 3 times: {primary_err}. "
            f"Attempting fallback to: {GATEKEEPER_API_FALL_BACK_URL}"
        )
        try:
            file_content = await fetch_with_retry(_get, GATEKEEPER_API_FALL_BACK_URL)
        except Exception as fallback_err:
            logger.error(
                f"Network error fetching {file_data.filename} from both primary and fallback URLs. "
                f"Primary error: {primary_err}, Fallback error: {fallback_err}"
            )
            return False

    outfile_path = target_dir / file_data.filename

    try:
        async with aiofiles.open(outfile_path, "wb") as f:
            await f.write(file_content)
            logger.info(f"Saved file to {outfile_path}")

        return True
    except Exception as e:
        logger.exception(f"An error occurred while saving file: {e}")
        return False
