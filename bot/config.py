import os

from dotenv import load_dotenv

load_dotenv()


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value else None


DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = _int_or_none(os.getenv("GUILD_ID"))
DROP_SIZE = int(os.getenv("DROP_SIZE", "3"))
DROP_COOLDOWN_SECONDS = int(os.getenv("DROP_COOLDOWN_SECONDS", "3600"))
