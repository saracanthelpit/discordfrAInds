# AI Art Cards Bot

A Discord bot that turns members' AI-generated art into collectible trading
cards: submit art and it goes straight into the drop pool, then anyone can
`/drop` a random set of cards for the server to claim.

## How it works

1. `/submit name:<str> image:<attachment>` — post your art. It's
   announced with an embed and immediately joins the drop pool.
2. `/drop` — posts a celebratory panel (random accent colour, hype
   copy) with `DROP_SIZE` cards, each shown with its art and its own
   Claim button. First click wins that copy; claims are numbered in the
   order they're claimed (print #1 gets a shout-out).
3. `/inventory [member]` — browse a collection card by card, paginated,
   each card shown with its art. On your own you get a **Select** button
   per card; selected cards (across pages) can be posted with **Show in
   channel** or restyled with **Apply a frame** — no card IDs to copy
   around. `/frames` previews the styles (bronze, silver, gold, emerald,
   obsidian, rose). Frames are per-copy: two owners of the same card can
   frame it differently. `/card <id>` — see one card's art and how many
   copies exist.
4. `/gift <member>` — pick one or more of your cards from a menu and hand
   them to someone else.
5. `/trade <member>` — build a two-way offer: pick cards from each side,
   send it, and the other member gets Accept / Decline buttons. The swap
   is atomic and re-checks ownership at accept time.
6. `/help` — an ephemeral rundown of every command.

Every place a card is shown — drops, claims, `/card`, `/inventory`,
frame previews, trade offers — credits the original submitter.

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
  database.py       aiosqlite access layer (schema in SCHEMA, later
                    columns in MIGRATIONS, applied on startup)
  frames.py         procedurally-drawn cosmetic frames (Pillow)
  cogs/
    submissions.py  /submit
    cards.py        /drop, /inventory (paged, per-card select), /card, /frames
    trading.py      /gift (one-way), /trade (two-way offer + accept/decline)
    help.py         /help
data/
  cards.db          created automatically on first run (gitignored)
```

Card art is stored as the Discord CDN URL from the original attachment —
no separate file storage needed, since Discord already hosts it. Framed
images are composited on the fly from that URL and attached to the reply,
so there's still nothing extra to host.

## Ideas to build next

This is set up as a working core loop on purpose, so there's plenty left to
make it yours:

- **Rarity tiers** — e.g. a `rarity` column on `cards`, weighted random
  selection in `get_random_approved_cards`, different embed colors per tier.
- **Currency & a shop** — coins for claiming/burning cards, spend them on
  extra drops or to unlock frames (frames are currently free to apply).
- **Fancier frames** — `bot/frames.py` draws each frame from a `Frame`
  spec; add gradient fills, ornate corners, or drop in real PNG overlays
  without touching the rest of the pipeline.
- **Persistent views** — `DropBoard`, `InventoryBoard`, and the trade
  views go inert after a restart (and on timeout). Give their controls
  `custom_id`s and register with `bot.add_view` to survive restarts.
- **Series/packs** — group submissions into themed sets (e.g. one per art
  challenge or month) and let `/drop` pull from a specific series.
- **Leaderboard** — `/leaderboard` for most cards owned, rarest card held,
  etc.
- **Print-number bragging rights** — highlight low print numbers (#1, #2) as
  extra valuable in `/card`.
- **Pagination** — `/inventory` currently caps at 25 cards; add real
  pagination with buttons for bigger collections.
- **Moderation** — submissions currently skip review and go straight into
  the pool. If spam/abuse becomes a problem, add a `/remove <card_id>`
  admin command (set `status = 'removed'`, already excluded by the
  `WHERE status = 'approved'` queries) or bring back a review queue.
