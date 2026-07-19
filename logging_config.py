import logging
import os
import re
import filelock

class DisgramLogHandler(logging.Handler):
    """Custom handler that writes to Disgram.log and auto-cleans on overflow."""

    _PRESERVE_PATTERNS = ("WARNING", "ERROR", "CRITICAL")

    def __init__(self, path: str = "Disgram.log", max_bytes: int = 5 * 1024 * 1024):
        """
        Initializes the DisgramLogHandler.

        Args:
            path (str): Path to the log file (default: "Disgram.log").
            max_bytes (int): Maximum size of the log file before triggering an auto-cleanup. Default is 5MB.
        """
        super().__init__()
        self._path = path
        self._max_bytes = max_bytes
        self._lock = filelock.FileLock(f"{path}.lock")

    def emit(self, record: logging.LogRecord) -> None:
        """
        Processes and writes a log record to the file.
        
        Automatically triggers a background "soft clean" if the file exceeds `max_bytes` after writing.
        This ensures the log file never grows unbounded on constrained environments.

        Args:
            record (logging.LogRecord): The log record to process.
        """
        try:
            msg = self.format(record)
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
                
                try:
                    if os.path.getsize(self._path) > self._max_bytes:
                        self._perform_cleanup(hard=False)
                except OSError:
                    pass
        except Exception:
            self.handleError(record)

    def trigger_cleanup(self, hard: bool = False) -> None:
        """
        Manually trigger a cleanup of the Disgram.log file.

        Args:
            hard (bool): If True, drops everything except the latest sent message markers. 
                         If False, preserves markers AND recent WARNING/ERROR logs.
        """
        with self._lock:
            self._perform_cleanup(hard=hard)

    def _perform_cleanup(self, hard: bool) -> None:
        """
        Core logic to parse and rewrite the log file, stripping out standard informational logs.
        """
        header_line = None
        latest_messages: dict[str, int] = {}
        preserved_lines: list[str] = []

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if "Add your message links" in stripped:
                        header_line = stripped
                        continue

                    # Track latest message link per channel
                    matches = re.findall(r"https://t\.me/([^/\s]+)/(\d+)", stripped)
                    if matches:
                        for channel, msg_num in matches:
                            num = int(msg_num)
                            if channel not in latest_messages or num > latest_messages[channel]:
                                latest_messages[channel] = num
                        
                    # Preserve WARNING/ERROR/CRITICAL lines if not hard cleanup
                    if not hard and any(level in stripped for level in self._PRESERVE_PATTERNS):
                        preserved_lines.append(stripped)
        except FileNotFoundError:
            return

        channel_links = [
            f"https://t.me/{ch}/{num}"
            for ch, num in sorted(latest_messages.items())
        ]

        with open(self._path, "w", encoding="utf-8") as f:
            if header_line:
                f.write(f"{header_line}\n")
            for link in channel_links:
                f.write(f"{link}\n")
            for line in preserved_lines:
                f.write(f"{line}\n")

    def is_message_logged(self, channel: str, number: int) -> bool:
        """
        Checks if a specific message number has already been forwarded for a given channel.

        Args:
            channel (str): The Telegram channel name.
            number (int): The message ID.

        Returns:
            bool: True if the message has been forwarded (logged), False otherwise.
        """
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        match = re.search(rf"https://t.me/{channel}/(\d+)", line)
                        if match and int(match.group(1)) >= number:
                            return True
            except FileNotFoundError:
                pass
            return False

_disgram_handler = None

def get_disgram_handler() -> DisgramLogHandler | None:
        """
        Retrieve the active singleton DisgramLogHandler instance.

        Returns:
            DisgramLogHandler: The handler instance, or None if `configure_logging` hasn't run.
        """
        return _disgram_handler

def configure_logging(
    process_name: str = "main",
    log_max_bytes: int = 5 * 1024 * 1024,
) -> None:
    """
    Configures the root python logger to output to both the console and Disgram.log.

    This function sets up the unified `DisgramLogHandler` and assigns a custom formatter
    that tags logs with the calling `process_name` (e.g., 'main' or 'worker-0').

    Args:
        process_name (str): Name tag to prefix logs with.
        log_max_bytes (int): Trigger threshold in bytes before the file auto-cleans itself.
    """
    global _disgram_handler
    
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        f"%(asctime)s [{process_name}] %(levelname)s: %(message)s"
    )

    # Console
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # Disgram.log
    _disgram_handler = DisgramLogHandler("Disgram.log", log_max_bytes)
    _disgram_handler.setFormatter(formatter)
    root_logger.addHandler(_disgram_handler)

def is_message_logged(channel: str, number: int) -> bool:
    """
    Global helper to check if a specific message ID has been processed.

    Args:
        channel (str): The Telegram channel name.
        number (int): The message ID.

    Returns:
        bool: True if the message has been forwarded, False otherwise.
    """
    if _disgram_handler:
        return _disgram_handler.is_message_logged(channel, number)
    return False
