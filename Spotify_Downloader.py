#
"""
Sunnify (Spotify Downloader)
Copyright (C) 2024-2026 Sunny Patel <sunnypatel124555@gmail.com>

EDUCATIONAL PROJECT DISCLAIMER:
This software is a student portfolio project developed for educational purposes only.
It is intended to demonstrate software engineering skills and is provided free of charge.
Users are solely responsible for ensuring compliance with applicable laws in their jurisdiction.
This software should only be used with content you own or have permission to download.
See DISCLAIMER.md for full terms.

For the program to work, the playlist URL pattern must follow the format of
/playlist/abcdefghijklmnopqrstuvwxyz... If the program stops working, email
<sunnypatel124555@gmail.com> or open an issue in the repository.
"""

from __future__ import annotations

__version__ = "2.6.0"

import atexit
import collections
import concurrent.futures
import contextlib
import faulthandler
import io
import logging
import os
import platform
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler

import requests
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from yt_dlp import YoutubeDL

from spotifydown_api import (
    ContentUnavailableError,
    ExtractionError,
    NetworkError,
    PlaylistClient,
    PlaylistInfo,
    RateLimitError,
    SpotifyDownAPIError,
    cap_filename,
    detect_spotify_url_type,
    extract_playlist_id,
    sanitize_filename,
)
from Template import ToggleSwitch, Ui_MainWindow

# Module logger. Stays a no-op (no handlers) until _setup_logging() runs at
# startup, so importing this module in tests stays silent and writes nothing.
log = logging.getLogger("sunnify")


def _log_excepthook(exc_type, exc, tb):
    """Route uncaught main-thread exceptions to the log before the default handler."""
    # ctrl+c and an intentional sys.exit() are clean exits, not crashes
    if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        with contextlib.suppress(Exception):
            log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
    if sys.stderr is not None:  # windowed builds have no stderr to write to
        sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args):
    """Same, for python threads; qt threads log inside their own run()."""
    if issubclass(args.exc_type, SystemExit):
        return
    with contextlib.suppress(Exception):
        log.critical(
            "uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )


def _install_crash_handlers() -> None:
    """Make every abnormal exit land in the log; logging is our only diagnostic."""
    sys.excepthook = _log_excepthook
    threading.excepthook = _thread_excepthook
    # faulthandler catches native crashes (qt/ffmpeg segfaults) excepthook can't;
    # crash.log sits next to sunnify.log and stays open for the process lifetime.
    with contextlib.suppress(Exception):
        crash_path = os.path.join(os.path.dirname(log_file_path()), "crash.log")
        faulthandler.enable(open(crash_path, "a"))  # noqa: SIM115
    atexit.register(lambda: log.info("==== sunnify session end ===="))


class _YtdlpLog:
    """Bridge yt-dlp's own output into our log and remember the last error.

    With ignoreerrors=True a failed download won't raise, so capturing error()
    here is the only way to record *why* no audio file landed (bot-block vs
    unavailable vs format vs nsig). Routine yt-dlp chatter goes to our DEBUG so
    the default INFO log stays lean.
    """

    def __init__(self):
        self.last_error = None

    def debug(self, msg):
        log.debug("yt-dlp: %s", msg)

    info = debug

    def warning(self, msg):
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg):
        self.last_error = msg
        log.debug("yt-dlp error: %s", msg)


def get_ffmpeg_path():
    """Get path to FFmpeg - checks bundled first, then system paths."""
    # Check bundled FFmpeg first (for PyInstaller builds)
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
        if sys.platform == "win32":
            ffmpeg = os.path.join(base_path, "ffmpeg", "ffmpeg.exe")
        else:
            ffmpeg = os.path.join(base_path, "ffmpeg", "ffmpeg")
        if os.path.exists(ffmpeg):
            return os.path.join(base_path, "ffmpeg")

    # Check common system paths (for homebrew/system installs)
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    common_paths = [
        "/opt/homebrew/bin",  # macOS ARM homebrew
        "/usr/local/bin",  # macOS Intel homebrew / Linux
        "/usr/bin",  # Linux system
    ]
    if sys.platform == "win32":
        # package managers put these on PATH, so shutil.which below usually
        # wins first; these cover the install-then-same-shell case, and are
        # read from each tool's own env var so a relocated install still hits
        choco = os.environ.get("CHOCOLATEYINSTALL") or r"C:\ProgramData\chocolatey"
        scoop = os.environ.get("SCOOP") or os.path.join(os.path.expanduser("~"), "scoop")
        common_paths += [
            os.path.join(choco, "bin"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
            os.path.join(scoop, "shims"),
            r"C:\ffmpeg\bin",  # the unzip convention, the one that never lands on PATH
        ]

    for path in common_paths:
        ffmpeg = os.path.join(path, ffmpeg_name)
        if os.path.exists(ffmpeg):
            return path

    # Check if ffmpeg is in PATH
    import shutil

    ffmpeg_in_path = shutil.which("ffmpeg")
    if ffmpeg_in_path:
        return os.path.dirname(ffmpeg_in_path)

    # On Windows, check user/machine environment in case PATH was modified recently
    if sys.platform == "win32":
        try:
            import winreg

            for hkey in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                sub = (
                    r"Environment"
                    if hkey == winreg.HKEY_CURRENT_USER
                    else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                )
                try:
                    with winreg.OpenKey(hkey, sub) as key:
                        val, _ = winreg.QueryValueEx(key, "PATH")
                        for p in val.split(os.pathsep):
                            expanded = os.path.expandvars(p.strip())
                            if expanded and os.path.exists(os.path.join(expanded, "ffmpeg.exe")):
                                return expanded
                except OSError:
                    pass
        except Exception:
            pass

    return None


# yt-dlp's own preference order; deno's runtime only gets fixes until about october 2027
_JS_RUNTIMES = ("deno", "node")


def _js_runtime_candidates(name: str) -> list[str]:
    """PATH first, then the usual install spots, since a Finder-launched app gets a bare PATH."""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    home = os.path.expanduser("~")
    candidates = [shutil.which(name)]
    if name == "deno":
        candidates.append(os.path.join(home, ".deno", "bin", exe))
    if sys.platform == "win32":
        scoop = os.environ.get("SCOOP") or os.path.join(home, "scoop")
        choco = os.environ.get("CHOCOLATEYINSTALL") or r"C:\ProgramData\chocolatey"
        candidates += [
            os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{exe}"),
            os.path.join(scoop, "shims", exe),
            os.path.join(choco, "bin", exe),
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "nodejs", exe),
        ]
    else:
        candidates += [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    return [c for c in dict.fromkeys(candidates) if c and os.path.isfile(c)]


def find_js_runtime() -> tuple[str, str] | None:
    """(name, path) of a javascript runtime yt-dlp supports, which a logged-in youtube
    session needs for its challenge solver. yt-dlp judges the version, so this never
    accepts a runtime yt-dlp would then refuse."""
    from yt_dlp.globals import supported_js_runtimes

    for name in _JS_RUNTIMES:
        runtime = supported_js_runtimes.value.get(name)
        for path in _js_runtime_candidates(name) if runtime else ():
            info = runtime(path=path).info
            if info and info.supported:
                return name, path
    return None


def _read_cookie_jar(path: str):
    """Load a cookies.txt without yt-dlp echoing malformed lines (cookie values) to stderr."""
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(path)
    with contextlib.redirect_stderr(io.StringIO()):
        jar.load()
    return jar


def _cookies_text(path: str) -> str:
    """The file's valid cookies, re-serialized for yt-dlp's private copies."""
    jar = _read_cookie_jar(path)
    fd, tmp = tempfile.mkstemp(prefix="sunnify-cookies-", suffix=".txt")
    os.close(fd)
    try:
        jar.save(tmp)
        with open(tmp, encoding="utf-8") as f:
            return f.read()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def cookies_problem(path: str) -> tuple[str, str] | None:
    """(error code, message) when a youtube cookies file can't be used, else None."""
    if not os.path.isfile(path):
        return "cookies_unusable", f"cookies file not found: {path}"
    try:
        jar = _read_cookie_jar(path)
    except Exception:
        return "cookies_unusable", f"not a netscape cookies.txt file: {path}"
    if not any(
        c.domain.lstrip(".") == "youtube.com" or c.domain.endswith(".youtube.com") for c in jar
    ):
        return "cookies_unusable", f"no youtube.com cookies in {path}"
    if find_js_runtime() is None:
        return (
            "deno_missing",
            "no supported javascript runtime (deno, or node 22+); youtube needs one with cookies",
        )
    return None


# Supported output formats. "lossy" means quality/bitrate applies; "lossless"
# means the ffmpeg postprocessor ignores preferredquality.
SUPPORTED_FORMATS = {
    "mp3": {"ext": "mp3", "lossy": True},
    "m4a": {"ext": "m4a", "lossy": True},
    "opus": {"ext": "opus", "lossy": True},
    "flac": {"ext": "flac", "lossy": False},
    "wav": {"ext": "wav", "lossy": False},
}
SUPPORTED_QUALITIES = ("128", "192", "256", "320")
# "auto" keeps whatever rate the source stream has (YouTube audio is 48 kHz).
SUPPORTED_SAMPLE_RATES = ("auto", "44100", "48000")


def _counted(count: int, noun: str) -> str:
    """ "1 track", "3 tracks": status text never shows "track(s)"."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class _Setting:
    """One user setting: config key, validation, scraper wiring, CLI surface."""

    __slots__ = ("key", "default", "kind", "choices", "scraper_kwarg", "cli_flag", "help")

    def __init__(self, key, default, kind, choices=(), scraper_kwarg=None, cli_flag=None, help=""):
        self.key = key
        self.default = default
        self.kind = kind  # "choice" | "bool" | "path"
        self.choices = choices
        self.scraper_kwarg = scraper_kwarg
        self.cli_flag = cli_flag
        self.help = help

    def coerce(self, value):
        """Validated value or the default - never raises on user data."""
        if self.kind == "bool":
            return value if isinstance(value, bool) else self.default
        if self.kind == "choice":
            return value if value in self.choices else self.default
        return value if isinstance(value, str) or value is None else self.default


# Single source of truth for user settings. Config load/validation, scraper
# construction, and every CLI flag derive from this - a new setting added
# here reaches the CLI with zero CLI changes (the dialog row stays bespoke).
SETTINGS = (
    _Setting("download_path", None, "path", help="where downloads land"),
    _Setting(
        "format",
        "mp3",
        "choice",
        tuple(SUPPORTED_FORMATS),
        scraper_kwarg="audio_format",
        cli_flag="--format",
        help="audio format",
    ),
    _Setting(
        "quality",
        "192",
        "choice",
        SUPPORTED_QUALITIES,
        scraper_kwarg="audio_quality",
        cli_flag="--quality",
        help="bitrate in kbps, lossy formats only",
    ),
    _Setting(
        "sample_rate",
        "auto",
        "choice",
        SUPPORTED_SAMPLE_RATES,
        scraper_kwarg="sample_rate",
        cli_flag="--sample-rate",
        help="output sample rate, applies to mp3/flac/wav",
    ),
    _Setting(
        "include_track_number",
        False,
        "bool",
        scraper_kwarg="include_track_number",
        cli_flag="--track-numbers",
        help="prefix filenames with playlist position",
    ),
    _Setting(
        "artist_first",
        False,
        "bool",
        scraper_kwarg="artist_first",
        cli_flag="--artist-first",
        help='name files "Artist - Song" instead of "Song - Artist"',
    ),
    _Setting(
        "title_only",
        False,
        "bool",
        scraper_kwarg="title_only",
        cli_flag="--title-only",
        help="name files by song title alone; the artist stays in the tags",
    ),
    _Setting(
        "loose_match",
        False,
        "bool",
        scraper_kwarg="loose_match",
        cli_flag="--loose-match",
        help="fall back to the closest result when strict matching finds nothing",
    ),
    _Setting(
        "prefer_extended",
        False,
        "bool",
        scraper_kwarg="prefer_extended",
        cli_flag="--prefer-extended",
        help="look for each track's extended mix first, falling back to the normal version",
    ),
    _Setting(
        "cookies_file",
        None,
        "path",
        scraper_kwarg="cookies_file",
        cli_flag="--cookies",
        help="youtube cookies.txt for networks youtube gates (needs deno or node 22+)",
    ),
)


def scraper_kwargs_from(settings: dict) -> dict:
    """Registry-derived MusicScraper kwargs from a config/settings dict."""
    return {s.scraper_kwarg: settings.get(s.key, s.default) for s in SETTINGS if s.scraper_kwarg}


# Resume manifest: JSON-lines file per playlist folder recording landed tracks,
# so a rate-limited playlist finishes across sessions instead of restarting (#40).
MANIFEST_FILENAME = ".sunnify-manifest.jsonl"

# One writer per playlist folder, across every desktop window and terminal.
LOCK_FILENAME = ".sunnify.lock"


class FolderLockedError(RuntimeError):
    """Another live sunnify process is writing to this folder."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_file_pid(path: str) -> int | None:
    with contextlib.suppress(OSError, ValueError), open(path, encoding="utf-8") as fh:
        return int(fh.read().strip())
    return None


def read_lock_pid(folder: str) -> int | None:
    """Pid of the live process holding folder's lock, or None."""
    pid = _lock_file_pid(os.path.join(folder, LOCK_FILENAME))
    return pid if pid is not None and _pid_alive(pid) else None


# a lock with no pid yet is a process mid-claim, not a stale lock, until it's this old
_LOCK_CLAIM_GRACE_S = 5


@contextlib.contextmanager
def folder_lock(folder: str):
    """Hold folder for this process; a lock left by a dead process is claimed."""
    path = os.path.join(folder, LOCK_FILENAME)
    for _ in range(2):
        try:
            # O_EXCL makes the claim atomic, so two processes starting together can't both win
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _lock_file_pid(path)
            if holder is not None and holder != os.getpid() and _pid_alive(holder):
                raise FolderLockedError(
                    f"another Sunnify download (pid {holder}) is already writing to {folder}; "
                    f"if none is running, delete {LOCK_FILENAME} there"
                ) from None
            if holder is None:
                with contextlib.suppress(OSError):
                    if time.time() - os.path.getmtime(path) < _LOCK_CLAIM_GRACE_S:
                        raise FolderLockedError(
                            f"another Sunnify download is already writing to {folder}"
                        ) from None
            with contextlib.suppress(OSError):
                os.remove(path)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        break
    else:
        raise FolderLockedError(f"another Sunnify download is already writing to {folder}")
    try:
        yield
    finally:
        if read_lock_pid(folder) == os.getpid():
            with contextlib.suppress(OSError):
                os.remove(path)


def _config_dir() -> str:
    """Return the per-user config directory, creating it if needed."""
    import json as _json  # noqa: F401 (used by load/save)

    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    path = os.path.join(base, "Sunnify")
    os.makedirs(path, exist_ok=True)
    return path


def _log_dir() -> str:
    """Return the per-user log directory path (does not create it).

    Pure path computation, no filesystem side effects, so callers that only
    need the string (e.g. a settings tooltip) don't create stray folders;
    setup_logging() and _open_logs() create the dir when they actually use it.

    Uses each platform's conventional spot for app logs (not config), so the
    files are where a user (or a support request) would expect them:
      windows -> %LOCALAPPDATA%\\Sunnify\\logs
      macOS   -> ~/Library/Logs/Sunnify
      linux   -> $XDG_STATE_HOME/sunnify/logs (defaults to ~/.local/state)
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(base, "Sunnify", "logs")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Logs", "Sunnify")
    else:
        base = os.environ.get(
            "XDG_STATE_HOME", os.path.join(os.path.expanduser("~"), ".local", "state")
        )
        path = os.path.join(base, "sunnify", "logs")
    return path


def log_file_path() -> str:
    """Absolute path of the current log file (used by the 'open logs' action)."""
    return os.path.join(_log_dir(), "sunnify.log")


def setup_logging() -> str:
    """Configure file logging once; return the log file path.

    Rotating handler caps disk use at ~6MB total (1MB x 5 backups) so logs are
    diagnostic, never bloat. Idempotent: safe to call more than once. The line
    format is deliberately dense (timestamp, level, function:line) so a single
    log pasted into an issue is enough to pinpoint where a download went wrong.
    A session header records the environment every launch.
    """
    if any(getattr(h, "_sunnify", False) for h in log.handlers):
        return log_file_path()

    path = log_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    handler._sunnify = True  # tag so we don't double-attach on re-call
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(funcName)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    # default INFO stays lean (scales with failures, not track count); set
    # SUNNIFY_DEBUG=1 to get the full per-track + yt-dlp trail for hard cases.
    level = logging.DEBUG if os.environ.get("SUNNIFY_DEBUG") else logging.INFO
    log.setLevel(level)
    log.addHandler(handler)
    log.propagate = False

    try:
        ytdlp_ver = __import__("yt_dlp").version.__version__
    except Exception:
        ytdlp_ver = "?"
    log.info("==== sunnify session start ====")
    log.info(
        "version=%s platform=%s python=%s yt-dlp=%s",
        __version__,
        f"{sys.platform}-{platform.machine()}",
        platform.python_version(),
        ytdlp_ver,
    )
    log.info("ffmpeg=%s", get_ffmpeg_path() or "(not found)")
    log.info("logs=%s level=%s", path, logging.getLevelName(level))
    _install_crash_handlers()
    return path


def _config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


def load_config() -> dict:
    """Load persisted user config. Missing or corrupt file returns defaults.

    Every user setting is validated through the SETTINGS registry;
    star_prompt_shown is internal state, not a setting."""
    import json

    defaults = {s.key: s.default for s in SETTINGS}
    defaults["version"] = 1
    defaults["star_prompt_shown"] = False
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return defaults
        for s in SETTINGS:
            if s.key in data:
                defaults[s.key] = s.coerce(data[s.key])
        if isinstance(data.get("star_prompt_shown"), bool):
            defaults["star_prompt_shown"] = data["star_prompt_shown"]
        return defaults
    except (OSError, json.JSONDecodeError):
        return defaults


def save_config(config: dict) -> None:
    """Persist user config to disk. Best-effort, swallows IO errors."""
    import json

    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError as exc:
        log.warning("could not save config: %s", exc)


GITHUB_REPO = "sunnypatell/sunnify-spotify-downloader"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def _parse_version(s: str) -> tuple:
    """'v2.0.13' / '2.0.13' / '2.0.13-beta' -> (2, 0, 13). Stops at the first non-int part."""
    parts = []
    for chunk in (s or "").strip().lstrip("vV").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        parts.append(int(num))
    return tuple(parts)


def _is_newer_version(latest: str, current: str) -> bool:
    """True if latest is a strictly newer release than current (numeric, not lexical)."""
    lv, cv = _parse_version(latest), _parse_version(current)
    return bool(lv) and lv > cv


def _check_for_update(current: str, timeout: int = 5):
    """Return (latest_version, release_url) if a newer release exists, else None.

    Fail-silent (returns None) on any network/parse error so launch is never
    blocked or crashed by the check.
    """
    try:
        r = requests.get(
            _LATEST_RELEASE_API,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
        if r.status_code != 200:
            log.debug("update check: github api returned %s", r.status_code)
            return None
        data = r.json()
        tag = data.get("tag_name") or ""
        url = data.get("html_url") or _RELEASES_PAGE
        if _is_newer_version(tag, current):
            return (tag.lstrip("vV"), url)
        log.debug("update check: on latest (%s, newest %s)", current, tag or "?")
        return None
    except Exception as exc:  # network/parse must never disrupt launch
        log.debug("update check skipped: %s", exc)
        return None


class UpdateCheckThread(QThread):
    """Runs _check_for_update off the UI thread and signals if a release is newer."""

    update_available = pyqtSignal(str, str)  # (latest_version, release_url)

    def __init__(self, current_version: str):
        super().__init__()
        self._current = current_version

    def run(self):
        result = _check_for_update(self._current)
        if result:
            self.update_available.emit(result[0], result[1])


# Clients for the retry attempt, as a set rather than a preference list:
# android/ios expose streams the default path may not, and tv/web_safari
# supply an alternative URL when those return 403. The combination is what
# recovers a track - measured per client, each alone fails on videos the
# set handles, and yt-dlp's own defaults 403 on some of them too. Kept
# deliberately, not by inertia; validated below so a retired client can't
# rot into a hard failure, and exercised end to end by the youtube_retry
# check in scripts/check_api_status.py.
_RETRY_CLIENTS = ("android", "ios", "tv", "web_safari")


def _retry_player_clients() -> tuple[str, ...]:
    """_RETRY_CLIENTS filtered to the clients yt-dlp still ships.

    YouTube retires client names and yt-dlp follows; naming a dead one is a
    hard extractor error, so unknown names are dropped with a warning that
    says which. Everything dropping out means the retry falls back to
    yt-dlp's maintained defaults, which is degraded but never broken.
    """
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
    except Exception:
        # table moved: our names are still the best guess we have
        log.debug("yt-dlp client table not introspectable; using retry clients unvalidated")
        return _RETRY_CLIENTS
    live = tuple(name for name in _RETRY_CLIENTS if name in INNERTUBE_CLIENTS)
    retired = [name for name in _RETRY_CLIENTS if name not in INNERTUBE_CLIENTS]
    if retired:
        log.warning("retry clients no longer shipped by yt-dlp, dropped: %s", retired)
    if not live:
        log.warning("no retry clients survive; retry now uses yt-dlp's defaults")
    return live


class MusicScraper(QThread):
    PlaylistCompleted = pyqtSignal(str)
    PlaylistID = pyqtSignal(str)
    song_Album = pyqtSignal(str)
    song_meta = pyqtSignal(dict)
    add_song_meta = pyqtSignal(dict)
    count_updated = pyqtSignal(int)
    dlprogress_signal = pyqtSignal(int)
    Resetprogress_signal = pyqtSignal(int)
    resume_skipped = pyqtSignal(int)  # manifest-resumed tracks never reach song_meta
    error_signal = pyqtSignal(str)  # Signal for error messages to UI

    # Max concurrent track downloads. 4 is the measured sweet spot:
    # linear speedup through 4, diminishing returns past 6 (CPU-bound ffmpeg).
    MAX_WORKERS = 4

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        *,
        audio_format: str = "mp3",
        audio_quality: str = "192",
        include_track_number: bool = False,
        artist_first: bool = False,
        title_only: bool = False,
        sample_rate: str = "auto",
        loose_match: bool = False,
        prefer_extended: bool = False,
        cookies_file: str | None = None,
    ):
        super().__init__()
        self.counter = 0  # Initialize counter to zero
        self.session = requests.Session()
        self.spotifydown_api = None
        self._cancel_event = cancel_event or threading.Event()
        self._failed_tracks: list[str] = []  # Track failed downloads
        # per-track notes for the end-of-run report (failed / check / regular);
        # _failed_tracks stays the CLI's source of truth for counts and exit codes
        self._report: list[dict] = []
        self._report_name = ""
        self._report_folder = ""
        # which YouTube pick landed for the track this worker thread is on
        self._tls = threading.local()
        # Output options. audio_format must be a key of SUPPORTED_FORMATS;
        # audio_quality only applies to lossy formats (mp3/m4a/opus).
        self.audio_format = audio_format if audio_format in SUPPORTED_FORMATS else "mp3"
        self.audio_quality = audio_quality if audio_quality in SUPPORTED_QUALITIES else "192"
        self.include_track_number = bool(include_track_number)
        self.artist_first = bool(artist_first)
        self.title_only = bool(title_only)
        self.sample_rate = sample_rate if sample_rate in SUPPORTED_SAMPLE_RATES else "auto"
        # opt-in: when strict title/artist matching fails, fall back to the
        # duration-closest youtube result (recovers cross-script matches);
        # off by default so the wrong-audio safeguard (#52) stays the default.
        self.loose_match = bool(loose_match)
        # opt-in: try the extended mix of each track
        # before the regular match; the regular match stays the fallback.
        self.prefer_extended = bool(prefer_extended)
        self.cookies_file = os.path.expanduser(cookies_file) if cookies_file else None
        self._cookies_text = None
        self._js_runtime = None
        if self.cookies_file:
            # read once here, on the caller's thread: loading redirects stderr
            try:
                self._cookies_text = _cookies_text(self.cookies_file)
                self._js_runtime = find_js_runtime()
                log.info("youtube cookies: on, js runtime: %s", self._js_runtime)
            except Exception as exc:
                log.error("youtube cookies unreadable, running without them: %s", exc)
                self.cookies_file = None
        self._counter_lock = threading.Lock()
        self._failed_lock = threading.Lock()
        self._filename_lock = threading.Lock()
        self._manifest_lock = threading.Lock()
        self._manifest_path: str | None = None
        self._manifest_owners: dict[str, str] = {}
        # youtube blocks per-IP, so it hits every track at once; say it once
        # rather than 300 times, and say what it actually means
        self._network_blocked = False
        self._in_flight_files: set[str] = set()
        # Set to True during parallel playlist downloads so workers can suppress
        # per-track UI noise (label flicker, thumbnail spam, progress bar jitter)
        # that only makes sense for a single active download.
        self._parallel_mode = False
        self._total_tracks = 0

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_event.is_set()

    @contextlib.contextmanager
    def _youtube(self, opts: dict):
        """YoutubeDL for one request, with the user's cookies when set."""
        if not self.cookies_file:
            # no js runtime: anonymous requests keep yt-dlp's js-less client set
            with YoutubeDL({**opts, "js_runtimes": {}}) as ydl:
                yield ydl
            return
        # yt-dlp rewrites its cookie file on close and workers run in parallel,
        # so each request gets a private copy and the user's file is only read
        fd, copy = tempfile.mkstemp(prefix="sunnify-cookies-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._cookies_text)
            name, path = self._js_runtime or ("deno", None)
            runtimes = {name: {"path": path}} if path else {name: {}}
            with YoutubeDL({**opts, "cookiefile": copy, "js_runtimes": runtimes}) as ydl:
                yield ydl
        finally:
            with contextlib.suppress(OSError):
                os.remove(copy)

    _BLOCK_MARKERS = ("sign in to confirm", "not a bot", "confirm you're not a bot")

    def _note_if_network_blocked(self, reason: str) -> None:
        """Emit one plain-language notice the first time YouTube gates us.

        The gate is applied to the whole network, so without this every track
        fails with its own generic message and the real cause never surfaces.
        """
        text = (reason or "").lower()
        if not any(m in text for m in self._BLOCK_MARKERS):
            return
        with self._failed_lock:
            if self._network_blocked:
                return
            self._network_blocked = True
        if self.cookies_file:
            log.error("youtube is gating this network even with cookies; they are likely stale")
            self.error_signal.emit(
                "YouTube is still asking this network to prove it isn't a bot with your "
                "cookies loaded, which usually means they have expired. Export them again."
            )
            return
        log.error("youtube is gating this network (bot check); downloads cannot proceed")
        self.error_signal.emit(
            "YouTube is asking this network to prove it isn't a bot, so downloads are "
            "being refused. This is applied to your IP, not to Sunnify: try another "
            "network or connection, wait a few hours, or use YouTube cookies (see the docs)."
        )

    def _note_report(self, kind, title, artists, detail, url=None):
        """One line for the end-of-run report. kind: "failed" (not downloaded),
        "check" (closest-result pick, may be the wrong audio) or "regular"
        (extended mix asked for, normal version used)."""
        with self._failed_lock:
            self._report.append(
                {"kind": kind, "title": title, "artists": artists, "detail": detail, "url": url}
            )

    def _failure_reason(self, error: Exception | None) -> str:
        """Short why-it-failed for the report, without the track name the
        status-line message repeats."""
        if error is None:
            return "No audio file was produced"
        text = str(error).lower()
        if any(m in text for m in self._BLOCK_MARKERS):
            return "YouTube blocked this network"
        if isinstance(error, ExtractionError):
            return "Unavailable"
        if "no video formats" in text or "no playable audio source" in text:
            return "No confident match on YouTube"
        if "unavailable" in text:
            return "Unavailable on YouTube"
        return str(error).strip().splitlines()[0][:120] or type(error).__name__

    def _landed_title(self, track_title: str) -> str:
        """The title for what actually downloaded: Spotify's says "Radio Edit"
        even when the extended mix landed."""
        how, _url, video_title = getattr(self._tls, "landed", None) or ("match", None, None)
        if how not in ("extended", "full"):
            return track_title
        base = self._EDIT_SUFFIX.sub("", track_title).strip()
        if how == "full":
            return base
        # keep the upload's own label: an "Extended Vocal Mix" isn't an "Extended Mix"
        found = re.search(r"[\(\[]([^\)\]]*\bextended\b[^\)\]]*)[\)\]]", video_title or "", re.I)
        label = found.group(1).strip() if found and len(found.group(1)) <= 40 else "Extended Mix"
        return f"{base} ({label})"

    def _rename_landed(self, path, title, sanitized_artists, track_num, track_id) -> str:
        """Move a landed file to its real title under the same collision rules
        as the first claim. Returns the path it ends up at."""
        folder = os.path.dirname(path)
        name = self._compose_filename(self.sanitize_text(title), sanitized_artists, track_num)
        dest = os.path.join(folder, cap_filename(name))
        with self._filename_lock:
            if dest.casefold() in self._in_flight_files or self._file_belongs_to_other(
                dest, track_id
            ):
                name = self._compose_filename(
                    self.sanitize_text(title), sanitized_artists, track_num, disambig=track_id
                )
                dest = os.path.join(folder, cap_filename(name))
            try:
                os.replace(path, dest)
            except OSError:
                log.warning("could not rename %r to its real title", path, exc_info=True)
                return path
            self._in_flight_files.discard(path.casefold())
            self._in_flight_files.add(dest.casefold())
        return dest

    def _note_landed(self, track_title, artists):
        """Report what the landed pick means for this track, if anything."""
        how, url, video_title = getattr(self._tls, "landed", None) or ("match", None, None)
        if how == "loose":
            self._note_report(
                "check", track_title, artists, f'Closest result: "{video_title}"', url
            )
        elif (
            self.prefer_extended
            and how not in ("extended", "full")
            and not self._is_extended_title(track_title)
        ):
            self._note_report("regular", track_title, artists, "No usable extended mix", url)

    def _get_user_friendly_error(self, error: Exception, track_title: str = "") -> str:
        """Convert exception to user-friendly error message."""
        if isinstance(error, RateLimitError):
            return "Rate limited by Spotify - waiting..."
        if isinstance(error, NetworkError):
            return "Network error - retrying..."
        if isinstance(error, ContentUnavailableError):
            return str(error)
        if isinstance(error, ExtractionError):
            return f"Could not access '{track_title}' - may be unavailable"
        if any(m in str(error).lower() for m in self._BLOCK_MARKERS):
            return f"YouTube blocked this network - '{track_title}' skipped"
        if "HTTP Error 429" in str(error):
            return "YouTube rate limit - waiting..."
        error_text = str(error).lower()
        if (
            "no video formats" in error_text
            or "no playable audio source" in error_text
            or "unavailable" in error_text
        ):
            return f"'{track_title}' not found on YouTube"
        return f"Error: {str(error)[:50]}"

    def ensure_spotifydown_api(self):
        if self.spotifydown_api is None:
            self.spotifydown_api = PlaylistClient(session=self.session)
        return self.spotifydown_api

    def sanitize_text(self, text):
        """Sanitize text for filename usage."""
        return sanitize_filename(text, allow_spaces=True)

    def _compose_filename(self, sanitized_title, sanitized_artists, track_num=None, disambig=None):
        """Filename from the naming settings (#77, #91). Parts arrive
        sanitized, so composition can't change path safety. disambig is a
        track id appended when two tracks resolve to the same name."""
        if self.title_only:
            # a title that sanitizes to nothing falls back rather than yield ".mp3"
            stem = sanitized_title or sanitized_artists or "track"
        elif self.artist_first:
            stem = f"{sanitized_artists} - {sanitized_title}"
        else:
            stem = f"{sanitized_title} - {sanitized_artists}"
        if disambig:
            stem = f"{stem} [{disambig}]"
        fmt = self.audio_format if self.audio_format in SUPPORTED_FORMATS else "mp3"
        ext = SUPPORTED_FORMATS[fmt]["ext"]
        if self.include_track_number and track_num is not None:
            return f"{track_num:02d}. {stem}.{ext}"
        return f"{stem}.{ext}"

    def format_playlist_name(self, metadata: PlaylistInfo):
        owner = metadata.owner or "Spotify"
        return f"{metadata.name} - {owner}".strip(" -")

    def prepare_playlist_folder(self, base_folder, playlist_name):
        os.makedirs(base_folder, exist_ok=True)
        # Same cross-platform sanitizer as track files (windows reserved
        # chars/device names, posix rules - see sanitize_filename's doc refs).
        safe_name = sanitize_filename(playlist_name)
        if not safe_name or safe_name == "Unknown":
            safe_name = "Sunnify Playlist"
        playlist_folder = os.path.join(base_folder, safe_name)
        # If the ascii-allowlist-era folder name ("Name  Owner") exists, keep
        # using it so re-runs resume there instead of orphaning the manifest (#40).
        legacy_name = "".join(
            ch for ch in playlist_name if ch.isalnum() or ch in (" ", "_")
        ).strip()
        legacy_folder = os.path.join(base_folder, legacy_name)
        if legacy_name and legacy_name != safe_name and os.path.isdir(legacy_folder):
            playlist_folder = legacy_folder
        try:
            os.makedirs(playlist_folder, exist_ok=True)
        except OSError:
            log.error("could not create playlist folder %r", playlist_folder, exc_info=True)
            raise
        return playlist_folder

    @staticmethod
    def _widen_search(search_query: str) -> str:
        """Search several YouTube results instead of only the top hit.

        A track's #1 result can be region-locked or removed; `ytsearch1`
        fails the whole download in that case (closes #42). Widening to
        `ytsearch5` lets yt-dlp skip unavailable results and download the
        first one that actually plays.
        """
        if search_query.startswith("ytsearch1:"):
            return "ytsearch5:" + search_query[len("ytsearch1:") :]
        return search_query

    @staticmethod
    def _simplify_search(search_query: str) -> str:
        """Strip parenthetical/bracketed qualifiers for a looser fallback.

        Hyper-specific titles (classical works like `(Wiegenlied, Op. 49,
        No. 4)`, tone tracks like `(528 Hz)`) can return zero YouTube
        matches. Dropping the qualifiers widens the net on a second attempt.
        Returns the original query unchanged if there is nothing to strip.
        """
        _, sep, terms = search_query.partition(":")
        if not sep:
            terms = search_query
        stripped = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", terms)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if not stripped or stripped == terms.strip():
            return search_query
        return f"ytsearch5:{stripped}"

    # Max spotify-vs-youtube length gap to count as the same recording; the
    # top hit is often the music video or an extended cut.
    _DURATION_TOLERANCE_S = 7

    # Wider bound for the title+duration combined check: right title but >30s
    # off means remix/live/extended - fail loudly rather than ship it.
    _DURATION_TOLERANCE_S_WIDE = 30

    @staticmethod
    def _normalize_title(s: str | None) -> str:
        """Lowercase, strip diacritics, drop bracketed segments + `feat./ft.`
        tails, collapse to word characters + spaces (any script). Used on
        BOTH sides of title comparison.

        Critically: this does NOT split on ` - ` because YouTube titles
        commonly use the `Artist - Song` convention; splitting would turn
        "The Weeknd - Blinding Lights" into just "The Weeknd" and lose the
        song name. Spotify-side variant stripping (`Title - Remastered`)
        is handled separately in `_spotify_title_core`.
        """
        if not s:
            return ""
        import unicodedata

        # NFKD + drop combining marks: "Café" -> "Cafe"
        s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
        s = s.lower()
        s = re.sub(r"\([^)]*\)", " ", s)  # "Hello (Remix)" -> "Hello "
        s = re.sub(r"\[[^\]]*\]", " ", s)  # "Hello [Edit]" -> "Hello "
        s = re.sub(r"\b(feat\.?|ft\.?)\s+.*$", "", s, flags=re.IGNORECASE)
        # Strip apostrophes BEFORE the general punctuation->space step so
        # "I'm" becomes "im" not "i m".
        s = s.replace("'", "").replace("’", "")
        # Keep letters/digits/marks from ANY script (#77). Category M is
        # load-bearing: indic/thai vowel signs are combining-class-0 marks,
        # and dropping them collapses distinct words ("दिल"/"दाल").
        s = "".join(
            ch if (ch.isspace() or unicodedata.category(ch)[0] in "LNM") else " " for ch in s
        )
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def _spotify_title_core(s: str | None) -> str:
        """Drop the ` - Variant` suffix Spotify adds to differentiate releases.

        Examples:
            "Bohemian Rhapsody - Remastered 2011" -> "Bohemian Rhapsody"
            "Hello - Live"                        -> "Hello"
            "Sweet Disposition - Remix Edit"      -> "Sweet Disposition"
            "Take-Off"                            -> "Take-Off" (literal hyphen, no spaces)
            "Mi Gente"                            -> "Mi Gente"

        Only applied to the Spotify-side title before fuzzy comparison so
        a YouTube upload titled just "Bohemian Rhapsody" still matches.
        Don't apply this to YouTube titles - they use ` - ` for
        `Artist - Song` and the strip would lose the song name.
        """
        if not s:
            return ""
        return s.split(" - ", 1)[0]

    @classmethod
    def _title_plausibly_matches(cls, yt_title: str | None, expected_title: str | None) -> bool:
        """True when the YouTube candidate's title could reasonably be the
        Spotify track. The Spotify side gets its variant suffix dropped
        first so "Hello - Live" matches a plain "Hello" upload; the YouTube
        side is normalized as-is (its ` - ` is usually `Artist - Song`).
        Substring match for titles >= 4 chars, word-boundary match for
        shorter ones (so a single-letter song name like "i" doesn't match
        every YouTube video)."""
        yt = cls._normalize_title(yt_title)
        target = cls._normalize_title(cls._spotify_title_core(expected_title))
        if not target or not yt:
            return False
        if len(target) >= 4:
            return target in yt
        return target in yt.split()

    @classmethod
    def _artist_tokens(cls, expected_artists: str | None) -> list[str]:
        """Normalized artist names from Spotify's "A, B & C feat. D" string.
        Splits on collaboration separators BEFORE normalizing - normalization
        eats commas, which would collapse the multi-artist string into one
        unmatchable token."""
        if not expected_artists:
            return []
        raw_tokens = re.split(
            r"[,&]+|\s+(?:feat\.?|ft\.?)\s+",
            expected_artists,
            flags=re.IGNORECASE,
        )
        return [t for t in (cls._normalize_title(t) for t in raw_tokens) if t]

    # Words that mark a different recording than an extended mix.
    # Each is only rejected when the Spotify title doesn't carry it itself, so
    # a track that IS a remix can still get its extended remix.
    _EXTENDED_REJECT_WORDS = (
        "live",
        "remix",
        "cover",
        "karaoke",
        "instrumental",
        "acapella",
        "sped up",
        "slowed",
        "nightcore",
        "reverb",
        "8d",
        "hour",
        "loop",
        "mashup",
        "bootleg",
    )

    # An extended mix is longer than the radio edit, but not wildly so.
    _EXTENDED_MIN_GAIN_S = 15
    _EXTENDED_MAX_RATIO = 3.0
    _EXTENDED_MAX_S = 20 * 60

    # What Spotify calls a shortened cut. An artist-named edit, like "(Alesso
    # Edit)", is its own version and stays out of this.
    _EDIT_MARK = r"(?:radio edit|radio version|radio mix|single edit|single version|short edit|short version|edit)"
    _EDIT_SUFFIX = re.compile(
        rf"\s*(?:-\s*{_EDIT_MARK}\s*$|[\(\[]\s*{_EDIT_MARK}\s*[\)\]])", re.IGNORECASE
    )
    # In a full-length candidate these mean it's a cut or fragment, not the song.
    _FULL_REJECT_WORDS = ("edit", "radio", "part", "snippet", "teaser")

    @staticmethod
    def _is_extended_title(title: str | None) -> bool:
        return bool(title) and re.search(r"\bextended\b", title, re.IGNORECASE) is not None

    def _select_extended_match(self, expected_title, expected_artists, expected_duration_s):
        """Settings > Matching: the YouTube URL of the track's extended
        mix, or None so the caller falls back to the regular match.

        A candidate must carry the song title, one of the artists (in the
        title or the channel name), the word "extended", none of the words
        that mark a live/remix/cover/edit-for-tiktok upload, and a duration
        that is longer than the Spotify track without being a 1-hour loop.
        Official uploads win: YouTube's auto-generated "Artist - Topic"
        channels (label-delivered audio) first, then the most viewed. With no
        such upload, a radio edit may instead get its full-length version.
        There is no way to prove an upload is official; this is a best-effort
        filter, and the setting's hint says so.
        """
        if not expected_title or self._is_extended_title(expected_title):
            # already the extended version on Spotify: the regular match finds it
            return None
        core = self._spotify_title_core(expected_title)
        core = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", core)
        core = re.sub(r"\s+", " ", core).strip()
        if not core:
            return None
        query = f"ytsearch10:{core} {expected_artists or ''} extended mix".replace("  ", " ")
        select_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "retries": 5,
            "socket_timeout": 15,
        }
        try:
            with self._youtube(select_opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception as exc:
            log.warning("extended search raised %s: %s", type(exc).__name__, str(exc)[:300])
            return None
        entries = [e for e in (info or {}).get("entries", []) if e and e.get("id")]

        # raw lowercased titles, not _normalize_title: it drops the
        # parentheses that "(Remix)" and "(Live)" live in
        spotify_raw = expected_title.lower()
        rejects = [
            w
            for w in self._EXTENDED_REJECT_WORDS
            if not re.search(rf"\b{re.escape(w)}\b", spotify_raw)
        ]
        artists = self._artist_tokens(expected_artists)

        def acceptable(entry) -> bool:
            title = entry.get("title") or ""
            if not self._is_extended_title(title):
                return False
            if not self._title_plausibly_matches(title, expected_title):
                return False
            raw = title.lower()
            if any(re.search(rf"\b{re.escape(w)}\b", raw) for w in rejects):
                return False
            if artists:
                where = self._normalize_title(
                    f"{title} {entry.get('channel') or ''} {entry.get('uploader') or ''}"
                )
                if not any(a in where for a in artists):
                    return False
            duration = entry.get("duration")
            if not duration:
                return False  # can't tell an extended mix from a radio edit
            if expected_duration_s:
                if duration < expected_duration_s + self._EXTENDED_MIN_GAIN_S:
                    return False
                if duration > expected_duration_s * self._EXTENDED_MAX_RATIO:
                    return False
            return duration <= self._EXTENDED_MAX_S

        def full_length(entry) -> bool:
            """The long cut of a radio edit often has no "extended" in its title
            (it's just the song), so with no such word the upload must come from
            the artist's own channel."""
            title = entry.get("title") or ""
            duration = entry.get("duration")
            if not duration or not expected_duration_s:
                return False
            if not self._title_plausibly_matches(title, expected_title):
                return False
            raw = title.lower()
            words = [*rejects, *self._FULL_REJECT_WORDS]
            if any(re.search(rf"\b{re.escape(w)}\b", raw) for w in words):
                return False
            channel = self._normalize_title(entry.get("channel") or entry.get("uploader") or "")
            if not any(a in channel for a in artists):
                return False
            return (
                expected_duration_s + self._EXTENDED_MIN_GAIN_S
                <= duration
                <= min(expected_duration_s * self._EXTENDED_MAX_RATIO, self._EXTENDED_MAX_S)
            )

        how = "extended"
        pool = [e for e in entries if acceptable(e)]
        if not pool and self._EDIT_SUFFIX.search(expected_title):
            how = "full"
            pool = [e for e in entries if full_length(e)]
        if not pool:
            log.info("no extended mix found for %r, using the regular version", expected_title)
            return None

        def rank(entry):
            channel = entry.get("channel") or entry.get("uploader") or ""
            return (channel.endswith(" - Topic"), entry.get("view_count") or 0)

        chosen = max(pool, key=rank)
        log.info("extended mix for %r: %s (%r)", expected_title, chosen["id"], chosen.get("title"))
        self._tls.pick = (how, chosen.get("title"))
        return f"https://www.youtube.com/watch?v={chosen['id']}"

    def _select_youtube_match(
        self, search_query, expected_duration_s, expected_title=None, expected_artists=None
    ):
        """Return the best YouTube watch URL for a search, or None.

        Selection policy (closes #52):
          1. Filter to candidates whose title plausibly matches the Spotify
             track title (substring / word match on a normalized form).
             Rules out the failure mode where YouTube's top hit is a
             DIFFERENT track by the SAME artist with a similar duration -
             e.g. searching "Mi Gente DJ Goja audio" returns "Dj Goja -
             Mi Chico" at the top, only ~2s off the real Mi Gente, and the
             prior pure-duration matcher would happily pick it and write
             Mi Gente metadata onto Mi Chico audio.
          2. Require an artist plausibly appearing in the YouTube title too,
             and return None if none does. Artists written only in non-latin
             script skip this gate, since YouTube usually romanizes them.
          3. Among the resulting pool, pick the duration-closest if duration
             is known, but reject the whole result if even the best
             candidate's duration is >30s off the Spotify track - that means
             the closest title-matching upload is a remix / live cover /
             extended edit, and shipping a 5-minute remix under a 2-minute
             track's metadata still corrupts the library.
          4. If no candidate's title passes, return None. The caller treats
             that as "not found on YouTube" - strictly better than shipping
             the wrong audio under the right cover. With loose_match on, the
             None of steps 2 and 4 becomes _loose_pick's duration-closest result.

        `expected_title` + `expected_artists` are optional so legacy callers
        that haven't been updated still work via the older trust-the-top-
        hit-unless-duration-is-clearly-off policy.
        """
        select_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "retries": 5,
            "socket_timeout": 15,
            "concurrent_fragment_downloads": 4,
        }
        log.debug(
            "yt search: query=%r title=%r artists=%r dur=%ss",
            search_query,
            expected_title,
            expected_artists,
            expected_duration_s,
        )
        try:
            with self._youtube(select_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
        except Exception as exc:
            # The single most useful log line for triage: a real exception here
            # (bot-challenge, SSL, network, region block) is otherwise invisible
            # because the caller only ever sees "not found on YouTube".
            log.warning("yt search raised %s: %s", type(exc).__name__, str(exc)[:300])
            return None
        entries = [e for e in (info or {}).get("entries", []) if e and e.get("id")]
        if not entries:
            # Empty results with no exception is the classic bot-block / rate-limit
            # / region signature. Distinct from "found results but filtered out".
            log.warning(
                "yt search returned 0 entries for %r (bot-block/network/region?)", search_query
            )
            return None
        log.debug("yt search returned %d entries", len(entries))

        if expected_title:
            title_ok = [
                e for e in entries if self._title_plausibly_matches(e.get("title"), expected_title)
            ]
            if not title_ok:
                log.info(
                    "title filter rejected all %d candidates for %r (e.g. %r)",
                    len(entries),
                    expected_title,
                    (entries[0].get("title") if entries else None),
                )
                if self.loose_match:
                    return self._loose_pick(entries, expected_duration_s)
                return None

            # Require an artist in the YouTube title alongside the song name:
            # rejects right-title-wrong-uploader remixes. Prefer not-found
            # over wrong audio (#52). Title-only when no artists are known.
            pool = title_ok
            if expected_artists:
                artist_tokens = self._artist_tokens(expected_artists)
                if artist_tokens:
                    pool = [
                        e
                        for e in title_ok
                        if any(
                            artist in self._normalize_title(e.get("title") or "")
                            for artist in artist_tokens
                        )
                    ]
                    if not pool and not any(re.search(r"[a-z0-9]", t) for t in artist_tokens):
                        # Native-script artists usually appear romanized on
                        # YouTube, so an all-non-latin token set would reject
                        # everything; fall back to title-only.
                        log.info(
                            "artist gate skipped: no latin token in %r, falling back to title-only",
                            expected_artists,
                        )
                        pool = title_ok
                    if not pool:
                        log.info(
                            "artist filter rejected all %d title-matches (artists=%r)",
                            len(title_ok),
                            expected_artists,
                        )
                        if self.loose_match:
                            return self._loose_pick(title_ok, expected_duration_s)
                        return None

            chosen = pool[0]
            if expected_duration_s:
                timed = [e for e in pool if e.get("duration")]
                if timed:
                    chosen = min(timed, key=lambda e: abs(e["duration"] - expected_duration_s))
                    # Right title+artist but wildly wrong duration = live
                    # cover / extended mix; still the wrong audio to ship.
                    off = abs(chosen["duration"] - expected_duration_s)
                    if off > self._DURATION_TOLERANCE_S_WIDE:
                        log.info(
                            "closest candidate duration off by %.0fs (>%ss), rejecting",
                            off,
                            self._DURATION_TOLERANCE_S_WIDE,
                        )
                        return None
            log.debug("selected youtube video %s", chosen["id"])
            return f"https://www.youtube.com/watch?v={chosen['id']}"

        # Legacy path - kept for any caller that hasn't been updated yet.
        chosen = entries[0]
        if expected_duration_s:
            top_duration = chosen.get("duration")
            top_off = top_duration is None or (
                abs(top_duration - expected_duration_s) > self._DURATION_TOLERANCE_S
            )
            if top_off:
                timed = [e for e in entries if e.get("duration")]
                if timed:
                    chosen = min(timed, key=lambda e: abs(e["duration"] - expected_duration_s))
        return f"https://www.youtube.com/watch?v={chosen['id']}"

    def _loose_pick(self, candidates, expected_duration_s):
        """Opt-in fallback (Settings: "use closest result if no match").

        When strict title/artist matching finds nothing, return the
        duration-closest candidate (or the top result if durations are
        missing). Recovers cross-script matches the ascii title filter can
        never make - e.g. a Latin Spotify title vs a Greek/Cyrillic/CJK
        youtube title. Trades the never-grab-the-wrong-audio guarantee for
        coverage, which is why the caller only reaches here when loose_match
        is on (off by default).
        """
        if not candidates:
            return None
        chosen = candidates[0]
        if expected_duration_s:
            timed = [e for e in candidates if e.get("duration")]
            if timed:
                chosen = min(timed, key=lambda e: abs(e["duration"] - expected_duration_s))
        log.warning("loose match (no confident title/artist match): selected %s", chosen["id"])
        self._tls.pick = ("loose", chosen.get("title"))
        return f"https://www.youtube.com/watch?v={chosen['id']}"

    def download_track_audio(
        self,
        search_query,
        destination,
        expected_duration_s=None,
        expected_title=None,
        expected_artists=None,
        track_title=None,
        track_artists=None,
    ):
        """Download the best YouTube match for a track to `destination`.

        expected_title/expected_artists turn on strict matching. track_title/
        track_artists only feed the extended-mix search, for callers that
        keep the legacy matcher (the single-track path)."""
        # Check for FFmpeg first
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            if sys.platform == "win32":
                instructions = "Install via: winget install Gyan.FFmpeg or choco install ffmpeg"
            elif sys.platform == "darwin":
                instructions = "Install via: brew install ffmpeg (macOS)"
            else:
                instructions = "Install via: apt install ffmpeg (Linux)"
            raise RuntimeError(f"FFmpeg not found! {instructions}")

        fmt = self.audio_format if self.audio_format in SUPPORTED_FORMATS else "mp3"
        ext = SUPPORTED_FORMATS[fmt]["ext"]
        is_lossy = SUPPORTED_FORMATS[fmt]["lossy"]

        base, _ = os.path.splitext(destination)
        output_template = base + ".%(ext)s"
        postprocessor = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
        }
        if is_lossy:
            postprocessor["preferredquality"] = self.audio_quality

        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": output_template,
            "ffmpeg_location": ffmpeg_path,
            "retries": 5,
            "socket_timeout": 15,
            "concurrent_fragment_downloads": 4,
            "ignoreerrors": True,
            "postprocessors": [postprocessor],
        }
        if self.sample_rate != "auto" and fmt in ("mp3", "flac", "wav"):
            # "extractaudio" is the only key yt-dlp matches for this PP.
            # opus excluded (libopus is 48 kHz-only); m4a excluded (may
            # stream-copy aac, where ffmpeg silently drops -ar).
            ydl_opts["postprocessor_args"] = {"extractaudio": ["-ar", self.sample_rate]}

        expected_path = base + "." + ext

        # Widened query then a simplified fallback; success = an audio file
        # actually on disk, so an empty search fails loudly.
        queries = [self._widen_search(search_query)]
        fallback = self._simplify_search(search_query)
        if fallback not in queries:
            queries.append(fallback)

        # yt-dlp's maintained defaults first, then a different client family:
        # youtube bot-challenges per-IP and per-client, so a second family is
        # worth one retry. Never a pinned list - see _retry_player_clients.
        fallback_opts = dict(ydl_opts)
        retry_clients = _retry_player_clients()
        if retry_clients:
            fallback_opts["extractor_args"] = {"youtube": {"player_client": list(retry_clients)}}
        attempts = [("default", ydl_opts), ("fallback", fallback_opts)]

        def candidate_urls():
            # (url, how it was picked, youtube title); extended mix first when
            # asked for, and a failed download of it falls through to the
            # regular match like any other unplayable result
            extended = None
            if self.prefer_extended:
                self._tls.pick = None
                extended = self._select_extended_match(
                    expected_title or track_title,
                    expected_artists or track_artists,
                    expected_duration_s,
                )
                if extended:
                    yield (extended, *(self._tls.pick or ("extended", None)))
            for query in queries:
                self._tls.pick = None
                url = self._select_youtube_match(
                    query,
                    expected_duration_s,
                    expected_title=expected_title,
                    expected_artists=expected_artists,
                )
                if url and url != extended:
                    yield (url, *(self._tls.pick or ("match", None)))

        self._tls.landed = None
        for video_url, how, video_title in candidate_urls():
            for label, opts in attempts:
                # per-attempt bridge captures yt-dlp's own error even when
                # ignoreerrors swallows it (no exception, no file)
                ytlog = _YtdlpLog()
                try:
                    with self._youtube({**opts, "logger": ytlog}) as ydl:
                        ydl.extract_info(video_url, download=True)
                except Exception as exc:
                    log.warning(
                        "download attempt (%s) failed for %s: %s",
                        label,
                        video_url,
                        str(exc)[:300],
                    )
                    self._note_if_network_blocked(str(exc))
                else:
                    if not os.path.exists(expected_path):
                        # the silent case: yt-dlp produced no file without
                        # raising; the bridge holds the real reason
                        reason = (ytlog.last_error or "no error reported by yt-dlp").strip()
                        log.warning(
                            "download attempt (%s) produced no file for %s: %s",
                            label,
                            video_url,
                            reason[:300],
                        )
                        self._note_if_network_blocked(reason)
                if os.path.exists(expected_path):
                    if label != "default":
                        log.info("recovered via %s player clients", label)
                    self._tls.landed = (how, video_url, video_title)
                    return expected_path

        log.debug("no playable audio landed for query set %r", queries)
        raise RuntimeError("no playable audio source found on YouTube for this track")

    def download_http_file(self, url, destination):
        response = self.session.get(url, stream=True, timeout=60)
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    progress = int(downloaded / total * 100)
                    self.dlprogress_signal.emit(progress)
        return destination

    def _download_one_track(self, track, playlist_folder_path, default_cover_url, track_num=0):
        """Download a single track. Runs inside a ThreadPoolExecutor worker.

        Returns None on success, the track title on failure (for _failed_tracks).
        Qt signals emitted here cross thread boundaries via queued connections,
        which is safe.

        In parallel mode (self._parallel_mode), per-track UI noise (song_meta
        preview, per-byte progress) is suppressed because those widgets are
        single-track and would flicker with N workers in flight. add_song_meta
        still fires so ID3 tags + cover art get written to every mp3.

        track_num (1-based) is passed through to song_meta so the ID3 TRCK
        frame can be populated for playlist ordering.
        """
        if self.is_cancelled():
            return None

        track_title = track.title
        artists = track.artists
        sanitized_title = self.sanitize_text(track_title)
        sanitized_artists = self.sanitize_text(artists)

        filename = self._compose_filename(sanitized_title, sanitized_artists, track_num)
        filepath = os.path.join(playlist_folder_path, cap_filename(filename))

        # Collision guard: distinct tracks can resolve to the same name
        # (always possible, likely under title_only), and parallel workers
        # racing os.path.exists would clobber each other. Claim under a lock;
        # if taken in-flight, on a case-folding filesystem, or on disk owned
        # by a different track per the manifest, suffix the track id.
        with self._filename_lock:
            claim = filepath.casefold()
            if claim in self._in_flight_files or self._file_belongs_to_other(filepath, track.id):
                log.info(
                    "filename collision: %r already claimed, suffixing track id %s",
                    os.path.basename(filepath),
                    track.id,
                )
                filename = self._compose_filename(
                    sanitized_title, sanitized_artists, track_num, disambig=track.id
                )
                filepath = os.path.join(playlist_folder_path, cap_filename(filename))
                claim = filepath.casefold()
            self._in_flight_files.add(claim)

        # Per-track enrichment: the playlist embed carries no per-track cover
        # url (the "all 300 songs have the same cover" report) and no album
        # at all (#104), so fetch /embed/track/{id} when any of them is
        # missing. ~100-300ms per track, overlapped by other workers in
        # parallel mode.
        cover_url = track.cover_url
        album_name = track.album or ""
        release_date = track.release_date or ""
        if (
            (not cover_url or not album_name or not release_date)
            and track.id
            and self.spotifydown_api is not None
            and not self.is_cancelled()
        ):
            try:
                enriched = self.spotifydown_api.get_track(track.id)
                if enriched:
                    if enriched.cover_url:
                        cover_url = enriched.cover_url
                    if not album_name and enriched.album:
                        album_name = enriched.album
                    if not release_date and enriched.release_date:
                        release_date = enriched.release_date
            except SpotifyDownAPIError as exc:
                log.debug("track enrichment failed for '%s': %s", track_title, exc)

        cover_url = cover_url or default_cover_url

        song_meta = {
            "title": track_title,
            "artists": artists,
            "album": album_name,
            "releaseDate": release_date,
            "cover": cover_url or "",
            "file": filepath,
            "trackNumber": track_num,
        }

        # Preview panel shows whichever track most recently started; the
        # worker race is fine (better than a blank panel).
        self.song_meta.emit(dict(song_meta))

        try:
            if os.path.exists(filepath):
                self._record_in_manifest(track.id, filepath)
                self.add_song_meta.emit(song_meta)
                self._finish_track_ui(ok=True)
                return None

            search_query = f"ytsearch1:{track_title} {artists} audio"
            expected_dur = (track.duration_ms / 1000) if track.duration_ms else None
            try:
                final_path = self.download_track_audio(
                    search_query,
                    filepath,
                    expected_duration_s=expected_dur,
                    expected_title=track_title,
                    expected_artists=artists,
                )
            except Exception as error_status:
                error_msg = self._get_user_friendly_error(error_status, track_title)
                self.error_signal.emit(error_msg)
                # concise reason at WARNING (the per-attempt yt-dlp reason is
                # already logged above); full traceback only when verbose
                log.warning("track failed: '%s': %s", track_title, str(error_status)[:200])
                log.debug("track failure traceback for '%s'", track_title, exc_info=True)
                with self._failed_lock:
                    self._failed_tracks.append(track_title)
                self._note_report(
                    "failed", track_title, artists, self._failure_reason(error_status)
                )
                self._finish_track_ui(ok=False)
                return track_title

            if not final_path or not os.path.exists(final_path):
                self.error_signal.emit(f"'{track_title}' - download failed")
                log.warning(
                    "track produced no audio file (no confident match or blocked): '%s'",
                    track_title,
                )
                with self._failed_lock:
                    self._failed_tracks.append(track_title)
                self._note_report("failed", track_title, artists, self._failure_reason(None))
                self._finish_track_ui(ok=False)
                return track_title

            self._note_landed(track_title, artists)
            real_title = self._landed_title(track_title)
            if real_title != track_title:
                song_meta["title"] = real_title
                final_path = self._rename_landed(
                    final_path, real_title, sanitized_artists, track_num, track.id
                )
                filepath = final_path  # the claim moved with the file
            self._record_in_manifest(track.id, final_path)
            song_meta["file"] = final_path
            self.add_song_meta.emit(song_meta)
            self._finish_track_ui(ok=True)
            return None
        finally:
            with self._filename_lock:
                self._in_flight_files.discard(filepath.casefold())

    def _finish_track_ui(self, ok: bool) -> None:
        """Update counter + progress bar after a track completes or fails."""
        self.increment_counter()
        if self._parallel_mode and self._total_tracks > 0:
            # Aggregate progress across all workers: show how many tracks are
            # done as a percentage. Avoids the N-workers-jittering-one-bar
            # problem where per-byte emits from 4 downloads make the bar jump.
            pct = int(self.counter / self._total_tracks * 100)
            self.dlprogress_signal.emit(min(pct, 100))
        elif ok:
            self.dlprogress_signal.emit(100)

    def _load_manifest(self, folder: str) -> set:
        """Load the set of track IDs already downloaded into `folder`.

        The manifest is a JSON-lines file inside the folder; each line is a
        `{"id", "file"}` record. Entries whose file is missing are ignored so
        a track the user deleted re-downloads. Returns the set of valid IDs
        and arms `_manifest_path` for incremental appends during this run.
        """
        import json

        path = os.path.join(folder, MANIFEST_FILENAME)
        self._manifest_path = path
        self._manifest_owners = {}
        done: set[str] = set()
        if not os.path.exists(path):
            return done
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    track_id = record.get("id")
                    filename = record.get("file")
                    if track_id and filename and os.path.exists(os.path.join(folder, filename)):
                        done.add(track_id)
                        self._manifest_owners[filename.casefold()] = track_id
        except OSError:
            return set()
        return done

    def _file_belongs_to_other(self, filepath: str, track_id) -> bool:
        """True when a file already at this path is a different track's per
        the manifest - a title collision across runs, not a resume hit (#91).
        An unmapped existing file stays a resume hit so crash recovery
        (files present, manifest lost) keeps skipping instead of re-downloading."""
        if not track_id or not os.path.exists(filepath):
            return False
        owner = self._manifest_owners.get(os.path.basename(filepath).casefold())
        return owner is not None and owner != track_id

    def _record_in_manifest(self, track_id, filepath: str) -> None:
        """Append a completed track to the manifest (thread-safe).

        Append-only JSON-lines so recording a track is O(1) regardless of how
        large the playlist is. Failures are swallowed: the manifest is an
        optimization for resuming, never a hard dependency of a download.
        """
        if not track_id or not self._manifest_path:
            return
        import json

        record = json.dumps({"id": track_id, "file": os.path.basename(filepath)})
        with self._manifest_lock:
            # keep the ownership map live so a same-title track later in this
            # run suffixes instead of mistaking the finished file for its own
            self._manifest_owners.setdefault(os.path.basename(filepath).casefold(), track_id)
            try:
                with open(self._manifest_path, "a", encoding="utf-8") as handle:
                    handle.write(record + "\n")
            except OSError:
                pass

    def scrape_playlist(self, spotify_playlist_link, music_folder):
        # Reset mutable state so repeat invocations on the same scraper
        # instance don't carry stale counters or failure lists.
        with self._counter_lock:
            self.counter = 0
        with self._failed_lock:
            self._failed_tracks.clear()
            self._report.clear()
        self._report_name = ""
        self._report_folder = ""
        with self._filename_lock:
            self._in_flight_files.clear()
        self._parallel_mode = False
        self._total_tracks = 0

        # A playlist or an album both flow through here. detect_spotify_url_type
        # returns ("playlist"|"album", id); albums reuse the same embed-parsing
        # path with the album embed endpoint (closes #38).
        content_type, playlist_id = detect_spotify_url_type(spotify_playlist_link)
        if content_type not in ("playlist", "album"):
            raise ValueError("Expected a playlist or album URL")
        self.PlaylistID.emit(playlist_id)

        # Bail before network work if stop was already clicked.
        if self.is_cancelled():
            self.PlaylistCompleted.emit("Download cancelled")
            return

        try:
            spotify_api = self.ensure_spotifydown_api()
        except SpotifyDownAPIError as exc:
            raise RuntimeError(str(exc)) from exc

        metadata = spotify_api.get_playlist_metadata(playlist_id, content_type=content_type)
        playlist_display_name = self.format_playlist_name(metadata)
        self.song_Album.emit(playlist_display_name)

        playlist_folder_path = self.prepare_playlist_folder(music_folder, playlist_display_name)
        self._report_name = playlist_display_name
        self._report_folder = playlist_folder_path
        with folder_lock(playlist_folder_path):
            # Resume support: skip tracks already downloaded in a previous run of
            # this folder before fetching their (rate-limited) metadata, so a huge
            # playlist can be finished across multiple sessions (closes #40).
            already_done = self._load_manifest(playlist_folder_path)
            if already_done:
                self.resume_skipped.emit(len(already_done))
                self.error_signal.emit(
                    f"Resuming: skipping {_counted(len(already_done), 'downloaded track')}"
                )

            # Materialize the generator (not thread-safe; count also picks the
            # worker pool size). Cancel is checked between yields so huge
            # playlists abort mid-fetch instead of after the full window.
            expected_total = metadata.track_count or 0
            tracks: list = []
            for track in spotify_api.iter_playlist_tracks(
                playlist_id, content_type=content_type, skip_ids=already_done
            ):
                if self.is_cancelled():
                    break
                tracks.append(track)
                if expected_total and len(tracks) % 10 == 0:
                    self.error_signal.emit(
                        f"Fetching track metadata ({len(tracks)} of {expected_total})..."
                    )
            self._total_tracks = len(tracks)

            if self.is_cancelled():
                self.PlaylistCompleted.emit("Download cancelled")
                return

            self.Resetprogress_signal.emit(0)

            # Small playlists don't benefit from parallelism. Keep 1 worker for
            # playlists under 3 tracks to preserve the single-track UI feel.
            worker_count = 1 if len(tracks) < 3 else min(self.MAX_WORKERS, len(tracks))
            self._parallel_mode = worker_count > 1

            log.info(
                "%s scrape: name=%r id=%s tracks=%d (resume-skipped %d) mode=%s workers=%d fmt=%s/%s naming=%s",
                content_type,
                playlist_display_name,
                playlist_id,
                len(tracks),
                len(already_done),
                "parallel" if self._parallel_mode else "sequential",
                worker_count,
                self.audio_format,
                self.audio_quality,
                "title-only"
                if self.title_only
                else ("artist-first" if self.artist_first else "default"),
            )

            # Canonical playlist position over enumerate order: spclient yields
            # in http-completion order on >100-track playlists (#51). Enumerate
            # only for albums/small playlists, which arrive already ordered.
            def _track_num_for(track, idx):
                return track.position if getattr(track, "position", None) else idx

            if worker_count == 1:
                for idx, track in enumerate(tracks, start=1):
                    if self.is_cancelled():
                        break
                    # Reset the per-track progress bar at the top of each iteration
                    # so the single-track UI behaves the way it always has.
                    self.Resetprogress_signal.emit(0)
                    self._download_one_track(
                        track,
                        playlist_folder_path,
                        metadata.cover_url,
                        track_num=_track_num_for(track, idx),
                    )
            else:
                try:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=worker_count
                    ) as executor:
                        futures = [
                            executor.submit(
                                self._download_one_track,
                                track,
                                playlist_folder_path,
                                metadata.cover_url,
                                _track_num_for(track, idx),
                            )
                            for idx, track in enumerate(tracks, start=1)
                        ]
                        for future in concurrent.futures.as_completed(futures):
                            if self.is_cancelled():
                                # Cancel remaining futures that haven't started
                                # yet. In-flight downloads check is_cancelled at
                                # their own top and return early.
                                for f in futures:
                                    f.cancel()
                                break
                            try:
                                future.result()
                            except Exception as exc:
                                # _download_one_track handles its own errors; this is
                                # only framework-level fallout (a worker crashed hard).
                                log.error("unexpected worker error", exc_info=exc)
                                self.error_signal.emit(f"Unexpected worker error: {exc}")
                finally:
                    # Reset only after executor shutdown: in-flight workers that
                    # observed False mid-run would emit single-track UI signals.
                    self._parallel_mode = False

            if self.is_cancelled():
                log.info("scrape cancelled by user (%d done before cancel)", self.counter)
                self.PlaylistCompleted.emit("Download cancelled")
                return

            # Report completion with failed track count
            ok = max(self._total_tracks - len(self._failed_tracks), 0)
            if self._failed_tracks:
                log.info("scrape done: %d ok, %d failed", ok, len(self._failed_tracks))
                log.info("failed tracks: %s", " | ".join(self._failed_tracks))
                self.PlaylistCompleted.emit(
                    f"Done, {_counted(len(self._failed_tracks), 'track')} failed"
                )
            else:
                log.info("scrape done: %d ok, 0 failed", self._total_tracks)
                self.PlaylistCompleted.emit("Download complete")

    def returnSPOT_ID(self, link):
        """Extract playlist ID from Spotify URL."""
        return extract_playlist_id(link)

    def scrape_track(self, spotify_track_link, music_folder):
        """Download a single track from Spotify."""
        url_type, track_id = detect_spotify_url_type(spotify_track_link)
        if url_type != "track":
            raise ValueError("Expected a track URL")

        try:
            spotify_api = self.ensure_spotifydown_api()
        except SpotifyDownAPIError as exc:
            raise RuntimeError(str(exc)) from exc

        track = spotify_api.get_track(track_id)
        log.info(
            "single-track scrape: %r by %r id=%s fmt=%s/%s",
            track.title,
            track.artists,
            track_id,
            self.audio_format,
            self.audio_quality,
        )
        self.song_Album.emit("Single Track Download")

        if not os.path.exists(music_folder):
            os.makedirs(music_folder)

        self.Resetprogress_signal.emit(0)

        track_title = track.title
        artists = track.artists
        sanitized_title = self.sanitize_text(track_title)
        sanitized_artists = self.sanitize_text(artists)
        filename = self._compose_filename(sanitized_title, sanitized_artists)
        filepath = os.path.join(music_folder, cap_filename(filename))

        album_name = track.album or ""
        release_date = track.release_date or ""
        cover_url = track.cover_url

        song_meta = {
            "title": track_title,
            "artists": artists,
            "album": album_name,
            "releaseDate": release_date,
            "cover": cover_url or "",
            "file": filepath,
            "trackNumber": 1,
        }

        self.song_meta.emit(dict(song_meta))

        if os.path.exists(filepath):
            self.add_song_meta.emit(song_meta)
            self.increment_counter()
            self.PlaylistCompleted.emit("Track already exists")
            return

        # Download via YouTube search
        search_query = f"ytsearch1:{track_title} {artists} audio"
        expected_dur = (track.duration_ms / 1000) if track.duration_ms else None
        try:
            final_path = self.download_track_audio(
                search_query,
                filepath,
                expected_duration_s=expected_dur,
                track_title=track_title,
                track_artists=artists,
            )
        except Exception as error_status:
            error_msg = self._get_user_friendly_error(error_status, track_title)
            log.error("single-track download failed: '%s'", track_title, exc_info=True)
            # record it: the CLI derives its failure count and exit code from
            # this list, and a silent single-track failure exited 0 with no file
            with self._failed_lock:
                self._failed_tracks.append(track_title)
            self.PlaylistCompleted.emit(error_msg)
            return

        if not final_path or not os.path.exists(final_path):
            log.warning("single-track produced no audio file: '%s'", track_title)
            with self._failed_lock:
                self._failed_tracks.append(track_title)
            self.PlaylistCompleted.emit("Download failed - no audio file produced")
            return

        real_title = self._landed_title(track_title)
        if real_title != track_title:
            song_meta["title"] = real_title
            final_path = self._rename_landed(final_path, real_title, sanitized_artists, None, None)
        song_meta["file"] = final_path
        self.add_song_meta.emit(song_meta)
        self.increment_counter()
        self.dlprogress_signal.emit(100)
        self.PlaylistCompleted.emit("Download complete")

    def increment_counter(self):
        with self._counter_lock:
            self.counter += 1
            current = self.counter
        self.count_updated.emit(current)  # Emit the signal with the updated count


# Scraper Thread
class ScraperThread(QThread):
    progress_update = pyqtSignal(str)

    def __init__(
        self,
        spotify_link,
        music_folder=None,
        cancel_event: threading.Event | None = None,
        **scraper_opts,
    ):
        super().__init__()
        self.spotify_link = spotify_link
        self.music_folder = music_folder or os.path.join(os.getcwd(), "music")
        self._cancel_event = cancel_event or threading.Event()
        # MusicScraper's explicit signature validates the option names
        self.scraper = MusicScraper(cancel_event=self._cancel_event, **scraper_opts)

    def request_cancel(self):
        """Request cancellation of the download."""
        self._cancel_event.set()

    def run(self):
        self.progress_update.emit("Starting")
        try:
            # Detect URL type and handle accordingly
            url_type, _ = detect_spotify_url_type(self.spotify_link)
            if url_type == "track":
                self.scraper.scrape_track(self.spotify_link, self.music_folder)
            else:
                self.scraper.scrape_playlist(self.spotify_link, self.music_folder)
        except Exception as e:
            log.exception("scrape failed for %s", self.spotify_link)
            self.progress_update.emit(f"{e}")


def _fetch_cover_bytes(url: str) -> bytes | None:
    """Download cover image bytes, returning None on any failure."""
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except (requests.RequestException, OSError) as exc:
        log.debug("cover fetch failed: %s", exc)
    return None


def _detect_image_mime(data: bytes) -> str:
    """Return the MIME string for image bytes, sniffed from magic numbers.

    Spotify currently serves JPEG covers; this function exists so a future
    switch to PNG (or a mid-flight content-type change) doesn't silently
    produce broken cover-art frames mis-tagged as JPEG.

    ref: JPEG magic ff d8 ff (any JFIF/Exif variant), per ISO/IEC 10918-1
    ref: PNG signature 89 50 4e 47 0d 0a 1a 0a, per W3C PNG spec section 5.2
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"  # safe default; Spotify has served JPEG since 2015


def _write_metadata_mp3(filename: str, tags: dict, cover_bytes: bytes | None) -> None:
    """Write ID3 tags + embedded cover art to an MP3.

    Tags and the APIC cover frame are written as ID3v2.3 with UTF-16 text
    encoding instead of mutagen's v2.4 / UTF-8 default. v2.3 + UTF-16 is the
    lowest common denominator that's understood by older iTunes, Windows
    Media Player, most car head-units, and stock Android players, none of
    which read v2.4 APIC frames reliably (closes #46).

    ref: ID3v2.3 spec section 3.3 (only encoding values $00 ISO-8859-1
         and $01 Unicode UTF-16+BOM are defined) https://id3.org/id3v2.3.0
    ref: ID3v2.4 spec adds $02 UTF-16BE and $03 UTF-8 (which is what
         mutagen writes by default) https://id3.org/id3v2.4.0-frames
    ref: mutagen `update_to_v23()` downgrades any UTF-8 frames to UTF-16
         before saving as v2.3 https://mutagen.readthedocs.io/en/latest/api/id3.html
    """
    audio = EasyID3(filename)
    audio["title"] = tags.get("title", "")
    audio["artist"] = tags.get("artists", "")
    audio["album"] = tags.get("album", "")
    audio["date"] = tags.get("releaseDate", "")
    track_num = tags.get("trackNumber") or 0
    if track_num:
        audio["tracknumber"] = str(track_num)
    # EasyID3.save() defaults to v2.4 + UTF-8. Passing v2_version=3 tells
    # mutagen to downgrade text frames to a v2.3-allowed encoding (UTF-16
    # with BOM for non-ASCII, Latin-1 for ASCII) before writing.
    audio.save(v2_version=3)
    if cover_bytes:
        id3 = ID3(filename)
        mime = _detect_image_mime(cover_bytes)
        # encoding=1 (UTF-16+BOM) is the only Unicode encoding v2.3 defines.
        # type=3 is "Cover (front)" per the v2.3 APIC enum.
        id3.add(APIC(encoding=1, mime=mime, type=3, desc="Cover", data=cover_bytes))
        id3.update_to_v23()
        id3.save(v2_version=3)


def _write_metadata_m4a(filename: str, tags: dict, cover_bytes: bytes | None) -> None:
    """Write iTunes atom tags + embedded cover art to an M4A/MP4.

    iTunes atoms (`covr`, `\xa9nam`, etc.) are a stable, version-less spec
    used by every MP4-aware player. The only knob worth getting right is
    the cover-art image format, which we sniff so a future PNG cover from
    Spotify doesn't get mis-tagged as JPEG.

    ref: mutagen MP4Tags atom keys (`\xa9nam`/`\xa9ART`/`\xa9alb`/`\xa9day`/
         `trkn`/`covr`) and MP4Cover.FORMAT_JPEG/PNG, which is the spec we
         write against https://mutagen.readthedocs.io/en/latest/api/mp4.html
    """
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(filename)
    audio["\xa9nam"] = tags.get("title", "")
    audio["\xa9ART"] = tags.get("artists", "")
    audio["\xa9alb"] = tags.get("album", "")
    date = tags.get("releaseDate", "")
    if date:
        audio["\xa9day"] = date
    track_num = tags.get("trackNumber") or 0
    if track_num:
        audio["trkn"] = [(int(track_num), 0)]
    if cover_bytes:
        mime = _detect_image_mime(cover_bytes)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
    audio.save()


def _write_metadata_flac(filename: str, tags: dict, cover_bytes: bytes | None) -> None:
    """Write Vorbis comments + embedded cover art to a FLAC.

    FLAC's Picture block carries an explicit MIME string, so we sniff the
    image type and pass it through. Vorbis comments are always UTF-8 and
    universally supported, so nothing else is version-sensitive here.

    ref: FLAC METADATA_BLOCK_PICTURE format spec
         https://xiph.org/flac/format.html#metadata_block_picture
    """
    from mutagen.flac import FLAC, Picture

    audio = FLAC(filename)
    audio["title"] = tags.get("title", "")
    audio["artist"] = tags.get("artists", "")
    audio["album"] = tags.get("album", "")
    date = tags.get("releaseDate", "")
    if date:
        audio["date"] = date
    track_num = tags.get("trackNumber") or 0
    if track_num:
        audio["tracknumber"] = str(track_num)
    if cover_bytes:
        # add_picture() appends; clear first so a re-tag doesn't stack duplicate covers
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3  # Front cover
        pic.mime = _detect_image_mime(cover_bytes)
        pic.desc = "Cover"
        pic.data = cover_bytes
        audio.add_picture(pic)
    audio.save()


_METADATA_WRITERS = {
    ".mp3": _write_metadata_mp3,
    ".m4a": _write_metadata_m4a,
    ".flac": _write_metadata_flac,
}


class WritingMetaTagsThread(QThread):
    tags_success = pyqtSignal(str)

    def __init__(self, tags, filename):
        super().__init__()
        self.tags = tags
        self.filename = filename

    def run(self):
        """Write tags + cover art synchronously, dispatching on file extension.

        Each container uses a different tag system (ID3 for mp3, iTunes atoms
        for m4a, Vorbis comments for flac). Opus/WAV are skipped with a log
        line; those formats have limited or no standard cover-art story that
        would repay the extra dependency surface for this project's scope.
        """
        try:
            log.info("writing tags: %s", self.filename)
            ext = os.path.splitext(self.filename)[1].lower()
            writer = _METADATA_WRITERS.get(ext)
            if writer is None:
                self.tags_success.emit("Tags skipped (unsupported container)")
                return

            cover_bytes = _fetch_cover_bytes(self.tags.get("cover", ""))
            writer(self.filename, self.tags, cover_bytes)
            self.tags_success.emit("Tags added successfully")
        except Exception:
            log.error("tag write failed: %s", self.filename, exc_info=True)


class DownloadThumbnail(QThread):
    thumbnail_ready = pyqtSignal(bytes)  # Signal to safely update UI from main thread

    def __init__(self, url, main_UI):
        super().__init__()
        self.url = url
        self.main_UI = main_UI
        self.thumbnail_ready.connect(self._update_ui)

    def run(self):
        if not self.url:
            return
        try:
            response = requests.get(self.url, stream=True, timeout=10)
            # no image clears the preview, so it can't keep the last song's cover
            self.thumbnail_ready.emit(response.content if response.status_code == 200 else b"")
        except Exception as exc:
            log.debug("thumbnail fetch failed: %s", exc)
            self.thumbnail_ready.emit(b"")

    def _update_ui(self, data):
        """Update UI from main thread via signal."""
        # workers overlap: an older track's cover can arrive after the current one's
        if self.url != self.main_UI._preview_cover_url:
            return
        pic = QImage()
        pic.loadFromData(data)
        self.main_UI.CoverImg.setPixmap(QPixmap(pic))
        self.main_UI.CoverImg.show()


class SettingsDialog(QDialog):
    """Download folder + audio format + quality in one dialog."""

    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.setWindowTitle("Sunnify Settings")
        self.setModal(True)
        # min width so long macOS paths fit; height is sized at the end once hints exist
        self.setMinimumWidth(560)
        self._config = dict(config)

        from PyQt6.QtWidgets import QLabel, QLineEdit

        # Read-only QLineEdit: long paths scroll instead of truncating;
        # tooltip carries the full value.
        self._folder_label = QLineEdit(self._config.get("download_path") or "(not set)")
        self._folder_label.setReadOnly(True)
        self._folder_label.setFrame(False)
        # QLineEdit pads its text 2px; pull it back onto the row's left edge
        self._folder_label.setTextMargins(-2, 0, 0, 0)
        self._folder_label.setCursorPosition(0)
        self._folder_label.setToolTip(self._folder_label.text())
        self._folder_label.setStyleSheet(
            "QLineEdit { background: transparent; padding: 0; font-size: 12px; }"
        )
        browse = QPushButton("Choose...")
        browse.clicked.connect(self._choose_folder)

        self._format_cb = QComboBox()
        for key in SUPPORTED_FORMATS:
            self._format_cb.addItem(key)
        self._format_cb.setCurrentText(self._config.get("format", "mp3"))
        self._format_cb.currentTextChanged.connect(self._on_format_change)

        self._quality_cb = QComboBox()
        for q in SUPPORTED_QUALITIES:
            self._quality_cb.addItem(f"{q} kbps")
        current_q = self._config.get("quality", "192")
        self._quality_cb.setCurrentText(f"{current_q} kbps")

        self._include_track_number_cb = ToggleSwitch()
        self._include_track_number_cb.setChecked(self._config.get("include_track_number", False))

        # the three naming shapes are mutually exclusive, so they present as
        # one pick-one control; underneath they map to the artist_first and
        # title_only config booleans the engine and CLI flags already use
        self._filename_style_cb = QComboBox()
        self._filename_style_cb.addItems(["Song - Artist", "Artist - Song", "Song only"])
        if self._config.get("title_only", False):
            self._filename_style_cb.setCurrentText("Song only")
        elif self._config.get("artist_first", False):
            self._filename_style_cb.setCurrentText("Artist - Song")

        self._filename_preview = QLabel()
        self._filename_preview.setStyleSheet("font-weight: 600;")
        self._include_track_number_cb.toggled.connect(self._update_filename_preview)
        self._filename_style_cb.currentTextChanged.connect(self._update_filename_preview)

        # display text <-> stored value; stored value is what ffmpeg -ar gets
        self._sample_rate_labels = {
            "auto": "auto (keep source)",
            "44100": "44.1 kHz",
            "48000": "48 kHz",
        }
        self._sample_rate_cb = QComboBox()
        for value in SUPPORTED_SAMPLE_RATES:
            self._sample_rate_cb.addItem(self._sample_rate_labels[value])
        current_sr = self._config.get("sample_rate", "auto")
        if current_sr not in SUPPORTED_SAMPLE_RATES:
            current_sr = "auto"
        self._sample_rate_cb.setCurrentText(self._sample_rate_labels[current_sr])

        self._loose_match_cb = ToggleSwitch()
        self._loose_match_cb.setChecked(self._config.get("loose_match", False))

        self._prefer_extended_cb = ToggleSwitch()
        self._prefer_extended_cb.setChecked(self._config.get("prefer_extended", False))

        # initial enable/disable sync needs every dependent combo to exist
        self._on_format_change(self._format_cb.currentText())
        self._update_filename_preview()

        from PyQt6.QtGui import QPalette
        from PyQt6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QWidget

        # Grouped panels of rows, each row: name + hint on the left, control
        # on the right. Everything shares one left edge, so no label column
        # sized by the longest name. Rows are QFrame+QHBoxLayout blocks rather
        # than a QFormLayout, which clips word-wrapped hints.
        _groups = [
            (
                "Output",
                [
                    (
                        "Download folder",
                        browse,
                        "Each playlist or album gets its own folder inside it.",
                        self._folder_label,
                    ),
                    (
                        "Audio format",
                        self._format_cb,
                        "mp3 plays everywhere. m4a is smaller at the same quality. "
                        "flac and wav lose nothing further, at a much larger size.",
                    ),
                    (
                        "Audio quality",
                        self._quality_cb,
                        "mp3, m4a and opus only. YouTube's source is ~128 kbps: higher "
                        "settings lose less of it but can't add to it.",
                    ),
                    (
                        "Sample rate",
                        self._sample_rate_cb,
                        "auto keeps YouTube's 48 kHz; 44.1 kHz suits CDs and older "
                        "players. mp3, flac and wav only.",
                    ),
                ],
            ),
            (
                "File names",
                [
                    (
                        "Track number",
                        self._include_track_number_cb,
                        'Adds "01. " so files sort in playlist order.',
                    ),
                    (
                        "Filename style",
                        self._filename_style_cb,
                        '"Song only" still writes every tag; songs with the same title '
                        "get a short id instead of overwriting. Files already on disk "
                        "keep their names.",
                    ),
                    ("Preview", self._filename_preview, ""),
                ],
            ),
            (
                "Matching",
                [
                    (
                        "Use closest result if no match",
                        self._loose_match_cb,
                        "Off skips a track rather than risk the wrong audio. On takes the "
                        "closest length, which recovers titles in other scripts (Greek, "
                        "Cyrillic, Korean) but can let a cover or remix through.",
                    ),
                    (
                        "Prefer extended mixes",
                        self._prefer_extended_cb,
                        "Tries each song's extended mix first (official channel, then most "
                        "viewed) and, for a radio edit, the artist's full-length upload. "
                        "Falls back to the normal version. Nothing proves an upload is "
                        "official, so give the files a listen.",
                    ),
                ],
            ),
        ]

        tints = _palette_tints(self)
        _fg = self.palette().color(QPalette.ColorRole.WindowText)
        for switch in (
            self._include_track_number_cb,
            self._loose_match_cb,
            self._prefer_extended_cb,
        ):
            switch.set_colors(
                off=QColor(_fg.red(), _fg.green(), _fg.blue(), 70),
                ring=QColor(_fg.red(), _fg.green(), _fg.blue(), 0),
                focus=QColor(_fg.red(), _fg.green(), _fg.blue(), 120),
            )
        for combo in (
            self._format_cb,
            self._quality_cb,
            self._sample_rate_cb,
            self._filename_style_cb,
        ):
            combo.setFixedWidth(180)

        def _setting_row(title: str, control, hint_text: str, extra=None) -> QFrame:
            container = QFrame()
            row = QHBoxLayout(container)
            row.setContentsMargins(16, 12, 16, 12)
            row.setSpacing(16)
            text = QVBoxLayout()
            text.setSpacing(4)
            name = QLabel(title)
            name.setStyleSheet("font-weight: 500;")
            text.addWidget(name)
            if extra is not None:
                text.addWidget(extra)
            if hint_text:
                hint = QLabel(hint_text)
                hint.setWordWrap(True)
                hint.setStyleSheet(f"color: {tints['hint']}; font-size: 11px;")
                hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                text.addWidget(hint)
            row.addLayout(text, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            return container

        def _group(title: str, settings: list, first: bool) -> QWidget:
            host = QWidget()
            box = QVBoxLayout(host)
            box.setContentsMargins(0, 0 if first else 24, 0, 0)
            box.setSpacing(8)
            name = QLabel(title)
            name.setStyleSheet("font-size: 14px; font-weight: 600;")
            box.addWidget(name)
            box.addWidget(_rounded_panel([_setting_row(*row) for row in settings], tints))
            return host

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        # Gives users a one-click way to grab the log file to attach to a bug
        # report, without hunting through ~/Library/Logs or %LOCALAPPDATA%.
        open_logs = btns.addButton("Open logs folder", QDialogButtonBox.ButtonRole.ActionRole)
        open_logs.setToolTip(log_file_path())
        open_logs.clicked.connect(self._open_logs)

        rows_host = QWidget()
        rows = QVBoxLayout(rows_host)
        rows.setSpacing(0)
        rows.setContentsMargins(4, 4, 4, 8)
        for index, (title, settings) in enumerate(_groups):
            rows.addWidget(_group(title, settings, first=index == 0))
        rows.addStretch(1)

        # settings scroll when the screen can't fit them (small laptops,
        # windows dpi scaling); the ok/cancel row never leaves the screen
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(rows_host)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(btns)

        # activate() first: hint labels word-wrap, so their heights are
        # height-for-width and only correct after layout activation. Then
        # open at the content's natural size, clamped to the screen so small
        # laptops and dpi scaling scroll instead of clipping.
        rows.activate()
        layout.activate()
        content_w = max(rows_host.sizeHint().width() + 40, 620)
        content_h = (
            rows.totalHeightForWidth(rows_host.sizeHint().width()) + btns.sizeHint().height() + 48
        )
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            content_w = min(content_w, int(avail.width() * 0.9))
            content_h = min(content_h, int(avail.height() * 0.9))
        self.resize(content_w, content_h)

    def _open_logs(self):
        """Reveal the log folder in the OS file manager."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        log_dir = _log_dir()
        with contextlib.suppress(OSError):
            os.makedirs(log_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir))

    def _choose_folder(self):
        start = (
            self._folder_label.text()
            if os.path.isdir(self._folder_label.text())
            else os.path.expanduser("~")
        )
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Download Folder",
            start,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            # Only append "Sunnify" when the user picked a non-Sunnify folder,
            # otherwise re-selecting the existing destination creates nested
            # Sunnify/Sunnify/... paths.
            chosen = (
                folder
                if os.path.basename(folder.rstrip(os.sep)) == "Sunnify"
                else os.path.join(folder, "Sunnify")
            )
            self._folder_label.setText(chosen)
            self._folder_label.setCursorPosition(0)
            self._folder_label.setToolTip(chosen)

    def _on_format_change(self, fmt: str) -> None:
        """Lossless formats (flac/wav) ignore the bitrate selector. The
        sample-rate selector only applies to formats that always transcode
        (mp3/flac/wav): opus is 48 kHz-only and m4a may stream-copy."""
        is_lossy = SUPPORTED_FORMATS.get(fmt, {}).get("lossy", True)
        self._quality_cb.setEnabled(is_lossy)
        self._sample_rate_cb.setEnabled(fmt in ("mp3", "flac", "wav"))

    def _update_filename_preview(self) -> None:
        style = self._filename_style_cb.currentText()
        stem = "Song" if style == "Song only" else style
        prefix = "01. " if self._include_track_number_cb.isChecked() else ""
        self._filename_preview.setText(f"{prefix}{stem}.mp3")

    def result_config(self) -> dict:
        self._config["download_path"] = self._folder_label.text()
        self._config["format"] = self._format_cb.currentText()
        self._config["quality"] = self._quality_cb.currentText().split()[0]
        self._config["include_track_number"] = self._include_track_number_cb.isChecked()
        style = self._filename_style_cb.currentText()
        self._config["title_only"] = style == "Song only"
        if style == "Song only":
            # keep the stored order preference so switching back remembers it
            self._config.setdefault("artist_first", False)
        else:
            self._config["artist_first"] = style == "Artist - Song"
        label_to_value = {v: k for k, v in self._sample_rate_labels.items()}
        self._config["sample_rate"] = label_to_value.get(self._sample_rate_cb.currentText(), "auto")
        self._config["loose_match"] = self._loose_match_cb.isChecked()
        self._config["prefer_extended"] = self._prefer_extended_cb.isChecked()
        return self._config


def _palette_tints(widget) -> dict:
    """rgba() strings derived from the widget's text color, so plain dialogs
    read on light (win/linux) and dark (mac) themes alike."""
    from PyQt6.QtGui import QPalette

    fg = widget.palette().color(QPalette.ColorRole.WindowText)
    rgb = f"{fg.red()}, {fg.green()}, {fg.blue()}"
    return {
        "hint": f"rgba({rgb}, 165)",
        "panel": f"rgba({rgb}, 12)",
        "rule": f"rgba({rgb}, 28)",
    }


def _rounded_panel(rows: list, tints: dict):
    """Rows in one rounded panel, hairlines between them."""
    from PyQt6.QtWidgets import QFrame

    panel = QFrame()
    panel.setObjectName("roundedPanel")
    panel.setStyleSheet(
        f"QFrame#roundedPanel {{ background-color: {tints['panel']}; border-radius: 12px; }}"
    )
    inner = QVBoxLayout(panel)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(0)
    for index, row in enumerate(rows):
        if index:
            rule = QFrame()
            rule.setFixedHeight(1)
            rule.setStyleSheet(f"background-color: {tints['rule']}; margin: 0 16px;")
            inner.addWidget(rule)
        inner.addWidget(row)
    return panel


# report sections: kind -> (title, one-line explanation)
REPORT_SECTIONS = {
    "failed": (
        "Not downloaded",
        "These are not in the folder. Running the same link again retries only these.",
    ),
    "check": (
        "Check these",
        "Downloaded from the closest YouTube result, which may be a cover or the wrong song.",
    ),
    "regular": (
        "Normal version used",
        "No extended mix turned up, so the regular version was downloaded.",
    ),
}


def report_summary(report: dict) -> str:
    """ "21 of 24 downloaded · 3 failed · 2 to check" for the dialog and button."""
    entries = report.get("entries", [])
    failed = sum(1 for e in entries if e["kind"] == "failed")
    check = sum(1 for e in entries if e["kind"] == "check")
    regular = sum(1 for e in entries if e["kind"] == "regular")
    total = report.get("total") or 0
    parts = [f"{max(total - failed, 0)} of {total} downloaded" if total else ""]
    if failed:
        parts.append(f"{failed} failed")
    if check:
        parts.append(f"{check} to check")
    if regular:
        parts.append(f"{regular} not extended")
    return " · ".join(p for p in parts if p)


def report_text(report: dict) -> str:
    """Plain-text copy of the report, for the clipboard."""
    lines = [report.get("name") or "Sunnify download", report_summary(report)]
    for kind, (title, _hint) in REPORT_SECTIONS.items():
        rows = [e for e in report.get("entries", []) if e["kind"] == kind]
        if not rows:
            continue
        lines += ["", f"{title} ({len(rows)})"]
        for e in rows:
            line = f"- {e['title']} · {e['artists']}: {e['detail']}"
            if e.get("url"):
                line += f" ({e['url']})"
            lines.append(line)
    return "\n".join(lines) + "\n"


class RunReportDialog(QDialog):
    """End-of-run report: what didn't download, and what to double-check.
    Non-modal, so a queued download carries on underneath it."""

    def __init__(self, parent, report: dict):
        super().__init__(parent)
        from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QWidget

        self.setWindowTitle("Download report")
        self.setModal(False)
        self.setMinimumWidth(520)
        self._report = report
        tints = _palette_tints(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(4)
        name = QLabel(report.get("name") or "Download")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(name)
        self._summary = QLabel(report_summary(report))
        self._summary.setStyleSheet(f"color: {tints['hint']};")
        layout.addWidget(self._summary)
        layout.addSpacing(12)

        host = QWidget()
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(24)
        if report.get("network_blocked"):
            banner = QLabel(
                "YouTube asked this network to prove it isn't a bot, so most downloads "
                "were refused. That check is on your IP, not on Sunnify: try another "
                "network, wait a few hours, or add YouTube cookies in Settings."
            )
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background-color: rgba(226, 52, 88, 0.12); color: palette(window-text);"
                "border-radius: 12px; padding: 12px 16px;"
            )
            rows.addWidget(banner)

        for kind, (title, hint) in REPORT_SECTIONS.items():
            entries = [e for e in report.get("entries", []) if e["kind"] == kind]
            if not entries:
                continue
            group = QVBoxLayout()
            group.setSpacing(4)
            header = QLabel(f"{title} ({len(entries)})")
            header.setStyleSheet("font-size: 14px; font-weight: 600;")
            group.addWidget(header)
            explain = QLabel(hint)
            explain.setWordWrap(True)
            explain.setStyleSheet(f"color: {tints['hint']}; font-size: 11px;")
            group.addWidget(explain)
            group.addSpacing(4)
            group.addWidget(_rounded_panel([self._row(e, tints) for e in entries], tints))
            rows.addLayout(group)
        rows.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.close)
        copy = btns.addButton("Copy list", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self._copy)
        if report.get("folder"):
            folder = btns.addButton("Open folder", QDialogButtonBox.ButtonRole.ActionRole)
            folder.clicked.connect(self._open_folder)
        layout.addSpacing(8)
        layout.addWidget(btns)

        host.adjustSize()
        screen = self.screen() or QApplication.primaryScreen()
        height = host.sizeHint().height() + 150
        if screen is not None:
            height = min(height, int(screen.availableGeometry().height() * 0.8))
        self.resize(560, max(height, 260))

    @staticmethod
    def _row(entry, tints):
        from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy

        container = QFrame()
        row = QHBoxLayout(container)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel(entry["title"])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight: 500;")
        text.addWidget(title)
        detail = QLabel(" · ".join(p for p in (entry.get("artists"), entry.get("detail")) if p))
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail.setStyleSheet(f"color: {tints['hint']}; font-size: 12px;")
        text.addWidget(detail)
        row.addLayout(text, 1)
        if entry.get("url") and entry["kind"] != "failed":
            listen = QPushButton("Open")
            listen.setToolTip(entry["url"])
            listen.clicked.connect(lambda _=False, url=entry["url"]: webbrowser.open(url))
            row.addWidget(listen, 0, Qt.AlignmentFlag.AlignVCenter)
        return container

    def _copy(self):
        QApplication.clipboard().setText(report_text(self._report))
        self._summary.setText(report_summary(self._report) + " · copied")

    def _open_folder(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._report["folder"]))


class _ToastCard(QDialog):
    """Frameless card shared by the update and star notices. Sizes are px in
    qss with no font family, like the main window, so it uses the OS font."""

    def __init__(self, parent, window_title, eyebrow, title, body, dismiss, action, on_action):
        super().__init__(parent)
        from PyQt6.QtWidgets import QFrame, QLabel

        self.setWindowTitle(window_title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # no OS shadow: macOS would trace a hard outline around our soft one
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setModal(True)

        ink, mute = "#15151F", "#5E5E70"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 32)  # room for the drop shadow
        # fractional dpi under-measures the wrapped body and squeezes the card (#64)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(432)
        card.setStyleSheet("QFrame#card{background:#FFFFFF;border-radius:16px;}")
        shadow = QGraphicsDropShadowEffect(blurRadius=48, xOffset=0, yOffset=16)
        shadow.setColor(QColor(20, 10, 40, 110))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(24, 24, 24, 20)
        v.setSpacing(0)
        small = QLabel(eyebrow)
        small.setStyleSheet(f"color:{mute};font-size:12px;")
        v.addWidget(small)
        v.addSpacing(4)
        name = QLabel(title)
        name.setStyleSheet(f"color:{ink};font-size:20px;font-weight:600;")
        v.addWidget(name)
        v.addSpacing(12)
        msg = QLabel(body)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color:{mute};font-size:13px;")
        v.addWidget(msg)
        v.addSpacing(20)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        later = QPushButton(dismiss)
        later.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        later.setFixedHeight(40)
        later.setStyleSheet(
            f"QPushButton{{background:transparent;color:{mute};border:none;"
            "font-size:13px;font-weight:500;text-align:left;}"
            f"QPushButton:hover{{color:{ink};}}"
        )
        later.clicked.connect(self.reject)
        footer.addWidget(later)
        footer.addStretch(1)
        go = QPushButton(action)
        go.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        go.setFixedHeight(40)
        go.setStyleSheet(
            "QPushButton{background:#1ED760;color:#06240F;border:none;border-radius:10px;"
            "font-size:13px;font-weight:600;padding:0 20px;}"
            "QPushButton:hover{background:#3BE477;}"
        )
        go.clicked.connect(on_action)
        footer.addWidget(go)
        v.addLayout(footer)

    def _open_and_accept(self, url: str, what: str):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl(url)):
            log.warning("could not open %s in browser: %s", what, url)
        self.accept()


class UpdateNotifier(_ToastCard):
    """'New version available' card. Static copy, dynamic versions, so there's
    no per-release content to maintain. Download opens the releases page
    (where the auto-generated changelog already lives)."""

    def __init__(self, parent, current: str, latest: str, url: str):
        self._url = url
        super().__init__(
            parent,
            "Update available",
            "Update available",
            f"Sunnify {current} → {latest}",
            "Download the new version from the releases page to get the latest "
            "fixes and improvements.",
            "Remind me later",
            "Download",
            self._open_releases,
        )

    def _open_releases(self):
        self._open_and_accept(self._url, "releases page")


class StarPromptNotifier(_ToastCard):
    """One-time 'star the repo' card, shown the moment the first song of the
    user's first download lands on disk (owner call: value is proven by a
    real file and the user is mid-wait; huge playlists never reach
    'complete' in one sitting, so completion was the wrong hook). Shown
    exactly once per install: the config flag is persisted before the dialog
    opens, so even a crash mid-dialog can never make it nag twice."""

    def __init__(self, parent):
        self._url = f"https://github.com/{GITHUB_REPO}"
        super().__init__(
            parent,
            "Enjoying Sunnify?",
            "First song downloaded",
            "Enjoying Sunnify?",
            "A star on GitHub keeps this project alive: it's how new people find "
            "Sunnify, and it takes five seconds. This asks once and never again.",
            "Maybe later",
            "Star on GitHub",
            self._open_repo,
        )

    def _open_repo(self):
        self._open_and_accept(self._url, "repo page")


# Main Window
# inner text widths of the activity card and the preview drawer (see Template.py)
ACTIVITY_TEXT_WIDTH = 248
PREVIEW_TEXT_WIDTH = 288


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        """MainWindow constructor"""
        super().__init__()
        self.setupUi(self)

        # Load persisted user config so format/quality/folder survive restarts
        self._config = load_config()
        self.download_path = self._config.get("download_path") or self._get_default_download_path()
        self._download_path_set = bool(self._config.get("download_path"))
        self._active_threads = []  # Keep references to running threads to prevent GC crashes
        self._is_downloading = False  # Track download state for stop button
        self._queue: collections.deque[str] = collections.deque()
        self._current_link = ""
        self._has_track = False  # did the current run get as far as naming a song?
        self._idle_placeholder = self.PlaylistLink.placeholderText()
        self._cancel_event = threading.Event()  # Event for cooperative thread cancellation

        # the window is transparent, so each card draws its own shadow
        for card in (self.frame, self.SONGINFORMATION):
            card.setGraphicsEffect(
                QGraphicsDropShadowEffect(
                    blurRadius=20, xOffset=0, yOffset=2, color=QColor(0, 0, 0, 90)
                )
            )
        self.PlaylistLink.returnPressed.connect(self._on_link_entered)
        self.DownloadBtn.clicked.connect(self.on_returnButton)

        self.showPreviewCheck.stateChanged.connect(self.show_preview)

        self.Closed.clicked.connect(self.exitprogram)
        self.Select_Home.clicked.connect(self.Linkedin)
        self.SettingsBtn.clicked.connect(self.open_settings)
        self._preview_cover_url = None
        self._last_report: dict | None = None
        self._report_dialog = None
        self.ReportBtn.clicked.connect(self.show_report)
        counter_font = self.CounterLabel.font()
        counter_font.setFeature(QFont.Tag("tnum"), 1)
        self.CounterLabel.setFont(counter_font)

        # check for a newer release in the background; fail-silent, shows a toast only if found
        self._update_thread = UpdateCheckThread(__version__)
        self._update_thread.update_available.connect(self._show_update_notifier)
        self._active_threads.append(self._update_thread)
        self._update_thread.start()

    @pyqtSlot(str, str)
    def _show_update_notifier(self, latest: str, url: str):
        if QApplication.activeModalWidget() is not None:
            # never stack on another toast; the check simply runs again next launch
            log.info("update notifier skipped: another modal dialog is active")
            return
        log.info("update available: %s -> %s; showing notifier", __version__, latest)
        UpdateNotifier(self, __version__, latest, url).exec()

    @pyqtSlot(int)
    def _maybe_show_star_prompt(self, count: int):
        """One-time star ask the moment the first song of the user's first
        run lands on disk. Skipped after a Stop (a beg right then reads as
        nagging). Deferred cases (update toast on screen) retry naturally
        when the next song lands - the flag only persists once the dialog
        actually shows, so the one shot is never burned silently."""
        if self._config.get("star_prompt_shown"):
            return
        # count ticks per finished track either way, so a fully failed run
        # would otherwise beg for a star having saved nothing
        if count - self._failed_track_count() < 1:
            return
        if self._cancel_event.is_set():
            return
        if QApplication.activeModalWidget() is not None:
            # never stack on the update notifier; the next landed song retries
            log.info("star prompt deferred: another modal dialog is active")
            return
        # persist before showing so a crash mid-dialog can never re-prompt
        self._config["star_prompt_shown"] = True
        save_config(self._config)
        log.info("first song landed - showing one-time star prompt")
        StarPromptNotifier(self).exec()

    def _get_default_download_path(self):
        """Get a sensible default download path that's writable."""
        # Try user's Music folder first
        home = os.path.expanduser("~")
        music_folder = os.path.join(home, "Music", "Sunnify")

        # On Windows, Music might be in a different location
        if sys.platform == "win32":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
                )
                music_folder = os.path.join(winreg.QueryValueEx(key, "My Music")[0], "Sunnify")
                winreg.CloseKey(key)
            except Exception:
                music_folder = os.path.join(home, "Music", "Sunnify")

        return music_folder

    def _ensure_download_path(self):
        """Ensure download path exists and is writable. Returns True if valid."""
        try:
            os.makedirs(self.download_path, exist_ok=True)
            # Test write access
            test_file = os.path.join(self.download_path, ".sunnify_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return True
        except OSError as exc:
            log.error("download path not writable: %s (%s)", self.download_path, exc)
            return False

    def _prompt_download_location(self):
        """Prompt user to select download location. Returns True if selected."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Download Folder",
            os.path.expanduser("~"),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            # Keep downloads contained in a "Sunnify" subfolder, but avoid
            # creating nested Sunnify/Sunnify/... paths when the user picked
            # a folder that's already named Sunnify.
            if os.path.basename(folder.rstrip(os.sep)) == "Sunnify":
                self.download_path = folder
            else:
                self.download_path = os.path.join(folder, "Sunnify")
            self._download_path_set = True
            self._config["download_path"] = self.download_path
            save_config(self._config)
            return True
        return False

    def open_settings(self):
        """Full settings dialog: folder + audio format + bitrate."""
        cfg_for_dialog = dict(self._config)
        cfg_for_dialog["download_path"] = self.download_path
        dialog = SettingsDialog(self, cfg_for_dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new = dialog.result_config()
            if new.get("download_path"):
                self.download_path = new["download_path"]
                self._download_path_set = True
            self._config.update({s.key: s.coerce(new.get(s.key, s.default)) for s in SETTINGS})
            self._config["download_path"] = self.download_path
            save_config(self._config)
            self.statusMsg.setText("Settings saved")

    @pyqtSlot()
    def on_returnButton(self):
        # If already downloading, stop the download
        if self._is_downloading:
            self._stop_download()
            return

        links = self.PlaylistLink.text().split()
        if not links:
            self.statusMsg.setText("Please enter a Spotify URL")
            return

        if not get_ffmpeg_path():
            if sys.platform == "win32":
                instructions = (
                    "Install FFmpeg on Windows using:\n"
                    "  winget install Gyan.FFmpeg\n"
                    "or\n"
                    "  choco install ffmpeg\n\n"
                    "Then restart Sunnify."
                )
            elif sys.platform == "darwin":
                instructions = "Install FFmpeg on macOS using:\n  brew install ffmpeg"
            else:
                instructions = "Install FFmpeg on Linux using:\n  sudo apt install ffmpeg"
            self.statusMsg.setText("FFmpeg not found")
            QMessageBox.critical(
                self,
                "FFmpeg Required",
                f"FFmpeg is required for audio downloads and conversion but was not found on your system.\n\n{instructions}",
            )
            return

        cookies = self._config.get("cookies_file")
        problem = cookies_problem(os.path.expanduser(cookies)) if cookies else None
        if problem:
            self.statusMsg.setText("YouTube cookies can't be used")
            QMessageBox.warning(
                self,
                "YouTube Cookies",
                f"{problem[1]}.\n\nFix it, or turn cookies off with:\n"
                "  sunnify config --set cookies_file=",
            )
            return

        # ALWAYS prompt for download location on first download
        if not self._download_path_set:
            self.statusMsg.setText("Select download location...")
            if not self._prompt_download_location():
                self.statusMsg.setText("Download cancelled - no folder selected")
                return

        # Verify the selected path is still writable
        if not self._ensure_download_path():
            self.statusMsg.setText("Cannot write to download folder")
            QMessageBox.warning(
                self,
                "Invalid Download Location",
                f"Cannot write to:\n{self.download_path}\n\nPlease select a different folder.",
            )
            if not self._prompt_download_location():
                return

        bad = self._invalid_links(links)
        if bad:
            self.statusMsg.setText(f"Not a Spotify playlist, album or track link: {bad[0]}")
            return
        self._queue.extend(u for u in dict.fromkeys(links) if u != links[0])
        self._start_download(links[0])

    @staticmethod
    def _invalid_links(links: list[str]) -> list[str]:
        bad = []
        for link in links:
            try:
                url_type, item_id = detect_spotify_url_type(link)
            except ValueError:
                url_type, item_id = "unknown", None
            if url_type == "unknown" or not item_id:
                bad.append(link)
        return bad

    def _on_link_entered(self):
        """Enter starts a download, or queues the link while one is running."""
        if not self._is_downloading:
            self.on_returnButton()
            return
        links = self.PlaylistLink.text().split()
        if not links:
            return
        if self._cancel_event.is_set():
            self.statusMsg.setText("Stopping - add links again once it finishes")
            return
        bad = self._invalid_links(links)
        if bad:
            self.statusMsg.setText(f"Not a Spotify playlist, album or track link: {bad[0]}")
            return
        new = [u for u in dict.fromkeys(links) if u != self._current_link and u not in self._queue]
        self._queue.extend(new)
        self.PlaylistLink.clear()
        self.statusMsg.setText(
            f"Queued {_counted(len(new), 'link')}, {len(self._queue)} waiting"
            if new
            else "That link is already downloading or queued"
        )
        self._update_queue_hint()

    def _update_queue_hint(self):
        if not self._is_downloading:
            hint = self._idle_placeholder
        elif self._queue:
            hint = f"{len(self._queue)} queued - paste another, press Enter"
        else:
            hint = "Paste another link, press Enter to queue"
        self.PlaylistLink.setPlaceholderText(hint)

    def _set_download_running(self, running: bool):
        self.DownloadBtn.setText("Stop" if running else "Download")
        self.DownloadBtn.setProperty("running", running)
        # qss only re-reads a dynamic property on a re-polish
        self.DownloadBtn.style().unpolish(self.DownloadBtn)
        self.DownloadBtn.style().polish(self.DownloadBtn)

    def _set_activity_visible(self, active: bool):
        """Swap the card between the idle hint and live progress, so nothing
        shows as an empty field or a zero count before there's data for it."""
        self.idleBox.setVisible(not active)
        self.activeBox.setVisible(active)
        self.SongDownloadprogress.setVisible(active)
        side = Qt.AlignmentFlag.AlignLeft if active else Qt.AlignmentFlag.AlignHCenter
        self.statusMsg.setAlignment(Qt.AlignmentFlag.AlignTop | side)
        if not active:
            self.CounterLabel.hide()
            self.ReportBtn.hide()

    def _set_release_line(self, album: str, release_date: str):
        """Album and year on one muted line, the way streaming apps credit a
        release ("Is This It · 2001"); a full date would be noise here."""
        match = re.match(r"\d{4}", release_date or "")
        parts = [p for p in ((album or "").strip(), match.group(0) if match else "") if p]
        if not parts:
            self.YearText.setText("")
            return
        self._set_elided(self.YearText, " · ".join(parts), PREVIEW_TEXT_WIDTH)

    @staticmethod
    def _set_elided(label, text: str, width: int, lines: int = 1):
        """Fit text in a fixed-size label with an ellipsis; tooltip keeps all of it.
        Wrapped lines break at spaces and waste some width, hence the 0.9."""
        label.ensurePolished()
        room = width if lines == 1 else int(width * lines * 0.9)
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room))
        label.setToolTip(text)

    def _start_download(self, spotify_url: str):
        try:
            # Validate URL type
            url_type, _ = detect_spotify_url_type(spotify_url)
            self.statusMsg.setText(f"Detected: {url_type}")

            # Reset cancel event and set downloading state
            self._cancel_event = threading.Event()
            self._current_link = spotify_url
            self.PlaylistLink.clear()
            self._is_downloading = True
            self._set_download_running(True)

            self._has_track = False
            self.playlistCaption.setText(url_type.capitalize())
            self._set_elided(self.AlbumName, "Fetching details...", ACTIVITY_TEXT_WIDTH)
            self._set_elided(
                self.MainSongName, "Waiting for the first song...", ACTIVITY_TEXT_WIDTH
            )
            self.CounterLabel.hide()
            self._set_counter_warn(False)
            self.ReportBtn.hide()
            self.PlaylistMsg_2.setText("Now downloading")
            self._set_activity_visible(True)

            self.scraper_thread = ScraperThread(
                spotify_url,
                self.download_path,
                cancel_event=self._cancel_event,
                **scraper_kwargs_from(self._config),
            )
            self.scraper_thread.progress_update.connect(self.update_progress)
            self.scraper_thread.finished.connect(self.thread_finished)
            self.scraper_thread.scraper.song_Album.connect(self.update_AlbumName)
            self.scraper_thread.scraper.song_meta.connect(self.update_song_META)
            self.scraper_thread.scraper.add_song_meta.connect(self.add_song_META)
            self.scraper_thread.scraper.dlprogress_signal.connect(self.update_song_progress)
            self.scraper_thread.scraper.Resetprogress_signal.connect(self.Reset_song_progress)
            self.scraper_thread.scraper.PlaylistCompleted.connect(
                lambda x: self.statusMsg.setText(x)
            )
            self.scraper_thread.scraper.error_signal.connect(lambda x: self.statusMsg.setText(x))

            # Connect the count_updated signal to the update_counter slot
            self.scraper_thread.scraper.count_updated.connect(self.update_counter)
            # after update_counter so the label reads "1" before the prompt opens
            self.scraper_thread.scraper.count_updated.connect(self._maybe_show_star_prompt)

            self.scraper_thread.start()
            self._update_queue_hint()

        except ValueError as e:
            self.statusMsg.setText(str(e))
            self._is_downloading = False
            self._set_download_running(False)
            self._set_activity_visible(False)

    def _stop_download(self):
        """Stop the current download gracefully using cooperative cancellation."""
        dropped = len(self._queue)
        self._queue.clear()
        self.statusMsg.setText(
            f"Stopping download ({_counted(dropped, 'queued link')} cleared)..."
            if dropped
            else "Stopping download..."
        )
        self.DownloadBtn.setEnabled(False)

        # Signal cancellation via event (thread checks this periodically)
        self._cancel_event.set()

        if hasattr(self, "scraper_thread") and self.scraper_thread.isRunning():
            self.scraper_thread.request_cancel()
            # Thread will finish current track and exit; UI resets via thread_finished signal

    def thread_finished(self):
        """Reset UI state when download thread finishes."""
        self._collect_report()
        self._is_downloading = False
        self._current_link = ""
        self._set_download_running(False)
        self.DownloadBtn.setEnabled(True)
        if not self._has_track:
            # failed or cancelled before any song: statusMsg still says why
            self._set_activity_visible(False)
        else:
            self.PlaylistMsg_2.setText("Last song")
        if hasattr(self, "scraper_thread"):
            self.scraper_thread.deleteLater()  # Clean up the thread properly
        if self._queue and not self._cancel_event.is_set():
            self._start_download(self._queue.popleft())
        self._update_queue_hint()

    def _collect_report(self):
        """Keep the finished run's report and surface it when there is
        something in it: a button in the card always, the window too unless
        the run was stopped (the user already knows it's incomplete)."""
        scraper = self._live_scraper()
        entries = list(getattr(scraper, "_report", None) or [])
        if not entries:
            return
        self._last_report = {
            "name": getattr(scraper, "_report_name", "") or "",
            "folder": getattr(scraper, "_report_folder", "") or "",
            "total": getattr(scraper, "_total_tracks", 0) or 0,
            "entries": entries,
            "network_blocked": bool(getattr(scraper, "_network_blocked", False)),
        }
        self.ReportBtn.setText("View report")
        self.ReportBtn.show()
        if not self._cancel_event.is_set():
            self.show_report()

    def show_report(self):
        if not self._last_report:
            return
        if self._report_dialog is not None:
            # one report window at a time: the newest run replaces the last
            self._report_dialog.close()
            self._report_dialog.deleteLater()
        self._report_dialog = RunReportDialog(self, self._last_report)
        self._report_dialog.show()
        self._report_dialog.raise_()

    def update_progress(self, message):
        self.statusMsg.setText(message)

    @pyqtSlot(dict)
    def update_song_META(self, song_meta):
        """Update UI with current track info (called BEFORE download starts)."""
        if self.showPreviewCheck.isChecked():
            cover_url = song_meta.get("cover", "")
            self._preview_cover_url = cover_url
            if cover_url:
                thumb_thread = DownloadThumbnail(cover_url, self)
                self._active_threads.append(thumb_thread)
                thumb_thread.finished.connect(lambda: self._cleanup_thread(thumb_thread))
                thumb_thread.start()
            else:
                self.CoverImg.clear()
            artists_full = song_meta.get("artists", "")
            artist_list = [a.strip() for a in artists_full.split(",") if a.strip()]
            if len(artist_list) > 2:
                artists_display = f"{artist_list[0]}, {artist_list[1]} +{len(artist_list) - 2}"
            else:
                artists_display = artists_full
            self.ArtistNameText.setText(artists_display)
            self.ArtistNameText.setToolTip(artists_full)
            self._set_elided(self.SongName, song_meta.get("title", ""), PREVIEW_TEXT_WIDTH, lines=2)
            self._set_release_line(song_meta.get("album", ""), song_meta.get("releaseDate", ""))

        self._has_track = True
        self._set_elided(
            self.MainSongName,
            song_meta.get("title", "") + " - " + song_meta.get("artists", ""),
            ACTIVITY_TEXT_WIDTH,
            lines=2,
        )
        # NOTE: Meta tags are written in add_song_META (after file exists), not here

    @pyqtSlot(dict)
    def add_song_META(self, song_meta):
        if self.AddMetaDataCheck.isChecked():
            meta_thread = WritingMetaTagsThread(song_meta, song_meta["file"])
            meta_thread.tags_success.connect(lambda x: self.statusMsg.setText(f"{x}"))
            self._active_threads.append(meta_thread)
            meta_thread.finished.connect(lambda: self._cleanup_thread(meta_thread))
            meta_thread.start()

    def _cleanup_thread(self, thread):
        """Remove finished thread from active list."""
        if thread in self._active_threads:
            self._active_threads.remove(thread)

    @pyqtSlot(str)
    def update_AlbumName(self, AlbumName):
        self._set_elided(self.AlbumName, AlbumName, ACTIVITY_TEXT_WIDTH)

    def _live_scraper(self):
        """The running scraper, or None once the thread is gone (qt raises
        RuntimeError on a deleted object, which outlives the python ref)."""
        try:
            return getattr(getattr(self, "scraper_thread", None), "scraper", None)
        except RuntimeError:
            return None

    def _failed_track_count(self) -> int:
        return len(getattr(self._live_scraper(), "_failed_tracks", []) or [])

    @pyqtSlot(int)
    def update_counter(self, count):
        scraper = self._live_scraper()
        total = getattr(scraper, "_total_tracks", 0) or 0
        failed = self._failed_track_count()
        # count is "tracks finished", not "tracks saved": a failure ticks it too
        done = max(0, count - failed)
        if total:
            # tracklist style, padded so the digits don't shift as it counts
            width = max(2, len(str(total)))
            text = f"{done:0{width}d} / {total:0{width}d} downloaded"
        else:
            text = f"{done} downloaded"
        if failed:
            text += f" · {failed} failed"
        self.CounterLabel.setText(text)
        self._set_counter_warn(bool(failed))
        self.CounterLabel.show()

    def _set_counter_warn(self, warn: bool):
        """Green while everything lands, soft red once anything failed."""
        if bool(self.CounterLabel.property("warn")) == warn:
            return
        self.CounterLabel.setProperty("warn", warn)
        self.CounterLabel.style().unpolish(self.CounterLabel)
        self.CounterLabel.style().polish(self.CounterLabel)

    @pyqtSlot(int)
    def update_song_progress(self, progress):
        self.SongDownloadprogress.setValue(progress)

    @pyqtSlot(int)
    def Reset_song_progress(self, progress):
        self.SongDownloadprogress.setValue(0)

    # DRAGGLESS INTERFACE
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.m_drag = True
            self.m_DragPosition = event.globalPosition().toPoint() - self.pos()
            event.accept()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    def mouseMoveEvent(self, QMouseEvent):
        try:
            if Qt.MouseButton.LeftButton and self.m_drag:
                self.move(QMouseEvent.globalPosition().toPoint() - self.m_DragPosition)
                QMouseEvent.accept()
        except AttributeError:
            pass

    def mouseReleaseEvent(self, QMouseEvent):
        self.m_drag = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def CloseSongInformation(self):
        self.animation = QPropertyAnimation(self.SONGINFORMATION, b"size")
        self.animation.setDuration(250)
        self.animation.setEndValue(QSize(0, 480))
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.animation.start()

    def OpenSongInformation(self):
        self.animation = QPropertyAnimation(self.SONGINFORMATION, b"size")
        self.animation.setDuration(250)
        self.animation.setEndValue(QSize(350, 480))
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.animation.start()

    def show_preview(self, state):
        if state == 2:  # 2 corresponds to checked state
            self.preview_window = self.OpenSongInformation()
        else:
            self.CloseSongInformation()

    def exitprogram(self):
        # QApplication.quit() unwinds the event loop cleanly so app.exec()
        # returns and the atexit session-end runs; sys.exit() inside a slot
        # raised SystemExit into qt's excepthook and logged a false crash
        QApplication.quit()

    def Linkedin(self):
        webbrowser.open("https://www.linkedin.com/in/sunny-patel-30b460204/")


# Main
if __name__ == "__main__":
    # Headless CLI dispatch (before any Qt/logging setup): a known first arg
    # routes to sunnify_cli; anything else - including a bare double-click -
    # is the GUI, byte-identical to before. The alias makes the frozen
    # binary's __main__ and `import Spotify_Downloader` the same module.
    _cli_commands = ("download", "info", "status", "config", "doctor", "help")
    _arg1 = sys.argv[1] if len(sys.argv) > 1 else ""
    if _arg1 in _cli_commands or _arg1 in ("--version", "-V", "--help", "-h"):
        sys.modules.setdefault("Spotify_Downloader", sys.modules[__name__])
        import sunnify_cli

        sys.exit(sunnify_cli.main(sys.argv[1:]))
    if _arg1.isascii() and _arg1.isalpha():
        # a bare word is a command attempt, not launcher argv (macOS -psn_*,
        # file paths all carry non-letters); don't swallow it into a GUI launch
        import difflib

        close = difflib.get_close_matches(_arg1.lower(), _cli_commands, n=1)
        hint = f" (did you mean '{close[0]}'?)" if close else ""
        print(
            f"sunnify: unknown command '{_arg1}'{hint}\nrun 'sunnify --help' for usage",
            file=sys.stderr,
        )
        sys.exit(2)

    # Logging must never stop the app from launching (e.g. a locked-down or
    # read-only log dir). Failure here just means no log file this session.
    with contextlib.suppress(Exception):
        setup_logging()

    # sigint/sigterm: log WHICH signal killed us, then re-raise under
    # SIG_DFL. A default-action signal skips atexit and every hook (why
    # signal deaths never left a log line); this keeps instant-kill
    # semantics with no event-loop dependence, plus one forensic line.
    def _fatal_signal(sig, _frame):
        with contextlib.suppress(Exception):
            log.info("terminated by signal %s (ctrl+c or external)", signal.Signals(sig).name)
            logging.shutdown()
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, _fatal_signal)
        signal.signal(signal.SIGTERM, _fatal_signal)
    # fixed-pixel ui overflows on fractional dpi without this (#64); PassThrough avoids
    # rounding 150%->100%. must precede QApplication. ref: doc.qt.io/qt-6/highdpi.html
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception as exc:  # log so a scaling failure (rendering bugs) is diagnosable
        log.debug("high-dpi setup skipped: %s", exc)
    app = QApplication(sys.argv)
    # wake python every 200ms during exec() so a pending signal's handler runs
    # promptly (qt's c++ loop otherwise defers python signals until a qt slot)
    _signal_waker = QTimer()
    _signal_waker.start(200)
    _signal_waker.timeout.connect(lambda: None)
    Screen = MainWindow()
    Screen.setFixedHeight(540)
    Screen.setFixedWidth(750)
    # no OS shadow: macOS would trace a hard outline around the cards' soft ones
    Screen.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    Screen.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    Screen.show()
    sys.exit(app.exec())
