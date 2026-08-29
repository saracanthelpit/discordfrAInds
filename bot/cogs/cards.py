"""Dropping cards for members to claim, viewing collections, and framing them."""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot import config, database, frames
from bot.database import Card, OwnedCard

# Discord allows at most 10 attachments/embeds per message.
MAX_SHOWCASE = 10
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def render_framed_file(image_url: str, frame_key: str, filename: str) -> discord.File:
    """Download art from ``image_url`` and return it matted in ``frame_key``."""
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(image_url) as resp:
            resp.raise_for_status()
            art_bytes = await resp.read()
    buf = await asyncio.to_thread(frames.compose, art_bytes, frame_key)
    return discord.File(buf, filename=filename)


def _inventory_line(card: OwnedCard) -> str:
    line = f"#{card.card_id} {card.card_name} (print #{card.print_number})"
    if card.frame != frames.DEFAULT:
        line += f" · {frames.label_for(card.frame)} frame"
    return line


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
                f"{interaction.user.mention} claimed **{card.name}** (print #{print_number}) "
                f"— art by <@{card.submitted_by}>!"
            )

        button.callback = callback
        return button

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class ShowcaseSelect(discord.ui.Select):
    """Lets an owner pick copies from their collection to post into the channel, framed."""

    def __init__(self, owned: list[OwnedCard]) -> None:
        self._by_id = {str(c.id): c for c in owned}
        options = [
            discord.SelectOption(
                label=f"#{c.card_id} {c.card_name}"[:100],
                value=str(c.id),
                description=(
                    None if c.frame == frames.DEFAULT else f"{frames.label_for(c.frame)} frame"
                ),
            )
            for c in owned[:25]
        ]
        super().__init__(
            placeholder="Pick card(s) to show in the channel…",
            min_values=1,
            max_values=min(len(options), MAX_SHOWCASE),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        picks = [self._by_id[value] for value in self.values]

        embeds: list[discord.Embed] = []
        files: list[discord.File] = []
        for index, card in enumerate(picks):
            filename = f"card_{index}.png"
            try:
                files.append(await render_framed_file(card.image_url, card.frame, filename))
            except Exception:
                continue
            embed = discord.Embed(
                title=card.card_name,
                description=(
                    f"{frames.label_for(card.frame)} · print #{card.print_number}\n"
                    f"Art by <@{card.submitted_by}>"
                ),
            )
            embed.set_image(url=f"attachment://{filename}")
            embed.set_footer(text=f"Card #{card.card_id} · shown by {interaction.user.display_name}")
            embeds.append(embed)

        if not files:
            await interaction.followup.send("Couldn't render those right now — try again in a moment.", ephemeral=True)
            return

        self.disabled = True
        await interaction.edit_original_response(view=self.view)
        await interaction.followup.send(embeds=embeds, files=files)


class ShowcaseView(discord.ui.View):
    def __init__(self, owned: list[OwnedCard]) -> None:
        super().__init__(timeout=180)
        self.add_item(ShowcaseSelect(owned))


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
            embed.add_field(
                name=card.name,
                value=f"Card #{card.id} · art by <@{card.submitted_by}>",
                inline=True,
            )
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

    @app_commands.command(name="inventory", description="See a collection — pick your own cards to show off in the channel")
    @app_commands.describe(member="Whose collection to view (defaults to you)")
    async def inventory(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        target = member or interaction.user
        owned = await database.get_user_cards(target.id)
        if not owned:
            await interaction.response.send_message(f"{target.display_name} has no cards yet.", ephemeral=True)
            return

        embed = discord.Embed(title=f"{target.display_name}'s collection ({len(owned)} cards)")
        embed.description = "\n".join(_inventory_line(c) for c in owned[:25])
        if len(owned) > 25:
            embed.set_footer(text="Showing the 25 most recent.")

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                embed=embed, view=ShowcaseView(owned), ephemeral=True
            )
        else:
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="frames", description="List the frames you can put on your cards")
    async def frames_list(self, interaction: discord.Interaction) -> None:
        lines = [f"**{f.label}** — `{f.key}`" for f in frames.CHOICES]
        embed = discord.Embed(title="Available frames", description="\n".join(lines))
        embed.set_footer(text="Apply one with /frame, then post it with /inventory")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="frame", description="Put a fancy frame on one of your cards")
    @app_commands.describe(
        user_card_id="The collection entry ID (from /inventory)",
        frame="Which frame to apply",
    )
    @app_commands.choices(
        frame=[app_commands.Choice(name=f.label, value=f.key) for f in frames.CHOICES]
    )
    async def frame(
        self,
        interaction: discord.Interaction,
        user_card_id: int,
        frame: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        owned = await database.set_frame(user_card_id, interaction.user.id, frame.value)
        if owned is None:
            await interaction.followup.send("That collection entry isn't yours.", ephemeral=True)
            return

        try:
            file = await render_framed_file(owned.image_url, owned.frame, "card.png")
        except Exception:
            await interaction.followup.send(
                f"Applied the **{frame.name}** frame to **{owned.card_name}** — "
                "couldn't render a preview, but /inventory will show it.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=owned.card_name,
            description=(
                f"{frame.name} frame · print #{owned.print_number}\n"
                f"Art by <@{owned.submitted_by}>"
            ),
        )
        embed.set_image(url="attachment://card.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @app_commands.command(name="card", description="Show details for one card")
    async def card(self, interaction: discord.Interaction, card_id: int) -> None:
        card = await database.get_card(card_id)
        if not card or card.status != "approved":
            await interaction.response.send_message("No approved card with that ID.", ephemeral=True)
            return

        count = await database.get_card_print_count(card_id)
        embed = discord.Embed(
            title=card.name,
            description=f"{count} copies claimed so far\nArt by <@{card.submitted_by}>",
        )
        embed.set_image(url=card.image_url)
        embed.set_footer(text=f"Card #{card.id}")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cards(bot))
