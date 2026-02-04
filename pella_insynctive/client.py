from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

LineCallback = Callable[[str], Awaitable[None]]


@dataclass
class TelnetClientConfig:
    host: str
    port: int
    reconnect_min_seconds: int = 2
    reconnect_max_seconds: int = 60


class TelnetClient:
    def __init__(self, cfg: TelnetClientConfig, on_line: LineCallback):
        self._cfg = cfg
        self._on_line = on_line

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._write_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="pella_insynctive_telnet")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._close()

    async def send(self, command: str) -> None:
        line = command.strip()
        if not line:
            return
        async with self._write_lock:
            if not self._writer:
                _LOGGER.debug("TX dropped (not connected): %s", line)
                return
            self._writer.write((line + "\r\n").encode("utf-8", errors="ignore"))
            try:
                await self._writer.drain()
                _LOGGER.debug("TX: %s", line)
            except Exception as err:
                _LOGGER.debug("TX failed, closing: %s", err)
                await self._close()

    async def _run(self) -> None:
        backoff = self._cfg.reconnect_min_seconds
        while not self._stop.is_set():
            try:
                await self._connect()
                backoff = self._cfg.reconnect_min_seconds
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Telnet loop error: %s", err)

            self._connected.clear()
            await self._close()

            if self._stop.is_set():
                break

            _LOGGER.info("Reconnecting in %ss", backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._cfg.reconnect_max_seconds)

    async def _connect(self) -> None:
        _LOGGER.info("Connecting to %s:%s", self._cfg.host, self._cfg.port)
        self._reader, self._writer = await asyncio.open_connection(self._cfg.host, self._cfg.port)
        self._connected.set()
        _LOGGER.info("Connected")

    async def _close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected.clear()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while not self._stop.is_set():
            raw = await self._reader.readline()
            if not raw:
                raise ConnectionError("Socket closed")

            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            _LOGGER.debug("RX: %s", line)
            await self._on_line(line)
