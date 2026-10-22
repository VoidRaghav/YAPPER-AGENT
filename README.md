# Yapper

An AI that joins your group chat like a real member. It reads along, stays quiet most of the time, and jumps in when it has something worth saying: a roast, a callback to an old joke, a reminder, a meme.

Works on **WhatsApp, Telegram and Discord**. Free, open source, and runs on free AI models.

```
aman:   bhai 5 min mein pahunch raha hoon
priya:  tu ghar se nikla bhi hai?
aman:   haan haan bas nikal raha
yapper: aman ki dictionary mein "nikal raha" matlab abhi naha raha hai. ETA 45 min
```

## What it does

- **Knows when to shut up.** Every message goes through a check: reply, react or stay quiet.
- **Remembers the lore.** Inside jokes, running bits, who is always late.
- **Has a personality per group.** Pick a pack: friends, family, study, work or community. Or write your own.
- **Weekly awards.** A Sunday recap with awards like Ghost of the Week.
- **Voice notes, memes and stickers.** It understands voice notes and can reply with its own.
- **Speaks Hinglish, Hindi or English**, and knows what Indian internet is laughing at this week.
- **Safe by default.** It says it's an AI, never jokes about sensitive topics, and anyone can type `!forget me`.

## What you need

- **Docker**, or Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node 22.18+
- **Two free API keys:** [Sarvam](https://dashboard.sarvam.ai) and [Z.ai](https://z.ai). Other providers work too, see [Models](#models).
- **At least one platform:**
  - WhatsApp: a phone number
  - Telegram: a bot token from [@BotFather](https://t.me/BotFather)
  - Discord: a bot token from the [developer portal](https://discord.com/developers/applications)

## Setup

### 1. Get the code

```bash
git clone https://github.com/VoidRaghav/YAPPER-AGENT.git
cd YAPPER-AGENT
cp .env.example .env
```

Open `.env` and fill in:

```env
SARVAM_API_KEY=your-key
ZAI_API_KEY=your-key
INTERNAL_TOKEN=any-long-random-string
OWNER_NAME=YourName
```

### 2. Try it in your terminal (optional)

Chat with it locally before adding it to a real group:

```bash
cd brain
uv sync --extra all
uv run python -m scripts.doctor      # checks your keys and models
uv run python -m scripts.simulate -v # chat as different people
```

### 3. Start it

With Docker:

```bash
docker compose up -d --build
```

Without Docker, use two terminals:

```bash
cd brain && uv sync --extra all && uv run uvicorn app.main:app --port 8000
```

```bash
cd gateway && npm ci && npm start    # on Node older than 22.18, use: npm run start:compat
```

### 4. Connect a platform

**WhatsApp**
1. Watch the gateway logs (`docker compose logs -f gateway`, or the gateway terminal) and scan the QR code. On your phone, go to WhatsApp > Linked devices > Link a device.
2. Add that number to your group.

No spare number? Set `SHARED_NUMBER=true` to run it on your own number. Every bot message then starts with 🤖 so friends can tell it apart from you.

> WhatsApp support uses an unofficial library (Baileys). This breaks WhatsApp's terms, and the number could get banned. Keep it to a few groups.

**Telegram**
1. Put your bot token in `TELEGRAM_BOT_TOKEN`.
2. In BotFather, send `/setprivacy` and choose **Disable**, so the bot can read the group.
3. Add the bot to your group.

**Discord**
1. Put your bot token in `DISCORD_BOT_TOKEN`.
2. In the developer portal, open **Bot** and turn on **Message Content Intent**.
3. Invite the bot with the Send Messages, Read Message History and Add Reactions permissions.

Only using Telegram or Discord? Set `WHATSAPP_ENABLED=false`.

### 5. Allow your group

Yapper ignores every chat until you allow it. List the groups it can see:

```bash
curl -H "x-internal-token: YOUR_INTERNAL_TOKEN" http://127.0.0.1:3001/groups
```

Copy the id you want into `.env`. Add more with commas:

```env
ALLOWED_GROUP_JIDS=1203630000000000@g.us,tg:-1001234567890,dc:123456789012345678
```

Restart (`docker compose up -d`, or restart both terminals).

### 6. Dry run, then go live

`DRY_RUN=true` is on by default. Yapper reads and decides, but only logs what it would say. When you like what you see, set `DRY_RUN=false` and restart. It introduces itself in the group and tells everyone it's an AI.

## Persona packs

Each group gets a pack that sets the bot's personality.

| Pack | Vibe |
| --- | --- |
| `friends` | Hinglish roasts, hype and callbacks (default) |
| `family` | Warm, slightly dramatic cousin. No roasts. |
| `study` | Accountability buddy. Nudges, deadlines, quick explanations. |
| `work` | Short, dry humour, tracks decisions and owners |
| `community` | Friendly host that welcomes new people |

Switch packs from the group with `!vibe study`.

**Make your own:**
1. Copy `packs/custom.template.json` to `packs/my-pack.json`.
2. Edit the personality, language, sliders and examples.
3. Type `!vibe my-pack` in the group. No restart needed.

The [website](web/) has a pack builder that writes this file for you.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!off` / `!on` | owner | Turn it off or on in this group |
| `!quiet [minutes]` | owner | Stay silent for a while |
| `!chatty low/med/high` | owner | How often it joins in |
| `!vibe <pack>` | owner | Switch the personality pack |
| `!spice 0-5` | owner | How savage the roasts get |
| `!roast @name` | everyone | Roast on request |
| `!meme <text>` | everyone | Make a meme |
| `!sticker` | everyone | Reply to an image to make a sticker |
| `!stats` | everyone | This week's stats |
| `!help` | everyone | List all commands |
| `!whatdoyouknow me` | everyone | See what it remembers about you |
| `!forget me` | everyone | Delete everything it knows about you |
| `!offlimits <topic>` | everyone | A topic it must never joke about with you |

**Who is the owner:**
- With `SHARED_NUMBER=true`, the owner is you.
- Otherwise, put your WhatsApp number, Telegram user id or Discord user id in `OWNER_NUMBERS`.

## Models

Set `LLM_PROVIDER` in `.env`. Each option picks sensible models for every job.

| `LLM_PROVIDER` | Keys | Cost |
| --- | --- | --- |
| `free` | `SARVAM_API_KEY`, `ZAI_API_KEY` | Free (Sarvam gives free starter credit) |
| `openrouter` | `OPENROUTER_API_KEY` | A few dollars a month |
| `deepseek` | `DEEPSEEK_API_KEY` | Very cheap |
| `gemini` | `GEMINI_API_KEY` | Free tier available |
| `groq` | `GROQ_API_KEY` | Free tier available |
| `anthropic` | `ANTHROPIC_API_KEY` | Paid |
| `openai_compat` | `LLM_API_KEY`, `LLM_BASE_URL` | Any OpenAI-compatible API, including Ollama |

To use a different model for one job, set it in `.env`, for example `WRITER_MODEL=openrouter:z-ai/glm-5.3`.

## Settings you might change

| Setting | Default | What it does |
| --- | --- | --- |
| `BOT_NAME` | `Yapper` | Its name, and what people type to call it |
| `DEFAULT_PACK` | `friends` | Pack for new groups |
| `LANGUAGE_MIX` | `hinglish` | `hinglish`, `english` or `hindi` |
| `TIMEZONE` | `Asia/Kolkata` | For daily limits and weekly awards |
| `HOURLY_CAP_PER_GROUP` | `12` | Max replies per group per hour |
| `AWARDS_DAY` / `AWARDS_HOUR` | `sun` / `20` | When the weekly awards go out |
| `TTS_VOICE` | `hi-IN-MadhurNeural` | Voice for voice notes |

Everything else is listed in `.env.example`.

## Privacy

- **Your data stays on your machine.** It runs on your own computer or server, and chats are stored in a local database (`data/`).
- **Only allowed groups are touched.** Messages from other chats are never stored.
- **The AI provider sees messages.** Messages are sent to the AI provider you pick to generate replies, so choose one you trust.
- **Delete on request.** `!forget me` deletes a person's data, and `!off` stops it from reading the group.

## Project layout

```
brain/     Python: decides when and what to reply (FastAPI + LangGraph)
gateway/   Node: connects to WhatsApp, Telegram and Discord
packs/     personality packs (JSON)
web/       the website (Astro)
```

## Contributing

Pull requests are welcome. Run the tests before opening one:

```bash
cd brain && uv run pytest
cd gateway && npm test
```

## License

[MIT](LICENSE)
