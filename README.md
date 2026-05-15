# AI Travel Agent — Telegram + Playwright

An AI travel-planning agent that operates Google Maps in a real browser (via Playwright) instead of using paid Google Maps APIs. Accessible through a Telegram bot.

## What it does

Send the bot one message in Bahasa Indonesia:

> "Mau jalan-jalan di Surabaya besok Sabtu, suka kuliner malam & wisata alam"

The agent will:

1. Open Google Maps in a stealthed Chromium browser.
2. Search for relevant places (wisata alam, kuliner, ...).
3. Open each candidate to read opening hours and filter out closed places.
4. Pull an optimized multi-stop route + total distance/duration.
5. Calculate estimated fuel cost based on Indonesian BBM prices.
6. Reply with a structured itinerary + a Google Maps deep link for navigation.

Expected total response time: **30–60 seconds**.

## Stack

- **LLM (agent brain)**: GPT-4o via **GitHub Copilot / GitHub Models** (no OpenAI account needed)
- **Agent framework**: LangChain `create_tool_calling_agent`
- **Browser automation**: Playwright + `playwright-stealth`
- **Interface**: `python-telegram-bot` v21
- **Language**: Python 3.11+ (async)

## Project layout

```
travel-agent/
├── bot/telegram_bot.py          # entry point
├── agent/
│   ├── agent_core.py            # LangChain ReAct agent
│   ├── prompts.py               # system prompt
│   ├── tools_registry.py        # adapter: tools → LangChain StructuredTools
│   └── response_formatter.py    # split + sanitize for Telegram
├── tools/
│   ├── maps_search.py           # search_places_on_maps
│   ├── maps_details.py          # get_place_details
│   ├── maps_directions.py       # get_directions
│   ├── web_search.py            # web_search (via Google Search)
│   ├── fuel_calculator.py       # calculate_fuel_cost
│   └── schemas.py               # Pydantic models for tool I/O
├── browser/
│   └── playwright_manager.py    # shared stealthed Chromium
├── config/
│   ├── selectors.py             # Google Maps / Google Search selectors
│   └── settings.py              # env-loaded settings
├── requirements.txt
└── .env.example
```

## Setup

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Install Playwright's Chromium
playwright install chromium
# On Debian/Ubuntu:
playwright install-deps chromium  # may require sudo
# On RHEL / OpenCloudOS (no apt — install-deps fails):
# dnf install -y alsa-lib atk at-spi2-atk at-spi2-core cairo cups-libs dbus-libs \
#   libdrm mesa-libEGL mesa-libgbm glib2 gtk3 nspr nss pango libX11 libX11-xcb \
#   libxcb libXcomposite libXdamage libXext libXfixes libXrandr libxshmfence \
#   liberation-fonts libXcursor libXi libXrender libXtst libxkbcommon

# 3. Configure secrets
cp .env.example .env
# edit .env and fill TELEGRAM_BOT_TOKEN + OPENAI_API_KEY
```

### Getting tokens

- **Telegram bot token**: chat with [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
- **GitHub token**: go to [https://github.com/settings/tokens](https://github.com/settings/tokens) → **Generate new token (classic)** → tick `copilot` scope (or use a fine-grained token with Copilot access) → copy the token into `GITHUB_TOKEN`.

The GitHub Models endpoint (`https://models.inference.ai.azure.com`) accepts this token exactly like an OpenAI API key, so no OpenAI account is required.

## Run

```bash
cd travel-agent
python -m bot.telegram_bot
```

The first request boots Chromium (~1 second extra); subsequent requests reuse the browser.

## Configuration knobs

All in `.env`:


| Variable                                | Purpose                                  | Default                                 |
| --------------------------------------- | ---------------------------------------- | --------------------------------------- |
| `GITHUB_TOKEN`                          | GitHub PAT for Copilot/Models API auth   | *(required)*                            |
| `GITHUB_MODELS_BASE_URL`                | GitHub Models endpoint                   | `https://models.inference.ai.azure.com` |
| `GITHUB_MODEL_NAME`                     | Model to use (must support tool calling) | `gpt-4o`                                |
| `PLAYWRIGHT_HEADLESS`                   | run Chromium headless                    | `true`                                  |
| `PLAYWRIGHT_TIMEOUT_MS`                 | per-operation timeout                    | `20000`                                 |
| `GOOGLE_MAPS_HL`                        | Maps UI language                         | `id`                                    |
| `FUEL_PRICE_PERTALITE`                  | Rp / liter                               | `10000`                                 |
| `FUEL_PRICE_PERTAMAX`                   | Rp / liter                               | `12500`                                 |
| `FUEL_PRICE_SOLAR`                      | Rp / liter                               | `6800`                                  |
| `DEFAULT_FUEL_CONSUMPTION_KM_PER_LITER` | km/L                                     | `12`                                    |
| `LOG_LEVEL`                             | `DEBUG`/`INFO`/...                       | `INFO`                                  |


Update fuel prices manually when Pertamina announces changes.

## How the agent decides what to do

See `[agent/prompts.py](agent/prompts.py)`. The system prompt forces this loop:

1. Parse intent (kota, hari, kategori, titik awal).
2. `search_places_on_maps` (one or more calls per category).
3. `get_place_details` per candidate to verify opening hours.
4. Filter closed places, pick 3–5 best.
5. `get_directions` with the planned order.
6. `calculate_fuel_cost` from the total km.
7. (Optional) `web_search` for ticket prices or other off-Maps facts.
8. Final reply: numbered itinerary + Google Maps link.

