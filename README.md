# AI Art Cards Bot

A Discord bot that turns members' AI-generated art into collectible trading
cards: submit art, a mod approves it into the pool, then anyone can `/drop`
a random set of cards for the server to claim.

## How it works

1. `/submit name:<str> image:<attachment>` — post your art for review. It
   goes to the review channel as a pending submission.
2. A mod clicks **Approve** or **Reject** on the submission post. Approved
   cards join the drop pool.
3. `/drop` — posts a set of cards (size set by `DROP_SIZE`) with a claim
   button on each. First click wins that copy; claims are numbered in the
   order they're claimed (print #1, #2, ...).
4. `/inventory [member]` — see a collection. `/card <id>` — see one card's
   art and how many copies exist.
5. `/gift <user_card_id> <member>` — hand one of your cards to someone else.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:
- `DISCORD_TOKEN` — from the [Discord Developer Portal](https://discord.com/developers/applications).
  Create an application, add a Bot, enable it, copy the token.
- `GUILD_ID` — your server's ID (enable Developer Mode in Discord, right-click
  the server icon, Copy Server ID). Set this during development so slash
  commands sync instantly instead of waiting up to an hour for a global sync.
- `REVIEW_CHANNEL_ID` — the channel where pending submissions get posted.
- `MOD_ROLE_ID` — the role allowed to approve/reject submissions. If unset,
  anyone with "Manage Server" can approve.

Invite the bot to your server with the `bot` and `applications.commands`
scopes, and grant it `Send Messages`, `Embed Links`, and `Read Message
History` in the channels it'll use.

Run it:

```bash
python -m bot.main
```

## Project layout

```
bot/
  main.py           entry point, loads cogs, syncs slash commands
  config.py         reads .env
  database.py       aiosqlite access layer (schema lives in SCHEMA)
  cogs/
    submissions.py  /submit, /pending, mod approve/reject buttons
    cards.py        /drop, /inventory, /card
    trading.py      /gift (one-way transfer)
data/
  cards.db          created automatically on first run (gitignored)
```

Card art is stored as the Discord CDN URL from the original attachment —
no separate file storage needed, since Discord already hosts it.

## Ideas to build next

This is set up as a working core loop on purpose, so there's plenty left to
make it yours:

- **Real two-way `/trade`** — right now `/gift` only does one-way transfers.
  There's a TODO sketch in `bot/cogs/trading.py` for a mutual offer/accept
  flow.
- **Rarity tiers** — e.g. a `rarity` column on `cards`, weighted random
  selection in `get_random_approved_cards`, different embed colors per tier.
- **Currency & a shop** — coins for claiming/burning cards, spend them on
  extra drops or cosmetic frames.
- **Series/packs** — group submissions into themed sets (e.g. one per art
  challenge or month) and let `/drop` pull from a specific series.
- **Leaderboard** — `/leaderboard` for most cards owned, rarest card held,
  etc.
- **Print-number bragging rights** — highlight low print numbers (#1, #2) as
  extra valuable in `/card`.
- **Pagination** — `/inventory` currently caps at 25 cards; add real
  pagination with buttons for bigger collections.
