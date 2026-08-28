"""One-way gifting is implemented. Two-way trade offers are left for you to build —
see the TODO at the bottom and the README's "ideas to build next" section."""

import discord
from discord import app_commands
from discord.ext import commands

from bot import database


class Trading(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="gift", description="Give one of your cards to another member")
    @app_commands.describe(user_card_id="The collection entry ID (from /inventory)", to="Who receives it")
    async def gift(self, interaction: discord.Interaction, user_card_id: int, to: discord.Member) -> None:
        if to.id == interaction.user.id:
            await interaction.response.send_message("You already own that one.", ephemeral=True)
            return

        ok = await database.transfer_card(user_card_id, interaction.user.id, to.id)
        if not ok:
            await interaction.response.send_message("That collection entry isn't yours.", ephemeral=True)
            return

        await interaction.response.send_message(f"{interaction.user.mention} gifted a card to {to.mention}!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trading(bot))


# TODO(you): a real /trade command with mutual offer + accept/decline, so two
# people can swap cards in one transaction instead of gifting one-way. Rough
# shape: a TradeSession (proposer, target, offered user_card_ids, requested
# user_card_ids), a View with Accept/Decline buttons shown to `target`, and a
# database.swap_cards(...) helper that runs both transfers atomically.
