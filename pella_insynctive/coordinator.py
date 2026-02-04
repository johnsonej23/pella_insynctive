from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import TelnetClient, TelnetClientConfig
from .const import (
    CONF_HOST,
    CONF_PORT,
    DEVICE_GARAGE,
    DEVICE_LOCK,
    DEVICE_SHADE,
    DEVICE_WINDOW_DOOR,
    DEFAULT_BATTERY_POLL_MINUTES,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_RECONNECT_MAX_SECONDS,
    DEFAULT_RECONNECT_MIN_SECONDS,
    DEFAULT_SCAN_ALL_128,
    OPT_BATTERY_POLL_MINUTES,
    OPT_POLL_INTERVAL_SECONDS,
    OPT_RECONNECT_MAX_SECONDS,
    OPT_RECONNECT_MIN_SECONDS,
    OPT_SCAN_ALL_128,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

RE_UNSOL = re.compile(r"^POINTSTATUS-(?P<idx>\d{3}),(?:\$)?(?P<val>[0-9A-Fa-f]{2})$")
RE_HEX_DOLLAR = re.compile(r"\$([0-9A-Fa-f]{2})")
RE_AFTER_COMMA = re.compile(r",\s*(.+)$")


@dataclass
class DeviceInfo:
    index: int
    point_id: str | None
    device_type: int | None
    name: str
    status_hex: str | None
    battery_hex: str | None


class PellaCoordinator(DataUpdateCoordinator[dict[int, DeviceInfo]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry

        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]

        o = entry.options
        self._poll_s = int(o.get(OPT_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS))
        self._battery_poll_min = int(o.get(OPT_BATTERY_POLL_MINUTES, DEFAULT_BATTERY_POLL_MINUTES))
        self._scan_all_128 = bool(o.get(OPT_SCAN_ALL_128, DEFAULT_SCAN_ALL_128))
        self._shade_invert = True  # permanently invert shade positions
        self._client = TelnetClient(
            TelnetClientConfig(
                host=self._host,
                port=self._port,
                reconnect_min_seconds=int(o.get(OPT_RECONNECT_MIN_SECONDS, DEFAULT_RECONNECT_MIN_SECONDS)),
                reconnect_max_seconds=int(o.get(OPT_RECONNECT_MAX_SECONDS, DEFAULT_RECONNECT_MAX_SECONDS)),
            ),
            on_line=self._handle_line,
        )

        self._cmd_lock = asyncio.Lock()
        self._pending: asyncio.Future[str] | None = None
        self._last_cmd: str | None = None

        self._poll_unsub = None
        self._battery_unsub = None

        super().__init__(hass, _LOGGER, name="pella_insynctive", update_interval=None)
        self.data: dict[int, DeviceInfo] = {}

    @property
    def client(self) -> TelnetClient:
        return self._client

    @property
    def bridge_id(self) -> str:
        return f"bridge_{self._host}_{self._port}"

    @property
    def bridge_name(self) -> str:
        return f"Pella Insynctive ({self._host})"

    def point_device_info(self, idx: int) -> dict:
        dev = self.data.get(idx)
        point_key = dev.point_id if dev and dev.point_id else f"point_{idx:03d}"
        return {
            "identifiers": {(DOMAIN, f"{self.bridge_id}_{point_key}")},
            "name": self._device_name_override(dev, idx),
            "manufacturer": "Pella",
            "model": self._device_model(dev),
            "via_device": (DOMAIN, self.bridge_id),
        }


    def _device_name_override(self, dev: DeviceInfo | None, idx: int) -> str:
        key = f"device_name_{idx:03d}"
        v = self.entry.options.get(key)
        if v:
            return str(v)
        return self._format_device_name(dev, idx)

    def _device_area_override(self, idx: int) -> str | None:
        key = f"device_area_{idx:03d}"
        v = self.entry.options.get(key)
        if v:
            return str(v)
        return None

    def _apply_device_overrides_to_registry(self) -> None:
        dev_reg = dr.async_get(self.hass)
        for idx, dev in self.data.items():
            point_key = dev.point_id if dev.point_id else f"point_{idx:03d}"
            identifiers = {(DOMAIN, f"{self.bridge_id}_{point_key}")}
            ha_dev = dev_reg.async_get_device(identifiers=identifiers)
            if not ha_dev:
                continue

            updates = {}
            new_name = self._device_name_override(dev, idx)
            if new_name and ha_dev.name != new_name:
                updates["name"] = new_name

            area_id = self._device_area_override(idx)
            if area_id is not None and ha_dev.area_id != area_id:
                updates["area_id"] = area_id

            if updates:
                dev_reg.async_update_device(ha_dev.id, **updates)


    def _device_model(self, dev: DeviceInfo | None) -> str:
        # Prefer deriving model from the first two digits of POINTID (serial),
        # per Pella doc mapping (08/18/68/98). Fallback to device_type.
        if dev and dev.point_id and len(dev.point_id) >= 2:
            prefix = dev.point_id[:2]
            if prefix == "08":
                return "Open/Close Sensor"
            if prefix == "18":
                return "Garage Door Sensor"
            if prefix == "68":
                return "Door Lock Sensor"
            if prefix == "98":
                return "Shade/Blind"

        if dev and dev.device_type is not None:
            if dev.device_type == DEVICE_WINDOW_DOOR:
                return "Open/Close Sensor"
            if dev.device_type == DEVICE_GARAGE:
                return "Garage Door Sensor"
            if dev.device_type == DEVICE_LOCK:
                return "Door Lock Sensor"
            if dev.device_type == DEVICE_SHADE:
                return "Shade/Blind"

        return "Insynctive Device"


    @property
    def shade_invert(self) -> bool:
        return self._shade_invert

    def shade_value_to_position(self, value_hex: str | None) -> int | None:
        if not value_hex:
            return None
        try:
            pos = int(value_hex, 16)
        except ValueError:
            return None
        pos = max(0, min(100, pos))
        if self._shade_invert:
            pos = 100 - pos
        return pos

    def position_to_shade_value(self, position: int) -> int:
        pos = max(0, min(100, int(position)))
        if self._shade_invert:
            pos = 100 - pos
        return pos

    async def set_shade_position(self, idx: int, position: int) -> None:
        """Set shade position and refresh state shortly after."""
        await self.pointset(idx, self.position_to_shade_value(position))
        await asyncio.sleep(0.4)
        try:
            resp = await self._query(f"?POINTSTATUS-{idx:03d}", timeout=5.0)
            v = self._parse_status_hex(resp)
            if v is not None and idx in self.data:
                self.data[idx].status_hex = v
                self.async_set_updated_data(self.data)
        except Exception:
            pass

    async def async_start(self) -> None:
        await self._client.start()
        self.hass.async_create_task(self._startup_discovery())

        if self._poll_s > 0:
            self._poll_unsub = async_track_time_interval(self.hass, self._poll_tick, timedelta(seconds=self._poll_s))
        if self._battery_poll_min > 0:
            self._battery_unsub = async_track_time_interval(
                self.hass, self._battery_tick, timedelta(minutes=self._battery_poll_min)
            )

    async def async_stop(self) -> None:
        if self._poll_unsub:
            self._poll_unsub()
            self._poll_unsub = None
        if self._battery_unsub:
            self._battery_unsub()
            self._battery_unsub = None
        await self._client.stop()

    async def _startup_discovery(self) -> None:
        await asyncio.sleep(2)
        if not self._client.is_connected:
            return

        count = 0
        try:
            count_str = await self._query("?POINTCOUNT", timeout=5.0)
            digits = "".join(ch for ch in count_str if ch.isdigit())
            count = int(digits) if digits else 0
        except TimeoutError:
            _LOGGER.warning("Timeout on ?POINTCOUNT; falling back to scan")

        # If POINTCOUNT is 2, we should at least try points 001..002.
        indices = range(1, 129) if (self._scan_all_128 or count == 0) else range(1, min(128, count) + 1)
        _LOGGER.debug("Discovery scanning %s points (POINTCOUNT=%s, scan_all_128=%s)", len(list(indices)), count, self._scan_all_128)

        for i in indices:
            idx = f"{i:03d}"
            try:
                dtype_raw = await self._query(f"?POINTDEVICE-{idx}", timeout=5.0)
                pid_raw = await self._query(f"?POINTID-{idx}", timeout=5.0)
                status_raw = await self._query(f"?POINTSTATUS-{idx}", timeout=5.0)

                # Battery is not included in POINTSTATUS; fetch once during discovery so the sensor
                # doesn't sit at Unknown for hours until the first battery poll interval.
                battery_raw = None
                try:
                    battery_raw = await self._query(f"?POINTBATTERYGET-{idx}", timeout=5.0)
                except TimeoutError:
                    _LOGGER.debug("Timeout querying battery for point %s during discovery", idx)

                device_type = self._parse_device_type(dtype_raw)
                point_id = self._parse_point_id(pid_raw)
                status_hex = self._parse_status_hex(status_raw)

                battery_hex = None
                if battery_raw:
                    m = RE_HEX_DOLLAR.search(battery_raw)
                    if m:
                        battery_hex = f"${m.group(1).upper()}"

                # If we can't parse a device type, still create the device so HA shows it,
                # and logs will tell us what came back.
                name = self._default_name(device_type, i, point_id)

                self.data[i] = DeviceInfo(i, point_id, device_type, name, status_hex, battery_hex)
                _LOGGER.debug("Discovered point %s: type_raw=%s type=%s id_raw=%s id=%s status_raw=%s status=%s",
                              idx, dtype_raw, device_type, pid_raw, point_id, status_raw, status_hex)
            except TimeoutError:
                _LOGGER.debug("Timeout querying point %s; skipping", idx)
                continue
            except Exception as err:
                _LOGGER.debug("Error querying point %s; skipping: %s", idx, err)
                continue

        self.async_set_updated_data(self.data)
        self._apply_device_overrides_to_registry()

    async def _poll_tick(self, _now) -> None:
        if not self._client.is_connected or not self.data:
            return
        for i, dev in list(self.data.items()):
            idx = f"{i:03d}"
            try:
                resp = await self._query(f"?POINTSTATUS-{idx}", timeout=5.0)
                v = self._parse_status_hex(resp)
                if v is not None:
                    dev.status_hex = v
            except TimeoutError:
                _LOGGER.debug("Timeout polling status for point %s", idx)
        self.async_set_updated_data(self.data)

    async def _battery_tick(self, _now) -> None:
        if not self._client.is_connected or not self.data:
            return
        for i, dev in list(self.data.items()):
            idx = f"{i:03d}"
            try:
                resp = await self._query(f"?POINTBATTERYGET-{idx}", timeout=5.0)
                # Battery responses tend to be $xx but may come as POINTBATTERYGET-XXX,$xx
                m = RE_HEX_DOLLAR.search(resp)
                if m:
                    dev.battery_hex = f"${m.group(1).upper()}"
                else:
                    tail = self._after_comma(resp).strip()
                    if tail.startswith("$") and len(tail) == 3:
                        tail = tail[1:]
                    if len(tail) == 2 and all(c in "0123456789abcdefABCDEF" for c in tail):
                        dev.battery_hex = f"${tail.upper()}"
            except TimeoutError:
                _LOGGER.debug("Timeout polling battery for point %s", idx)
        self.async_set_updated_data(self.data)


    async def async_refresh_point_status(self, idx: int) -> None:
        """Refresh a single point's status from the bridge."""
        resp = await self._query(f"?POINTSTATUS-{idx:03d}", timeout=5.0)
        v = self._parse_status_hex(resp)
        if v is not None and idx in self.data:
            self.data[idx].status_hex = v
            self.async_set_updated_data(self.data)

    async def async_refresh_point_battery(self, idx: int) -> None:
        """Refresh a single point's battery from the bridge."""
        resp = await self._query(f"?POINTBATTERYGET-{idx:03d}", timeout=5.0)
        m = RE_HEX_DOLLAR.search(resp)
        if not m:
            tail = self._after_comma(resp).strip()
            if tail.startswith("$") and len(tail) == 3:
                tail = tail[1:]
            if len(tail) == 2 and all(c in "0123456789abcdefABCDEF" for c in tail):
                battery_hex = f"${tail.upper()}"
            else:
                battery_hex = None
        else:
            battery_hex = f"${m.group(1).upper()}"

        if battery_hex is not None and idx in self.data:
            self.data[idx].battery_hex = battery_hex
            self.async_set_updated_data(self.data)

    async def pointset(self, index: int, value_hex: int) -> None:
        idx = f"{index:03d}"
        await self._client.send(f"!POINTSET-{idx},${value_hex:02X}")

    async def _query(self, cmd: str, timeout: float = 5.0) -> str:
        async with self._cmd_lock:
            if not self._client.is_connected:
                raise ConnectionError("Not connected")

            async def _send_and_wait() -> str:
                loop = asyncio.get_running_loop()
                self._pending = loop.create_future()
                self._last_cmd = cmd.strip()
                await self._client.send(cmd)
                return await asyncio.wait_for(self._pending, timeout=timeout)

            try:
                return await _send_and_wait()
            except TimeoutError:
                _LOGGER.debug("Timeout waiting for response to %s; retrying once", cmd)
                return await _send_and_wait()
            finally:
                self._pending = None

    async def _handle_line(self, line: str) -> None:
        # Unsolicited status format: POINTSTATUS-XXX,VV
        m = RE_UNSOL.match(line)
        if m:
            idx = int(m.group("idx"))
            val = m.group("val").upper()
            if idx in self.data:
                self.data[idx].status_hex = val
            else:
                self.data[idx] = DeviceInfo(idx, None, None, f"Pella Device ({idx:03d})", val, None)
            self.async_set_updated_data(self.data)
            return

        # Ignore echoed command lines
        if self._last_cmd and line.strip() == self._last_cmd:
            return

        if self._pending and not self._pending.done():
            self._pending.set_result(line)

    @staticmethod
    def _after_comma(s: str) -> str:
        m = RE_AFTER_COMMA.search(s)
        return m.group(1).strip() if m else s.strip()

    @classmethod
    def _parse_device_type(cls, s: str) -> int | None:
        # Common: "$13" or "POINTDEVICE-001,$13"
        m = RE_HEX_DOLLAR.search(s)
        if m:
            return int(m.group(1), 16)
        # Sometimes the device type is bare hex (rare); try after comma then parse as hex if 2 chars
        tail = cls._after_comma(s)
        tail = tail.strip()
        if len(tail) == 2 and all(c in "0123456789abcdefABCDEF" for c in tail):
            return int(tail, 16)
        return None

    @classmethod
    def _parse_point_id(cls, s: str) -> str | None:
        # Common: "S083C57" or "POINTID-001,S083C57"
        tail = cls._after_comma(s)
        tail = tail.strip()
        if not tail or tail.startswith("?"):
            return None
        # Keep alnum + a few safe chars
        cleaned = "".join(ch for ch in tail if ch.isalnum() or ch in "-_")
        return cleaned or None

    @classmethod
    def _parse_status_hex(cls, s: str) -> str | None:
        """Parse a POINTSTATUS value.

        Bridge responses vary:
        - "01"
        - "$01"
        - "POINTSTATUS-001,01"
        - "POINTSTATUS-001,$01"
        """
        tail = cls._after_comma(s).strip()

        if tail.startswith("$") and len(tail) == 3:
            tail = tail[1:]

        if len(tail) == 2 and all(c in "0123456789abcdefABCDEF" for c in tail):
            return tail.upper()
        return None

    @staticmethod
    def _default_name(device_type: int | None, index: int, point_id: str | None) -> str:
        suffix = point_id if point_id else f"{index:03d}"
        if device_type == DEVICE_SHADE:
            return f"Pella Shade ({suffix})"
        if device_type == DEVICE_WINDOW_DOOR:
            return f"Pella Open/Close ({suffix})"
        if device_type == DEVICE_GARAGE:
            return f"Pella Garage Door ({suffix})"
        if device_type == DEVICE_LOCK:
            return f"Pella Lock ({suffix})"
        return f"Pella Device ({suffix})"

    @staticmethod
    def _format_device_name(dev: DeviceInfo | None, idx: int) -> str:
        if not dev:
            return f"Pella Device ({idx:03d})"
        # Use dev.name which is already formatted as "Pella <Type> (<id>)"
        return dev.name
