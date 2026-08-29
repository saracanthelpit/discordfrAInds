"""Members submit their AI art and it goes straight into the drop pool."""

import discord
from discord import app_commands
from discord.ext import commands

from bot import database

NAME_MAX = 80


class Submissions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="submit", description="Submit your AI art as a trading card")
    @app_commands.describe(name="Card name", image="The artwork (PNG/JPG)")
    async def submit(self, interaction: discord.Interaction, name: str, image: discord.Attachment) -> None:
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
            return

        name = name.strip()[:NAME_MAX]
        if not name:
            await interaction.response.send_message("Give the card a name.", ephemeral=True)
            return

        card_id = await database.create_card(name, image.url, interaction.user.id)

        embed = discord.Embed(title=name, description=f"Submitted by {interaction.user.mention} — now in the drop pool!")
        embed.set_image(url=image.url)
        embed.set_footer(text=f"Card #{card_id}")

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Submissions(bot))
