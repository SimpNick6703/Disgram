import random

# Add channels' links under quotes in the following format. 
Channels = [
    "https://t.me/randomchannel",
    "https://t.me/anotherchannel",
    "https://t.me/yetanotherchannel"
]
COOLDOWN = 300 # Strongly recommended to keep more than 5-20s in the long run to avoid being IP banned by Telegram. 
EMBED_COLOR = int(f"0x{random.randint(0, 0xFFFFFF):06x}", 16) # Change if you wish to.
ERROR_PLACEHOLDER = f"Unable to parse this message. Try heading to the message link leading to preview page or Telegram." # Placeholder for unparseable messages 
WEBHOOK_URL = "https://discord.com/api/webhooks/{webhookID}/{webhookToken}" # Replace this with your Discord webhook, webhookID and webhookToken are 19 and 68 characters long respectively.