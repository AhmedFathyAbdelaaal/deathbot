import os

import discord
from discord import app_commands
from discord.ext import commands


def _role_id() -> int:
    return int(os.getenv("DESIGNATED_ROLE_ID", "0"))


def _admin_id() -> int:
    return int(os.getenv("ADMIN_USER_ID", "0"))


# ---------------------------------------------------------------------------
# Prefix command checks
# ---------------------------------------------------------------------------

def has_designated_role():
    """Allows admin and members with the designated role."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id == _admin_id():
            return True
        if ctx.guild is None:
            return False
        role = ctx.guild.get_role(_role_id())
        if role and role in ctx.author.roles:
            return True
        await ctx.send(
            embed=discord.Embed(
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ),
            delete_after=5,
        )
        return False

    return commands.check(predicate)


def is_admin():
    """Allows only the configured admin user."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id == _admin_id():
            return True
        await ctx.send(
            embed=discord.Embed(
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ),
            delete_after=5,
        )
        return False

    return commands.check(predicate)


# ---------------------------------------------------------------------------
# Slash command checks
# ---------------------------------------------------------------------------

def slash_designated_role():
    """App-command check: allows admin and designated role members."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == _admin_id():
            return True
        if not interaction.guild:
            raise app_commands.CheckFailure("This command can only be used in a server.")
        role = interaction.guild.get_role(_role_id())
        if role and role in interaction.user.roles:
            return True
        raise app_commands.CheckFailure("You do not have permission to use this command.")

    return app_commands.check(predicate)


def slash_admin_only():
    """App-command check: allows only the configured admin user."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == _admin_id():
            return True
        raise app_commands.CheckFailure("You do not have permission to use this command.")

    return app_commands.check(predicate)
