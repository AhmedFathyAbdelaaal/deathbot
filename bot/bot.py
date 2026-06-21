import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

COGS = [
    "cogs.jokes",
    "cogs.music",
    "cogs.feedback",
    "cogs.website",
]


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return
    logger.error(f"Unhandled command error in {ctx.command}: {error}")


async def _run_api():
    """Run the FastAPI/uvicorn server inside the bot's event loop.

    Best-effort by design: any failure here (bad DATABASE_URL, port in use,
    import error) is logged but never propagated, so the Discord playback
    pipeline keeps running regardless. The web platform is additive.
    """
    try:
        import uvicorn

        import config
        from api.app import create_app
        from db.base import init_db

        try:
            await init_db()
        except Exception:
            logger.critical(
                "Database init failed — web API will run without DB features.",
                exc_info=True,
            )

        app = create_app(bot)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=config.API_PORT,
                log_level="info",
                # Reuse the discord.py loop instead of letting uvicorn manage one.
                loop="none",
            )
        )
        # Don't let uvicorn hijack process signal handling in a shared loop.
        server.install_signal_handlers = lambda: None
        logger.info(f"Starting web API on port {config.API_PORT}")
        await server.serve()
    except Exception:
        logger.critical("Web API server stopped unexpectedly.", exc_info=True)


async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                logger.info(f"Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)

        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.critical("DISCORD_TOKEN is not set — cannot start bot")
            return

        # Web API runs alongside the Discord client; the bot is the lifeline.
        api_task = asyncio.create_task(_run_api())
        try:
            await bot.start(token)
        finally:
            api_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
