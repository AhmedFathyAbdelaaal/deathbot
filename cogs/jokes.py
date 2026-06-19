import json
import logging
import os
import random

import discord
from discord.ext import commands

from utils.checks import has_designated_role, is_admin

logger = logging.getLogger(__name__)

JOKES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jokes.json")


def _load_jokes() -> list[str]:
    try:
        with open(JOKES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("jokes", [])
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, KeyError):
        logger.error("jokes.json is malformed")
        return []


def _save_jokes(jokes: list[str]) -> None:
    with open(JOKES_FILE, "w", encoding="utf-8") as f:
        json.dump({"jokes": jokes}, f, indent=2, ensure_ascii=False)


class Jokes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jokes: list[str] = _load_jokes()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _embed_joke(self, joke: str, index: int) -> discord.Embed:
        embed = discord.Embed(description=joke, color=discord.Color.gold())
        embed.set_author(name="Random Joke", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Joke #{index + 1} of {len(self.jokes)}")
        return embed

    def _embed_info(self, message: str, color: discord.Color = discord.Color.blurple()) -> discord.Embed:
        return discord.Embed(description=message, color=color)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group(name="joke", invoke_without_command=True)
    @has_designated_role()
    async def joke(self, ctx: commands.Context):
        """Return a random joke."""
        if not self.jokes:
            await ctx.send(embed=self._embed_info("No jokes yet — ask the admin to add some!", discord.Color.orange()))
            return
        idx = random.randrange(len(self.jokes))
        await ctx.send(embed=self._embed_joke(self.jokes[idx], idx))

    @joke.command(name="list")
    @has_designated_role()
    async def joke_list(self, ctx: commands.Context):
        """DM the full joke list with indices."""
        if not self.jokes:
            await ctx.send(embed=self._embed_info("The joke list is empty.", discord.Color.orange()))
            return

        lines = [f"`{i + 1}.` {joke}" for i, joke in enumerate(self.jokes)]

        # Split into ≤1900-char chunks to stay under Discord's 2000-char limit
        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = line
            else:
                current = (current + "\n" + line) if current else line
        if current:
            chunks.append(current)

        try:
            await ctx.author.send(f"**Joke List — {len(self.jokes)} joke(s):**")
            for chunk in chunks:
                await ctx.author.send(chunk)
            await ctx.send(embed=self._embed_info("Sent you the full joke list in DMs!"), delete_after=8)
        except discord.Forbidden:
            await ctx.send(
                embed=self._embed_info(
                    "I couldn't DM you. Please enable **Direct Messages** from server members and try again.",
                    discord.Color.red(),
                )
            )

    @joke.command(name="add")
    @is_admin()
    async def joke_add(self, ctx: commands.Context, *, text: str):
        """Add a new joke to the list (admin only)."""
        self.jokes.append(text)
        _save_jokes(self.jokes)
        embed = discord.Embed(
            description=f"Joke added as **#{len(self.jokes)}**:\n\n{text}",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @joke.command(name="remove")
    @is_admin()
    async def joke_remove(self, ctx: commands.Context, index: int):
        """Remove a joke by its list index (admin only)."""
        if not self.jokes:
            await ctx.send(embed=self._embed_info("The joke list is already empty.", discord.Color.orange()))
            return
        if index < 1 or index > len(self.jokes):
            await ctx.send(
                embed=self._embed_info(
                    f"Index out of range. Provide a number between **1** and **{len(self.jokes)}**.",
                    discord.Color.red(),
                )
            )
            return
        removed = self.jokes.pop(index - 1)
        _save_jokes(self.jokes)
        embed = discord.Embed(
            description=f"Removed joke **#{index}**:\n\n{removed}",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @joke.error
    async def joke_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            return
        logger.error(f"joke error: {error}")

    @joke_add.error
    async def joke_add_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=self._embed_info("Usage: `!joke add <joke text>`", discord.Color.red()))

    @joke_remove.error
    async def joke_remove_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=self._embed_info("Usage: `!joke remove <number>`", discord.Color.red()))


async def setup(bot: commands.Bot):
    await bot.add_cog(Jokes(bot))
