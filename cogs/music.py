import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import secrets

from utils.checks import slash_designated_role

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
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio[ext=opus]/bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
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
YTDL_OPTIONS["extractor_args"] = {"youtube": {"player_client": ["android", "ios", "tv_embedded"]}}
logger.info("yt-dlp player client chain: android, ios, tv_embedded")

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

SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")


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
    """A track in the queue. stream_url may be None until resolved at play-time."""

    query: str             # ytdl query or direct URL
    title: str             # display title (may equal query until resolved)
    webpage_url: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None
    requested_by: Optional[discord.Member] = None
    stream_url: Optional[str] = None   # resolved just before playback

    def fmt_duration(self) -> str:
        if not self.duration:
            return "?"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @classmethod
    def from_ytdl(cls, data: dict, member: discord.Member) -> "QueueEntry":
        return cls(
            query=data.get("webpage_url", data.get("url", "")),
            title=data.get("title", "Unknown"),
            webpage_url=data.get("webpage_url", ""),
            thumbnail=data.get("thumbnail"),
            duration=data.get("duration"),
            uploader=data.get("uploader"),
            requested_by=member,
            stream_url=data.get("url"),
        )

    @classmethod
    def from_search(cls, query: str, member: discord.Member, title: Optional[str] = None) -> "QueueEntry":
        return cls(
            query=query,
            title=title or query,
            webpage_url="",
            requested_by=member,
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


async def _resolve_entry(entry: QueueEntry) -> QueueEntry:
    """Fill in stream_url (and metadata) if not already set."""
    if entry.stream_url:
        return entry
    data = await _ytdl_extract(entry.query)
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
        self._queue: list[QueueEntry] = []
        self._current: Optional[QueueEntry] = None
        self._loop_mode: int = LOOP_OFF
        self._volume: float = int(os.getenv("DEFAULT_VOLUME", "70")) / 100.0
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self._inactivity_task: Optional[asyncio.Task] = None
        self._inactivity_timeout: int = int(os.getenv("INACTIVITY_TIMEOUT", "180"))

    # -- Properties --

    @property
    def current(self) -> Optional[QueueEntry]:
        return self._current

    @property
    def queue(self) -> list[QueueEntry]:
        return self._queue

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
                self._queue.clear()
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

    async def play_next(self, error=None):
        """Advance to the next track. Called by the after-play callback."""
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
            # Re-fetch stream URL so it doesn't expire on very long loops
            self._current.stream_url = None
            await self._play_entry(self._current)
            return

        if self._loop_mode == LOOP_QUEUE and self._current:
            # Cycle current track to the back of the queue
            recycled = QueueEntry.from_search(
                self._current.query,
                self._current.requested_by,
                title=self._current.title,
            )
            recycled.webpage_url = self._current.webpage_url
            recycled.thumbnail = self._current.thumbnail
            recycled.duration = self._current.duration
            recycled.uploader = self._current.uploader
            self._queue.append(recycled)

        if not self._queue:
            self._current = None
            self._schedule_inactivity()
            return

        next_entry = self._queue.pop(0)
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
            source = discord.FFmpegPCMAudio(entry.stream_url, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=self._volume)

            if self.voice_client.is_playing():
                self.voice_client.stop()

            def _after(err):
                asyncio.run_coroutine_threadsafe(self.play_next(err), self._bot.loop)

            self.voice_client.play(source, after=_after)
        except Exception as e:
            logger.error(f"Failed to start playback of '{entry.title}': {e}")
            self._current = None
            await self.play_next()

    def cleanup(self):
        self._cancel_inactivity()
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
        self._queue.clear()
        self._current = None


# ---------------------------------------------------------------------------
# Music cog
# ---------------------------------------------------------------------------

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}

    def _state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self._states:
            self._states[guild.id] = GuildMusicState(self.bot, guild)
        return self._states[guild.id]

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
    @app_commands.describe(query="Song name, YouTube URL, Spotify URL, or SoundCloud URL")
    @slash_designated_role()
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        state = await self._ensure_voice(interaction)
        if state is None:
            return

        # --- Spotify ---
        if SPOTIFY_RE.search(query):
            try:
                search_queries = await _spotify_queries(query)
            except Exception as e:
                await interaction.followup.send(embed=self._err(str(e)), ephemeral=True)
                return

            if not search_queries:
                await interaction.followup.send(embed=self._err("No tracks found in that Spotify link."), ephemeral=True)
                return

            # Resolve the first track immediately so playback starts without delay
            first_entry = QueueEntry.from_search(search_queries[0], interaction.user)
            try:
                first_entry = await _resolve_entry(first_entry)
            except Exception as e:
                await interaction.followup.send(embed=self._err(f"Could not load first track: {e}"), ephemeral=True)
                return

            # Add remaining as lazy entries (resolved at play-time)
            for q in search_queries[1:]:
                state.queue.append(QueueEntry.from_search(q, interaction.user))

            is_playing = state.voice_client.is_playing() or state.voice_client.is_paused()
            if not is_playing:
                await state._play_entry(first_entry)
                embed = discord.Embed(
                    title="Now Playing",
                    description=f"[{first_entry.title}]({first_entry.webpage_url or '#'})",
                    color=discord.Color.green(),
                )
                if first_entry.thumbnail:
                    embed.set_thumbnail(url=first_entry.thumbnail)
                if len(search_queries) > 1:
                    embed.add_field(name="Queued", value=f"{len(search_queries) - 1} more track(s)", inline=True)
            else:
                state.queue.insert(0, first_entry)  # first track plays soonest
                embed = discord.Embed(
                    description=f"Added **{len(search_queries)}** track(s) from Spotify to the queue.",
                    color=discord.Color.blurple(),
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # --- YouTube / SoundCloud / plain search ---
        try:
            data = await _ytdl_extract(query)
        except Exception as e:
            await interaction.followup.send(embed=self._err(f"Could not load track: {e}"), ephemeral=True)
            return

        entry = QueueEntry.from_ytdl(data, interaction.user)
        is_playing = state.voice_client.is_playing() or state.voice_client.is_paused()

        if not is_playing:
            await state._play_entry(entry)
            embed = discord.Embed(
                title="Now Playing",
                description=f"[{entry.title}]({entry.webpage_url or '#'})",
                color=discord.Color.green(),
            )
            if entry.thumbnail:
                embed.set_thumbnail(url=entry.thumbnail)
            embed.add_field(name="Duration", value=entry.fmt_duration(), inline=True)
            embed.add_field(name="Uploader", value=entry.uploader or "Unknown", inline=True)
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        else:
            state.queue.append(entry)
            embed = discord.Embed(
                description=f"Added to queue at position **#{len(state.queue)}**",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Track", value=f"[{entry.title}]({entry.webpage_url or '#'})", inline=False)
            embed.add_field(name="Duration", value=entry.fmt_duration(), inline=True)
            if entry.thumbnail:
                embed.set_thumbnail(url=entry.thumbnail)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /skip
    # ------------------------------------------------------------------

    @app_commands.command(name="skip", description="Skip the current track")
    @slash_designated_role()
    async def skip(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.voice_client or not state.voice_client.is_playing():
            await interaction.response.send_message(embed=self._err("Nothing is currently playing."), ephemeral=True)
            return
        state.voice_client.stop()  # triggers after → play_next
        await interaction.response.send_message(embed=self._ok("Skipped!"), ephemeral=True)

    # ------------------------------------------------------------------
    # /stop
    # ------------------------------------------------------------------

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    @slash_designated_role()
    async def stop(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.voice_client:
            await interaction.response.send_message(embed=self._err("Not connected to a voice channel."), ephemeral=True)
            return
        state.cleanup()
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
        state = self._state(interaction.guild)
        view = QueueView(list(state.queue), state.current)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /shuffle
    # ------------------------------------------------------------------

    @app_commands.command(name="shuffle", description="Shuffle the queue (does not affect current track)")
    @slash_designated_role()
    async def shuffle(self, interaction: discord.Interaction):
        state = self._state(interaction.guild)
        if not state.queue:
            await interaction.response.send_message(embed=self._err("The queue is empty."), ephemeral=True)
            return
        random.shuffle(state.queue)
        await interaction.response.send_message(embed=self._ok(f"Shuffled {len(state.queue)} track(s)."), ephemeral=True)

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
        if e.requested_by:
            embed.set_footer(text=f"Requested by {e.requested_by.display_name}")
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
