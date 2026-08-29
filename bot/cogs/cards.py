"""Dropping cards to claim, browsing collections, and framing them."""

from __future__ import annotations

import asyncio
import random
from io import BytesIO
from typing import Callable, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot import config, database, frames
from bot.database import Card, OwnedCard

MAX_SHOWCASE = 10        # Discord's per-message attachment / embed limit
INVENTORY_PAGE = 5       # cards shown per /inventory page
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Drop celebration flavor — one of each is picked per drop.
_DROP_HEADERS = (
    "# 🎉 A WILD DROP APPEARED! 🎉",
    "# ✨ DROP INCOMING ✨",
    "# 🎊 FRESH CARDS JUST DROPPED 🎊",
    "# 💥 DROP! DROP! DROP! 💥",
    "# 🪄 THE POOL STIRS… 🪄",
    "# 🌟 LOOK WHAT FELL OUT OF THE PACK 🌟",
)
_DROP_FLAVOR = (
    "The cards shimmer into view…",
    "Fortune favors the fast. 🍀",
    "Snooze and you lose. 👀",
    "May the quickest click win. 🖱️",
    "Someone's collection is about to level up. 📈",
    "Gloves off. 🥊",
)
_DROP_ACCENTS = (
    0xF1C40F, 0xE91E63, 0x9B59B6, 0x2ECC71, 0x3498DB, 0xE67E22, 0x1ABC9C, 0xFF5E5B,
)
_CLAIM_CHEERS = ("🎊", "🎉", "✨", "💥", "🙌", "🔥")


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


class FrameSelect(discord.ui.Select):
    """"Apply a frame" step two: choose one frame for the cards selected so far."""

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
        note = f"Applied the **{label}** frame to {len(updated)} card{plural}. Re-run /inventory to refresh it."
        embeds, files = await _render_cards(updated, footer=None)
        if files:
            await interaction.edit_original_response(content=note, embeds=embeds, attachments=files, view=None)
        else:
            await interaction.edit_original_response(content=f"{note} (preview unavailable)", embeds=[], view=None)


class DropBoard(discord.ui.LayoutView):
    """A splashy panel with one art tile + Claim button per dropped card."""

    def __init__(self, cards: list[Card], have_art: set[int]) -> None:
        super().__init__(timeout=900)
        self.claimed_by: dict[int, int] = {}  # card_id -> user_id

        container = discord.ui.Container(accent_colour=random.choice(_DROP_ACCENTS))
        container.add_item(
            discord.ui.TextDisplay(
                f"{random.choice(_DROP_HEADERS)}\n"
                f"{random.choice(_DROP_FLAVOR)}\n"
                f"**{len(cards)} up for grabs** · first click wins each 🏁"
            )
        )
        for index, card in enumerate(cards):
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            if card.id in have_art:
                container.add_item(
                    discord.ui.MediaGallery(
                        discord.MediaGalleryItem(media=f"attachment://drop_{index}.png")
                    )
                )
            container.add_item(
                discord.ui.TextDisplay(f"### 🃏 {card.name}\n#{card.id} · art by <@{card.submitted_by}>")
            )
            row = discord.ui.ActionRow()
            row.add_item(self._claim_button(card))
            container.add_item(row)
        self.add_item(container)

    def _claim_button(self, card: Card) -> discord.ui.Button:
        button = discord.ui.Button(
            label=f"Claim {card.name}"[:80], emoji="✋", style=discord.ButtonStyle.success
        )

        async def callback(interaction: discord.Interaction) -> None:
            if card.id in self.claimed_by:
                await interaction.response.send_message(
                    "Too slow — someone already grabbed that one! 😤", ephemeral=True
                )
                return
            self.claimed_by[card.id] = interaction.user.id
            print_number = await database.claim_card(card.id, interaction.user.id)
            button.disabled = True
            button.emoji = None
            button.label = f"Claimed by {interaction.user.display_name}"[:80]
            await interaction.response.edit_message(view=self)

            if print_number == 1:
                announcement = (
                    f"🥇 **FIRST PRINT!** {interaction.user.mention} nabbed **{card.name}** #1 "
                    f"— art by <@{card.submitted_by}>! 🎉"
                )
            else:
                announcement = (
                    f"{random.choice(_CLAIM_CHEERS)} {interaction.user.mention} claimed "
                    f"**{card.name}** (print #{print_number}) — art by <@{card.submitted_by}>!"
                )
            await interaction.followup.send(announcement)

        button.callback = callback
        return button


class InventoryBoard(discord.ui.LayoutView):
    """Paged: one art panel + Select toggle per card. The owner also gets Show / Frame."""

    def __init__(self, owned: list[OwnedCard], owner_id: Optional[int], *, title: str) -> None:
        super().__init__(timeout=300)
        self.owned = owned
        self.owner_id = owner_id  # None => read-only view of someone else's collection
        self.title = title
        self.page = 0
        self.selected: set[int] = set()
        self._cache: dict[tuple[str, str], bytes] = {}  # (url, frame) -> composited PNG

    @property
    def _pages(self) -> int:
        return max(1, -(-len(self.owned) // INVENTORY_PAGE))

    def _page_cards(self) -> list[OwnedCard]:
        start = self.page * INVENTORY_PAGE
        return self.owned[start : start + INVENTORY_PAGE]

    def _picked(self) -> list[OwnedCard]:
        return [c for c in self.owned if c.id in self.selected]

    async def build(self) -> list[discord.File]:
        """Rebuild the components for the current page; returns that page's attachments."""
        self.clear_items()
        cards = self._page_cards()

        missing = [c.image_url for c in cards if (c.image_url, c.frame) not in self._cache]
        fetched = await _fetch_art(missing) if missing else {}
        for card in cards:
            key = (card.image_url, card.frame)
            if key not in self._cache and card.image_url in fetched:
                try:
                    buf = await asyncio.to_thread(frames.compose, fetched[card.image_url], card.frame)
                    self._cache[key] = buf.getvalue()
                except Exception:
                    pass

        files: list[discord.File] = []
        status = f"## {self.title}\n{len(self.owned)} cards · page {self.page + 1}/{self._pages}"
        if self.owner_id is not None:
            status += f" · {len(self.selected)} selected"
        self.add_item(discord.ui.TextDisplay(status))

        for index, card in enumerate(cards):
            data = self._cache.get((card.image_url, card.frame))
            if data is not None:
                filename = f"inv_{self.page}_{index}.png"
                files.append(discord.File(BytesIO(data), filename=filename))
                self.add_item(
                    discord.ui.MediaGallery(discord.MediaGalleryItem(media=f"attachment://{filename}"))
                )
            tag = "" if card.frame == frames.DEFAULT else f" · {frames.label_for(card.frame)} frame"
            self.add_item(
                discord.ui.TextDisplay(
                    f"**{card.card_name}** · #{card.card_id} · print #{card.print_number}{tag} "
                    f"· art by <@{card.submitted_by}>"
                )
            )
            if self.owner_id is not None:
                row = discord.ui.ActionRow()
                row.add_item(self._toggle_button(card))
                self.add_item(row)
            self.add_item(discord.ui.Separator())

        nav = discord.ui.ActionRow()
        nav.add_item(self._nav_button("◀ Prev", -1))
        nav.add_item(self._nav_button("Next ▶", +1))
        if self.owner_id is not None:
            nav.add_item(self._plain_button("Show in channel", self._show, discord.ButtonStyle.primary))
            nav.add_item(self._plain_button("Apply a frame", self._frame, discord.ButtonStyle.secondary))
        self.add_item(nav)
        return files

    def _toggle_button(self, card: OwnedCard) -> discord.ui.Button:
        on = card.id in self.selected
        button = discord.ui.Button(
            label="Selected ✓" if on else "Select",
            style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            self.selected.discard(card.id) if card.id in self.selected else self.selected.add(card.id)
            await self.build()  # cache is warm, so this just restyles the buttons
            await interaction.response.edit_message(view=self)

        button.callback = callback
        return button

    def _nav_button(self, label: str, delta: int) -> discord.ui.Button:
        target = self.page + delta
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=not 0 <= target < self._pages,
        )

        async def callback(interaction: discord.Interaction) -> None:
            self.page = max(0, min(self._pages - 1, self.page + delta))
            files = await self.build()
            await interaction.response.edit_message(view=self, attachments=files)

        button.callback = callback
        return button

    def _plain_button(
        self,
        label: str,
        handler: Callable[[discord.Interaction], object],
        style: discord.ButtonStyle,
    ) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style)
        button.callback = handler
        return button

    async def _show(self, interaction: discord.Interaction) -> None:
        picks = self._picked()
        if not picks:
            await interaction.response.send_message("Select some cards first.", ephemeral=True)
            return
        await interaction.response.defer()
        embeds, files = await _render_cards(
            picks[:MAX_SHOWCASE], footer=f"shown by {interaction.user.display_name}"
        )
        if not files:
            await interaction.followup.send("Couldn't render those right now — try again in a moment.", ephemeral=True)
            return
        await interaction.followup.send(embeds=embeds, files=files)

    async def _frame(self, interaction: discord.Interaction) -> None:
        picks = self._picked()
        if not picks:
            await interaction.response.send_message("Select the cards to frame first.", ephemeral=True)
            return
        picker = discord.ui.View(timeout=120)
        picker.add_item(FrameSelect(picks, self.owner_id))
        await interaction.response.send_message(
            f"Choose a frame for {len(picks)} card(s):", view=picker, ephemeral=True
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your inventory.", ephemeral=True)
            return False
        return True


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
        art = await _fetch_art([c.image_url for c in cards])
        files: list[discord.File] = []
        have_art: set[int] = set()
        for index, card in enumerate(cards):
            raw = art.get(card.image_url)
            if raw is None:
                continue
            try:
                buf = await asyncio.to_thread(frames.compose, raw, frames.DEFAULT)
            except Exception:
                continue
            files.append(discord.File(buf, filename=f"drop_{index}.png"))
            have_art.add(card.id)

        await interaction.followup.send(view=DropBoard(cards, have_art), files=files)

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
        description="Browse a collection card by card — for your own, select cards to show or reframe",
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
        board = InventoryBoard(
            owned,
            interaction.user.id if is_self else None,
            title=f"{target.display_name}'s collection",
        )
        files = await board.build()
        await interaction.followup.send(view=board, files=files, ephemeral=is_self)

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
