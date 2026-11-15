"""Show what you're playing on YouTube Music as your Discord status.

Reads track info from the operating system's media controls (Windows media
controls, or MPRIS on Linux) and sends it to the Discord desktop app over its
local RPC connection. Nothing is scraped from YouTube and nothing is stored on
disk. The only network requests this app makes itself are an optional
album-cover lookup (song title and artist) to Apple's public iTunes Search API,
and a time check against an internet time server so the song timer is right
even if the PC clock is off.
"""

import argparse
import asyncio
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pypresence import ActivityType, AioPresence, StatusDisplayType
from pypresence.exceptions import PipeClosed, DiscordNotFound, DiscordError

WINDOWS = sys.platform == "win32"
if WINDOWS:
    from media_windows import Media
else:
    from media_linux import Media

FROZEN = getattr(sys, "frozen", False)
if WINDOWS:
    # Next to the .exe when packaged with PyInstaller, next to this file otherwise.
    CONFIG_PATH = (Path(sys.executable).parent if FROZEN else Path(__file__).parent) / "config.json"
else:
    CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ytmusic-presence" / "config.json"

DEFAULTS = {
    "client_id": "1571469261182992445",
    # YouTube Music installed as an app from Chrome/Edge/Brave/Vivaldi (Windows),
    # or a YouTube Music desktop app such as th-ch/youtube-music (Linux).
    "sources": ["cinhimbnkkghhklpknlkffjgod", "youtube music", "youtube-music"],
    # Also accept YouTube Music playing in a normal browser tab. The system can't tell
    # which site a tab is, so this only accepts tracks with an album (regular
    # YouTube videos don't have one), but other music sites may slip through.
    "browser_tabs": False,
    "fetch_covers": True,
    "icon": "ytmusic",
    "show_search_button": True,
}
BROWSERS = ("chrome", "chromium", "msedge", "edge", "brave", "vivaldi", "opera",
            "firefox", "librewolf", "floorp", "zen")
TIME_SERVERS = ("time.windows.com", "pool.ntp.org", "time.google.com")

POLL_SECONDS = 2      # fallback check; media change notifications normally wake us sooner
SETTLE_SECONDS = 0.3  # after a change, let the title and timeline both update
MIN_UPDATE_GAP = 4    # Discord allows 5 presence updates per 20s
SEEK_TOLERANCE = 3    # seconds of drift before we resend timestamps
GAP_DELAY = 5         # YT Music briefly reports "not playing" between songs; don't clear for that
END_MARGIN = 3        # a pause this close to the end of a song is probably the gap before the next
CLOCK_REFRESH = 300   # re-check the PC clock every 5 minutes
CLOCK_JUMP = 1        # seconds the PC clock can be changed by before we re-check at once


class ConfigError(Exception):
    pass


class Controller:
    """State shared between the presence loop and the tray icon (other thread)."""

    def __init__(self):
        self.paused = False
        self.status = "Starting..."
        self.on_status = None   # set by the tray to refresh its tooltip
        self._loop = None
        self._wake = None

    def say(self, text):
        print(time.strftime("%H:%M:%S"), text, flush=True)
        self.status = text
        if self.on_status:
            self.on_status(text)

    def bind(self, loop, wake):
        self._loop, self._wake = loop, wake

    def poke(self):
        """Wake the presence loop, e.g. after Pause sharing is toggled. Thread-safe."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{CONFIG_PATH} is not valid JSON: {e}")
    if not str(cfg["client_id"]).isdigit():
        raise ConfigError("client_id in config.json must be a Discord application ID.")
    return cfg


def clock_offset():
    """Seconds to add to the PC clock to get real time, from a time server (SNTP).

    Friends' Discord apps draw the progress bar from the song's start time using
    their own clocks, so if this PC's clock is off, their timer is off by as much.
    Returns 0 if no time server answers.
    """
    for host in TIME_SERVERS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3)
                sent = time.time()
                s.sendto(b"\x1b" + 47 * b"\0", (host, 123))
                data, _ = s.recvfrom(48)
                received = time.time()
            secs, frac = struct.unpack("!II", data[40:48])
            server = secs - 2208988800 + frac / 2**32  # NTP counts from 1900
            return server - (sent + received) / 2
        except (OSError, struct.error):
            continue
    return 0.0


def source_matches(app_id, album, cfg):
    app_id = app_id.lower()
    if any(s.lower() in app_id for s in cfg["sources"]):
        return True
    return cfg["browser_tabs"] and bool(album) and any(b in app_id for b in BROWSERS)


def pick_track(sessions, cfg, ignored, ctl, sharing):
    """Choose what to show from the media sessions.

    Returns (track, user_paused): the playing YouTube Music session, or None plus
    whether the song we're sharing (`sharing` = its title, artist) was paused
    partway through, which should clear the status at once.
    """
    user_paused = False
    for s in sessions:
        if not s["title"]:
            continue
        if not source_matches(s["app_id"], s["album"], cfg):
            if s["status"] == "playing" and s["app_id"] not in ignored:
                ignored.add(s["app_id"])
                hint = ("set \"browser_tabs\": true" if any(b in s["app_id"].lower() for b in BROWSERS)
                        else "add it to \"sources\"")
                ctl.say(f"Ignoring media from {s['app_id']!r}. If that's YouTube Music, "
                        f"{hint} in {CONFIG_PATH}.")
            continue
        if s["status"] == "playing":
            return s, False
        if (s["status"] == "paused" and (s["title"], s["artist"]) == sharing
                and not (s["duration"] and s["position"] > s["duration"] - END_MARGIN)):
            user_paused = True
    return None, user_paused


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _artists(s):
    """Split "A and B", "A & B", "A, B feat. C" into normalized names."""
    parts = re.split(r",|&|\+|\band\b|\bx\b|\bfeat\.?|\bft\.?|\bwith\b", (s or "").lower())
    return {_norm(p) for p in parts if _norm(p)}


def fetch_icon_available(client_id, icon):
    """True if `icon` is an image uploaded to the Discord app (or a URL)."""
    if not icon or icon.startswith("http"):
        return bool(icon)
    url = f"https://discord.com/api/v10/oauth2/applications/{client_id}/assets"
    req = urllib.request.Request(url, headers={"User-Agent": "YTMusicPresence/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return any(a.get("name") == icon for a in json.load(resp))
    except (OSError, ValueError):
        return True  # can't tell; assume the user set it up


def lookup_cover(title, artist, album):
    """Find album art for a song via the iTunes Search API. Returns a URL or None."""
    wanted = _artists(artist)
    if not wanted:
        return None
    # Try the full title first, then without "(feat. ...)" / "(From ...)" style extras.
    titles = dict.fromkeys([title, re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()])
    candidates = []
    for t in titles:
        term = urllib.parse.quote_plus(f"{t} {artist}")
        url = f"https://itunes.apple.com/search?entity=song&limit=10&term={term}"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                results = json.load(resp).get("results", [])
        except (OSError, ValueError):
            return None
        # Only accept a result sharing an artist; prefer one from the same album.
        candidates = [r for r in results if wanted & _artists(r.get("artistName"))]
        if candidates:
            break
    if not candidates:
        return None
    best = next((r for r in candidates if album and _norm(r.get("collectionName")) == _norm(album)),
                candidates[0])
    art = best.get("artworkUrl100")
    return art.replace("100x100bb", "512x512bb") if art else None


def build_activity(track, cfg, cover):
    activity = {
        "activity_type": ActivityType.LISTENING,
        "status_display_type": StatusDisplayType.DETAILS,  # "Listening to <song>" in the member list
        "details": track["title"][:128],
        "state": f"by {track['artist']}"[:128] if track["artist"] else None,
        "large_image": cover or cfg["icon"] or None,
        "large_text": (track["album"] or "YouTube Music")[:128],
        # Discord wants milliseconds; with start and end set it draws a progress bar.
        "start": int(track["start"] * 1000),
    }
    if track["duration"] > 0:
        activity["end"] = int((track["start"] + track["duration"]) * 1000)
    if cover and cfg["icon"]:
        activity["small_image"] = cfg["icon"]
        activity["small_text"] = "YouTube Music"
    if cfg["show_search_button"]:
        query = urllib.parse.quote_plus(f"{track['title']} {track['artist']}")
        activity["buttons"] = [{"label": "Search on YouTube Music",
                                "url": f"https://music.youtube.com/search?q={query}"}]
    return activity


async def connect(client_id, ctl):
    while True:
        try:
            rpc = AioPresence(client_id)
            await rpc.connect()
            ctl.say("Connected to Discord.")
            return rpc
        except (DiscordNotFound, PipeClosed, ConnectionError, FileNotFoundError):
            ctl.say("Waiting for the Discord app to open...")
            await asyncio.sleep(15)


async def list_sources():
    media = Media()
    await media.start()
    sessions = await media.sessions()
    if not sessions:
        print("No media sessions. Start playing something first.")
    for s in sessions:
        print(f"{s['app_id']}  ->  {s['status']}: {s['title']!r} by {s['artist']!r}")


async def update_clock(ctl):
    offset = await asyncio.to_thread(clock_offset)
    if abs(offset) >= 2:
        ctl.say(f"Your PC clock is {abs(offset):.0f}s {'behind' if offset > 0 else 'ahead'}; "
                f"correcting the song timer for that.")
    return offset


async def run(ctl, cfg):
    if not await asyncio.to_thread(fetch_icon_available, cfg["client_id"], cfg["icon"]):
        ctl.say(f"Note: no image named {cfg['icon']!r} is uploaded to your Discord app, "
                f"so songs without a cover will show no picture.")
        cfg["icon"] = None
    offset = await update_clock(ctl)
    clock_checked = time.monotonic()
    # time.time() follows the PC clock, time.monotonic() doesn't; if the gap between
    # them moves, someone synced or changed the clock and our offset is stale.
    clock_base = time.time() - time.monotonic()
    rpc = await connect(cfg["client_id"], ctl)
    ctl.say("Watching for YouTube Music.")

    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    ctl.bind(loop, wake)
    media = Media()
    await media.start(lambda: loop.call_soon_threadsafe(wake.set))

    covers = {}          # (title, artist) -> URL or None, so each song is looked up once
    ignored = set()      # app IDs we've already told the user about
    sent = None          # (title, artist, start) we last sent, or "cleared"
    last_push = 0.0
    idle_since = None    # when we last saw nothing playing
    while True:
        clock_moved = abs(time.time() - time.monotonic() - clock_base) > CLOCK_JUMP
        if clock_moved or time.monotonic() - clock_checked > CLOCK_REFRESH:
            # A changed offset shifts track["start"], so the timer is resent below.
            offset = await update_clock(ctl)
            clock_checked = time.monotonic()
            clock_base = time.time() - time.monotonic()

        sharing = sent[:2] if isinstance(sent, tuple) else None
        track, user_paused = None, False
        if not ctl.paused:
            track, user_paused = pick_track(await media.sessions(), cfg, ignored, ctl, sharing)
        if track:
            idle_since = None
            key = (track["title"], track["artist"])
            start = time.time() + offset - track["position"]
            changed = (sharing != key or abs(sent[2] - start) > SEEK_TOLERANCE)
        else:
            idle_since = idle_since or time.time()
            idle_for = time.time() - idle_since
            changed = sent != "cleared" and (ctl.paused or user_paused or idle_for >= GAP_DELAY)
            if sent != "cleared" and not changed:
                # Probably the gap between songs; check again once it would count as stopped.
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), GAP_DELAY - idle_for)
                    await asyncio.sleep(SETTLE_SECONDS)
                except asyncio.TimeoutError:
                    pass
                continue

        wait = MIN_UPDATE_GAP - (time.time() - last_push)
        if changed and wait > 0:
            # Too soon after the last update; send it as soon as Discord allows.
            await asyncio.sleep(wait)
            continue
        if changed:
            if track and cfg["fetch_covers"] and key not in covers:
                covers[key] = await asyncio.to_thread(lookup_cover, *key, track["album"])
            try:
                if track:
                    await rpc.update(**build_activity({**track, "start": start}, cfg, covers.get(key)))
                    sent = (*key, start)
                    ctl.say(f"Sharing: {track['title']} - {track['artist']}")
                else:
                    await rpc.clear()
                    sent = "cleared"
                    ctl.say("Sharing paused." if ctl.paused else "Nothing playing.")
                last_push = time.time()
            except (PipeClosed, DiscordError, ConnectionError, OSError):
                ctl.say("Lost connection to Discord.")
                rpc = await connect(cfg["client_id"], ctl)
                sent = None

        try:
            await asyncio.wait_for(wake.wait(), POLL_SECONDS)
            await asyncio.sleep(SETTLE_SECONDS)
        except asyncio.TimeoutError:
            pass
        wake.clear()


def claim_single_instance_linux():
    """False if another copy is already running (two copies would fight over the status)."""
    import fcntl
    global _lock
    lock_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    _lock = open(os.path.join(lock_dir, "ytmusic-presence.lock"), "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-sources", action="store_true",
                        help="print the app IDs of current media sessions and exit")
    parser.add_argument("--console", action="store_true",
                        help="(Windows) run in this console window instead of the system tray")
    args = parser.parse_args()
    if args.list_sources:
        asyncio.run(list_sources())
        return

    if WINDOWS:
        import tray
        already_running, show = not tray.claim_single_instance(), tray.message
    else:
        already_running, show = not claim_single_instance_linux(), print
    if already_running:
        show("YT Music Presence is already running.")
        return
    try:
        cfg = load_config()
    except ConfigError as e:
        show(str(e))
        return

    ctl = Controller()
    if WINDOWS and not args.console:
        tray.run_tray(ctl, lambda: asyncio.run(run(ctl, cfg)), FROZEN)
    else:
        try:
            asyncio.run(run(ctl, cfg))
        except KeyboardInterrupt:
            pass  # Discord removes the status when the RPC connection closes


if __name__ == "__main__":
    main()
