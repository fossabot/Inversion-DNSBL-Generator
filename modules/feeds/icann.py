"""
For fetching and scanning URLs from ICANN CZDS
"""

import asyncio
import json
import zlib
from collections.abc import AsyncIterator

from dotenv import dotenv_values
from modules.utils.feeds import generate_hostname_expressions
from modules.utils.http_requests import get_async, get_async_stream, post_async
from modules.utils.log import init_logger

logger = init_logger()


async def _authenticate(username: str, password: str) -> str:
    """Make a POST request for an Access Token from ICANN CZDS. The
    Access Token expires in 24 hours upon receipt.

    Args:
        username (str): ICANN CZDS username
        password (str): ICANN CZDS password

    Returns:
        str: ICANN CZDS Access Token
    """
    authentication_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    credential = {"username": username, "password": password}
    authentication_url = "https://account-api.icann.org/api/authenticate"
    authentication_payload = json.dumps(credential).encode()

    resp = await post_async(
        [authentication_url],
        [authentication_payload],
        headers=authentication_headers,
    )
    body = json.loads(resp[0][1])

    if "accessToken" not in body:
        logger.error("Failed to authenticate ICANN user")

    return body.get("accessToken", "")


async def _get_approved_endpoints(access_token: str) -> list[str]:
    """Download a list of zone file endpoints from ICANN CZDS. Only
    zone files which current ICANN CZDS user has approved access
    to will be listed.

    Args:
        access_token (str): ICANN CZDS Access Token

    Returns:
        list[str]: List of zone file endpoints
    """
    links_url = "https://czds-api.icann.org/czds/downloads/links"
    resp = (
        await get_async(
            [links_url],
            headers={
                "Content-Type": "application/json",
                "Connection": "keep-alive",
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
        )
    )[links_url]

    body = json.loads(resp)
    if not isinstance(body, list):
        logger.warning("No user-accessible zone files found.")
        return []
    return body


async def _get_icann_domains(endpoint: str, access_token: str) -> AsyncIterator[set[str]]:
    """Download domains from ICANN zone file endpoint
    and yield all listed URLs in batches.

    Args:
        endpoint (str): ICANN zone file endpoint
        access_token (str): ICANN CZDS Access Token

    Yields:
        AsyncIterator[set[str]]: Batch of URLs as a set

    """

    url_generator = extract_zonefile_urls(
        endpoint,
        headers={
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Accept": "text/event-stream",
            "Accept-Encoding": "gzip",
            "Authorization": f"Bearer {access_token}",
        },
    )

    try:
        async for batch in url_generator:
            yield generate_hostname_expressions(batch)
    except Exception as error:
        logger.warning("Failed to retrieve ICANN list %s | %s", endpoint, error)
        yield set()


async def extract_zonefile_urls(endpoint: str, headers: dict = None) -> AsyncIterator[list[str]]:
    """Extract URLs from GET request stream of ICANN `txt.gz` zone file

    https://stackoverflow.com/a/68928891

    Args:
        endpoint (str): HTTP GET request endpoint
        headers (dict, optional): HTTP Headers to send with every request.
        Defaults to None.

    Raises:
        aiohttp.client_exceptions.ClientError: Stream disrupted

    Yields:
        AsyncIterator[list[str]]: Batch of URLs as a list
    """
    temp_file = await get_async_stream(endpoint, headers=headers)
    if temp_file is None:
        yield []
    else:
        with temp_file:
            # Decompress and extract URLs from each chunk
            d = zlib.decompressobj(zlib.MAX_WBITS | 32)
            last_line: str = ""

            for chunk in iter(lambda: temp_file.read(1024**2) if temp_file else lambda: b"", b""):
                # Decompress and decode chunk to `current_chunk_string`
                current_chunk_string = d.decompress(chunk).decode()
                # Append `last_line` of previous chunk to
                # front of `current_chunk_string`
                current_chunk_string = f"{last_line}{current_chunk_string}"
                # Split to lines
                lines = current_chunk_string.splitlines()
                # The last line of `lines` is likely incomplete,
                # the rest of it is at the beginning of the next chunk,
                # so pop it out and cache it as `last_line`
                last_line = lines.pop()
                # Yield list of URLs from the cleaned `lines`,
                # ensuring that all of them are lowercase
                yield [url for line in lines if (splitted_line := line.split()) and (url := splitted_line[0].lower().rstrip("."))]

            # Yield last remaining URL from `last_line`
            # if splitted_line has a length of at least 1
            if (splitted_line := last_line.split()) and (url := splitted_line[0].lower().rstrip(".")):
                yield [url]


class ICANN:
    """
    For fetching and scanning URLs from ICANN CZDS
    """

    def __init__(self, parser_args: dict, update_time: int):
        username = str(dotenv_values(".env").get("ICANN_ACCOUNT_USERNAME", ""))
        password = str(dotenv_values(".env").get("ICANN_ACCOUNT_PASSWORD", ""))

        self.db_filenames: list[str] = []
        self.jobs: list[tuple] = []

        if "icann" in parser_args["sources"]:
            access_token = asyncio.get_event_loop().run_until_complete(_authenticate(username, password))
            endpoints: list[str] = asyncio.get_event_loop().run_until_complete(_get_approved_endpoints(access_token))
            self.db_filenames = [f"icann_{url.rsplit('/', 1)[-1].rsplit('.')[-2]}" for url in endpoints]
            if parser_args["fetch"]:
                # Download and Add ICANN URLs to database
                self.jobs = [
                    (
                        _get_icann_domains,
                        update_time,
                        db_filename,
                        {"endpoint": endpoint, "access_token": access_token},
                    )
                    for db_filename, endpoint in zip(self.db_filenames, endpoints)
                ]
