"""A single /help command that explains the bot."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot import config, frames

SECTIONS: list[tuple[str, str]] = [
    (
        "Get cards",
        "**/submit** `name` `image` — turn a piece of AI art into a card; it "
        "joins the drop pool right away.\n"
        "**/drop** — post a set of cards with claim buttons. First click wins "
        f"each copy. One drop per member every {config.DROP_COOLDOWN_SECONDS}s.",
    ),
    (
        "Your collection",
        "**/inventory** `[member]` — view a collection. Your own comes with a "
        "picker: choose one or more copies and they post into the channel, "
        "framed.\n"
        "**/card** `id` — one card's art and how many copies exist.",
    ),
    (
        "Frames",
        "**/frames** — list the styles.\n"
        "**/frame** `user_card_id` `frame` — mat one of your copies. Frames "
        "are per copy, so your version can look different from everyone "
        "else's.",
    ),
    (
        "Trading",
        "**/gift** `user_card_id` `member` — hand a card to someone, no strings.\n"
        "**/trade** `member` — build a two-way offer, pick cards for each "
        "side, and they get Accept / Decline buttons.",
    ),
]


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How the card bot works and every command it has")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="AI Art Cards — how it works",
            description=(
                "Members submit AI art, it becomes a collectible card, and the "
                "server claims, frames, and trades copies. Every card credits "
                "whoever submitted it, everywhere it shows up."
            ),
        )
        for name, value in SECTIONS:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="Frames: " + ", ".join(f.label for f in frames.CHOICES))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
