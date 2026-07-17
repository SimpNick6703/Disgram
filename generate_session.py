import os
import asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import dotenv

dotenv.load_dotenv()

api_id = os.getenv("TG_API_ID")
api_hash = os.getenv("TG_API_HASH")

if not api_id or not api_hash:
    print("Please set TG_API_ID and TG_API_HASH in your .env file.")
    exit(1)

async def main():
    print("Starting session generator...")
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    
    print("\n--- YOUR SESSION STRING ---")
    print("Please copy the string below and paste it into your .env file as TG_SESSION_STRING:\n")
    print(client.session.save())
    print("\n---------------------------")
    print("Store this string securely! Anyone with this string can access your Telegram account.")

if __name__ == "__main__":
    asyncio.run(main())
