"""Shared paginated, per-card art picker built on Components V2.

/inventory, /gift and /trade all show a page of cards, each with its framed art
and a Select toggle, plus Prev/Next and whatever action buttons the subclass
adds. Composited pages are cached, so toggling a selection never re-fetches or
re-uploads anything.

Cards passed in must expose: ``id``, ``card_id``, ``card_name``, ``image_url``,
``print_number``, ``submitted_by``, ``frame`` (i.e. ``database.OwnedCard``).
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Callable, Optional, Sequence

import aiohttp
import discord

from bot import frames
from bot.database import OwnedCard

PAGE_SIZE = 5
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def fetch_art(urls: Sequence[str]) -> dict[str, bytes]:
    """Download every distinct URL, a few at a time. Missing ones are omitted."""
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


async def render_cards(
    cards: Sequence[OwnedCard], *, footer: Optional[str] = None
) -> tuple[list[discord.Embed], list[discord.File]]:
    """One embed + framed-art attachment per card. Cards that fail to load are skipped."""
    art = await fetch_art([c.image_url for c in cards])
    embeds: list[discord.Embed] = []
    files: list[discord.File] = []
    for index, card in enumerate(cards):
        raw = art.get(card.image_url)
        if raw is None:
            continue
        try:
            buf = await asyncio.to_thread(frames.compose, raw, card.frame)
        except Exception:
            continue
        filename = f"show_{index}.png"
        files.append(discord.File(buf, filename=filename))
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


class CardPickerBoard(discord.ui.LayoutView):
    """Paged card list with per-card art + Select toggle. Subclass to add actions.

    Subclasses that juggle more than one selection (e.g. a trade's two sides)
    override ``_active_cards`` and ``_selected_set`` to swap what the page shows.
    """

    def __init__(
        self,
        cards: Sequence[OwnedCard],
        *,
        title: str,
        user_id: int,
        selectable: bool = True,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cards: list[OwnedCard] = list(cards)
        self.title = title
        self.user_id = user_id
        self.selectable = selectable
        self.page = 0
        self.selected: set[int] = set()
        self._cache: dict[tuple[str, str], bytes] = {}  # (url, frame) -> composited PNG

    # --- what the current page draws from (override for multi-selection) ---
    def _active_cards(self) -> list[OwnedCard]:
        return self.cards

    def _selected_set(self) -> set[int]:
        return self.selected

    # --- pagination -------------------------------------------------------
    @property
    def pages(self) -> int:
        return max(1, -(-len(self._active_cards()) // PAGE_SIZE))

    def _page_cards(self) -> list[OwnedCard]:
        start = self.page * PAGE_SIZE
        return self._active_cards()[start : start + PAGE_SIZE]

    def picked(self) -> list[OwnedCard]:
        chosen = self._selected_set()
        return [c for c in self._active_cards() if c.id in chosen]

    # --- subclass hooks -------------------------------------------------
    def extra_buttons(self) -> list[discord.ui.Button]:
        return []

    def status_line(self) -> str:
        line = f"{len(self._active_cards())} cards · page {self.page + 1}/{self.pages}"
        if self.selectable:
            line += f" · {len(self._selected_set())} selected"
        return line

    def action_button(
        self,
        label: str,
        handler: Callable[[discord.Interaction], object],
        style: discord.ButtonStyle = discord.ButtonStyle.primary,
    ) -> discord.ui.Button:
        button = discord.ui.Button(label=label[:80], style=style)
        button.callback = handler
        return button

    # --- rendering ------------------------------------------------------
    async def _warm_cache(self, cards: list[OwnedCard]) -> None:
        missing = [c.image_url for c in cards if (c.image_url, c.frame) not in self._cache]
        if not missing:
            return
        fetched = await fetch_art(missing)
        for card in cards:
            key = (card.image_url, card.frame)
            if key not in self._cache and card.image_url in fetched:
                try:
                    buf = await asyncio.to_thread(frames.compose, fetched[card.image_url], card.frame)
                    self._cache[key] = buf.getvalue()
                except Exception:
                    pass

    async def build(self) -> list[discord.File]:
        """Rebuild components for the current page; returns that page's attachments."""
        self.clear_items()
        self.page = max(0, min(self.page, self.pages - 1))
        cards = self._page_cards()
        await self._warm_cache(cards)
        chosen = self._selected_set()

        files: list[discord.File] = []
        self.add_item(discord.ui.TextDisplay(f"## {self.title}\n{self.status_line()}"))
        for index, card in enumerate(cards):
            data = self._cache.get((card.image_url, card.frame))
            if data is not None:
                filename = f"pick_{self.page}_{index}.png"
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
            if self.selectable:
                row = discord.ui.ActionRow()
                row.add_item(self._toggle_button(card, selected=card.id in chosen))
                self.add_item(row)
            self.add_item(discord.ui.Separator())

        nav = discord.ui.ActionRow()
        nav.add_item(self._nav_button("◀ Prev", -1))
        nav.add_item(self._nav_button("Next ▶", +1))
        for button in self.extra_buttons():
            nav.add_item(button)
        self.add_item(nav)
        return files

    def _toggle_button(self, card: OwnedCard, *, selected: bool) -> discord.ui.Button:
        button = discord.ui.Button(
            label="Selected ✓" if selected else "Select",
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            chosen = self._selected_set()
            chosen.discard(card.id) if card.id in chosen else chosen.add(card.id)
            await self.build()  # cache is warm; this just restyles buttons
            await interaction.response.edit_message(view=self)

        button.callback = callback
        return button

    def _nav_button(self, label: str, delta: int) -> discord.ui.Button:
        target = self.page + delta
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=not 0 <= target < self.pages,
        )

        async def callback(interaction: discord.Interaction) -> None:
            self.page = max(0, min(self.pages - 1, self.page + delta))
            files = await self.build()
            await interaction.response.edit_message(view=self, attachments=files)

        button.callback = callback
        return button

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.selectable and interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't yours to touch.", ephemeral=True)
            return False
        return True
