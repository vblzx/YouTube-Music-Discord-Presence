"""Reads what's playing from the Windows media controls (the volume flyout)."""

from datetime import datetime, timezone

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

STATUS = {PlaybackStatus.PLAYING: "playing", PlaybackStatus.PAUSED: "paused"}


class Media:
    async def start(self, wake=None):
        """Connect, and call `wake()` (from any thread) whenever something changes."""
        self._manager = await MediaManager.request_async()
        if wake is None:
            return

        def subscribe(*_):
            for session in self._manager.get_sessions():
                session.add_media_properties_changed(lambda *_: wake())
                session.add_playback_info_changed(lambda *_: wake())
            wake()

        self._manager.add_sessions_changed(subscribe)
        subscribe()

    async def sessions(self):
        out = []
        for session in self._manager.get_sessions():
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            status = STATUS.get(session.get_playback_info().playback_status, "stopped")
            position = timeline.position.total_seconds()
            if status == "playing":
                # Position is reported as of last_updated_time, so add the time since then.
                position += (datetime.now(timezone.utc) - timeline.last_updated_time).total_seconds()
            out.append({
                "app_id": session.source_app_user_model_id or "",
                "status": status,
                "title": props.title,
                "artist": props.artist,
                "album": props.album_title,
                "position": position,
                "duration": timeline.end_time.total_seconds(),
            })
        return out
