import os
import asyncio
import threading
import logging
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import dotenv
from config import MAX_FILESIZE_BYTES

dotenv.load_dotenv()
TELETHON_CONFIGURED = bool(os.getenv("TG_API_ID") and os.getenv("TG_API_HASH") and os.getenv("TG_SESSION_STRING"))
logger = logging.getLogger("Telethon")

class TelethonManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TelethonManager, cls).__new__(cls)
                cls._instance._init_once()
            return cls._instance
            
    def _init_once(self):
        self._client = None
        self._client_lock = asyncio.Lock()
        self._telethon_loop = asyncio.new_event_loop()
        self._loop_thread = None
        self._ensure_loop_running()
        
    def _start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _ensure_loop_running(self):
        if self._loop_thread is None or not self._loop_thread.is_alive():
            self._loop_thread = threading.Thread(target=self._start_background_loop, args=(self._telethon_loop,), daemon=True)
            self._loop_thread.start()

    async def _get_client(self) -> TelegramClient:
        if self._client is not None and self._client.is_connected():
            return self._client
            
        async with self._client_lock:
            if self._client is not None and self._client.is_connected():
                return self._client
                
            api_id = os.getenv("TG_API_ID")
            api_hash = os.getenv("TG_API_HASH")
            session_string = os.getenv("TG_SESSION_STRING")
            
            if not api_id or not api_hash or not session_string:
                raise ValueError("Telethon credentials not configured in .env")
                
            self._client = TelegramClient(StringSession(session_string), int(api_id), api_hash, loop=self._telethon_loop)
            await self._client.connect()
            
            if not await self._client.is_user_authorized():
                raise Exception("Telethon session is invalid or expired.")
                
            logger.info(f"Telethon connected successfully to DC {getattr(self._client.session, 'dc_id', 'Unknown')} at {self._client.session.server_address}")
            return self._client

    async def _async_get_telethon_media(self, channel: str, message_ids: list[int]):
        client = await self._get_client()
        try:
            messages = await client.get_messages(channel, ids=message_ids)
            if not messages: return []
            if not isinstance(messages, list): messages = [messages]
            
            downloaded_items = []
            for msg in messages:
                if not msg or not msg.media: continue
                is_spoiler = getattr(msg.media, 'spoiler', False)
                file_size_estimated = 0
                item_type = 'document'
                if hasattr(msg.media, 'photo'):
                    item_type = 'image'
                    if hasattr(msg.media.photo, 'sizes'):
                        file_size_estimated = max((getattr(s, 'size', 0) for s in msg.media.photo.sizes), default=0)
                elif hasattr(msg.media, 'document'):
                    file_size_estimated = getattr(msg.media.document, 'size', 0)
                    mime = getattr(msg.media.document, 'mime_type', '')
                    if mime.startswith('video/'): item_type = 'video'
                    elif mime.startswith('image/'): item_type = 'image'
                
                is_too_large = file_size_estimated > MAX_FILESIZE_BYTES
                import uuid
                unique_id = uuid.uuid4().hex[:8]
                file_bytes = None
                file_size = file_size_estimated
                file_path = None
                
                if not is_too_large:
                    import tempfile
                    temp_dir = tempfile.gettempdir()
                    target_path = os.path.join(temp_dir, f"telethon_dl_{unique_id}")
                    file_path = await client.download_media(msg.media, file=target_path)
                    if file_path:
                        file_size = os.path.getsize(file_path)
                        with open(file_path, 'rb') as f: file_bytes = f.read()
                        try: os.remove(file_path)
                        except OSError: pass
                
                filename = f"media_{unique_id}.bin"
                if file_path: filename = os.path.basename(file_path)
                downloaded_items.append({
                    'type': 'video_too_large' if is_too_large else item_type,
                    'data': file_bytes,
                    'filename': filename,
                    'is_spoiler': is_spoiler,
                    'is_too_large': is_too_large,
                    'size': file_size
                })
            return downloaded_items
        except Exception as e:
            logger.error(f"Error fetching media via Telethon for {channel}/{message_ids}: {e}")
            return []

    async def _async_check_health(self) -> bool:
        try:
            client = await self._get_client()
            return await client.is_user_authorized()
        except Exception:
            return False

    def _parse_text_node(self, text_obj):
        if not text_obj: return ""
        if hasattr(text_obj, 'text'):
            text_str = text_obj.text
            t_type = type(text_obj).__name__
            if t_type == 'TextBold': return f"**{text_str}**"
            elif t_type == 'TextItalic': return f"*{text_str}*"
            elif t_type == 'TextStrike': return f"~~{text_str}~~"
            elif t_type == 'TextFixed': return f"`{text_str}`"
            return text_str
        elif type(text_obj).__name__ == 'TextConcat':
            return "".join([self._parse_text_node(t) for t in getattr(text_obj, 'texts', [])])
        return ""

    def _parse_rich_message(self, rich_message):
        if not rich_message or not hasattr(rich_message, 'blocks'): return ""
        parts = []
        for block in rich_message.blocks:
            b_type = type(block).__name__
            if b_type in ('PageBlockParagraph', 'PageBlockHeader', 'PageBlockSubheader'):
                parts.append(self._parse_text_node(getattr(block, 'text', None)))
            elif b_type == 'PageBlockBlockquote':
                text = self._parse_text_node(getattr(block, 'text', None))
                parts.append("\n".join(["> " + line for line in text.split('\n')]))
            elif b_type == 'PageBlockPreformatted':
                text = self._parse_text_node(getattr(block, 'text', None))
                parts.append(f"```\n{text}\n```")
            elif hasattr(block, 'text'):
                parts.append(self._parse_text_node(getattr(block, 'text', None)))
        return "\n\n".join(parts)

    async def _async_get_telethon_text(self, channel: str, message_id: int) -> str | None:
        try:
            client = await self._get_client()
            message = await client.get_messages(channel, ids=message_id)
            if not message: return None
            if getattr(message, 'rich_message', None):
                return self._parse_rich_message(message.rich_message)
            return message.text
        except Exception as e:
            logger.error(f"Error fetching text via Telethon for {channel}/{message_id}: {e}")
            return None

    def get_media(self, channel: str, message_ids: list[int]):
        future = asyncio.run_coroutine_threadsafe(self._async_get_telethon_media(channel, message_ids), self._telethon_loop)
        return future.result()

    def check_health(self) -> bool:
        if not os.getenv("TG_SESSION_STRING"): return False
        future = asyncio.run_coroutine_threadsafe(self._async_check_health(), self._telethon_loop)
        return future.result()

    def get_text(self, channel: str, message_id: int) -> str | None:
        future = asyncio.run_coroutine_threadsafe(self._async_get_telethon_text(channel, message_id), self._telethon_loop)
        return future.result()

# Global singleton instance for backward compatibility
_manager = TelethonManager()

def get_telethon_media(channel: str, message_ids: list[int]):
    return _manager.get_media(channel, message_ids)

def check_telethon_health() -> bool:
    return _manager.check_health()

def get_telethon_text(channel: str, message_id: int) -> str | None:
    return _manager.get_text(channel, message_id)
