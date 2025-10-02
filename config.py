import os
import random
import dotenv

dotenv.load_dotenv()

# Rate limiting - sleep time between message processing (seconds)
COOLDOWN = 300  # Strongly recommended to keep more than 5-20s to avoid being IP banned by Telegram

# Default fallback values for jobs that don't specify these
_embed_color_env = os.getenv("EMBED_COLOR")
EMBED_COLOR = int(_embed_color_env, 16) if _embed_color_env else int(f"0x{random.randint(0, 0xFFFFFF):06x}", 16)
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Fallback webhook URL

# Authentication for API endpoints
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD") or "disgram_default_password_change_me"