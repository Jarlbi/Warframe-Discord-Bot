# Warframe Clan Bot

A Discord bot for Warframe clans. Handles quest coordination, Q&A, 
Standing/rank tracking, and automatic Warframe news updates.

## Features
- `/quest` — post a quest for clan members to accept and complete
- `/ask` — post a question, let members answer, mark best answer
- `/mystatus` — check your Standing and current rank
- `/leaderboard` — top 10 clan members by Standing
- Warframe news auto-posts every 15 minutes

## Rank Tiers
| Standing | Rank |
|---|---|
| 500 | Initiate |
| 1500 | Defender |
| 3500 | Warden |
| 5000 | Executor |
| 7500 | Architect |
| 10000 | (max) |

## Setup
1. Clone this repo
2. Install dependencies: `pip install discord.py aiosqlite aiohttp httpx google-generativeai`
3. In `bot.py`, replace:
   - `TOKEN = "token"` → your bot token
   - `id=Discord Server ID` → your server's numeric ID
4. In your Discord server, create roles with **exact names** matching the rank tiers above
5. Set channels with `/setquestchannel`, `/setqachannel`, `/setnewschannel`
6. Run: `python bot.py`

## Requirements
- Python 3.10+
- A Discord bot token from [Discord Developer Portal](https://discord.com/developers)