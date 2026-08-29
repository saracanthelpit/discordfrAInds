"""Card transfers: one-way /gift and a two-way /trade with offer + accept/decline.

Both use the same paginated art-per-card picker as /inventory (see
``bot.cardpicker``); /trade just runs it two-sided.
"""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from bot import database
from bot.cardpicker import CardPickerBoard, render_cards
from bot.database import OwnedCard

# A trade message shows the offer summary plus an art tile per card; cap the
# tiles so it always fits Discord's 10-embed / 10-attachment limit.
MAX_OFFER_TILES = 9

_GIFT_CHEERS = ("🎁", "🎉", "✨", "💝", "🥳", "🙌")


def _attribution_line(card: OwnedCard) -> str:
    return f"**{card.card_name}** (print #{card.print_number}) — art by <@{card.submitted_by}>"


def _offer_embed(
    initiator: discord.abc.User,
    target: discord.abc.User,
    give: list[OwnedCard],
    want: list[OwnedCard],
) -> discord.Embed:
    embed = discord.Embed(title="🤝 Trade offer", description=f"{initiator.mention} → {target.mention}")
    embed.add_field(
        name=f"{initiator.display_name} gives",
        value="\n".join(_attribution_line(c) for c in give),
        inline=False,
    )
    embed.add_field(
        name=f"{target.display_name} gives",
        value="\n".join(_attribution_line(c) for c in want) if want else "— nothing requested",
        inline=False,
    )
    embed.set_footer(text="Only the tagged member can accept or decline. Offer expires in 5 min.")
    return embed


class GiftBoard(CardPickerBoard):
    """Pick cards from your collection (art shown) and hand them to one member."""

    def __init__(
        self,
        giver: discord.abc.User,
        recipient: discord.Member,
        cards: list[OwnedCard],
    ) -> None:
        super().__init__(
            cards,
            title=f"Gift to {recipient.display_name}",
            user_id=giver.id,
            selectable=True,
        )
        self.giver = giver
        self.recipient = recipient

    def extra_buttons(self) -> list[discord.ui.Button]:
        return [
            self.action_button(
                f"Give to {self.recipient.display_name}", self._give, discord.ButtonStyle.success
            )
        ]

    async def _give(self, interaction: discord.Interaction) -> None:
        picks = self.picked()
        if not picks:
            await interaction.response.send_message("Select at least one card first.", ephemeral=True)
            return

        given: list[OwnedCard] = []
        for card in picks:
            if await database.transfer_card(card.id, self.giver.id, self.recipient.id):
                given.append(card)
        if not given:
            await interaction.response.send_message(
                "Those aren't yours to give anymore.", ephemeral=True
            )
            return

        given_ids = {c.id for c in given}
        self.cards = [c for c in self.cards if c.id not in given_ids]
        self.selected.clear()
        self.title = "✅ Gifted!" if not self.cards else f"Gift to {self.recipient.display_name}"
        files = await self.build()
        await interaction.response.edit_message(view=self, attachments=files)

        plural = "s" if len(given) != 1 else ""
        content = (
            f"{random.choice(_GIFT_CHEERS)} {self.giver.mention} gifted {self.recipient.mention} "
            f"{len(given)} card{plural}! {random.choice(_GIFT_CHEERS)}"
        )
        embeds, gift_files = await render_cards(
            given[:MAX_OFFER_TILES], footer=f"gifted by {self.giver.display_name}"
        )
        mentions = discord.AllowedMentions(users=[self.recipient])
        if embeds:
            await interaction.followup.send(
                content=content, embeds=embeds, files=gift_files, allowed_mentions=mentions
            )
        else:
            lines = "\n".join(_attribution_line(c) for c in given)
            await interaction.followup.send(content=f"{content}\n{lines}", allowed_mentions=mentions)


class TradeBuilderBoard(CardPickerBoard):
    """Two-sided picker: browse your cards and theirs, Select on each, then Send offer."""

    def __init__(
        self,
        initiator: discord.abc.User,
        target: discord.Member,
        mine: list[OwnedCard],
        theirs: list[OwnedCard],
    ) -> None:
        super().__init__(
            mine,
            title=f"Trade with {target.display_name}",
            user_id=initiator.id,
            selectable=True,
        )
        self.initiator = initiator
        self.target = target
        self.mine = mine
        self.theirs = theirs
        self.side = "give"  # "give" => browsing your cards, "want" => theirs
        self.give_ids: set[int] = set()
        self.want_ids: set[int] = set()

    def _active_cards(self) -> list[OwnedCard]:
        return self.mine if self.side == "give" else self.theirs

    def _selected_set(self) -> set[int]:
        return self.give_ids if self.side == "give" else self.want_ids

    def status_line(self) -> str:
        browsing = "your cards" if self.side == "give" else f"{self.target.display_name}'s cards"
        return (
            f"you give **{len(self.give_ids)}** · you get **{len(self.want_ids)}** "
            f"· page {self.page + 1}/{self.pages} · browsing {browsing}"
        )

    def extra_buttons(self) -> list[discord.ui.Button]:
        buttons: list[discord.ui.Button] = []
        if self.theirs:
            label = (
                f"Browse {self.target.display_name}'s cards →"
                if self.side == "give"
                else "← Back to your cards"
            )
            buttons.append(self.action_button(label, self._swap_side, discord.ButtonStyle.secondary))
        buttons.append(self.action_button("Send offer", self._send, discord.ButtonStyle.success))
        return buttons

    async def _swap_side(self, interaction: discord.Interaction) -> None:
        self.side = "want" if self.side == "give" else "give"
        self.page = 0
        files = await self.build()
        await interaction.response.edit_message(view=self, attachments=files)

    async def _send(self, interaction: discord.Interaction) -> None:
        if not self.give_ids:
            await interaction.response.send_message(
                "Pick at least one of your cards to give.", ephemeral=True
            )
            return
        give = [c for c in self.mine if c.id in self.give_ids]
        want = [c for c in self.theirs if c.id in self.want_ids]

        self.clear_items()
        self.add_item(discord.ui.TextDisplay("## Offer sent ✅\nWaiting on their response…"))
        await interaction.response.edit_message(view=self, attachments=[])

        embeds, files = await render_cards((give + want)[:MAX_OFFER_TILES], footer="on the table")
        await interaction.followup.send(
            content=self.target.mention,
            embeds=[_offer_embed(self.initiator, self.target, give, want), *embeds][:10],
            files=files[:10],
            view=OfferResponse(self.initiator, self.target, give, want),
            allowed_mentions=discord.AllowedMentions(users=[self.target]),
        )


class OfferResponse(discord.ui.View):
    """Public message; only the offer's target can act on it."""

    def __init__(
        self,
        initiator: discord.abc.User,
        target: discord.Member,
        give: list[OwnedCard],
        want: list[OwnedCard],
    ) -> None:
        super().__init__(timeout=300)
        self.initiator = initiator
        self.target = target
        self.give = give
        self.want = want

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "This trade offer isn't addressed to you.", ephemeral=True
            )
            return False
        return True

    def _lock(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Accept", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ok = await database.swap_cards(
            [c.id for c in self.give],
            [c.id for c in self.want],
            self.initiator.id,
            self.target.id,
        )
        self._lock()
        if not ok:
            await interaction.response.edit_message(
                content="❌ Trade fell through — one of these cards changed hands since the offer.",
                view=self,
            )
            return
        await interaction.response.edit_message(
            content=f"🎉 Trade complete! {self.initiator.mention} ↔ {self.target.mention} 🤝",
            view=self,
        )

    @discord.ui.button(label="Decline", emoji="✖️", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._lock()
        await interaction.response.edit_message(content="❌ Trade declined.", view=self)

    async def on_timeout(self) -> None:
        self._lock()


class Trading(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="gift", description="Give one or more of your cards to another member")
    @app_commands.describe(member="Who receives the cards")
    async def gift(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if member.id == interaction.user.id:
            await interaction.response.send_message("You already own those.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Bots don't collect cards.", ephemeral=True)
            return

        mine = await database.get_user_cards(interaction.user.id)
        if not mine:
            await interaction.response.send_message("You have no cards to give yet.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        board = GiftBoard(interaction.user, member, mine)
        files = await board.build()
        await interaction.followup.send(view=board, files=files, ephemeral=True)

    @app_commands.command(name="trade", description="Offer a two-way card trade to another member")
    @app_commands.describe(member="Who you want to trade with")
    async def trade(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if member.id == interaction.user.id:
            await interaction.response.send_message("You can't trade with yourself.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Bots don't collect cards.", ephemeral=True)
            return

        mine = await database.get_user_cards(interaction.user.id)
        if not mine:
            await interaction.response.send_message("You have no cards to trade yet.", ephemeral=True)
            return
        theirs = await database.get_user_cards(member.id)

        await interaction.response.defer(ephemeral=True)
        board = TradeBuilderBoard(interaction.user, member, mine, theirs)
        files = await board.build()
        await interaction.followup.send(view=board, files=files, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trading(bot))
