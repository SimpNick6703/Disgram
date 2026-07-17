import os
import random
import dotenv

dotenv.load_dotenv()

Channels = (os.getenv("TELEGRAM_CHANNELS") or "").split(",")
COOLDOWN = 300 # Strongly recommended to keep more than 5-20s in the long run to avoid being IP banned by Telegram. 
_embed_color_env = os.getenv("EMBED_COLOR")
EMBED_COLOR = int(_embed_color_env, 16) if _embed_color_env else int(f"0x{random.randint(0, 0xFFFFFF):06x}", 16) # int(f"0x89a7d9", 16)
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL") # Replace this with your Discord webhook, webhookID and webhookToken are 19 and 68 characters long respectively.
THREAD_ID = os.getenv("DISCORD_THREAD_ID") if os.getenv("DISCORD_THREAD_ID") else None
API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN")

# Git Configuration for log persistence
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
LOG_COMMIT_INTERVAL = int(os.getenv("LOG_COMMIT_INTERVAL", "2700"))  # Default 45 minutes

# Telethon / MTProto Configurations
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING")
