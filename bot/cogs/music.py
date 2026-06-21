import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import secrets

from utils.checks import slash_designated_role
from db.base import get_sessionmaker
from db.models import Track, User
from services import queue_service, queue_store, events, playback, user_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# yt-dlp config
# ---------------------------------------------------------------------------

_cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "")
_has_cookies = bool(_cookies_file and os.path.isfile(_cookies_file))
_proxy = os.getenv("YTDLP_PROXY") or None

if _cookies_file and not _has_cookies:
    logger.warning(
        f"YOUTUBE_COOKIES_FILE='{_cookies_file}' but file not found inside container — cookies disabled."
    )

YTDL_OPTIONS: dict = {
    # Adaptive audio-only formats (itag 251, etc.) are the ones being SABR-
    # walled and require a per-fragment token that frequently still 403s even
    # when the initial PO token succeeds. Progressive (combined audio+video)
    # formats like itag 18 are pre-muxed single-file streams that have so far
    # avoided this issue, at the cost of downloading unused video data. We
    # discard the video stream at playback via FFmpeg's -vn flag, so only the
    # bandwidth cost is paid, not a quality or storage cost.
    "format": "best[acodec!=none]/bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",
    "source_address": "0.0.0.0",
    "verbose": True,
}

_iproyal_user = os.getenv("IPROYAL_USERNAME")
_iproyal_pass = os.getenv("IPROYAL_PASSWORD")
_iproyal_host = os.getenv("IPROYAL_HOST")
_iproyal_port = os.getenv("IPROYAL_PORT")

_proxy_configured = all([_iproyal_user, _iproyal_pass, _iproyal_host, _iproyal_port])

if _proxy_configured:
    _session_id = secrets.token_hex(6)  # e.g. "a1b2c3d4e5f6" — generated once per bot process
    _proxy_auth = f"{_iproyal_user}:{_iproyal_pass}_session-{_session_id}_lifetime-24h"
    _ytdlp_proxy_url = f"http://{_proxy_auth}@{_iproyal_host}:{_iproyal_port}"
    _ffmpeg_proxy_url = f"socks5://{_proxy_auth}@{_iproyal_host}:{_iproyal_port}"
    logger.info(f"Proxy session established: {_session_id} (shared by yt-dlp and FFmpeg)")
else:
    _ytdlp_proxy_url = None
    _ffmpeg_proxy_url = None
    logger.warning(
        "IPROYAL_USERNAME/PASSWORD/HOST/PORT not fully set — proxy disabled. "
        "If YouTube's IP block is active, extraction or playback will fail."
    )

if _ytdlp_proxy_url:
    YTDL_OPTIONS["proxy"] = _ytdlp_proxy_url
    logger.info("yt-dlp proxy configured (session-bound)")

# Always use the player client chain, proxy or not. android/ios clients
# expose a much wider set of audio formats than the default "web" client,
# which requires a working PO token to unlock its full format list (PO
# tokens largely don't bypass bot checks anymore as of 2026). The proxy
# only solves YouTube's IP-reputation block — it doesn't affect which
# formats a given client is allowed to see, so this is needed either way.
YTDL_OPTIONS["extractor_args"] = {
    "youtube": {
        # mweb is the client called out by yt-dlp maintainers as still
        # exposing non-SABR formats when paired with a PO token, unlike
        # web/web_safari which YouTube has been progressively SABR-walling
        # since early 2025. web_safari kept as fallback since it's already
        # proven to successfully retrieve tokens via bgutil in our setup.
        "player_client": ["mweb", "web_safari"],
    },
    "youtubepot-bgutilhttp": {
        "base_url": [os.getenv("BGUTIL_POT_URL", "http://bgutil-pot:4416")],
    },
}
logger.info("yt-dlp player client chain: mweb, web_safari")
logger.info(f"bgutil POT provider URL: {os.getenv('BGUTIL_POT_URL', 'http://bgutil-pot:4416')}")

if shutil.which("deno") is None:
    logger.warning(
        "Deno not found on PATH — yt-dlp may fall back to degraded player "
        "clients and unstable formats (e.g. itag=18 SABR fallback)."
    )
else:
    logger.info("Deno runtime detected — yt-dlp signature solving enabled.")

if _has_cookies:
    YTDL_OPTIONS["cookiefile"] = _cookies_file
    logger.info(f"YouTube cookies loaded from: {_cookies_file}")

_ffmpeg_before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

if _ffmpeg_proxy_url:
    _ffmpeg_before = f"-http_proxy {_ffmpeg_proxy_url} {_ffmpeg_before}"
    logger.info("FFmpeg proxy configured (session-bound)")
else:
    logger.warning(
        "No FFmpeg proxy configured — if yt-dlp is using a proxy, this "
        "mismatch will cause 403 Forbidden errors during playback."
    )

FFMPEG_OPTIONS: dict = {
    "before_options": _ffmpeg_before,
    "options": "-vn",
}

# Loop mode constants
LOOP_OFF = 0
LOOP_TRACK = 1
LOOP_QUEUE = 2
LOOP_LABELS = {LOOP_OFF: "Off", LOOP_TRACK: "Track", LOOP_QUEUE: "Queue"}


def _fmt_seconds(seconds: Optional[int]) -> str:
    """Format a duration in seconds as H:MM:SS / M:SS, or '?' if unknown."""
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")
DIRECT_AUDIO_RE = re.compile(r"\.(mp3|wav|m4a|ogg|flac|opus|aac)(\?.*)?$", re.IGNORECASE)
SOUNDCLOUD_RE = re.compile(r"https?://(www\.)?soundcloud\.com/", re.IGNORECASE)
YOUTUBE_RE = re.compile(r"https?://(www\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Spotify client (optional)
# ---------------------------------------------------------------------------

def _build_spotify_client():
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials

        cid = os.getenv("SPOTIFY_CLIENT_ID")
        secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if cid and secret:
            return spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(client_id=cid, client_secret=secret)
            )
    except ImportError:
        logger.warning("spotipy not installed — Spotify support disabled")
    return None


_sp = _build_spotify_client()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class QueueEntry:
    """An in-memory, resolved-or-resolvable playback entry.

    Decoupled from Discord: attribution is a plain ``requested_by_name`` string
    plus the ``added_by_id`` users.id, so the same entry type serves tracks
    queued from Discord and from the web. The upcoming queue itself lives in
    Postgres (queue_items); a QueueEntry is materialized only when an item is
    popped to become the now-playing track (or for display)."""

    query: str                          # resolve query (URL/search) or local file path
    title: str
    webpage_url: str = ""
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None      # artist / channel
    requested_by_name: Optional[str] = None
    added_by_id: Optional[int] = None   # users.id
    track_id: Optional[int] = None      # set if this is a Library upload
    is_local: bool = False              # True → play the local file directly, no yt-dlp
    stream_url: Optional[str] = None    # resolved just before playback

    def fmt_duration(self) -> str:
        return _fmt_seconds(self.duration)

    @classmethod
    def from_queue_item(cls, item, track, requested_by_name: Optional[str]) -> "QueueEntry":
        """Build from a popped queue_items row. ``track`` is the joined Track
        row when ``item.track_id`` is set (a Library upload), else None."""
        if track is not None:
            return cls(
                query=track.file_path,
                title=track.title,
                uploader=track.artist,
                duration=track.duration_seconds,
                requested_by_name=requested_by_name,
                added_by_id=item.added_by,
                track_id=track.id,
                is_local=True,
                stream_url=track.file_path,
            )
        query = item.source_url or f"{item.title or ''} {item.artist or ''}".strip()
        return cls(
            query=query,
            title=item.title or query,
            webpage_url=item.source_url or "",
            uploader=item.artist,
            requested_by_name=requested_by_name,
            added_by_id=item.added_by,
        )


# ---------------------------------------------------------------------------
# yt-dlp helpers
# ---------------------------------------------------------------------------

async def _ytdl_extract(query: str) -> dict:
    """Run yt-dlp info extraction in a thread and return the data dict."""
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
            data = ytdl.extract_info(query, download=False)
        if not data:
            raise ValueError(f"No results for: {query}")
        if "entries" in data:
            entry = data["entries"][0]
            if not entry:
                raise ValueError(f"No results for: {query}")
            return entry
        return data

    return await loop.run_in_executor(None, _extract)


async def _ytdl_extract_with_fallback(query: str) -> dict:
    """
    Resolve a query with source priority: direct URL > explicit platform URL
    (played as-is) > SoundCloud search > YouTube search (best-effort fallback).

    YouTube is treated as unreliable due to ongoing SABR/PO-token enforcement
    that intermittently blocks playback even when extraction succeeds. We
    only reach for it when no SoundCloud result is available.
    """
    is_direct_audio = bool(DIRECT_AUDIO_RE.search(query))
    is_explicit_url = bool(SOUNDCLOUD_RE.search(query) or YOUTUBE_RE.search(query) or is_direct_audio)

    if is_explicit_url:
        # User gave a specific link — respect it, don't second-guess the source.
        return await _ytdl_extract(query)

    # Plain text query — try SoundCloud first, since it isn't subject to the
    # SABR/PO-token instability YouTube has had throughout 2026.
    try:
        sc_data = await _ytdl_extract(f"scsearch:{query}")
        logger.info(f"Resolved '{query}' via SoundCloud")
        return sc_data
    except Exception as sc_error:
        logger.warning(f"SoundCloud search failed for '{query}': {sc_error}. Falling back to YouTube.")

    # Best-effort YouTube fallback — may fail due to upstream SABR issues.
    try:
        yt_data = await _ytdl_extract(f"ytsearch:{query}")
        logger.info(f"Resolved '{query}' via YouTube (fallback)")
        return yt_data
    except Exception as yt_error:
        logger.error(f"YouTube fallback also failed for '{query}': {yt_error}")
        raise ValueError(
            f"Could not find '{query}' on SoundCloud or YouTube. "
            f"YouTube may currently be unavailable due to platform restrictions."
        )


async def _resolve_entry(entry: QueueEntry) -> QueueEntry:
    """Fill in stream_url (and metadata) if not already set."""
    if entry.stream_url:
        return entry
    data = await _ytdl_extract_with_fallback(entry.query)
    entry.title = data.get("title", entry.title)
    entry.webpage_url = data.get("webpage_url", entry.webpage_url)
    entry.thumbnail = data.get("thumbnail")
    entry.duration = data.get("duration")
    entry.uploader = data.get("uploader")
    entry.stream_url = data["url"]
    return entry


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

async def _spotify_queries(url: str) -> list[str]:
    """Return a list of 'title artist' search strings from a Spotify URL."""
    if _sp is None:
        raise RuntimeError(
            "Spotify credentials not configured. "
            "Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to your .env."
        )
    match = SPOTIFY_RE.search(url)
    if not match:
        raise ValueError("Invalid Spotify URL.")

    kind, sid = match.group(1), match.group(2)
    loop = asyncio.get_event_loop()

    def _track_label(t: dict) -> str:
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        return f"{t['name']} {artists}"

    if kind == "track":
        t = await loop.run_in_executor(None, lambda: _sp.track(sid))
        return [_track_label(t)]

    if kind == "playlist":
        queries: list[str] = []
        page = await loop.run_in_executor(None, lambda: _sp.playlist_tracks(sid, limit=50))
        while page:
            for item in page["items"]:
                t = item.get("track")
                if t and not t.get("is_local"):
                    queries.append(_track_label(t))
            nxt = page.get("next")
            page = await loop.run_in_executor(None, lambda: _sp.next(page)) if nxt else None
        return queries

    if kind == "album":
        queries: list[str] = []
        page = await loop.run_in_executor(None, lambda: _sp.album_tracks(sid, limit=50))
        while page:
            for t in page["items"]:
                queries.append(_track_label(t))
            nxt = page.get("next")
            page = await loop.run_in_executor(None, lambda: _sp.next(page)) if nxt else None
        return queries

    raise ValueError(f"Unsupported Spotify link type: {kind}")


# ---------------------------------------------------------------------------
# Queue pagination view
# ---------------------------------------------------------------------------

class QueueView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, entries: list[QueueEntry], current: Optional[QueueEntry]):
        super().__init__(timeout=60)
        self.entries = entries
        self.current = current
        self.page = 0
        self.total = max(1, (len(entries) + self.PER_PAGE - 1) // self.PER_PAGE)
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())

        if self.current:
            embed.add_field(
                name="Now Playing",
                value=f"[{self.current.title}]({self.current.webpage_url or '#'}) `{self.current.fmt_duration()}`",
                inline=False,
            )

        start = self.page * self.PER_PAGE
        page_items = self.entries[start : start + self.PER_PAGE]

        if page_items:
            lines = [
                f"`{start + i + 1}.` [{e.title}]({e.webpage_url or '#'}) `{e.fmt_duration()}`"
                for i, e in enumerate(page_items)
            ]
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Up Next", value="Queue is empty.", inline=False)

        embed.set_footer(text=f"Page {self.page + 1}/{self.total} • {len(self.entries)} track(s) queued")
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# ---------------------------------------------------------------------------
# Per-guild music state
# ---------------------------------------------------------------------------

class GuildMusicState:
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self._bot = bot
        self._guild = guild
        self._current: Optional[QueueEntry] = None
        self._loop_mode: int = LOOP_OFF
        self._volume: float = int(os.getenv("DEFAULT_VOLUME", "70")) / 100.0
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self._inactivity_task: Optional[asyncio.Task] = None
        self._inactivity_timeout: int = int(os.getenv("INACTIVITY_TIMEOUT", "180"))
        # Playback position tracking (for the WebSocket position tick). discord
        # doesn't expose elapsed time, so we time it off the event loop clock and
        # subtract any paused spans.
        self._play_started_at: Optional[float] = None
        self._paused_total: float = 0.0
        self._paused_since: Optional[float] = None

    # -- Properties --

    @property
    def current(self) -> Optional[QueueEntry]:
        return self._current

    def is_active(self) -> bool:
        vc = self.voice_client
        return bool(vc and (vc.is_playing() or vc.is_paused()))

    def mark_paused(self):
        if self._paused_since is None:
            self._paused_since = self._bot.loop.time()

    def mark_resumed(self):
        if self._paused_since is not None:
            self._paused_total += self._bot.loop.time() - self._paused_since
            self._paused_since = None

    def position_seconds(self) -> Optional[int]:
        """Elapsed seconds into the current track, or None when nothing is
        playing. Accounts for paused spans."""
        vc = self.voice_client
        if not vc or self._play_started_at is None:
            return None
        if not (vc.is_playing() or vc.is_paused()):
            return None
        now = self._bot.loop.time()
        paused = self._paused_total + (now - self._paused_since if self._paused_since else 0.0)
        return max(0, int(now - self._play_started_at - paused))

    @property
    def loop_mode(self) -> int:
        return self._loop_mode

    @loop_mode.setter
    def loop_mode(self, value: int):
        self._loop_mode = value % 3

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.01, min(1.0, value))
        if self.voice_client and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self._volume

    # -- Inactivity timer --

    def _cancel_inactivity(self):
        if self._inactivity_task and not self._inactivity_task.done():
            self._inactivity_task.cancel()
            self._inactivity_task = None

    def _schedule_inactivity(self):
        self._cancel_inactivity()
        self._inactivity_task = asyncio.create_task(self._inactivity_loop())

    async def _inactivity_loop(self):
        try:
            await asyncio.sleep(self._inactivity_timeout)
            vc = self.voice_client
            if vc and vc.is_connected() and not vc.is_playing() and not vc.is_paused():
                await vc.disconnect()
                self.voice_client = None
                self._current = None
                if self.text_channel:
                    await self.text_channel.send(
                        embed=discord.Embed(
                            description="Left the voice channel due to inactivity.",
                            color=discord.Color.light_grey(),
                        )
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Inactivity loop error: {e}")

    # -- Playback --

    async def _pop_next_entry(self) -> Optional[QueueEntry]:
        """Pop the next queue_item from Postgres and materialize it as a
        QueueEntry. Returns None if the queue is empty or the DB is down."""
        sm = get_sessionmaker()
        if sm is None:
            return None
        async with sm() as session:
            item = await queue_store.pop_next(session)
            if item is None:
                return None
            track = await session.get(Track, item.track_id) if item.track_id else None
            requester = await session.get(User, item.added_by) if item.added_by else None
            requested_by_name = requester.display_name if requester else None
        events.broadcast({"type": "queue_changed"})
        return QueueEntry.from_queue_item(item, track, requested_by_name)

    async def _recycle_current_to_queue(self):
        """LOOP_QUEUE: append the just-finished track back onto the Postgres tail."""
        cur = self._current
        sm = get_sessionmaker()
        if cur is None or sm is None:
            return
        async with sm() as session:
            await queue_store.add_item(
                session,
                added_by=cur.added_by_id,
                track_id=cur.track_id,
                source_url=None if cur.track_id else cur.query,
                title=cur.title,
                artist=cur.uploader,
            )
        events.broadcast({"type": "queue_changed"})

    async def play_next(self, error=None):
        """Advance to the next track. Called by the after-play callback and by
        the controller when starting from idle. Pulls from Postgres."""
        if error:
            logger.warning(f"[{self._guild.name}] Playback error: {error}")
            if self.text_channel:
                try:
                    await self.text_channel.send(
                        embed=discord.Embed(
                            description="⚠️ Playback error — skipping to next track.",
                            color=discord.Color.orange(),
                        )
                    )
                except Exception:
                    pass

        if self._loop_mode == LOOP_TRACK and self._current:
            # Replay current. Re-resolve remote streams (URLs expire); a local
            # file path stays valid.
            if not self._current.is_local:
                self._current.stream_url = None
            await self._play_entry(self._current)
            return

        if self._loop_mode == LOOP_QUEUE and self._current:
            await self._recycle_current_to_queue()

        next_entry = await self._pop_next_entry()
        if next_entry is None:
            self._current = None
            self._schedule_inactivity()
            events.broadcast({"type": "now_playing", "track": None})
            return

        try:
            next_entry = await _resolve_entry(next_entry)
        except Exception as e:
            logger.warning(f"Could not resolve '{next_entry.title}': {e}")
            if self.text_channel:
                try:
                    await self.text_channel.send(
                        embed=discord.Embed(
                            description=f"⚠️ Could not load **{next_entry.title}** — skipping.",
                            color=discord.Color.orange(),
                        )
                    )
                except Exception:
                    pass
            await self.play_next()
            return

        await self._play_entry(next_entry)

    async def _play_entry(self, entry: QueueEntry):
        if not self.voice_client or not self.voice_client.is_connected():
            self._current = None
            return

        # Resolve stream URL if missing
        if not entry.stream_url:
            try:
                entry = await _resolve_entry(entry)
            except Exception as e:
                logger.error(f"Stream resolution failed for '{entry.title}': {e}")
                await self.play_next()
                return

        self._cancel_inactivity()
        self._current = entry

        try:
            # Library uploads are local files — no proxy/reconnect input options.
            ffmpeg_opts = {"options": "-vn"} if entry.is_local else FFMPEG_OPTIONS
            source = discord.FFmpegPCMAudio(entry.stream_url, **ffmpeg_opts)
            source = discord.PCMVolumeTransformer(source, volume=self._volume)

            if self.voice_client.is_playing():
                self.voice_client.stop()

            def _after(err):
                asyncio.run_coroutine_threadsafe(self.play_next(err), self._bot.loop)

            self.voice_client.play(source, after=_after)
            self._play_started_at = self._bot.loop.time()
            self._paused_total = 0.0
            self._paused_since = None
            events.broadcast({
                "type": "now_playing",
                "track": {
                    "title": entry.title,
                    "artist": entry.uploader,
                    "duration": entry.duration,
                    "thumbnail": entry.thumbnail,
                    "webpage_url": entry.webpage_url,
                    "requested_by": entry.requested_by_name,
                },
            })
        except Exception as e:
            logger.error(f"Failed to start playback of '{entry.title}': {e}")
            self._current = None
            await self.play_next()

    def cleanup(self):
        self._cancel_inactivity()
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
        self._current = None


# ---------------------------------------------------------------------------
# Music cog
# ---------------------------------------------------------------------------

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}
        # Register as the single playback controller the queue service + web API
        # drive through (v1 is locked to one Discord server).
        playback.set_controller(self)

    def _state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self._states:
            self._states[guild.id] = GuildMusicState(self.bot, guild)
        return self._states[guild.id]

    # ------------------------------------------------------------------
    # Playback controller interface (called by queue_service / web API)
    # ------------------------------------------------------------------

    def _active_state(self) -> Optional[GuildMusicState]:
        """The state currently holding a voice connection, else any known one.
        Single-server scope makes this unambiguous."""
        for st in self._states.values():
            if st.voice_client and st.voice_client.is_connected():
                return st
        return next(iter(self._states.values()), None)

    async def ensure_playing(self) -> bool:
        """Start playback if connected, idle, and the Postgres queue has items.
        Returns True if a track started."""
        st = self._active_state()
        if st is None or not (st.voice_client and st.voice_client.is_connected()):
            return False
        if st.is_active():
            return False
        await st.play_next()
        return st.is_active()

    def is_active(self) -> bool:
        st = self._active_state()
        return bool(st and st.is_active())

    async def skip_current(self) -> bool:
        st = self._active_state()
        if not st or not st.is_active():
            return False
        st.voice_client.stop()  # after-callback advances to the next queue item
        return True

    async def stop_all(self) -> bool:
        st = self._active_state()
        if not st or not st.voice_client:
            return False
        st.cleanup()
        return True

    def now_playing(self) -> Optional[QueueEntry]:
        st = self._active_state()
        return st.current if st else None

    def position_seconds(self) -> Optional[int]:
        st = self._active_state()
        return st.position_seconds() if st else None

    def _err(self, msg: str) -> discord.Embed:
        return discord.Embed(description=f"❌ {msg}", color=discord.Color.red())

    def _ok(self, msg: str) -> discord.Embed:
        return discord.Embed(description=f"✅ {msg}", color=discord.Color.green())

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = str(error)
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            return
        logger.error(f"Music command error: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(self._err("An unexpected error occurred."), ephemeral=True)

    # ------------------------------------------------------------------
    # Voice channel guard
    # ------------------------------------------------------------------

    async def _ensure_voice(self, interaction: discord.Interaction) -> Optional[GuildMusicState]:
        """
        Ensure the invoking user is in a VC and the bot is connected.
        Returns the GuildMusicState or None if preconditions fail.
        """
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(embed=self._err("You must be in a voice channel."), ephemeral=True)
            return None

        state = self._state(interaction.guild)
        state.text_channel = interaction.channel

        if not state.voice_client or not state.voice_client.is_connected():
            try:
                state.voice_client = await interaction.user.voice.channel.connect()
            except Exception as e:
                await interaction.followup.send(embed=self._err(f"Could not join your channel: {e}"), ephemeral=True)
                return None

        return state

    # ------------------------------------------------------------------
    # /play
    # ------------------------------------------------------------------

    @app_commands.command(name="play", description="Play a song or add it to the queue")
    @app_commands.describe(query="Song name, SoundCloud URL, Spotify URL, or YouTube URL (YouTube used as fallback)")
    @slash_designated_role()
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        state = await self._ensure_voice(interaction)
        if state is None:
            return

        sm = get_sessionmaker()
        if sm is None:
            await interaction.followup.send(
                embed=self._err("The music database is unavailable right now."), ephemeral=True
            )
            return

        # Attribution: ensure the Discord user has a users row.
        async with sm() as session:
            user = await user_service.upsert(
                session, interaction.user.id, interaction.user.display_name
            )
            user_id = user.id

        # --- Spotify: expand to search labels and bulk-enqueue ---
        if SPOTIFY_RE.search(query):
            try:
                search_queries = await _spotify_queries(query)
            except Exception as e:
                await interaction.followup.send(embed=self._err(str(e)), ephemeral=True)
                return

            if not search_queries:
                await interaction.followup.send(embed=self._err("No tracks found in that Spotify link."), ephemeral=True)
                return

            async with sm() as session:
                for label in search_queries:
                    await queue_store.add_item(session, added_by=user_id, source_url=label, title=label)
            events.broadcast({"type": "queue_changed"})

            started = await self.ensure_playing()
            if started:
                cur = self.now_playing()
                embed = discord.Embed(
                    title="Now Playing",
                    description=f"[{cur.title}]({cur.webpage_url or '#'})" if cur else "Starting playback…",
                    color=discord.Color.green(),
                )
                if cur and cur.thumbnail:
                    embed.set_thumbnail(url=cur.thumbnail)
                if len(search_queries) > 1:
                    embed.add_field(name="Queued", value=f"{len(search_queries) - 1} more track(s)", inline=True)
            else:
                embed = discord.Embed(
                    description=f"Added **{len(search_queries)}** track(s) from Spotify to the queue.",
                    color=discord.Color.blurple(),
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # --- Direct URL / SoundCloud / YouTube / plain search (SoundCloud-first) ---
        # Resolve up-front to validate the query and capture display metadata;
        # the stream URL is re-resolved at play-time (URLs expire).
        try:
            data = await _ytdl_extract_with_fallback(query)
        except Exception as e:
            await interaction.followup.send(embed=self._err(f"Could not load track: {e}"), ephemeral=True)
            return

        async with sm() as session:
            item, started = await queue_service.add(
                session,
                added_by=user_id,
                source_url=data.get("webpage_url") or query,
                title=data.get("title"),
                artist=data.get("uploader"),
            )

        title = data.get("title", "Unknown")
        webpage_url = data.get("webpage_url", "")
        thumbnail = data.get("thumbnail")
        duration = data.get("duration")
        uploader = data.get("uploader")

        if started:
            embed = discord.Embed(
                title="Now Playing",
                description=f"[{title}]({webpage_url or '#'})",
                color=discord.Color.green(),
            )
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            embed.add_field(name="Duration", value=_fmt_seconds(duration), inline=True)
            embed.add_field(name="Uploader", value=uploader or "Unknown", inline=True)
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        else:
            embed = discord.Embed(
                description=f"Added to queue at position **#{item.position + 1}**",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Track", value=f"[{title}]({webpage_url or '#'})", inline=False)
            embed.add_field(name="Duration", value=_fmt_seconds(duration), inline=True)
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /skip
    # ------------------------------------------------------------------

    @app_commands.command(name="skip", description="Skip the current track")
    @slash_designated_role()
    async def skip(self, interaction: discord.Interaction):
        if not await queue_service.skip():  # triggers after → play_next
            await interaction.response.send_message(embed=self._err("Nothing is currently playing."), ephemeral=True)
            return
        await interaction.response.send_message(embed=self._ok("Skipped!"), ephemeral=True)

    # ------------------------------------------------------------------
    # /stop
    # ------------------------------------------------------------------

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    @slash_designated_role()
    async def stop(self, interaction: discord.Interaction):
        sm = get_sessionmaker()
        if sm is None:
            await interaction.response.send_message(embed=self._err("The music database is unavailable right now."), ephemeral=True)
            return
        async with sm() as session:
            stopped = await queue_service.stop(session)
        if not stopped:
            await interaction.response.send_message(embed=self._err("Nothing is playing and the queue is empty."), ephemeral=True)
            return
        await interaction.response.send_message(embed=self._ok("Stopped and cleared the queue."), ephemeral=True)

    # ------------------------------------------------------------------
    # /leave
    # ------------------------------------------------------------------

    @app_commands.command(name="leave", description="Disconnect the bot from voice")
    @slash_designated_role()
    async def leave(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.voice_client:
            await interaction.response.send_message(embed=self._err("Not connected to a voice channel."), ephemeral=True)
            return
        sm = get_sessionmaker()
        if sm is not None:
            async with sm() as session:
                await queue_service.stop(session)
        else:
            state.cleanup()
        await state.voice_client.disconnect()
        state.voice_client = None
        await interaction.response.send_message(embed=self._ok("Disconnected."), ephemeral=True)

    # ------------------------------------------------------------------
    # /queue
    # ------------------------------------------------------------------

    @app_commands.command(name="queue", description="Display the current music queue")
    @slash_designated_role()
    async def queue_cmd(self, interaction: discord.Interaction):
        current = self.now_playing()
        entries: list[QueueEntry] = []
        sm = get_sessionmaker()
        if sm is not None:
            async with sm() as session:
                for it in await queue_store.list_queue(session):
                    track = await session.get(Track, it.track_id) if it.track_id else None
                    entries.append(QueueEntry.from_queue_item(it, track, None))
        view = QueueView(entries, current)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /shuffle
    # ------------------------------------------------------------------

    @app_commands.command(name="shuffle", description="Shuffle the queue (does not affect current track)")
    @slash_designated_role()
    async def shuffle(self, interaction: discord.Interaction):
        sm = get_sessionmaker()
        if sm is None:
            await interaction.response.send_message(embed=self._err("The music database is unavailable right now."), ephemeral=True)
            return
        async with sm() as session:
            count = await queue_service.shuffle(session)
        if count == 0:
            await interaction.response.send_message(embed=self._err("The queue is empty."), ephemeral=True)
            return
        await interaction.response.send_message(embed=self._ok(f"Shuffled {count} track(s)."), ephemeral=True)

    # ------------------------------------------------------------------
    # /loop
    # ------------------------------------------------------------------

    @app_commands.command(name="loop", description="Cycle loop mode: Off → Track → Queue → Off")
    @slash_designated_role()
    async def loop_cmd(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        state.loop_mode = state.loop_mode + 1
        label = LOOP_LABELS[state.loop_mode]
        await interaction.response.send_message(
            embed=self._ok(f"Loop mode set to **{label}**."), ephemeral=True
        )

    # ------------------------------------------------------------------
    # /nowplaying
    # ------------------------------------------------------------------

    @app_commands.command(name="nowplaying", description="Show the currently playing track")
    @slash_designated_role()
    async def nowplaying(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.current:
            await interaction.response.send_message(embed=self._err("Nothing is playing right now."), ephemeral=True)
            return

        e = state.current
        embed = discord.Embed(
            title="Now Playing",
            description=f"[{e.title}]({e.webpage_url or '#'})",
            color=discord.Color.blurple(),
        )
        if e.thumbnail:
            embed.set_thumbnail(url=e.thumbnail)
        embed.add_field(name="Duration", value=e.fmt_duration(), inline=True)
        embed.add_field(name="Uploader", value=e.uploader or "Unknown", inline=True)
        embed.add_field(name="Loop", value=LOOP_LABELS[state.loop_mode], inline=True)
        embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
        if e.requested_by_name:
            embed.set_footer(text=f"Requested by {e.requested_by_name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /volume
    # ------------------------------------------------------------------

    @app_commands.command(name="volume", description="Set playback volume (1–100)")
    @app_commands.describe(level="Volume level from 1 to 100")
    @slash_designated_role()
    async def volume_cmd(self, interaction: discord.Interaction, level: int):
        if level < 1 or level > 100:
            await interaction.response.send_message(
                embed=self._err("Volume must be between **1** and **100**."), ephemeral=True
            )
            return
        state = self._state(interaction.guild)
        state.volume = level / 100.0
        await interaction.response.send_message(embed=self._ok(f"Volume set to **{level}%**."), ephemeral=True)

    # ------------------------------------------------------------------
    # /pause
    # ------------------------------------------------------------------

    @app_commands.command(name="pause", description="Pause the current track")
    @slash_designated_role()
    async def pause(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.voice_client or not state.voice_client.is_playing():
            await interaction.response.send_message(embed=self._err("Nothing is playing."), ephemeral=True)
            return
        state.voice_client.pause()
        state.mark_paused()
        await interaction.response.send_message(embed=self._ok("Paused."), ephemeral=True)

    # ------------------------------------------------------------------
    # /resume
    # ------------------------------------------------------------------

    @app_commands.command(name="resume", description="Resume paused playback")
    @slash_designated_role()
    async def resume(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.voice_client or not state.voice_client.is_paused():
            await interaction.response.send_message(embed=self._err("Playback is not paused."), ephemeral=True)
            return
        state.voice_client.resume()
        state.mark_resumed()
        await interaction.response.send_message(embed=self._ok("Resumed."), ephemeral=True)

    # ------------------------------------------------------------------
    # Voice state listener — handle bot kick / empty channel
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.guild.id not in self._states:
            return

        state = self._states[member.guild.id]

        # Bot was disconnected externally
        if member == self.bot.user and before.channel and not after.channel:
            state.cleanup()
            state.voice_client = None
            return

        # Bot remains in VC — check if it's now alone
        vc = state.voice_client
        if vc and vc.is_connected() and vc.channel:
            humans = [m for m in vc.channel.members if not m.bot]
            if not humans:
                state._schedule_inactivity()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
