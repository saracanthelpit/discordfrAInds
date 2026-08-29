"""Dropping cards to claim, browsing collections, and framing them."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import config, database, frames
from bot.cardpicker import CardPickerBoard, fetch_art, render_cards
from bot.database import Card, OwnedCard

MAX_SHOWCASE = 10        # Discord's per-message attachment / embed limit
DROP_WINDOW = 60         # seconds a drop stays claimable

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
        embeds, files = await render_cards(updated)
        if files:
            await interaction.edit_original_response(content=note, embeds=embeds, attachments=files, view=None)
        else:
            await interaction.edit_original_response(content=f"{note} (preview unavailable)", embeds=[], view=None)


class DropBoard(discord.ui.LayoutView):
    """A splashy panel with one art tile + Claim button per dropped card.

    Stays claimable for ``window`` seconds; after that the buttons lock and the
    unclaimed ones flip to a "too slow" state.
    """

    def __init__(self, cards: list[Card], have_art: set[int], *, window: int = DROP_WINDOW) -> None:
        super().__init__(timeout=window)
        self.message: Optional[discord.Message] = None  # set by /drop after sending
        self.claimed_by: dict[int, int] = {}  # card_id -> user_id
        self._claim_buttons: list[tuple[Card, discord.ui.Button]] = []
        self._intro = f"{random.choice(_DROP_HEADERS)}\n{random.choice(_DROP_FLAVOR)}"
        ends_at = int(time.time()) + window

        container = discord.ui.Container(accent_colour=random.choice(_DROP_ACCENTS))
        self._header = discord.ui.TextDisplay(
            f"{self._intro}\n**{len(cards)} up for grabs** · ends <t:{ends_at}:R> ⏳"
        )
        container.add_item(self._header)
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
            button = self._claim_button(card)
            self._claim_buttons.append((card, button))
            row.add_item(button)
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

    async def on_timeout(self) -> None:
        for card, button in self._claim_buttons:
            button.disabled = True
            if card.id not in self.claimed_by:
                button.emoji = "⏰"
                button.label = "Too slow — wait for the next drop"[:80]
                button.style = discord.ButtonStyle.secondary
        self._header.content = f"{self._intro}\n**⏰ This drop has ended.** Better luck next time!"
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class InventoryBoard(CardPickerBoard):
    """Collection browser. The owner also gets Show in channel / Apply a frame."""

    async def _show(self, interaction: discord.Interaction) -> None:
        picks = self.picked()
        if not picks:
            await interaction.response.send_message("Select some cards first.", ephemeral=True)
            return
        await interaction.response.defer()
        embeds, files = await render_cards(
            picks[:MAX_SHOWCASE], footer=f"shown by {interaction.user.display_name}"
        )
        if not files:
            await interaction.followup.send("Couldn't render those right now — try again in a moment.", ephemeral=True)
            return
        await interaction.followup.send(embeds=embeds, files=files)

    async def _frame(self, interaction: discord.Interaction) -> None:
        picks = self.picked()
        if not picks:
            await interaction.response.send_message("Select the cards to frame first.", ephemeral=True)
            return
        picker = discord.ui.View(timeout=120)
        picker.add_item(FrameSelect(picks, self.user_id))
        await interaction.response.send_message(
            f"Choose a frame for {len(picks)} card(s):", view=picker, ephemeral=True
        )

    def extra_buttons(self) -> list[discord.ui.Button]:
        if not self.selectable:
            return []
        return [
            self.action_button("Show in channel", self._show, discord.ButtonStyle.primary),
            self.action_button("Apply a frame", self._frame, discord.ButtonStyle.secondary),
        ]


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
        art = await fetch_art([c.image_url for c in cards])
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

        board = DropBoard(cards, have_art)
        board.message = await interaction.followup.send(view=board, files=files)

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
            title=f"{target.display_name}'s collection",
            user_id=interaction.user.id,
            selectable=is_self,
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
