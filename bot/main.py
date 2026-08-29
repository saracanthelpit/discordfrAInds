import asyncio
import logging

import discord
from discord.ext import commands

from bot import config
from bot.database import init_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-art-cards-bot")

INITIAL_COGS = (
    "bot.cogs.submissions",
    "bot.cogs.cards",
    "bot.cogs.trading",
    "bot.cogs.help",
)


class CardBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await init_db()
        for cog in INITIAL_COGS:
            await self.load_extension(cog)

        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced slash commands to guild %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced slash commands globally (can take up to an hour)")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id)


def main() -> None:
    bot = CardBot()
    asyncio.run(bot.start(config.DISCORD_TOKEN))


if __name__ == "__main__":
    main()
