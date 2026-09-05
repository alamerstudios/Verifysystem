"""Kleiner HTTP-Server, damit der Bot auf Render auch als *Web Service* laufen kann.

Render erwartet bei Web Services, dass ein Port geöffnet wird. Läuft der Bot als
*Background Worker*, ist die Umgebungsvariable PORT nicht gesetzt und dieser
Server wird gar nicht erst gestartet.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("verifybot.keepalive")

_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: 21\r\n"
    b"Connection: close\r\n"
    b"\r\n"
    b"Verify-Bot laeuft :)\n"
)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.read(1024), timeout=5)
    except (asyncio.TimeoutError, ConnectionError):
        pass
    try:
        writer.write(_RESPONSE)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def start_keepalive() -> asyncio.AbstractServer | None:
    port = os.getenv("PORT")
    if not port or not port.isdigit():
        return None
    server = await asyncio.start_server(_handle, host="0.0.0.0", port=int(port))
    log.info("Health-Server läuft auf Port %s", port)
    return server
