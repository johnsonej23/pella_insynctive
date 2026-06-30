from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv4Address, ip_address

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
RE_INDEXED_QUERY = re.compile(r"^\?(?P<cmd>POINTSTATUS|POINTBATTERYGET|POINTDEVICE|POINTID)-(?P<idx>\d{3})$")

BRIDGE_NETWORK_COMMANDS: tuple[tuple[str, str], ...] = (
    ("static_ip", "?GETSTATICIP"),
    ("netmask", "?GETNETMASK"),
    ("gateway", "?GETGATEWAY"),
    ("dns", "?GETDNS"),
)


@dataclass
class BridgeInfo:
    configured_host: str | None = None
    static_ip: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    dns: str | None = None


@dataclass
class DeviceInfo:
    index: int
    point_id: str | None
    device_type: int | None
    name: str
    status_hex: str | None
    battery_hex: str | None
    raw_status_hex: str | None = None


class PellaCoordinator(DataUpdateCoordinator[dict[int, DeviceInfo]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry

        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]
        self.bridge_info = BridgeInfo(configured_host=self._host)

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
        self._startup_task: asyncio.Task | None = None

        super().__init__(hass, _LOGGER, name="pella_insynctive", update_interval=None)
        self.data: dict[int, DeviceInfo] = {}

    @property
    def client(self) -> TelnetClient:
        return self._client

    @property
    def bridge_id(self) -> str:
        """Return a stable bridge identifier.

        Do not include the bridge IP address here. If the bridge IP changes,
        Home Assistant should continue treating it as the same bridge device.
        """
        return f"bridge_{self.entry.entry_id}"

    @property
    def bridge_name(self) -> str:
        return "Pella Insynctive Bridge"

    def bridge_device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self.bridge_id)},
            "name": self.bridge_name,
            "manufacturer": "Pella",
            "model": "Insynctive Bridge",
        }

    @staticmethod
    def point_key(idx: int) -> str:
        """Return a stable point key based only on the bridge point index."""
        return f"point_{idx:03d}"

    def point_unique_id(self, idx: int, suffix: str) -> str:
        """Return a stable unique ID for an entity on a bridge point."""
        return f"{self.entry.entry_id}_{self.point_key(idx)}_{suffix}"

    def point_serial_number(self, idx: int) -> str | None:
        """Return the Pella point serial number for a bridge point."""
        dev = self.data.get(idx)
        if dev and dev.point_id:
            return dev.point_id
        return None

    def point_device_info(self, idx: int) -> dict:
        dev = self.data.get(idx)
        point_key = self.point_key(idx)

        return {
            "identifiers": {(DOMAIN, f"{self.bridge_id}_{point_key}")},
            "name": self._device_name_override(dev, idx),
            "manufacturer": "Pella",
            "model": self._device_model(dev),
            "serial_number": self.point_serial_number(idx),
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
            point_key = self.point_key(idx)
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
        if pos < 0 or pos > 100:
            return None
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
            if v is not None:
                self._set_status_value(idx, v, source="set_shade_position")
                self.async_set_updated_data(self.data)
        except Exception:
            pass

    async def async_start(self) -> None:
        await self._client.start()
        self._startup_task = self.hass.async_create_task(self._startup_discovery())

        if self._poll_s > 0:
            self._poll_unsub = async_track_time_interval(self.hass, self._poll_tick, timedelta(seconds=self._poll_s))
        if self._battery_poll_min > 0:
            self._battery_unsub = async_track_time_interval(
                self.hass, self._battery_tick, timedelta(minutes=self._battery_poll_min)
            )

    async def async_stop(self) -> None:
        if self._startup_task:
            self._startup_task.cancel()
            try:
                await self._startup_task
            except asyncio.CancelledError:
                pass
            self._startup_task = None
        if self._poll_unsub:
            self._poll_unsub()
            self._poll_unsub = None
        if self._battery_unsub:
            self._battery_unsub()
            self._battery_unsub = None
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None
        self._last_cmd = None
        await self._client.stop()

    async def _wait_for_connection(self, timeout: float = 120.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._client.is_connected:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.5, remaining))
        return True

    async def _startup_discovery(self) -> None:
        try:
            if not await self._wait_for_connection():
                _LOGGER.warning("Timed out waiting for bridge connection during startup discovery")
                return

            # Give the bridge a brief moment after the TCP connection opens before
            # starting the command/response discovery sequence. This is especially
            # helpful immediately after changing the bridge host through reconfigure.
            await asyncio.sleep(0.5)

            await self._refresh_bridge_network_info()

            count = 0
            try:
                count_str = await self._query("?POINTCOUNT", timeout=5.0)
                digits = "".join(ch for ch in count_str if ch.isdigit())
                count = int(digits) if digits else 0
            except TimeoutError:
                _LOGGER.warning("Timeout on ?POINTCOUNT; falling back to scan")

            # If POINTCOUNT is 2, we should at least try points 001..002.
            indices = range(1, 129) if (self._scan_all_128 or count == 0) else range(1, min(128, count) + 1)
            _LOGGER.debug(
                "Discovery scanning %s points (POINTCOUNT=%s, scan_all_128=%s)",
                len(list(indices)),
                count,
                self._scan_all_128,
            )

            for i in indices:
                idx = f"{i:03d}"
                try:
                    dtype_raw = await self._query(f"?POINTDEVICE-{idx}", timeout=5.0)
                    pid_raw = await self._query(f"?POINTID-{idx}", timeout=5.0)
                    status_raw = await self._query(f"?POINTSTATUS-{idx}", timeout=5.0)

                    # Battery is not included in POINTSTATUS unless the bridge is replying to a
                    # POINTBATTERYGET command. Fetch once during discovery so the sensor does not
                    # sit at Unknown for hours until the first battery poll interval.
                    battery_raw = None
                    try:
                        battery_raw = await self._query(f"?POINTBATTERYGET-{idx}", timeout=5.0)
                        _LOGGER.debug("Battery discovery response for point %s: raw=%r", idx, battery_raw)
                    except TimeoutError:
                        _LOGGER.debug("Timeout querying battery for point %s during discovery", idx)

                    device_type = self._parse_device_type(dtype_raw)
                    point_id = self._parse_point_id(pid_raw)
                    status_hex = self._parse_status_hex(status_raw)

                    battery_hex = None
                    if battery_raw:
                        battery_hex = self._parse_battery_hex(battery_raw)
                        if not battery_hex:
                            _LOGGER.debug(
                                "Unable to parse discovery battery response for point %s: raw=%r device_type=%s point_id=%s",
                                idx,
                                battery_raw,
                                device_type,
                                point_id,
                            )

                    # If we can't parse a device type, still create the device so HA shows it,
                    # and logs will tell us what came back.
                    name = self._default_name(device_type, i, point_id)

                    self.data[i] = DeviceInfo(i, point_id, device_type, name, None, battery_hex)
                    if status_hex is not None:
                        self._set_status_value(i, status_hex, source="discovery")
                    _LOGGER.debug(
                        "Discovered point %s: type_raw=%s type=%s id_raw=%s id=%s status_raw=%s status=%s",
                        idx,
                        dtype_raw,
                        device_type,
                        pid_raw,
                        point_id,
                        status_raw,
                        status_hex,
                    )
                except TimeoutError:
                    _LOGGER.debug("Timeout querying point %s; skipping", idx)
                    continue
                except Exception as err:
                    _LOGGER.debug("Error querying point %s; skipping: %s", idx, err)
                    continue

            self.async_set_updated_data(self.data)
            self._apply_device_overrides_to_registry()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Startup discovery failed: %s", err)

    async def _refresh_bridge_network_info(self) -> None:
        for attr, cmd in BRIDGE_NETWORK_COMMANDS:
            try:
                raw = await self._query(cmd, timeout=5.0)
                value = self._parse_network_value(raw)
                setattr(self.bridge_info, attr, value)
                _LOGGER.debug("Bridge network response for %s: raw=%r parsed=%r", attr, raw, value)
            except TimeoutError:
                _LOGGER.debug("Timeout querying bridge network value with %s", cmd)
            except Exception as err:
                _LOGGER.debug("Error querying bridge network value with %s: %s", cmd, err)

    async def async_refresh_bridge_network_info(self) -> None:
        """Refresh bridge network diagnostic values on demand."""
        await self._refresh_bridge_network_info()
        self.async_set_updated_data(self.data)

    async def async_set_static_network(self, *, static_ip: str, netmask: str, gateway: str, dns: str) -> None:
        """Write static network settings to the bridge and reload using the new address."""
        static_ip = self._validate_ipv4(static_ip, "static_ip")
        netmask = self._validate_ipv4(netmask, "netmask")
        gateway = self._validate_ipv4(gateway, "gateway")
        dns = self._validate_ipv4(dns, "dns")

        commands = (
            ("static_ip", f"!SETSTATICIP,${static_ip}"),
            ("netmask", f"!SETNETMASK,${netmask}"),
            ("gateway", f"!SETGATEWAY,${gateway}"),
            ("dns", f"!SETDNS,${dns}"),
        )

        for attr, command in commands:
            response = await self._query(command, timeout=5.0)
            if "INVALID" in response.upper():
                raise HomeAssistantError(f"Bridge rejected {attr}: {response}")
            _LOGGER.debug("Bridge accepted %s command: %s", attr, response)

        await self._client.send("!BRIDGESETSTATIC")

        self.bridge_info.configured_host = static_ip
        self.bridge_info.static_ip = static_ip
        self.bridge_info.netmask = netmask
        self.bridge_info.gateway = gateway
        self.bridge_info.dns = dns
        self.async_set_updated_data(self.data)

        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_HOST: static_ip, CONF_PORT: self._port},
        )
        self.hass.async_create_task(self._reload_entry_after_bridge_reboot(self.entry.entry_id))

    async def async_set_dhcp(self) -> None:
        """Return the bridge to DHCP mode and reload the integration after reboot."""
        await self._client.send("!BRIDGESETDHCP")

        self.bridge_info.static_ip = None
        self.bridge_info.netmask = None
        self.bridge_info.gateway = None
        self.bridge_info.dns = None
        self.async_set_updated_data(self.data)

        self.hass.async_create_task(self._reload_entry_after_bridge_reboot(self.entry.entry_id))

    async def _reload_entry_after_bridge_reboot(self, entry_id: str) -> None:
        await asyncio.sleep(15)
        await self.hass.config_entries.async_reload(entry_id)

    @staticmethod
    def _validate_ipv4(value: str, field: str) -> str:
        value = str(value).strip()
        try:
            parsed = ip_address(value)
        except ValueError as err:
            raise HomeAssistantError(f"{field} must be a valid IPv4 address") from err
        if not isinstance(parsed, IPv4Address):
            raise HomeAssistantError(f"{field} must be a valid IPv4 address")
        return str(parsed)

    async def _poll_tick(self, _now) -> None:
        if not self._client.is_connected or not self.data:
            return
        for i, dev in list(self.data.items()):
            idx = f"{i:03d}"
            try:
                resp = await self._query(f"?POINTSTATUS-{idx}", timeout=5.0)
                v = self._parse_status_hex(resp)
                if v is not None:
                    self._set_status_value(i, v, source="status_poll")
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
                _LOGGER.debug(
                    "Battery poll response for point %s: raw=%r device_type=%s point_id=%s",
                    idx,
                    resp,
                    dev.device_type,
                    dev.point_id,
                )
                battery_hex = self._parse_battery_hex(resp)
                if battery_hex:
                    dev.battery_hex = battery_hex
                else:
                    _LOGGER.debug(
                        "Unable to parse battery poll response for point %s: raw=%r device_type=%s point_id=%s",
                        idx,
                        resp,
                        dev.device_type,
                        dev.point_id,
                    )
            except TimeoutError:
                _LOGGER.debug("Timeout polling battery for point %s", idx)
        self.async_set_updated_data(self.data)

    async def async_refresh_point_status(self, idx: int) -> None:
        """Refresh a single point's status from the bridge."""
        resp = await self._query(f"?POINTSTATUS-{idx:03d}", timeout=5.0)
        v = self._parse_status_hex(resp)
        if v is not None:
            self._set_status_value(idx, v, source="manual_status_refresh")
            self.async_set_updated_data(self.data)

    async def async_refresh_point_battery(self, idx: int) -> None:
        """Refresh a single point's battery from the bridge."""
        resp = await self._query(f"?POINTBATTERYGET-{idx:03d}", timeout=5.0)
        dev = self.data.get(idx)
        _LOGGER.debug(
            "Battery manual refresh response for point %03d: raw=%r device_type=%s point_id=%s",
            idx,
            resp,
            dev.device_type if dev else None,
            dev.point_id if dev else None,
        )
        battery_hex = self._parse_battery_hex(resp)
        if not battery_hex:
            _LOGGER.debug(
                "Unable to parse manual battery refresh response for point %03d: raw=%r device_type=%s point_id=%s",
                idx,
                resp,
                dev.device_type if dev else None,
                dev.point_id if dev else None,
            )

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
        line = line.strip()

        # Ignore echoed command lines.
        if self._last_cmd and line == self._last_cmd:
            return

        status_match = RE_UNSOL.match(line)

        if self._pending and not self._pending.done() and self._line_matches_pending_response(line, status_match):
            _LOGGER.debug("Interpreting %r as response to %s", line, self._last_cmd)
            self._pending.set_result(line)
            return

        if status_match:
            idx = int(status_match.group("idx"))
            val = status_match.group("val").upper()
            self._set_status_value(idx, val, source="unsolicited")
            self.async_set_updated_data(self.data)
            return

        if self._pending and not self._pending.done():
            self._pending.set_result(line)

    def _line_matches_pending_response(self, line: str, status_match: re.Match[str] | None) -> bool:
        """Return true if an incoming line should satisfy the pending query.

        The bridge can return POINTSTATUS-###,$xx lines both as unsolicited status
        events and as replies to shade status/battery queries. A POINTSTATUS line
        is only consumed as a command reply when it matches the same point index
        and the pending command is a status or battery query. Other POINTSTATUS
        lines are processed as unsolicited status while the query keeps waiting.
        """
        if not self._last_cmd:
            return status_match is None

        if status_match is None:
            return True

        pending_match = RE_INDEXED_QUERY.match(self._last_cmd)
        if not pending_match:
            return False

        pending_cmd = pending_match.group("cmd")
        if pending_cmd not in {"POINTSTATUS", "POINTBATTERYGET"}:
            return False

        return int(status_match.group("idx")) == int(pending_match.group("idx"))

    def _set_status_value(self, idx: int, val: str, *, source: str) -> None:
        val = val.upper()
        dev = self.data.get(idx)
        if not dev:
            dev = DeviceInfo(idx, None, None, f"Pella Device ({idx:03d})", None, None)
            self.data[idx] = dev

        dev.raw_status_hex = val

        if dev.device_type == DEVICE_SHADE:
            try:
                raw_position = int(val, 16)
            except ValueError:
                _LOGGER.debug("Ignoring invalid shade status for point %03d from %s: %s", idx, source, val)
                return
            if raw_position > 100:
                _LOGGER.debug(
                    "Ignoring out-of-range shade status for point %03d from %s: %s",
                    idx,
                    source,
                    val,
                )
                return

        dev.status_hex = val

    @staticmethod
    def _after_comma(s: str) -> str:
        m = RE_AFTER_COMMA.search(s)
        return m.group(1).strip() if m else s.strip()

    @classmethod
    def _parse_network_value(cls, s: str) -> str | None:
        tail = cls._after_comma(s).strip()
        if not tail or tail.startswith("?"):
            return None
        return tail

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

    @classmethod
    def _parse_battery_hex(cls, s: str) -> str | None:
        """Parse a battery value from direct or POINTSTATUS-wrapped responses."""
        m = RE_HEX_DOLLAR.search(s)
        if m:
            return f"${m.group(1).upper()}"

        tail = cls._after_comma(s).strip()
        if tail.startswith("$") and len(tail) == 3:
            tail = tail[1:]
        if len(tail) == 2 and all(c in "0123456789abcdefABCDEF" for c in tail):
            return f"${tail.upper()}"
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
