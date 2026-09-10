import asyncio
import logging
import aiofiles
import aiohttp
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

from app.core import constants

GATEKEEPER_URL = os.getenv("GATEKEEPER_URL")
GATEKEEPER_FALL_BACK_URL = os.getenv("GATEKEEPER_FALL_BACK_URL")
GATEKEEPER_AUTH_TOKEN = os.getenv("GATEKEEPER_AUTH_TOKEN")
TYPE_GREETING = "GREETING"

DEFAULT_GATEKEEPER_HEADERS = {
    "accept-charset": "UTF-8",
    "res": "720",
    "Connection": "Keep-Alive",
    "Content-Type": "text/plain",
    "CLIENTTYPE": constants.WS_CLIENT_TYPE,
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
    file_data: FileData,
    xauthtoken: str,
    session: aiohttp.ClientSession,
    target_dir: Path | str,
    agent_id: str,
    *,
    clienttype: Optional[str] = constants.WS_CLIENT_TYPE,
    authkey: Optional[str] = None,
    version: Optional[str | int] = 0,
    res: Optional[str | int] = None,
) -> bool:
    headers = {
        **DEFAULT_GATEKEEPER_HEADERS,
        "X-Auth-Token": xauthtoken,
        "filename": file_data.filename,
        "PUBLICLINK": file_data.public_link,
    }

    if clienttype:
        headers["CLIENTTYPE"] = clienttype
    if authkey:
        deviceNameAndSystemId = authkey.replace("-", "")
        headers["ws_device_name"] = deviceNameAndSystemId
        headers["ws_system_id"] = deviceNameAndSystemId
    if version is not None:
        headers["version"] = str(version)
    if res is not None:
        headers["res"] = str(res)

    # Internal function to allow retry logic on the GET request
    async def _get(url: str):
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30000)
        ) as response:
            if response.status != 200:
                err_text = ""
                try:
                    err_text = await response.text()
                except Exception:
                    pass
                raise aiohttp.ClientResponseError(
                    response.request_info, response.history, status=response.status, message=err_text
                )
            return await response.read()

    url = f"{GATEKEEPER_URL}/{constants.WS_GATEKEEPER}/{agent_id}"

    try:
        logger.info(f"Gatekeeper URL: {url}")
        file_content = await fetch_with_retry(_get, url)
    except Exception as primary_err:
        logger.warning(
            f"Primary Gatekeeper URL failed 3 times: {primary_err}. "
            f"Attempting fallback to: {GATEKEEPER_FALL_BACK_URL}"
        )
        if not GATEKEEPER_FALL_BACK_URL:
            logger.error(f"No fallback Gatekeeper URL configured. Primary error: {primary_err}")
            return False

        try:
            fallback_url = f"{GATEKEEPER_FALL_BACK_URL}/{constants.WS_GATEKEEPER}/{agent_id}"
            file_content = await fetch_with_retry(_get, fallback_url)
        except Exception as fallback_err:
            logger.error(
                f"Network error fetching {file_data.filename} from both primary and fallback URLs. "
                f"Primary error: {primary_err}, Fallback error: {fallback_err}"
            )
            return False

    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)
    outfile_path = target_path / file_data.filename

    try:
        async with aiofiles.open(outfile_path, "wb") as f:
            await f.write(file_content)
            logger.info(f"Saved file to {outfile_path} ({len(file_content)} bytes)")

        return True
    except Exception as e:
        logger.exception(f"An error occurred while saving file: {e}")
        return False
