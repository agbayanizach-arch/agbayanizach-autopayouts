# Discord Bot — GitHub + Render

## Files
- `main.py` — bot + Render HTTP server
- `requirements.txt` — Python dependencies
- `.gitignore` — keeps secrets/cache out of GitHub
- `.env.example` — example environment variable
- `render.yaml` — optional Render configuration

## Commands
### Slash
- `/ticket-setup`
- `/restock <file>`
- `/payout <channel>`

### Prefix
- `-i [@member]`
- `-resetinvites [@member]`

## GitHub
Upload these files to the repository root. Do NOT upload your real bot token.

## Render
Create a Web Service from the GitHub repository.
Build Command:
`pip install -r requirements.txt`

Start Command:
`python main.py`

Add Environment Variable:
`DISCORD_TOKEN` = your Discord bot token.

Optional (recommended while testing):
`GUILD_ID` = your Discord server ID. This makes the commands sync to that server immediately; global sync is still performed.

## Discord Developer Portal
Enable:
- Server Members Intent
- Message Content Intent

The bot also needs the permissions required by the commands, especially:
- View Channel
- Send Messages
- Embed Links
- Manage Channels
- Read Message History

## UptimeRobot
After Render gives you the public service URL, create an HTTP(s) monitor for:
`https://YOUR-RENDER-SERVICE.onrender.com/health`

Use a normal periodic monitor. Do not put your bot token in the monitor URL.
