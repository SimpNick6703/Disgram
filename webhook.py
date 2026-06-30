import time
import datetime
import requests
import sys
import re
import os
import io
from dateutil import parser
from bs4 import BeautifulSoup
import discord
from discord import SyncWebhook, Embed, File
from discord.ui import LayoutView, Container, TextDisplay, MediaGallery
import concurrent.futures
from config import WEBHOOK_URL, THREAD_ID, COOLDOWN, EMBED_COLOR, ERROR_PLACEHOLDER

TELEGRAM_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Disgram/2.0)"}
MAX_MEDIA_WORKERS = 8

def log_message(message: str, log_type: str = "info") -> None:
    """Log messages to console and to Disgram.log for specific message types."""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} {message}"
    print(log_entry)
    if log_type in ["error", "new_message", "status"]:
        with open("Disgram.log", "a", encoding="utf-8") as log_file:
            log_file.write(log_entry + "\n")

def is_message_logged(channel: str, number: int) -> bool:
    """Check if the given message number has already been logged for the channel."""
    try:
        with open("Disgram.log", "r", encoding="utf-8") as log_file:
            for line in log_file:
                match = re.search(rf"https://t.me/{channel}/(\d+)", line)
                if match and int(match.group(1)) >= number:
                    return True
    except FileNotFoundError:
        pass
    return False

def scrapeTelegramMessageBox(channel: str) -> list:
    """Scrape the latest messages from the Telegram channel preview page."""
    max_retries = 5
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            log_message(f"Scraping messages from Telegram channel: {channel} (Attempt {attempt + 1})")
            tg_html = requests.get(f'https://t.me/s/{channel}', headers=TELEGRAM_HEADERS, timeout=10)
            tg_html.raise_for_status()
            tg_soup = BeautifulSoup(tg_html.text, 'html.parser')
            return tg_soup.find_all('div', {'class': 'tgme_widget_message_wrap js-widget_message_wrap'})
        except requests.exceptions.RequestException as e:
            log_message(f"Error scraping Telegram: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                log_message("Max retries reached. Skipping this iteration.", log_type="error")
                return []
    return []

def getAuthorIcon(tg_box) -> str | None:
    """Extract the author's profile icon URL."""
    icon_element = tg_box.find('i', {'class': 'tgme_widget_message_user_photo'})
    if icon_element:
        img_tag = icon_element.find('img')
        if img_tag and 'src' in img_tag.attrs:
            return img_tag['src']
    return None

def getAuthorName(tg_box) -> str | None:
    """Extract the author's name."""
    author_name = tg_box.find('a', {'class': 'tgme_widget_message_owner_name'})
    return author_name.text.strip() if author_name else None

def getLink(tg_box) -> str | None:
    """Extract the Telegram message link."""
    msg_link = tg_box.find_all('a', {'class': 'tgme_widget_message_date'}, href=True)
    return msg_link[0]['href'] if msg_link else None

def _render_children(element, in_quote=False) -> str:
    """Helper to render elements inside blockquotes and other tags recursively."""
    parts = []
    for child in element.children:
        parts.append(_render_node(child, in_quote))
    return ''.join(parts)

def _render_node(node, in_quote=False) -> str:
    """Helper to format individual HTML elements into Markdown."""
    if getattr(node, 'name', None) is None:
        return str(node)

    name = node.name
    if name == 'a':
        text = _render_children(node, in_quote)
        href = node.get('href', '')
        if text == href:
            return href
        return f"[{text}]({href})" if href else text
    if name == 'pre':
        content = node.get_text()
        return f"```{content}```"
    if name in ('b', 'strong'):
        return f"**{_render_children(node, in_quote)}**"
    if name == 'tg-spoiler':
        return f"||{_render_children(node, in_quote)}||"
    if name in ('i', 'em'):
        return f"*{_render_children(node, in_quote)}*"
    if name == 'u':
        return f"__{_render_children(node, in_quote)}__"
    if name in ('s', 'strike', 'del'):
        return f"~~{_render_children(node, in_quote)}~~"
    if name == 'br':
        return '\n'
    if name == 'blockquote':
        if in_quote:
            return _render_children(node, in_quote=True)
        inner = _render_children(node, in_quote=True)
        inner = inner.replace('\r\n', '\n').replace('\r', '\n')
        lines = inner.split('\n')
        quoted = "\n".join(["> " + l if l != "" else "> " for l in lines]) + "\n"
        return quoted

    return _render_children(node, in_quote)

def getText(tg_box) -> str | None:
    """Extract and format the message text."""
    msg_text = tg_box.find_all('div', {'class': 'tgme_widget_message_text js-message_text'})
    if not msg_text:
        return None
    return _render_children(msg_text[0])

def getTextFromIndividualMessage(msg_link: str) -> str | None:
    """Extract text from an individual message page (fallback for media groups)."""
    if not msg_link:
        return None
        
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            response = requests.get(msg_link, headers=TELEGRAM_HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            text_div = soup.find('div', class_='tgme_widget_message_text')
            if text_div:
                text_content = text_div.get_text(strip=True)
                if text_content:
                    return text_content
            
            og_desc = soup.find('meta', property='og:description')
            if og_desc:
                content_attr = og_desc.get('content')
                if content_attr:
                    content = str(content_attr).strip()
                    if content and _is_likely_message_content(content):
                        return content
            return None
        except Exception as e:
            log_message(f"Error fetching text from individual message {msg_link}: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return None
    return None

def _is_likely_message_content(content: str) -> bool:
    """Check to filter out obvious channel descriptions."""
    if not content:
        return False
    
    content_lower = content.lower().strip()
    channel_desc_patterns = [
        r'^the official .+ on telegram',
        r'official .+ channel',
        r'.+ official channel', 
        r'welcome to .+',
        r'much recursion\. very telegram\. wow\.',
        r'^.+\s+–\s+.+$',
    ]
    
    for pattern in channel_desc_patterns:
        if re.match(pattern, content_lower):
            return False
    
    if len(content.split()) <= 1:
        return False
    
    return True

def getImage(tg_box) -> str | None:
    """Get single message image URL."""
    msg_image = tg_box.find('a', {'class': 'tgme_widget_message_photo_wrap'}, href=True)
    if msg_image:
        startIndex = msg_image['style'].find("background-image:url('") + 22
        endIndex = msg_image['style'].find(".jpg')") + 4
        if startIndex > 21 and endIndex > 3:
            return msg_image['style'][startIndex:endIndex]
    return None

def getAllImages(tg_box) -> list[str]:
    """Get all image URLs for media groups."""
    images = []
    msg_images = tg_box.find_all('a', {'class': 'tgme_widget_message_photo_wrap'})
    for msg_image in msg_images:
        if msg_image.get('style'):
            style = msg_image['style']
            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                start_pos = style.find("background-image:url('")
                end_pos = style.find(f"{ext}')")
                if start_pos > -1 and end_pos > -1:
                    start_index = start_pos + 22
                    end_index = end_pos + len(ext)
                    if start_index > 21 and end_index > start_index:
                        images.append(style[start_index:end_index])
                    break
    return images

def getVideo(tg_box) -> str | None:
    """Get single video URL."""
    video_element = tg_box.find('video', {'class': 'tgme_widget_message_video'})
    if video_element and 'src' in video_element.attrs:
        return video_element['src']
    return None

def getAllVideos(tg_box) -> list[str]:
    """Get all video URLs for media groups."""
    videos = []
    video_elements = tg_box.find_all('video', {'class': 'tgme_widget_message_video'})
    for video_element in video_elements:
        if video_element.get('src'):
            videos.append(video_element['src'])
    return videos

def getTimestamp(tg_box) -> datetime.datetime | None:
    """Extract message timestamp."""
    time_element = tg_box.find('time', {'datetime': True})
    if time_element and 'datetime' in time_element.attrs:
        return parser.isoparse(time_element['datetime'])
    return None

def download_file(url: str | None, prefix: str, ext_fallback: str, timeout: int = 10) -> tuple[io.BytesIO | None, str | None]:
    """Download a file from url and return a BytesIO object and filename."""
    if not url:
        return None, None
    max_retries = 3
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=TELEGRAM_HEADERS, timeout=timeout)
            response.raise_for_status()
            
            content_bytes = response.content
            if not content_bytes:
                raise ValueError("Downloaded file content is empty (0 bytes)")
            
            ext = os.path.splitext(url)[1]
            if not ext or '?' in ext:
                ext = url.split('.')[-1].split('?')[0] if '.' in url else ext_fallback
            if not ext.startswith('.'):
                ext = f".{ext}"
            if len(ext) > 5:
                ext = f".{ext_fallback}"
                
            filename = f"{prefix}_{int(time.time())}_{attempt}{ext}"
            return io.BytesIO(content_bytes), filename
        except Exception as e:
            log_message(f"Error downloading {prefix}: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
    return None, None

def download_image(url: str | None) -> tuple[io.BytesIO | None, str | None]:
    """Download an image file."""
    return download_file(url, "img", "jpg", timeout=10)

def download_video(url: str | None) -> tuple[io.BytesIO | None, str | None]:
    """Download a video file."""
    return download_file(url, "video", "mp4", timeout=30)

def send_webhook_message(webhook_url: str, thread_id: str | None = None, **kwargs) -> tuple[bool, bool]:
    """Send webhook message via discord.py SyncWebhook with native error handling.
    Returns (success, is_payload_too_large)"""
    try:
        webhook = SyncWebhook.from_url(webhook_url)
        if thread_id:
            kwargs['thread'] = discord.Object(id=int(thread_id))
        webhook.send(**kwargs)
        return True, False
    except discord.HTTPException as e:
        log_message(f"Discord HTTP Exception: {e}", log_type="error")
        is_payload_too_large = (e.status == 413)
        return False, is_payload_too_large
    except Exception as e:
        log_message(f"Error sending message to Discord: {e}", log_type="error")
        return False, False
def download_media_concurrently(media_list: list[tuple[str, str]]) -> list[tuple[str, io.BytesIO | None, str | None]]:
    """Download multiple media files concurrently while preserving their original order.
    media_list is a list of (media_type, url) tuples.
    Returns a list of (url, bytes_io, filename) tuples."""
    def download_one(item: tuple[str, str]) -> tuple[str, io.BytesIO | None, str | None]:
        media_type, url = item
        try:
            if media_type == 'image':
                data, filename = download_image(url)
            else:
                data, filename = download_video(url)
            return url, data, filename
        except Exception as e:
            log_message(f"Concurrent download error for {url}: {e}", log_type="error")
            return url, None, None
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(media_list), MAX_MEDIA_WORKERS) or 1) as executor:
        results = list(executor.map(download_one, media_list))
    return results

def sendMessage(msg_link: str, msg_text: str | None, msg_image: str | None, msg_video: str | None, 
                author_name: str, icon_url: str | None, timestamp: datetime.datetime | None = None, 
                all_images: list[str] | None = None, all_videos: list[str] | None = None) -> None:
    """Send a Telegram message to Discord webhook using Components V2 (with thread support)."""
    # 1. Collect all media in their correct sequence
    media_list = []
    images_to_send = all_images if all_images else ([msg_image] if msg_image else [])
    videos_to_send = all_videos if all_videos else ([msg_video] if msg_video else [])
    
    for img in images_to_send:
        if img:
            media_list.append(('image', img))
    for vid in videos_to_send:
        if vid:
            media_list.append(('video', vid))
            
    # Slicing to first 10 items since Discord CV2 gallery limit is 10
    media_list = media_list[:10]
    
    # 2. Download concurrently
    downloaded_results = []
    if media_list:
        log_message(f"Downloading {len(media_list)} media files concurrently...", log_type="status2")
        downloaded_results = download_media_concurrently(media_list)
        
    # 3. Build files list and gallery items
    files = []
    gallery_items = []
    media_status = []
    
    for url, data, filename in downloaded_results:
        is_video = any(item[0] == 'video' and item[1] == url for item in media_list)
        if data and filename:
            files.append(File(data, filename=filename))
            gallery_items.append(discord.MediaGalleryItem(f"attachment://{filename}"))
            media_status.append({
                'url': url,
                'data': data,
                'filename': filename,
                'is_video': is_video,
                'attached': True
            })
        else:
            gallery_items.append(discord.MediaGalleryItem(url))
            media_status.append({
                'url': url,
                'data': None,
                'filename': None,
                'is_video': is_video,
                'attached': False
            })
            
    try:
        # Format timestamp
        unix_time = int(timestamp.timestamp()) if timestamp else int(time.time())
        time_str = f" at <t:{unix_time}:f>"
        
        text_content = f"{msg_text}\n\n-# [Message Link](<{msg_link}>){time_str}" if msg_text else f"-# [Message Link](<{msg_link}>){time_str}"
        text_disp = TextDisplay(text_content)
        
        # Construct layout container
        container = Container(text_disp, accent_color=EMBED_COLOR)
        if gallery_items:
            gallery = MediaGallery(*gallery_items)
            container.add_item(gallery)
            
        view = LayoutView()
        view.add_item(container)
        
        log_message(f"Sending message to Discord: {msg_link}", log_type="new_message")
        
        kwargs = {
            'username': author_name,
            'avatar_url': icon_url,
            'view': view
        }
        if files:
            kwargs['files'] = files
            
        success, too_large = send_webhook_message(WEBHOOK_URL, THREAD_ID, **kwargs)
        
        # Targeted video fallback on HTTP 413 (Payload Too Large)
        if not success and too_large:
            log_message("Payload too large, applying targeted video fallback (replacing videos with CDN URLs)...", log_type="new_message")
            
            # Reset BytesIO streams before retry to prevent empty bytes read
            for item in media_status:
                if item['data']:
                    item['data'].seek(0)
                    
            fallback_files = []
            fallback_gallery_items = []
            
            for item in media_status:
                if item['attached']:
                    if item['is_video']:
                        video_size = len(item['data'].getvalue())
                        if video_size > 10 * 1024 * 1024:
                            log_message(f"Video {item['filename']} is too large ({video_size / (1024*1024):.2f} MB), replacing with CDN URL.", log_type="new_message")
                            fallback_gallery_items.append(discord.MediaGalleryItem(item['url']))
                            continue
                    fallback_files.append(File(item['data'], filename=item['filename']))
                    fallback_gallery_items.append(discord.MediaGalleryItem(f"attachment://{item['filename']}"))
                else:
                    fallback_gallery_items.append(discord.MediaGalleryItem(item['url']))
                    
            fallback_container = Container(text_disp, accent_color=EMBED_COLOR)
            if fallback_gallery_items:
                fallback_gallery = MediaGallery(*fallback_gallery_items)
                fallback_container.add_item(fallback_gallery)
                
            fallback_view = LayoutView()
            fallback_view.add_item(fallback_container)
            
            fallback_kwargs = {
                'username': author_name,
                'avatar_url': icon_url,
                'view': fallback_view
            }
            if fallback_files:
                fallback_kwargs['files'] = fallback_files
                
            success, too_large = send_webhook_message(WEBHOOK_URL, THREAD_ID, **fallback_kwargs)
            
        # Final fallback to plain text content if layout still fails
        if not success:
            log_message("Failed to send with layout, falling back to plain text content only...", log_type="new_message")
            content_parts = []
            if msg_text:
                content_parts.append(msg_text)
            for url in [item['url'] for item in media_status]:
                content_parts.append(url)
            content_parts.append(f"[Message Link](<{msg_link}>) at <t:{unix_time}:f>")
            
            fallback_content = "\n\n".join(content_parts)
            
            success, _ = send_webhook_message(
                WEBHOOK_URL,
                THREAD_ID,
                username=author_name,
                avatar_url=icon_url,
                content=fallback_content
            )
            if not success:
                log_message("Failed to send plain text fallback", log_type="error")
                return
                
        log_message("Message sent successfully.", log_type="new_message")
    except Exception as e:
        log_message(f"Error preparing or sending message to Discord: {e}", log_type="error")

def sendMissingMessages(channel: str, missing_numbers: list[int], author_name: str, icon_url: str | None, timestamp: datetime.datetime | None) -> None:
    """Send placeholders for specific missing message numbers."""
    for missing_number in missing_numbers:
        missing_link = f"https://t.me/{channel}/{missing_number}"
        log_message(f"Sending placeholder for missing message: {missing_link}", log_type="error")
        sendMessage(missing_link, ERROR_PLACEHOLDER, None, None, author_name, icon_url, timestamp=timestamp)

def main(tg_channel: str) -> None:
    SCRIPT_START_TIME = datetime.datetime.now()
    msg_log = []
    last_processed_number = 0
    grouped_media_ranges = set()
    log_message(f"Starting bot for channel: {tg_channel}", log_type="status2")

    while True:
        try:
            msg_temp = []
            log_message("Checking for new messages...", log_type="status2")
            message_boxes = scrapeTelegramMessageBox(tg_channel)
            if not message_boxes:
                continue
            for tg_box in message_boxes:
                msg_link = getLink(tg_box)
                if not msg_link:
                    continue

                match = re.match(rf"https://t.me/{tg_channel}/(\d+)", msg_link)
                if not match:
                    continue

                current_number = int(match.group(1))
                author_name = getAuthorName(tg_box)
                icon_url = getAuthorIcon(tg_box)
                timestamp = getTimestamp(tg_box)

                if current_number in grouped_media_ranges:
                    log_message(f"Skipping grouped media component: {msg_link}", log_type="status2")
                    msg_temp.append(msg_link)
                    last_processed_number = current_number
                    continue

                if last_processed_number > 0 and current_number > last_processed_number + 1:
                    filtered_missing = [num for num in range(last_processed_number + 1, current_number) if num not in grouped_media_ranges]
                    if filtered_missing:
                        sendMissingMessages(tg_channel, filtered_missing, author_name, icon_url, timestamp)

                if is_message_logged(tg_channel, current_number):
                    log_message(f"Skipping already logged message: {msg_link}", log_type="status2")
                    continue

                msg_text = getText(tg_box)
                msg_image = getImage(tg_box)
                msg_video = getVideo(tg_box)
                
                all_images = getAllImages(tg_box)
                all_videos = getAllVideos(tg_box)
                total_media = len(all_images) + len(all_videos)
                
                if total_media > 1 and not msg_text:
                    log_message(f"Grouped media detected with no text, trying individual message URL: {msg_link}", log_type="status2")
                    msg_text = getTextFromIndividualMessage(msg_link)
                    if msg_text:
                        log_message(f"Successfully extracted text from meta tags: '{msg_text[:50]}...'", log_type="status2")

                if msg_link not in msg_log:
                    log_message(f"New message found: {msg_link}", log_type="new_message")
                    msg_temp.append(msg_link)
                    
                    if total_media > 1:
                        log_message(f"Marking grouped media range: {current_number} + {total_media-1} components", log_type="status2")
                        for i in range(1, total_media):
                            grouped_media_ranges.add(current_number + i)
                    
                    sendMessage(msg_link, msg_text, msg_image, msg_video, author_name, icon_url, timestamp=timestamp, all_images=all_images, all_videos=all_videos)

                msg_temp.append(msg_link)
                last_processed_number = current_number

            msg_log = msg_temp
            current_time = datetime.datetime.now()
            time_passed = current_time - SCRIPT_START_TIME
            log_message(f"Bot working for {tg_channel}. Time passed: {time_passed}", log_type="status")
        except Exception as e:
            log_message(f"[   E R R O R   ]\n{e}\nScript ignored the error and keeps running.", log_type="error")
        time.sleep(COOLDOWN)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        log_message("Usage: python webhook.py <TG_CHANNEL>", log_type="status2") 
        sys.exit(1)
    TG_CHANNEL = sys.argv[1]
    log_message(f"Initializing bot with channel: {TG_CHANNEL}", log_type="status2")
    main(TG_CHANNEL)