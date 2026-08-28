"""Dropping cards for members to claim, and viewing collections."""

import discord
from discord import app_commands
from discord.ext import commands

from bot import config, database
from bot.database import Card


class DropView(discord.ui.View):
    """One button per dropped card. First click claims it, then that button locks."""

    def __init__(self, cards: list[Card]) -> None:
        super().__init__(timeout=300)
        self.claimed_by: dict[int, int] = {}  # card_id -> user_id
        for card in cards:
            self.add_item(self._make_button(card))

    def _make_button(self, card: Card) -> discord.ui.Button:
        button = discord.ui.Button(label=f"Claim {card.name}", style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction) -> None:
            if card.id in self.claimed_by:
                await interaction.response.send_message("Someone already claimed that one.", ephemeral=True)
                return

            self.claimed_by[card.id] = interaction.user.id
            print_number = await database.claim_card(card.id, interaction.user.id)
            button.disabled = True
            button.label = f"{card.name} — claimed by {interaction.user.display_name}"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                f"{interaction.user.mention} claimed **{card.name}** (print #{print_number})!"
            )

        button.callback = callback
        return button

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class Cards(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="drop", description="Drop a fresh set of cards for everyone to claim")
    @app_commands.checks.cooldown(1, config.DROP_COOLDOWN_SECONDS, key=lambda i: i.user.id)
    async def drop(self, interaction: discord.Interaction) -> None:
        cards = await database.get_random_approved_cards(config.DROP_SIZE)
        if not cards:
            await interaction.response.send_message(
                "No approved cards in the pool yet — submit some art with /submit!", ephemeral=True
            )
            return

        embed = discord.Embed(title="A drop appeared!", description="Click a button below to claim a card.")
        for card in cards:
            embed.add_field(name=card.name, value=f"Card #{card.id}", inline=True)
        if cards:
            embed.set_thumbnail(url=cards[0].image_url)

        await interaction.response.send_message(embed=embed, view=DropView(cards))

    @drop.error
    async def drop_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"You can drop again in {error.retry_after:.0f}s.", ephemeral=True
            )
            return
        raise error

    @app_commands.command(name="inventory", description="See a member's card collection")
    @app_commands.describe(member="Whose collection to view (defaults to you)")
    async def inventory(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        owned = await database.get_user_cards(target.id)
        if not owned:
            await interaction.response.send_message(f"{target.display_name} has no cards yet.", ephemeral=True)
            return

        lines = [f"#{c.card_id} {c.card_name} (print #{c.print_number})" for c in owned[:25]]
        embed = discord.Embed(title=f"{target.display_name}'s collection ({len(owned)} cards)")
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="card", description="Show details for one card")
    async def card(self, interaction: discord.Interaction, card_id: int) -> None:
        card = await database.get_card(card_id)
        if not card or card.status != "approved":
            await interaction.response.send_message("No approved card with that ID.", ephemeral=True)
            return

        count = await database.get_card_print_count(card_id)
        embed = discord.Embed(title=card.name, description=f"{count} copies claimed so far")
        embed.set_image(url=card.image_url)
        embed.set_footer(text=f"Card #{card.id} — submitted by <@{card.submitted_by}>")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cards(bot))
