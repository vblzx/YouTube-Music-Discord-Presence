"""Reads what's playing from MPRIS, the D-Bus interface Linux media players and
browsers (Chrome, Chromium, Firefox, Electron apps) use to publish now-playing info."""

import time

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
STATUS = {"Playing": "playing", "Paused": "paused"}


def _value(props, key, default=None):
    v = props.get(key)
    return v.value if v is not None else default


class Media:
    def __init__(self):
        self._positions = {}  # bus name -> (reported position, when we first saw it)

    def _live_position(self, name, reported, playing):
        """Some players only update Position on seeks; count forward ourselves if so."""
        now = time.monotonic()
        seen = self._positions.get(name)
        if playing and seen and seen[0] == reported:
            return reported + (now - seen[1])
        self._positions[name] = (reported, now)
        return reported

    async def start(self, wake=None):
        """Connect, and call `wake()` whenever a player changes, appears, or goes away."""
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        if wake is None:
            return
        for rule in (f"type='signal',path='{PATH}'",  # PropertiesChanged and Seeked
                     "type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                     f"arg0namespace='{ROOT}'"):
            await self._call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "AddMatch", "s", [rule])

        def on_message(msg):
            if msg.message_type == MessageType.SIGNAL:
                wake()

        self._bus.add_message_handler(on_message)

    async def _call(self, dest, path, interface, member, signature="", body=()):
        reply = await self._bus.call(Message(destination=dest, path=path, interface=interface,
                                             member=member, signature=signature, body=list(body)))
        return None if reply.message_type == MessageType.ERROR else reply.body

    async def _get_all(self, name, interface):
        body = await self._call(name, PATH, "org.freedesktop.DBus.Properties", "GetAll", "s", [interface])
        return body[0] if body else {}

    async def sessions(self):
        body = await self._call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                "org.freedesktop.DBus", "ListNames")
        out = []
        for name in (body[0] if body else []):
            if not name.startswith(PREFIX):
                continue
            player = await self._get_all(name, PLAYER)
            if not player:
                continue
            root = await self._get_all(name, ROOT)
            meta = _value(player, "Metadata", {})
            artist = _value(meta, "xesam:artist", [])
            # e.g. "chromium.instance1234 YouTube Music youtube-music", so "sources"
            # can match the bus name, the app's display name or its .desktop file.
            app_id = " ".join(filter(None, [name[len(PREFIX):], _value(root, "Identity"),
                                            _value(root, "DesktopEntry")]))
            status = STATUS.get(_value(player, "PlaybackStatus"), "stopped")
            reported = _value(player, "Position", 0) / 1e6  # microseconds
            out.append({
                "app_id": app_id,
                "status": status,
                "title": _value(meta, "xesam:title", ""),
                "artist": ", ".join(artist) if isinstance(artist, list) else str(artist),
                "album": _value(meta, "xesam:album", ""),
                "position": self._live_position(name, reported, status == "playing"),
                "duration": _value(meta, "mpris:length", 0) / 1e6,
            })
        return out
