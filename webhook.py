import time
import datetime
import requests
import sys
import re
import os
import io
import json
from dateutil import parser
from config import WEBHOOK_URL as DEFAULT_WEBHOOK_URL, COOLDOWN, EMBED_COLOR as DEFAULT_EMBED_COLOR
from bs4 import BeautifulSoup
from discord import SyncWebhook, Embed, File
from rate_limiter import discord_rate_limiter

# Job-specific configuration (will be set from command-line args)
WEBHOOK_URL = DEFAULT_WEBHOOK_URL
EMBED_COLOR = DEFAULT_EMBED_COLOR
JOB_NAME = None

def log_message(message, log_type="info"):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    job_prefix = f"[{JOB_NAME}]" if JOB_NAME else ""
    log_entry = f"{timestamp} {job_prefix} {message}"
    print(log_entry)
    if log_type in ["error", "new_message", "status", "status2"]:
        with open("Disgram.log", "a", encoding="utf-8") as log_file:
            log_file.write(log_entry + "\n")

def parse_channel_param(channel_param):
    """
    Parse channel parameter - can be either:
    1. Channel name only: 'channel'
    2. Full message URL: 'https://t.me/channel/123'
    
    Returns: (channel_name, start_from_message_number)
    """
    if channel_param.startswith('https://t.me/'):
        # Full URL provided
        parts = channel_param.split('/')
        if len(parts) >= 4:
            channel_name = parts[3]  # Extract channel name
            if len(parts) >= 5 and parts[4].isdigit():
                start_message = int(parts[4])
                return channel_name, start_message
            else:
                return channel_name, None
    
    # Just channel name provided
    return channel_param, None

def is_message_logged(channel, number, job_name=None):
    """
    Check if a message has been logged by a specific job.
    If job_name is provided, only return True if this specific job logged the message.
    If job_name is None, check if any job logged the message (legacy behavior).
    """
    try:
        with open("Disgram.log", "r") as log_file:
            for line in log_file:
                # Look for the message URL in the line
                match = re.search(rf"https://t.me/{channel}/(\d+)", line)
                if match and int(match.group(1)) >= int(number):
                    if job_name:
                        # Check if this specific job logged the message
                        if f"[{job_name}]" in line:
                            return True
                    else:
                        # Legacy behavior - any job logging counts
                        return True
    except FileNotFoundError:
        pass
    return False

def scrapeTelegramMessageBox(channel, start_from_message=None):
    max_retries = 5
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            # Build URL with starting point if specified
            if start_from_message:
                url = f'https://t.me/s/{channel}?before={start_from_message + 20}'  # Get messages around the target
                log_message(f"Scraping messages from Telegram channel: {channel} starting near message {start_from_message} (Attempt {attempt + 1})")
            else:
                url = f'https://t.me/s/{channel}'
                log_message(f"Scraping messages from Telegram channel: {channel} (Attempt {attempt + 1})")
                
            tg_html = requests.get(url, timeout=10)
            tg_html.raise_for_status()
            tg_soup = BeautifulSoup(tg_html.text, 'html.parser')
            tg_box = tg_soup.find_all('div', {'class': 'tgme_widget_message_wrap js-widget_message_wrap'})
            return tg_box
        except requests.exceptions.RequestException as e:
            log_message(f"Error scraping Telegram: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                log_message("Max retries reached. Skipping this iteration.", log_type="error")
                return []

def getAuthorIcon(tg_box):
    icon_element = tg_box.find('i', {'class': 'tgme_widget_message_user_photo'})
    if icon_element:
        img_tag = icon_element.find('img')
        if img_tag and 'src' in img_tag.attrs:
            return img_tag['src']
    return None

def getAuthorName(tg_box):
    author_name = tg_box.find('a', {'class': 'tgme_widget_message_owner_name'})
    return author_name.text.strip() if author_name else None

def getLink(tg_box):
    msg_link = tg_box.find_all('a', {'class':'tgme_widget_message_date'}, href=True)
    if msg_link:
        return msg_link[0]['href']
    return None

def getText(tg_box):
    msg_text = tg_box.find_all('div', {'class': 'tgme_widget_message_text js-message_text'})
    converted_text = ''
    if not msg_text:
        return None

    msg_text = msg_text[0]
    for child in msg_text.children:
        if child.name is None:
            converted_text += child
        elif child.name == 'a':
            if child.text == child['href']:
                converted_text += child['href']
            else:
                converted_text += f"[{child.text}]({child['href']})"
        elif child.name == 'pre':
            converted_text += f"```{child.text}```"
        elif child.name == 'b':
            converted_text += f"**{child.text}**"
        elif child.name == 'tg-spoiler':
            converted_text += f"||{child.text}||"
        elif child.name == 'i':
            converted_text += f"*{child.text}*"
        elif child.name == 'u':
            converted_text += f"__{child.text}__"
        elif child.name == 's':
            converted_text += f"~~{child.text}~~"
        elif child.name == 'br':
            converted_text += '\n'
        elif child.name == 'blockquote':
            converted_text += f"> {child.text}\n"
    return converted_text

def getTextFromIndividualMessage(msg_link):
    """Extract text from an individual message URL, useful for grouped media messages"""
    if not msg_link:
        return None
        
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            response = requests.get(msg_link, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # First try to find text in standard location
            text_div = soup.find('div', class_='tgme_widget_message_text')
            if text_div:
                text_content = text_div.get_text(strip=True)
                if text_content:
                    return text_content
            
            # For grouped media messages, check meta tags
            og_desc = soup.find('meta', property='og:description')
            if og_desc:
                content_attr = og_desc.get('content')
                if content_attr:
                    # Handle both string and list types
                    content = str(content_attr).strip() if hasattr(content_attr, 'strip') else str(content_attr)
                    if content and _is_likely_message_content(content):
                        return content
                    
            return None
            
        except Exception as e:
            log_message(f"Error fetching text from individual message {msg_link}: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return None

def _is_likely_message_content(content):
    """Simple check to filter out obvious channel descriptions"""
    if not content:
        return False
    
    content_lower = content.lower().strip()
    
    # Filter out common channel description patterns
    channel_desc_patterns = [
        r'^the official .+ on telegram',
        r'official .+ channel',
        r'.+ official channel', 
        r'welcome to .+',
        r'much recursion\. very telegram\. wow\.',  # Telegram's description
        r'^.+\s+–\s+.+$',  # Channel title format
    ]
    
    for pattern in channel_desc_patterns:
        if re.match(pattern, content_lower):
            return False
    
    # If content is very generic/short, be cautious
    if len(content.split()) <= 1:
        return False
    
    # Otherwise, likely message content
    return True

def getImage(tg_box):
    msg_image = tg_box.find('a', {'class': 'tgme_widget_message_photo_wrap'}, href=True)
    if msg_image:
        startIndex = msg_image['style'].find("background-image:url('") + 22
        endIndex = msg_image['style'].find(".jpg')") + 4
        if startIndex > 21 and endIndex > 3:
            return msg_image['style'][startIndex:endIndex]
    return None

def getAllImages(tg_box):
    """Extract all images from a message (for grouped media)"""
    images = []
    msg_images = tg_box.find_all('a', {'class': 'tgme_widget_message_photo_wrap'})
    
    for msg_image in msg_images:
        if msg_image.get('style'):
            style = msg_image['style']
            # Handle different image formats (.jpg, .png, .webp, etc.)
            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                start_pos = style.find("background-image:url('")
                end_pos = style.find(f"{ext}')")
                if start_pos > -1 and end_pos > -1:
                    start_index = start_pos + 22
                    end_index = end_pos + len(ext)
                    if start_index > 21 and end_index > start_index:
                        image_url = style[start_index:end_index]
                        images.append(image_url)
                    break
    
    return images

def getAllVideos(tg_box):
    """Extract all videos from a message (for grouped media)"""
    videos = []
    video_elements = tg_box.find_all('video', {'class': 'tgme_widget_message_video'})
    
    for video_element in video_elements:
        if video_element.get('src'):
            videos.append(video_element['src'])
    
    return videos

def getTimestamp(tg_box):
    time_element = tg_box.find('time', {'datetime': True})
    if time_element and 'datetime' in time_element.attrs:
        return parser.isoparse(time_element['datetime'])
    return None

def download_image(image_url: str | None) -> tuple[io.BytesIO | None, str | None]:
    if not image_url:
        return (None, None)
    
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            
            file_ext = os.path.splitext(image_url)[1]
            if not file_ext:
                file_ext = '.jpg'
                
            filename = f"img_{int(time.time())}_{attempt}{file_ext}"
            return io.BytesIO(response.content), filename
        except Exception as e:
            log_message(f"Error downloading image: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                log_message("Max retries reached. Unable to download image.", log_type="error")
                return (None, None)
    
    # This should never be reached, but added for type checker completeness
    return (None, None)

def download_video(video_url: str | None) -> tuple[io.BytesIO | None, str | None]:
    if not video_url:
        return (None, None)
    
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            response = requests.get(video_url, timeout=30)
            response.raise_for_status()
            
            # Get file extension from URL or default to mp4
            file_ext = video_url.split('.')[-1].split('?')[0] if '.' in video_url else 'mp4'
            if file_ext not in ['mp4', 'mov', 'avi', 'webm', 'mkv']:
                file_ext = 'mp4'
            
            filename = f"video_{int(time.time())}_{attempt}.{file_ext}"
            return io.BytesIO(response.content), filename
        except Exception as e:
            log_message(f"Error downloading video: {e}", log_type="error")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                log_message("Max retries reached. Unable to download video.", log_type="error")
                return (None, None)
    
    # This should never be reached, but added for type checker completeness
    return (None, None)

def getVideo(tg_box):
    video_element = tg_box.find('video', {'class': 'tgme_widget_message_video'})
    if video_element and 'src' in video_element.attrs:
        return video_element['src']
    return None

class RateLimitedWebhook:
    """
    A wrapper around SyncWebhook that integrates with our rate limiter
    """
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
        self.webhook = SyncWebhook.from_url(webhook_url)
    
    def send_with_rate_limiting(self, max_retries=5, **kwargs):
        """
        Send webhook message with rate limiting using SyncWebhook
        """
        for attempt in range(max_retries + 1):
            # Wait for rate limits before making request
            if not discord_rate_limiter.wait_for_rate_limit(self.webhook_url, "POST"):
                return False
            
            try:
                # Use SyncWebhook's send method which handles all Discord formatting
                self.webhook.send(**kwargs)
                
                # Manually track successful request for rate limiter
                discord_rate_limiter._increment_global_counter()
                bucket_key = discord_rate_limiter._get_bucket_key(self.webhook_url, "POST")
                discord_rate_limiter._decrement_bucket_remaining(bucket_key)
                
                return True
                
            except requests.exceptions.HTTPError as e:
                if e.response is not None:
                    # Handle the response with our rate limiter
                    should_retry, wait_time = discord_rate_limiter.handle_response(
                        e.response, self.webhook_url, "POST"
                    )
                    
                    if not should_retry:
                        log_message(f"Webhook request failed permanently: {e}", log_type="error")
                        return False
                    
                    if attempt < max_retries and wait_time:
                        log_message(f"Rate limited, retrying in {wait_time:.2f} seconds (attempt {attempt + 1}/{max_retries})", log_type="error")
                        time.sleep(wait_time)
                        continue
                
                # Max retries reached or no response
                if attempt >= max_retries:
                    log_message(f"Max retries ({max_retries}) exceeded for webhook", log_type="error")
                    return False
                    
            except Exception as e:
                log_message(f"Webhook request exception on attempt {attempt + 1}: {e}", log_type="error")
                if attempt < max_retries:
                    # Exponential backoff for network errors
                    wait_time = min(2.0 ** attempt, 60.0)
                    time.sleep(wait_time)
                    continue
                else:
                    return False
        
        return False

def sendMessage(msg_link, msg_text, msg_image, msg_video, author_name, icon_url, timestamp=None, all_images=None, all_videos=None):
    """Send a Discord message using SyncWebhook with proper rate limiting"""
    # Use all_images if provided (for grouped media), otherwise fall back to single image
    images_to_send = all_images if all_images else ([msg_image] if msg_image else [])
    videos_to_send = all_videos if all_videos else ([msg_video] if msg_video else [])
    
    # If this is a grouped media message, send all media items
    if len(images_to_send) > 1 or len(videos_to_send) > 1:
        log_message(f"Sending grouped media message with {len(images_to_send)} images and {len(videos_to_send)} videos: {msg_link}", log_type="new_message")
        sendGroupedMediaMessage(msg_link, msg_text, images_to_send, videos_to_send, author_name, icon_url, timestamp)
        return
    
    # Handle media files (image and video)
    image_data = None
    image_filename = None
    video_data = None
    video_filename = None
    video_sent_as_attachment = False
    
    # Try to get image file
    if images_to_send:
        result = download_image(images_to_send[0])
        if result and result[0] is not None and result[1] is not None:
            image_data, image_filename = result
        else:
            image_data, image_filename = None, None
    
    # Try to get video file if video exists
    if videos_to_send:
        video_url = videos_to_send[0]
        try:
            result = download_video(video_url)
            if result and result[0] is not None and result[1] is not None:
                video_data, video_filename = result
                video_sent_as_attachment = True
                log_message(f"Video will be sent as attachment: {video_url}", log_type="new_message")
        except Exception as e:
            log_message(f"Failed to download video, will fallback to link: {e}", log_type="error")
    
    try:
        webhook = RateLimitedWebhook(WEBHOOK_URL)
        
        embed = Embed(title='Message Link', url=msg_link, color=EMBED_COLOR, timestamp=timestamp)
        
        if msg_text:
            embed.description = msg_text
        
        embed.set_author(name=author_name, icon_url=icon_url, url=msg_link)
        
        files = []
        if image_data and image_filename:
            file = File(image_data, filename=image_filename)
            files.append(file)
            embed.set_image(url=f"attachment://{image_filename}")
        
        if video_data and video_filename:
            video_file = File(video_data, filename=video_filename)
            files.append(video_file)
        
        log_message(f"Sending message to Discord: {msg_link}", log_type="new_message")
        
        kwargs = {
            'username': author_name,
            'avatar_url': icon_url,
            'embed': embed
        }
        if files:
            kwargs['files'] = files
            
        success = webhook.send_with_rate_limiting(**kwargs)
        
        if not success:
            log_message("Failed to send main message", log_type="error")
            return
        
        if videos_to_send and not video_sent_as_attachment:
            video_url = videos_to_send[0]
            video_content = f"[Attached video]({video_url})\n[Message Link](<{msg_link}>)"
            
            log_message(f"Sending video link as separate message: {video_url}", log_type="new_message")
            
            video_success = webhook.send_with_rate_limiting(
                username=author_name,
                avatar_url=icon_url,
                content=video_content
            )
            
            if not video_success:
                log_message("Failed to send video link message", log_type="error")
        
        log_message("Message sent successfully.", log_type="new_message")
        
    except Exception as e:
        log_message(f"Error preparing or sending message to Discord: {e}", log_type="error")

def sendGroupedMediaMessage(msg_link, msg_text, images, videos, author_name, icon_url, timestamp=None):
    try:
        webhook = RateLimitedWebhook(WEBHOOK_URL)
        
        main_embed = Embed(title='Grouped Media Message Link', url=msg_link, color=EMBED_COLOR, timestamp=timestamp)
        
        description_parts = []
        if msg_text:
            description_parts.append(msg_text)
        
        description_parts.append(f"-# **Grouped Media:** {len(images)} images, {len(videos)} videos")
        main_embed.description = '\n\n'.join(description_parts)
        main_embed.set_author(name=author_name, icon_url=icon_url, url=msg_link)
        
        main_files = []
        if images:
            result = download_image(images[0])
            if result and result[0] is not None and result[1] is not None:
                image_data, image_filename = result
                if image_data and image_filename:
                    file = File(image_data, filename=image_filename)
                    main_files.append(file)
                    main_embed.set_image(url=f"attachment://{image_filename}")
        
        log_message(f"Sending grouped media main message: {msg_link}", log_type="new_message")
        kwargs = {
            'username': author_name,
            'avatar_url': icon_url,
            'embed': main_embed
        }
        if main_files:
            kwargs['files'] = main_files
            
        success = webhook.send_with_rate_limiting(**kwargs)
        
        if not success:
            log_message("Failed to send grouped media main message", log_type="error")
            return
        
        for i, image_url in enumerate(images[1:], 2):
            try:
                result = download_image(image_url)
                if result and result[0] is not None and result[1] is not None:
                    image_data, image_filename = result
                    if image_data and image_filename:
                        image_embed = Embed(
                            title='Message Link',
                            url=msg_link,
                            color=EMBED_COLOR,
                            timestamp=timestamp
                        )
                        
                        if msg_text:
                            image_embed.description = msg_text[:4096]
                            
                        image_embed.set_author(name=author_name, icon_url=icon_url, url=msg_link)
                        image_embed.set_footer(text=f'Image {i} of {len(images)}')
                        image_embed.set_image(url=f"attachment://{image_filename}")
                        
                        image_file = File(image_data, filename=image_filename)
                        
                        success = webhook.send_with_rate_limiting(
                            username=author_name,
                            avatar_url=icon_url,
                            embed=image_embed,
                            files=[image_file]
                        )
                        
                        if not success:
                            log_message(f"Failed to send image {i}", log_type="error")
                            
            except Exception as e:
                log_message(f"Error sending image {i}: {e}", log_type="error")
        
        for i, video_url in enumerate(videos, 1):
            try:
                video_sent_as_attachment = False
                
                try:
                    result = download_video(video_url)
                    if result and result[0] is not None and result[1] is not None:
                        video_data, video_filename = result
                        log_message(f"Attempting to send video {i} as attachment: {video_url}", log_type="new_message")
                        
                        video_embed = Embed(
                            title='Message Link',
                            url=msg_link,
                            color=EMBED_COLOR,
                            timestamp=timestamp
                        )
                        
                        if msg_text:
                            video_embed.description = msg_text[:4096]
                            
                        video_embed.set_author(name=author_name, icon_url=icon_url, url=msg_link)
                        video_embed.set_footer(text=f'Video {i} of {len(videos)}')
                        
                        assert video_data is not None and video_filename is not None
                        video_file = File(video_data, filename=video_filename)
                        
                        success = webhook.send_with_rate_limiting(
                            username=author_name,
                            avatar_url=icon_url,
                            embed=video_embed,
                            files=[video_file]
                        )
                        
                        if success:
                            video_sent_as_attachment = True
                            log_message(f"Video {i} sent successfully as attachment", log_type="new_message")
                        else:
                            log_message(f"Failed to send video {i} as attachment, will try link", log_type="error")
                            
                except Exception as e:
                    log_message(f"Failed to send video {i} as attachment, falling back to link: {e}", log_type="error")
                
                if not video_sent_as_attachment:
                    video_content = f"**Video** [**{i}**]({video_url}) of {len(videos)}\n[Message Link](<{msg_link}>)"
                    
                    success = webhook.send_with_rate_limiting(
                        username=author_name,
                        avatar_url=icon_url,
                        content=video_content
                    )
                    
                    if not success:
                        log_message(f"Failed to send video {i} link", log_type="error")
                        
            except Exception as e:
                log_message(f"Error sending video {i}: {e}", log_type="error")
        
        log_message(f"Grouped media message sent successfully: {len(images)} images, {len(videos)} videos", log_type="new_message")
        
    except Exception as e:
        log_message(f"Error sending grouped media message: {e}", log_type="error")



def main(tg_channel):
    SCRIPT_START_TIME = datetime.datetime.now()
    msg_log = []
    
    # Parse channel parameter to handle both channel names and message URLs
    channel_name, start_from_message = parse_channel_param(tg_channel)
    last_processed_number = start_from_message if start_from_message else 0
    
    grouped_media_ranges = set()  # Track message numbers that are part of grouped media
    log_message(f"Starting bot for channel: {channel_name}" + 
               (f" from message {start_from_message}" if start_from_message else ""), log_type="status2")

    while True:
        try:
            msg_temp = []
            log_message("Checking for new messages...", log_type="status2")
            message_boxes = scrapeTelegramMessageBox(channel_name, start_from_message)
            if not message_boxes:
                continue
            for tg_box in message_boxes:
                msg_link = getLink(tg_box)
                if not msg_link:
                    continue

                match = re.match(rf"https://t.me/{channel_name}/(\d+)", msg_link)
                if not match:
                    continue

                current_number = int(match.group(1))
                author_name = getAuthorName(tg_box)
                icon_url = getAuthorIcon(tg_box)
                timestamp = getTimestamp(tg_box)

                # Check if this message is part of a grouped media we already processed
                if current_number in grouped_media_ranges:
                    log_message(f"Skipping grouped media component: {msg_link}", log_type="status2")
                    msg_temp.append(msg_link)
                    last_processed_number = current_number
                    continue

                # Skip missing message gap detection - no more placeholder messages

                if is_message_logged(channel_name, current_number, JOB_NAME):
                    log_message(f"Skipping already logged message: {msg_link}", log_type="status2")
                    continue

                msg_text = getText(tg_box)
                msg_image = getImage(tg_box)
                msg_video = getVideo(tg_box)
                
                # Check for grouped media
                all_images = getAllImages(tg_box)
                all_videos = getAllVideos(tg_box)
                
                # Calculate total media count
                total_media = len(all_images) + len(all_videos)
                
                # For grouped media messages, if no text found in the box, try individual message URL
                if total_media > 1 and not msg_text:
                    log_message(f"Grouped media detected with no text, trying individual message URL: {msg_link}", log_type="status2")
                    msg_text = getTextFromIndividualMessage(msg_link)
                    if msg_text:
                        log_message(f"Successfully extracted text from meta tags: '{msg_text[:50]}...'", log_type="status2")

                if msg_link not in msg_log:
                    log_message(f"New message found: {msg_link}", log_type="new_message")
                    msg_temp.append(msg_link)
                    
                    # If this is grouped media, mark the component message numbers
                    if total_media > 1:
                        log_message(f"Marking grouped media range: {current_number} + {total_media-1} components", log_type="status2")
                        for i in range(1, total_media):  # Skip first message (already processed), mark next N-1
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
    if len(sys.argv) < 2:
        log_message("Usage: python webhook.py <TG_CHANNEL> [WEBHOOK_URL] [EMBED_COLOR] [JOB_NAME]", log_type="status2") 
        sys.exit(1)

    TG_CHANNEL = sys.argv[1]
    
    # Optional job-specific parameters
    if len(sys.argv) >= 3 and sys.argv[2]:
        WEBHOOK_URL = sys.argv[2]
    if len(sys.argv) >= 4 and sys.argv[3]:
        try:
            EMBED_COLOR = int(sys.argv[3], 16)
        except ValueError:
            log_message(f"Invalid embed color: {sys.argv[3]}, using default", log_type="error")
    if len(sys.argv) >= 5 and sys.argv[4]:
        JOB_NAME = sys.argv[4]
    
    log_message(f"Initializing bot with channel: {TG_CHANNEL}, job: {JOB_NAME or 'legacy'}", log_type="status2")
    main(TG_CHANNEL)