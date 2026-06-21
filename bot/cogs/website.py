"""
/website — issues the invoking user a pin and the link to the web app.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.base import get_sessionmaker
from services import auth_service
from utils.checks import slash_designated_role

logger = logging.getLogger(__name__)

# Brand accent from the visual design system (PRD 1.7) — same crimson rose the
# web app uses, so the two captionato.tech surfaces share an identity.
SIGNAL = discord.Color.from_str("#B23A52")


class Website(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="website",
        description="Get your personal pin and link for the Death Bot web app",
    )
    @slash_designated_role()
    async def website(self, interaction: discord.Interaction):
        sm = get_sessionmaker()
        if sm is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="❌ The web platform is not available right now.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        async with sm() as session:
            pin = await auth_service.issue_pin(
                session, interaction.user.id, interaction.user.display_name
            )

        embed = discord.Embed(
            title="Death Bot Web",
            description="Open the web app and enter your pin to view and control the queue.",
            color=SIGNAL,
        )
        embed.add_field(name="Your pin", value=f"`{pin}`", inline=True)
        if config.WEB_BASE_URL:
            embed.add_field(name="Link", value=config.WEB_BASE_URL, inline=False)
        embed.set_footer(text="Anyone with your pin can control the queue — keep it to yourself.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Website(bot))
