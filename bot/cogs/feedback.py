import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import slash_designated_role, slash_admin_only

logger = logging.getLogger(__name__)

VALID_STATUSES = ["OPEN", "IN PROGRESS", "RESOLVED", "WONT FIX"]
BUG_COLOR = discord.Color.red()
FEATURE_COLOR = discord.Color.blurple()


# ---------------------------------------------------------------------------
# Bug report modal
# ---------------------------------------------------------------------------

class BugModal(discord.ui.Modal, title="Submit a Bug Report"):
    bug_title = discord.ui.TextInput(
        label="Title",
        placeholder="Short description of the bug",
        max_length=100,
        required=True,
    )
    steps = discord.ui.TextInput(
        label="Steps to Reproduce",
        style=discord.TextStyle.paragraph,
        placeholder="1. Go to…\n2. Click on…\n3. See error",
        max_length=500,
        required=True,
    )
    expected_actual = discord.ui.TextInput(
        label="Expected vs. Actual Behaviour",
        style=discord.TextStyle.paragraph,
        placeholder="Expected: …\nActual: …",
        max_length=300,
        required=True,
    )
    severity = discord.ui.TextInput(
        label="Severity  (Low / Medium / High / Critical)",
        placeholder="Low",
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        sev = self.severity.value.strip().title()
        if sev not in ("Low", "Medium", "High", "Critical"):
            await interaction.response.send_message(
                "Invalid severity. Valid values: Low, Medium, High, Critical.", ephemeral=True
            )
            return

        channel_id = int(os.getenv("BUG_CHANNEL_ID", "0"))
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if not channel:
            await interaction.response.send_message(
                "Bug-report channel not found. Ask an admin to configure BUG_CHANNEL_ID.", ephemeral=True
            )
            return

        embed = discord.Embed(title=f"🐛  BUG: {self.bug_title.value}", color=BUG_COLOR)
        embed.add_field(name="Steps to Reproduce", value=self.steps.value, inline=False)
        embed.add_field(name="Expected vs. Actual", value=self.expected_actual.value, inline=False)
        embed.add_field(name="Severity", value=sev, inline=True)
        embed.add_field(name="Status", value="OPEN", inline=True)
        embed.add_field(
            name="Submitted by",
            value=f"{interaction.user.mention} ({interaction.user})",
            inline=True,
        )
        embed.timestamp = discord.utils.utcnow()

        msg = await channel.send(embed=embed)
        # Back-patch footer with the real message ID
        embed.set_footer(text=f"Submission ID: {msg.id}")
        await msg.edit(embed=embed)

        await interaction.response.send_message(
            "Your bug report has been submitted! ✅", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"BugModal error: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


# ---------------------------------------------------------------------------
# Feature request modal
# ---------------------------------------------------------------------------

class FeatureModal(discord.ui.Modal, title="Submit a Feature Request"):
    feature_title = discord.ui.TextInput(
        label="Feature Title",
        placeholder="What feature would you like?",
        max_length=100,
        required=True,
    )
    description = discord.ui.TextInput(
        label="Description & Use Case",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the feature and how you'd use it",
        max_length=500,
        required=True,
    )
    priority = discord.ui.TextInput(
        label="Priority  (Nice to Have / Important / Critical)",
        placeholder="Nice to Have",
        max_length=30,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.priority.value.strip()
        # Normalise casing
        prio_map = {
            "nice to have": "Nice to Have",
            "important": "Important",
            "critical": "Critical",
        }
        prio = prio_map.get(raw.lower())
        if not prio:
            await interaction.response.send_message(
                "Invalid priority. Valid values: Nice to Have, Important, Critical.", ephemeral=True
            )
            return

        channel_id = int(os.getenv("FEATURE_CHANNEL_ID", "0"))
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if not channel:
            await interaction.response.send_message(
                "Feature-request channel not found. Ask an admin to configure FEATURE_CHANNEL_ID.", ephemeral=True
            )
            return

        embed = discord.Embed(title=f"✨  FEATURE REQUEST: {self.feature_title.value}", color=FEATURE_COLOR)
        embed.add_field(name="Description & Use Case", value=self.description.value, inline=False)
        embed.add_field(name="Priority", value=prio, inline=True)
        embed.add_field(name="Status", value="OPEN", inline=True)
        embed.add_field(
            name="Submitted by",
            value=f"{interaction.user.mention} ({interaction.user})",
            inline=True,
        )
        embed.timestamp = discord.utils.utcnow()

        msg = await channel.send(embed=embed)
        embed.set_footer(text=f"Submission ID: {msg.id}")
        await msg.edit(embed=embed)

        await interaction.response.send_message(
            "Your feature request has been submitted! ✅", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"FeatureModal error: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


# ---------------------------------------------------------------------------
# Feedback cog
# ---------------------------------------------------------------------------

class Feedback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(str(error), ephemeral=True)
        else:
            logger.error(f"Feedback command error: {error}", exc_info=True)

    @app_commands.command(name="bug", description="Submit a bug report via a form")
    @slash_designated_role()
    async def bug(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BugModal())

    @app_commands.command(name="feature", description="Submit a feature request via a form")
    @slash_designated_role()
    async def feature(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeatureModal())

    @app_commands.command(name="status", description="Update the status of a submission (Admin only)")
    @app_commands.describe(
        message_id="The submission message ID (shown in embed footer)",
        new_status="OPEN | IN PROGRESS | RESOLVED | WONT FIX",
    )
    @slash_admin_only()
    async def status(self, interaction: discord.Interaction, message_id: str, new_status: str):
        status_upper = new_status.upper().strip()
        if status_upper not in VALID_STATUSES:
            await interaction.response.send_message(
                f"Invalid status. Valid options: {', '.join(VALID_STATUSES)}", ephemeral=True
            )
            return

        try:
            mid = int(message_id)
        except ValueError:
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        bug_cid = int(os.getenv("BUG_CHANNEL_ID", "0"))
        feat_cid = int(os.getenv("FEATURE_CHANNEL_ID", "0"))

        message: Optional[discord.Message] = None
        for cid in dict.fromkeys([bug_cid, feat_cid]):  # deduplicate, preserve order
            if not cid or not interaction.guild:
                continue
            ch = interaction.guild.get_channel(cid)
            if not ch:
                continue
            try:
                message = await ch.fetch_message(mid)
                break
            except (discord.NotFound, discord.Forbidden):
                continue

        if not message:
            await interaction.response.send_message(
                "Message not found in the configured bug/feature channels.", ephemeral=True
            )
            return

        if not message.embeds:
            await interaction.response.send_message(
                "That message has no embed to update.", ephemeral=True
            )
            return

        old = message.embeds[0]
        new_embed = old.copy()
        new_embed.clear_fields()

        status_written = False
        for f in old.fields:
            if f.name == "Status":
                new_embed.add_field(name="Status", value=status_upper, inline=f.inline)
                status_written = True
            else:
                new_embed.add_field(name=f.name, value=f.value, inline=f.inline)

        if not status_written:
            new_embed.add_field(name="Status", value=status_upper, inline=True)

        await message.edit(embed=new_embed)
        await interaction.response.send_message(
            f"Status updated to **{status_upper}**.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Feedback(bot))
