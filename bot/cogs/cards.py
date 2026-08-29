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


async def _fetch_art(urls: list[str]) -> dict[str, bytes]:
    """Download every distinct URL, a few at a time. Missing ones are just omitted."""
    fetched: dict[str, bytes] = {}
    semaphore = asyncio.Semaphore(6)

    async def one(session: aiohttp.ClientSession, url: str) -> None:
        async with semaphore:
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    fetched[url] = await resp.read()
            except Exception:
                pass

    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        await asyncio.gather(*(one(session, url) for url in set(urls)))
    return fetched


async def _collection_sheet(cards: list[OwnedCard]) -> Optional[discord.File]:
    """A single grid image of the collection, framed, or None if nothing rendered."""
    art = await _fetch_art([c.image_url for c in cards])
    tiles = [
        (art[c.image_url], c.frame, f"#{c.card_id} {c.card_name}")
        for c in cards
        if c.image_url in art
    ]
    if not tiles:
        return None
    buf = await asyncio.to_thread(frames.contact_sheet, tiles)
    return discord.File(buf, filename="inventory.png")


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


async def _render_cards(
    cards: list[OwnedCard], *, footer: Optional[str]
) -> tuple[list[discord.Embed], list[discord.File]]:
    """Composite each owned copy in its frame; returns matched embed/file lists."""
    embeds: list[discord.Embed] = []
    files: list[discord.File] = []
    for index, card in enumerate(cards):
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
        embed.set_footer(text=f"Card #{card.card_id}" + (f" · {footer}" if footer else ""))
        embeds.append(embed)
    return embeds, files


class InventorySelect(discord.ui.Select):
    """Multi-select of the owner's copies. The parent view reads ``picked()``."""

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
            placeholder="Pick card(s)…",
            min_values=1,
            max_values=min(len(options), MAX_SHOWCASE),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # selection is read when a button is pressed

    def picked(self) -> list[OwnedCard]:
        return [self._by_id[value] for value in self.values]


class FrameSelect(discord.ui.Select):
    """Second step of "Apply a frame": choose one frame for the cards picked so far."""

    def __init__(self, cards: list[OwnedCard], owner_id: int) -> None:
        self._cards = cards
        self._owner_id = owner_id
        options = [
            discord.SelectOption(
                label=f.label,
                value=f.key,
                description="removes the frame" if f.key == frames.DEFAULT else f"{f.label} frame",
            )
            for f in frames.CHOICES
        ]
        super().__init__(placeholder="Choose a frame…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        frame_key = self.values[0]
        updated: list[OwnedCard] = []
        for card in self._cards:
            row = await database.set_frame(card.id, self._owner_id, frame_key)
            if row is not None:
                updated.append(row)
        if not updated:
            await interaction.followup.send("Couldn't apply that — those aren't yours.", ephemeral=True)
            return

        label = frames.label_for(frame_key)
        plural = "s" if len(updated) != 1 else ""
        note = f"Applied the **{label}** frame to {len(updated)} card{plural}."
        embeds, files = await _render_cards(updated, footer=None)
        if files:
            await interaction.edit_original_response(content=note, embeds=embeds, attachments=files, view=None)
        else:
            await interaction.edit_original_response(content=f"{note} (preview unavailable)", embeds=[], view=None)


class InventoryView(discord.ui.View):
    """Ephemeral, owner-only: pick copies, then show them in-channel or reframe them."""

    def __init__(self, owned: list[OwnedCard], owner_id: int) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.select = InventorySelect(owned)
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your inventory.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Show in channel", style=discord.ButtonStyle.primary, row=1)
    async def show(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        picks = self.select.picked()
        if not picks:
            await interaction.response.send_message("Pick at least one card first.", ephemeral=True)
            return
        await interaction.response.defer()
        embeds, files = await _render_cards(picks, footer=f"shown by {interaction.user.display_name}")
        if not files:
            await interaction.followup.send("Couldn't render those right now — try again in a moment.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(embeds=embeds, files=files)

    @discord.ui.button(label="Apply a frame", style=discord.ButtonStyle.secondary, row=1)
    async def apply_frame(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        picks = self.select.picked()
        if not picks:
            await interaction.response.send_message("Pick the card(s) to frame first.", ephemeral=True)
            return
        picker = discord.ui.View(timeout=120)
        picker.add_item(FrameSelect(picks, self.owner_id))
        names = ", ".join(c.card_name for c in picks)
        await interaction.response.edit_message(content=f"Choose a frame for **{names}**:", embed=None, view=picker)


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

        await interaction.response.defer()

        embed = discord.Embed(title="A drop appeared!", description="Click a button below to claim a card.")
        for card in cards:
            embed.add_field(
                name=card.name,
                value=f"Card #{card.id} · art by <@{card.submitted_by}>",
                inline=True,
            )

        drop_file: Optional[discord.File] = None
        try:
            art = await _fetch_art([c.image_url for c in cards])
            tiles = [
                (art[c.image_url], frames.DEFAULT, f"#{c.id} {c.name}")
                for c in cards
                if c.image_url in art
            ]
            if tiles:
                buf = await asyncio.to_thread(
                    frames.contact_sheet, tiles, columns=min(len(tiles), 4)
                )
                drop_file = discord.File(buf, filename="drop.png")
                embed.set_image(url="attachment://drop.png")
        except Exception:
            drop_file = None
        if drop_file is None:
            embed.set_thumbnail(url=cards[0].image_url)

        kwargs: dict = {"embed": embed, "view": DropView(cards)}
        if drop_file is not None:
            kwargs["file"] = drop_file
        await interaction.followup.send(**kwargs)

    @drop.error
    async def drop_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"You can drop again in {error.retry_after:.0f}s.", ephemeral=True
            )
            return
        raise error

    @app_commands.command(
        name="inventory",
        description="View a collection — for your own, pick cards to show in-channel or reframe",
    )
    @app_commands.describe(member="Whose collection to view (defaults to you)")
    async def inventory(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        target = member or interaction.user
        is_self = target.id == interaction.user.id
        owned = await database.get_user_cards(target.id)
        if not owned:
            await interaction.response.send_message(f"{target.display_name} has no cards yet.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=is_self)
        shown = owned[:25]

        embed = discord.Embed(title=f"{target.display_name}'s collection ({len(owned)} cards)")
        embed.description = "\n".join(_inventory_line(c) for c in shown)
        footer = []
        if len(owned) > 25:
            footer.append("showing the 25 most recent")
        if is_self:
            footer.append("pick cards below to show in channel or reframe")
        if footer:
            embed.set_footer(text=" · ".join(footer))

        sheet = await _collection_sheet(shown)
        if sheet is not None:
            embed.set_image(url="attachment://inventory.png")

        kwargs: dict = {"embed": embed, "ephemeral": is_self}
        if sheet is not None:
            kwargs["file"] = sheet
        if is_self:
            kwargs["view"] = InventoryView(owned, interaction.user.id)
        await interaction.followup.send(**kwargs)

    @app_commands.command(name="frames", description="Preview the frame styles you can put on your cards")
    async def frames_list(self, interaction: discord.Interaction) -> None:
        lines = [f"**{f.label}**" for f in frames.CHOICES]
        embed = discord.Embed(title="Frame styles", description="\n".join(lines))
        embed.set_footer(text="Apply one from /inventory → Apply a frame")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
