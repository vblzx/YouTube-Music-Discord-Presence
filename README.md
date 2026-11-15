# YT Music → Discord status

Shows the song you're playing on YouTube Music as a **Listening to** status on Discord,
with the album cover and a progress bar. Works on Windows 10/11 and Linux.

*Not affiliated with, endorsed by, or sponsored by YouTube, Google, or Discord.*

## Use it
1. Install YouTube Music as an app: open https://music.youtube.com in Chrome, Edge,
   Brave or Vivaldi and choose **Install** from the address bar or browser menu.
2. Open the **Discord desktop app** (the browser version of Discord doesn't work).
3. Double-click `YTMusicPresence.exe` and play a song. It runs in the system tray
   (bottom-right, maybe under the ^ arrow) and starts with Windows from then on.
   Right-click the tray icon to **Pause sharing**, turn off **Start with Windows**,
   or **Quit**.

If your status doesn't appear, turn on Discord → User Settings → Activity Privacy →
**Share your detected activities with others**.

## Linux (Arch and others)
Needs Python 3, systemd, and the Discord desktop app (the official `discord` package,
Flatpak or Snap).
```
git clone <this repo> && cd ytmusic-discord-presence
sh linux/install.sh
```
It runs in the background and starts on every login. Check on it with
`journalctl --user -u ytmusic-presence -f`; uninstall with `sh linux/install.sh --uninstall`.

It picks up YouTube Music desktop apps (such as `youtube-music` from the AUR) by
default. If you listen in a browser tab, set `"browser_tabs": true` in
`~/.config/ytmusic-presence/config.json`, then run
`systemctl --user restart ytmusic-presence`.

## Settings (optional)
Copy `config.example.json` to `config.json` (next to the exe on Windows, or
`~/.config/ytmusic-presence/config.json` on Linux) and edit:

| Setting | What it does |
|---|---|
| `browser_tabs` | `true` to also detect YouTube Music in a normal browser tab. Windows can't tell which website a tab is, so other music sites may show up too. |
| `sources` | App IDs to watch. Run `YTMusicPresence.exe --list-sources` while music plays to find yours. |
| `fetch_covers` | `false` to turn off album-cover lookups (see Privacy). |
| `show_search_button` | `false` to remove the "Search on YouTube Music" button. |
| `client_id` | Use your own Discord application instead of the built-in one. |

## Privacy
- Song info comes from Windows' media controls. Nothing is scraped from YouTube and
  your YouTube or Discord login is never touched.
- Nothing is saved to disk, and there's no analytics or tracking.
- Your status is sent only to the Discord app on your PC, and is shown to whoever
  Discord's privacy settings allow.
- When `fetch_covers` is on, the song title and artist are sent to Apple's public
  iTunes Search API to find a cover image. Turn it off to keep everything local.
- The app asks an internet time server (time.windows.com) for the current time, so
  your friends' song timer is right even if your PC clock is off. Nothing about you
  is sent in that request.
- Start with Windows is stored in your user registry (`HKCU\...\Run\YTMusicPresence`);
  turning it off in the tray menu removes it.

## Build from source
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\python main.py --console
.venv\Scripts\pyinstaller --onefile --noconsole --name YTMusicPresence --collect-submodules winrt --collect-submodules pypresence --hidden-import pystray._win32 main.py
```
