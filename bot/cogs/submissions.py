"""Members submit their AI art as a card; mods approve or reject it."""

import discord
from discord import app_commands
from discord.ext import commands

from bot import config, database


def _is_mod(member: discord.Member) -> bool:
    if config.MOD_ROLE_ID is None:
        return member.guild_permissions.manage_guild
    return any(role.id == config.MOD_ROLE_ID for role in member.roles)


class ReviewView(discord.ui.View):
    """Approve/reject buttons attached to a pending submission post."""

    def __init__(self, card_id: int) -> None:
        super().__init__(timeout=None)
        self.card_id = card_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not _is_mod(interaction.user):
            await interaction.response.send_message(
                "Only mods can review submissions.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="submission_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await database.set_submission_status(self.card_id, "approved", interaction.user.id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Approved by {interaction.user.mention} — card is now in the drop pool.",
            view=self,
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="submission_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await database.set_submission_status(self.card_id, "rejected", interaction.user.id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Rejected by {interaction.user.mention}.", view=self
        )


class Submissions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="submit", description="Submit your AI art as a trading card")
    @app_commands.describe(name="Card name", image="The artwork (PNG/JPG)")
    async def submit(self, interaction: discord.Interaction, name: str, image: discord.Attachment) -> None:
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
            return

        card_id = await database.create_submission(name, image.url, interaction.user.id)

        review_channel = None
        if config.REVIEW_CHANNEL_ID:
            review_channel = interaction.guild.get_channel(config.REVIEW_CHANNEL_ID)

        embed = discord.Embed(title=name, description=f"Submitted by {interaction.user.mention}")
        embed.set_image(url=image.url)
        embed.set_footer(text=f"Card #{card_id}")

        if review_channel:
            await review_channel.send(embed=embed, view=ReviewView(card_id))
            await interaction.response.send_message(
                "Submitted! A mod will review it shortly.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Submitted, but no review channel is configured yet "
                "(set REVIEW_CHANNEL_ID in .env). Ask a mod to check the pending list.",
                ephemeral=True,
            )

    @app_commands.command(name="pending", description="List submissions awaiting review")
    async def pending(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not _is_mod(interaction.user):
            await interaction.response.send_message("Only mods can view this.", ephemeral=True)
            return

        submissions = await database.get_pending_submissions()
        if not submissions:
            await interaction.response.send_message("Nothing pending.", ephemeral=True)
            return

        lines = [f"#{c.id} — {c.name} (<@{c.submitted_by}>)" for c in submissions]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Submissions(bot))
