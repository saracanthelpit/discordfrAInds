"""Card transfers: one-way /gift and a two-way /trade with offer + accept/decline."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot import database, frames
from bot.database import OwnedCard

# Discord caps a select at 25 options and a message at 10 attachments; keep
# trades within the smaller limit so an accepted offer always fits one message.
MAX_PER_SIDE = 10


def _attribution_line(card: OwnedCard) -> str:
    return f"**{card.card_name}** (print #{card.print_number}) — art by <@{card.submitted_by}>"


def _offer_embed(
    initiator: discord.abc.User,
    target: discord.abc.User,
    give: list[OwnedCard],
    want: list[OwnedCard],
) -> discord.Embed:
    embed = discord.Embed(title="Trade offer", description=f"{initiator.mention} → {target.mention}")
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


def _card_options(cards: list[OwnedCard]) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for card in cards[:25]:
        desc = f"print #{card.print_number}"
        if card.frame != frames.DEFAULT:
            desc += f" · {frames.label_for(card.frame)} frame"
        options.append(
            discord.SelectOption(label=f"#{card.card_id} {card.card_name}"[:100], value=str(card.id), description=desc)
        )
    return options


class _CardSelect(discord.ui.Select):
    def __init__(self, cards: list[OwnedCard], placeholder: str, *, required: bool) -> None:
        options = _card_options(cards)
        super().__init__(
            placeholder=placeholder,
            min_values=1 if required else 0,
            max_values=min(len(options), MAX_PER_SIDE),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Selection state is read off the component when "Send offer" is pressed.
        await interaction.response.defer()


class OfferBuilder(discord.ui.View):
    """Ephemeral, initiator-only: choose cards for each side, then send the offer."""

    def __init__(
        self,
        initiator: discord.abc.User,
        target: discord.Member,
        mine: list[OwnedCard],
        theirs: list[OwnedCard],
    ) -> None:
        super().__init__(timeout=180)
        self.initiator = initiator
        self.target = target
        self._give_by_id = {str(c.id): c for c in mine}
        self._want_by_id = {str(c.id): c for c in theirs}

        self.give_select = _CardSelect(mine, "Cards you'll give…", required=True)
        self.add_item(self.give_select)

        self.want_select: _CardSelect | None = None
        if theirs:
            self.want_select = _CardSelect(
                theirs, f"Cards you want from {target.display_name}…", required=False
            )
            self.add_item(self.want_select)

    @discord.ui.button(label="Send offer", style=discord.ButtonStyle.success, row=4)
    async def send_offer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        give_ids = list(self.give_select.values)
        want_ids = list(self.want_select.values) if self.want_select else []
        if not give_ids:
            await interaction.response.send_message("Pick at least one card to give.", ephemeral=True)
            return

        give = [self._give_by_id[v] for v in give_ids]
        want = [self._want_by_id[v] for v in want_ids]

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Offer sent. ✅", view=self)
        await interaction.followup.send(
            content=self.target.mention,
            embed=_offer_embed(self.initiator, self.target, give, want),
            view=OfferResponse(self.initiator, self.target, give, want),
            allowed_mentions=discord.AllowedMentions(users=[self.target]),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=4)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Trade cancelled.", view=self)


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

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
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
            content=f"✅ Trade complete: {self.initiator.mention} ↔ {self.target.mention}.",
            view=self,
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._lock()
        await interaction.response.edit_message(content="❌ Trade declined.", view=self)

    async def on_timeout(self) -> None:
        self._lock()


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

        hint = "Pick what you'll give"
        hint += " and what you want back" if theirs else f" ({member.display_name} has no cards yet)"
        await interaction.response.send_message(
            content=f"{hint}, then hit **Send offer**.",
            view=OfferBuilder(interaction.user, member, mine, theirs),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trading(bot))
