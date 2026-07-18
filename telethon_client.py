import os
import asyncio
import threading
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import dotenv
from config import MAX_FILESIZE_BYTES

dotenv.load_dotenv()

TELETHON_CONFIGURED = bool(os.getenv("TG_API_ID") and os.getenv("TG_API_HASH") and os.getenv("TG_SESSION_STRING"))

_client = None
_client_lock = None
_telethon_loop = asyncio.new_event_loop()
_loop_thread = None

def _start_background_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

def _ensure_loop_running():
    global _loop_thread
    if _loop_thread is None or not _loop_thread.is_alive():
        _loop_thread = threading.Thread(target=_start_background_loop, args=(_telethon_loop,), daemon=True)
        _loop_thread.start()


async def _get_client() -> TelegramClient:
    """Get or initialize the Telethon client singleton."""
    global _client, _client_lock
    
    if _client_lock is None:
        _client_lock = asyncio.Lock()
        
    if _client is not None and _client.is_connected():
        return _client
        
    async with _client_lock:
        if _client is not None and _client.is_connected():
            return _client
            
        api_id = os.getenv("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        session_string = os.getenv("TG_SESSION_STRING")
        
        if not api_id or not api_hash or not session_string:
            raise ValueError("Telethon credentials not configured in .env")
            
        # Initialize client (bound to the background thread's loop)
        _client = TelegramClient(StringSession(session_string), int(api_id), api_hash, loop=_telethon_loop)
        await _client.connect()
        
        if not await _client.is_user_authorized():
            raise Exception("Telethon session is invalid or expired.")
            
        msg = f"Telethon connected successfully to DC {getattr(_client.session, 'dc_id', 'Unknown')} at {_client.session.server_address}"
        try:
            from webhook import log_message
            log_message(msg, log_type="telethon")
        except Exception:
            print(msg)
            
        return _client

async def _async_get_telethon_media(channel: str, message_ids: list[int]):
    """Async implementation to fetch media."""
    client = await _get_client()
    
    try:
        # Fetch the messages
        messages = await client.get_messages(channel, ids=message_ids)
        
        if not messages:
            return []
            
        # Ensure it's a list (get_messages returns a single object if given a single ID)
        if not isinstance(messages, list):
            messages = [messages]
            
        downloaded_items = []
        
        for msg in messages:
            if not msg or not msg.media:
                continue
                
            is_spoiler = getattr(msg.media, 'spoiler', False)
            
            # Check file size before downloading
            file_size_estimated = 0
            item_type = 'document'
            if hasattr(msg.media, 'photo'):
                item_type = 'image'
                if hasattr(msg.media.photo, 'sizes'):
                    file_size_estimated = max((getattr(s, 'size', 0) for s in msg.media.photo.sizes), default=0)
            elif hasattr(msg.media, 'document'):
                file_size_estimated = getattr(msg.media.document, 'size', 0)
                mime = getattr(msg.media.document, 'mime_type', '')
                if mime.startswith('video/'):
                    item_type = 'video'
                elif mime.startswith('image/'):
                    item_type = 'image'
            
            is_too_large = file_size_estimated > MAX_FILESIZE_BYTES
            
            import uuid
            unique_id = uuid.uuid4().hex[:8]
            
            file_bytes = None
            file_size = file_size_estimated
            file_path = None
            
            if not is_too_large:
                # Use telethon to download the file into memory
                import tempfile
                temp_dir = tempfile.gettempdir()
                target_path = os.path.join(temp_dir, f"telethon_dl_{unique_id}")
                
                # This might take some time for large videos
                file_path = await client.download_media(msg.media, file=target_path)
                
                if file_path:
                    file_size = os.path.getsize(file_path)
                    with open(file_path, 'rb') as f:
                        file_bytes = f.read()
                    
                    # Clean up the temp file
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
            
            # Determine filename
            filename = f"media_{unique_id}.bin"
            if file_path:
                filename = os.path.basename(file_path)
                
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
        msg = f"Error fetching media via Telethon for {channel}/{message_ids}: {e}"
        try:
            from webhook import log_message
            log_message(msg, log_type="error")
        except Exception:
            print(msg)
        return []

def get_telethon_media(channel: str, message_ids: list[int]):
    """
    Synchronous wrapper to fetch media via Telethon.
    Used by webhook.py which is currently synchronous.
    """
    _ensure_loop_running()
    future = asyncio.run_coroutine_threadsafe(_async_get_telethon_media(channel, message_ids), _telethon_loop)
    return future.result()

async def _async_check_health() -> bool:
    try:
        client = await _get_client()
        return await client.is_user_authorized()
    except Exception:
        return False

def check_telethon_health() -> bool:
    """
    Synchronous wrapper to check if Telethon session is alive.
    Used by main.py health endpoint.
    """
    if not os.getenv("TG_SESSION_STRING"):
        return False
    _ensure_loop_running()
    future = asyncio.run_coroutine_threadsafe(_async_check_health(), _telethon_loop)
    return future.result()
