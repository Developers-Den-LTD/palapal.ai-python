"""
Safe download of the optional reference photo for AI social content.

The URL comes from the caller, so it is treated as hostile: https only, every host
(including each redirect) must resolve to public addresses only, which blocks the
EC2 metadata address and anything on the private network. The body is streamed
with a hard size cap and its type is checked by magic bytes, not by headers.
"""

import ipaddress
import socket
import struct
from urllib.parse import urljoin, urlsplit

import httpx

from services.logger_services import logger
from services.social_content.constants import (
    MAX_REFERENCE_BYTES,
    MAX_REFERENCE_REDIRECTS,
    REFERENCE_TIMEOUT_SECONDS,
)

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def is_safe_reference_host(ip: str) -> bool:
    """True only for a public unicast address."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def sniff_image_type(data: bytes) -> str | None:
    """MIME type from magic bytes for JPEG, PNG and WebP; None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) read from a PNG, JPEG or WebP header; None when it cannot be read."""
    try:
        kind = sniff_image_type(data)
        if kind == "image/png" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if kind == "image/webp" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if chunk == b"VP8 ":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return None
        if kind == "image/jpeg":
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker == 0xFF:  # fill byte before a marker
                    index += 1
                    continue
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                segment_length = struct.unpack(">H", data[index + 2 : index + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    return width, height
                index += 2 + segment_length
        return None
    except struct.error:
        return None


def _assert_public_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("The reference photo link must be an https link")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("The reference photo link could not be reached") from exc
    addresses = {info[4][0] for info in infos}
    unsafe = [address for address in addresses if not is_safe_reference_host(address)]
    if not addresses or unsafe:
        logger.warning(
            "social_content [reference]: blocked non-public host — "
            f"host='{parts.hostname}', addresses={sorted(addresses)}"
        )
        raise ValueError("The reference photo link points to an address that is not allowed")


def download_reference_image(url: str) -> dict:
    """
    Download and check the reference photo.

    Returns {"data", "mime_type", "bytes"}; raises ValueError with a plain message
    for every expected failure so the caller can report it to the user.
    """
    current_url = url
    try:
        with httpx.Client(timeout=REFERENCE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            for _ in range(MAX_REFERENCE_REDIRECTS + 1):
                _assert_public_host(current_url)
                with client.stream("GET", current_url) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("The reference photo link redirected nowhere")
                        current_url = urljoin(current_url, location)
                        logger.info(
                            f"social_content [reference]: following redirect — to='{current_url}'"
                        )
                        continue

                    if response.status_code != 200:
                        raise ValueError(
                            f"The reference photo could not be downloaded (HTTP {response.status_code})"
                        )

                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_REFERENCE_BYTES:
                        raise ValueError("The reference photo is larger than 10 MB")

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_REFERENCE_BYTES:
                            raise ValueError("The reference photo is larger than 10 MB")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    break
            else:
                raise ValueError("The reference photo link redirected too many times")
    except httpx.HTTPError as exc:
        logger.warning(f"social_content [reference]: download failed — url='{current_url}', error={exc}")
        raise ValueError("The reference photo could not be downloaded") from exc

    mime_type = sniff_image_type(data)
    if mime_type is None:
        raise ValueError("The reference photo must be a JPEG, PNG or WebP image")

    logger.info(
        "social_content [reference]: downloaded — "
        f"bytes={len(data)}, mime_type='{mime_type}', dimensions={image_dimensions(data)}"
    )
    return {"data": data, "mime_type": mime_type, "bytes": len(data)}
