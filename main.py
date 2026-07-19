import subprocess
import time
import threading
import datetime
import os
import sys
import psutil
import requests
import re
import logging
import asyncio
from flask import Flask, jsonify, Response, request
from config import Channels, MAX_WORKERS, WEBHOOK_URL, THREAD_ID, COOLDOWN, API_BEARER_TOKEN
from git_manager import initialize_git_manager

def get_git_manager():
    """Get the current git_log_manager instance (avoids import stale reference issue)"""
    from git_manager import git_log_manager
    return git_log_manager

from logging_config import configure_logging, get_disgram_handler

# Configure logging for the main application
configure_logging(process_name="main")
logger = logging.getLogger('DisgramMain')

def sanitize_log_content(content: str) -> str:
    """Remove sensitive tokens from log content for safe display"""
    if not content:
        return content
    
    import re
    # Replace GitHub Personal Access Tokens with [REDACTED]
    sanitized = re.sub(r'github_pat_[A-Za-z0-9_]+', '[REDACTED]', content)
    sanitized = re.sub(r'ghp_[A-Za-z0-9_]+', '[REDACTED]', sanitized)
    sanitized = re.sub(r'ghs_[A-Za-z0-9_]+', '[REDACTED]', sanitized)
    
    # Also handle generic token patterns in URLs
    sanitized = re.sub(r'://[^@\s]+@', '://[REDACTED]@', sanitized)
    
    return sanitized

bot_start_time = None
last_health_check = None
health_status = {"status": "starting", "details": {}}
channel_chunks = []

def chunk_channels(channels: list[str], max_workers: int) -> list[list[str]]:
    """Split a list of channels into evenly distributed chunks."""
    if not channels:
        return []
    workers = min(len(channels), max_workers)
    k, m = divmod(len(channels), workers)
    return [channels[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(workers)]

app = Flask(__name__)

def verify_bearer_token():
    """Verify Bearer token for administrative POST endpoints."""
    if not API_BEARER_TOKEN:
        return False, (jsonify({"error": "Unauthorized: API Bearer Token is not configured on server"}), 401)
        
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, (jsonify({"error": "Unauthorized: Missing Authorization header"}), 401)
        
    provided_token = auth_header.split("Bearer ", 1)[1].strip()
    if provided_token != API_BEARER_TOKEN:
        return False, (jsonify({"error": "Forbidden: Invalid Bearer Token"}), 403)
        
    return True, None

def initialize_disgram_log():
    existing_links = set()
    if os.path.exists("Disgram.log"):
        with open("Disgram.log", "r", encoding="utf-8") as log_file:
            for line in log_file:
                line = line.strip()
                if line.startswith("https://t.me/"):
                    existing_links.add(line)
                    
    new_links = []
    for channel_url in Channels:
        if channel_url not in existing_links:
            new_links.append(channel_url)
    
    if new_links:
        with open("Disgram.log", "a", encoding="utf-8") as log_file:
            for channel_url in new_links:
                log_file.write(f"{channel_url}\n")

def extract_channel_name(channel_url):
    if channel_url.startswith("https://t.me/"):
        path = channel_url[13:]
        channel_name = path.split("/")[0]
        return channel_name
    else:
        return channel_url


def check_telegram_connectivity():
    try:
        response = requests.get("https://t.me/", timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def check_discord_webhook():
    if not WEBHOOK_URL or "{webhookID}" in WEBHOOK_URL:
        return False, "Webhook URL not configured"
    
    try:
        response = requests.get(WEBHOOK_URL, timeout=10)
        if response.status_code == 200:
            return True, "Webhook accessible"
        else:
            return False, f"Webhook returned status {response.status_code}"
    except Exception as e:
        return False, f"Webhook error: {str(e)}"

# Cache external check results to avoid Cloudflare/Discord rate-limiting issues
_ext_check_cache = {
    "last_check_time": 0.0,
    "telegram_ok": False,
    "discord_ok": False,
    "discord_msg": "Not checked yet",
    "telethon_ok": False
}
_ext_check_lock = threading.Lock()

def get_cached_external_checks():
    """Get cached connectivity status for Telegram and Discord, updating based on config.COOLDOWN."""
    global _ext_check_cache
    current_time = time.time()
    
    with _ext_check_lock:
        if current_time - _ext_check_cache["last_check_time"] > COOLDOWN:
            telegram_ok = check_telegram_connectivity()
            discord_ok, discord_msg = check_discord_webhook()
            
            from telethon_client import check_telethon_health
            telethon_ok = check_telethon_health()
            
            _ext_check_cache.update({
                "last_check_time": current_time,
                "telegram_ok": telegram_ok,
                "discord_ok": discord_ok,
                "discord_msg": discord_msg,
                "telethon_ok": telethon_ok
            })
        
        return _ext_check_cache["telegram_ok"], _ext_check_cache["discord_ok"], _ext_check_cache["discord_msg"], _ext_check_cache["telethon_ok"]

def check_log_freshness():
    log_file_path = "Disgram.log"
    max_age_minutes = 6  # Consider unhealthy if log is older than 6 minutes
    
    try:
        if not os.path.exists(log_file_path):
            return False, "Log file does not exist", None
        
        last_modified = os.path.getmtime(log_file_path)
        last_modified_dt = datetime.datetime.fromtimestamp(last_modified)
        
        current_time = datetime.datetime.now()
        age_minutes = (current_time - last_modified_dt).total_seconds() / 60
        
        is_fresh = age_minutes <= max_age_minutes
        
        if is_fresh:
            return True, f"Log is fresh (last updated {age_minutes:.1f} minutes ago)", last_modified_dt
        else:
            return False, f"Log is stale (last updated {age_minutes:.1f} minutes ago, max allowed: {max_age_minutes})", last_modified_dt
            
    except Exception as e:
        return False, f"Error checking log freshness: {str(e)}", None

def get_system_stats():
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get memory breakdown for Disgram processes
        process_breakdown = []
        app_total_mb = 0
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
            try:
                cmdline = proc.info.get('cmdline') or []
                # Check if it's a python process related to our app
                name = proc.info.get('name', '').lower()
                is_python = 'python' in name or any('python' in arg.lower() for arg in cmdline)
                
                if is_python:
                    is_main = any('main.py' in arg for arg in cmdline)
                    is_worker = any('webhook.py' in arg for arg in cmdline)
                    
                    if is_main or is_worker:
                        mem_info = proc.info.get('memory_info')
                        if mem_info:
                            mem_mb = round(mem_info.rss / 1024 / 1024, 2)
                            app_total_mb += mem_mb
                            
                            proc_type = "main" if is_main else "worker"
                            # For workers, include the channel names passed as arguments
                            details = " ".join(cmdline[1:]) if len(cmdline) > 1 else ""
                            
                            process_breakdown.append({
                                "pid": proc.info['pid'],
                                "type": proc_type,
                                "details": details,
                                "memory_mb": mem_mb
                            })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
                
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "system_memory_used_mb": round(memory.used / 1024 / 1024, 2),
            "app_memory_used_mb": round(app_total_mb, 2),
            "process_breakdown": process_breakdown,
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2)
        }
    except Exception as e:
        return {"error": str(e)}

async def _gather_health_checks():
    """Run all health checks concurrently using async threads."""
    return await asyncio.gather(
        asyncio.to_thread(get_cached_external_checks),
        asyncio.to_thread(check_log_freshness),
        asyncio.to_thread(get_system_stats),
        asyncio.to_thread(
            lambda: get_git_manager().get_commit_status()
            if get_git_manager() else {"git_available": False}
        ),
    )

@app.route('/health')
def health_check():
    global last_health_check
    last_health_check = datetime.datetime.now()
    
    (
        (telegram_ok, discord_ok, discord_msg, telethon_ok),
        (log_fresh, log_msg, log_last_modified),
        system_stats,
        git_commit_status,
    ) = asyncio.run(_gather_health_checks())
    
    uptime_seconds = (datetime.datetime.now() - bot_start_time).total_seconds() if bot_start_time else 0
    uptime_minutes = round(uptime_seconds / 60, 2)
    
    # Health is determined ONLY by external connectivity and log freshness.
    # Worker subprocess liveness is intentionally excluded — workers are
    # ephemeral (alive for seconds, sleeping for minutes) and their absence
    # between cycles is normal, not a failure signal.
    is_healthy = telegram_ok and discord_ok and log_fresh
    
    status_code = 200 if is_healthy else 503
    
    health_data = {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": last_health_check.isoformat(),
        "uptime_minutes": uptime_minutes,
        "workers": {
            "mode": "on-demand",
            "max_workers": MAX_WORKERS,
            "chunks": len(channel_chunks),
            "channels_per_chunk": [len(c) for c in channel_chunks],
            "channels": [extract_channel_name(ch) for ch in Channels]
        },
        "external_services": {
            "telegram_reachable": telegram_ok,
            "telethon_authorized": telethon_ok,
            "discord_webhook": {
                "accessible": discord_ok,
                "message": discord_msg
            }
        },
        "log_freshness": {
            "is_fresh": log_fresh,
            "message": log_msg,
            "last_modified": log_last_modified.isoformat() if log_last_modified else None,
            "age_minutes": round((datetime.datetime.now() - log_last_modified).total_seconds() / 60, 1) if log_last_modified else None
        },
        "system": system_stats,
        "git_commits": git_commit_status,
        "configuration": {
            "thread_id_configured": THREAD_ID is not None,
            "webhook_configured": WEBHOOK_URL and "{webhookID}" not in WEBHOOK_URL,
            "channels_count": len(Channels),
            "git_commits_configured": get_git_manager() is not None
        }
    }
    
    global health_status
    health_status = health_data
    
    return jsonify(health_data), status_code

@app.route('/logs')
def view_logs():
    try:
        log_file_path = "Disgram.log"
        
        if not os.path.exists(log_file_path):
            return Response(
                "Disgram.log file not found",
                status=404,
                mimetype='text/plain'
            )
        
        with open(log_file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
        last_lines = lines[-1000:] if len(lines) > 1000 else lines
        
        log_content = ''.join(last_lines)
        
        total_lines = len(lines)
        showing_lines = len(last_lines)
        header = f"Disgram Log Viewer\n"
        header += f"Total lines in log: {total_lines}\n"
        header += f"Showing last {showing_lines} lines\n"
        header += f"Log file: {os.path.abspath(log_file_path)}\n"
        header += f"Last modified: {datetime.datetime.fromtimestamp(os.path.getmtime(log_file_path)).isoformat()}\n"
        header += "=" * 80 + "\n\n"
        
        response_content = header + log_content
        
        return Response(
            response_content,
            mimetype='text/plain',
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'Cache-Control': 'no-cache'
            }
        )
        
    except Exception as e:
        return Response(
            f"Error reading log file: {str(e)}",
            status=500,
            mimetype='text/plain'
        )

@app.route('/logs/clear', methods=['POST'])
def clear_disgram_log():
    """Clear the contents of Disgram.log while preserving latest message links and warnings/errors"""
    is_valid, error_response = verify_bearer_token()
    if not is_valid:
        return error_response
        
    try:
        handler = get_disgram_handler()
        if not handler:
            return jsonify({"status": "error", "message": "DisgramLogHandler not initialized"}), 500
            
        handler.trigger_cleanup(hard=False)
        logger.info("Disgram.log cleared successfully (soft clean)")
        
        return jsonify({
            "status": "success",
            "message": "Disgram.log cleared successfully (warnings/errors preserved)"
        })
        
    except Exception as e:
        logger.error(f"Error clearing Disgram.log: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error clearing log file: {str(e)}"
        }), 500

@app.route('/logs/purge', methods=['POST'])
def purge_disgram_log():
    """Hard clear Disgram.log, preserving ONLY latest message links (dropping warnings/errors)"""
    is_valid, error_response = verify_bearer_token()
    if not is_valid:
        return error_response
        
    try:
        handler = get_disgram_handler()
        if not handler:
            return jsonify({"status": "error", "message": "DisgramLogHandler not initialized"}), 500
            
        handler.trigger_cleanup(hard=True)
        logger.info("Disgram.log purged successfully (hard clean)")
        
        return jsonify({
            "status": "success",
            "message": "Disgram.log purged successfully (only message markers preserved)"
        })
        
    except Exception as e:
        logger.error(f"Error purging Disgram.log: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error purging log file: {str(e)}"
        }), 500

@app.route('/force-commit', methods=['POST'])
def force_commit():
    """Endpoint to force immediate commit of all changes to repository"""
    is_valid, error_response = verify_bearer_token()
    if not is_valid:
        return error_response

    git_manager = get_git_manager()
    
    if not git_manager:
        return jsonify({"error": "Git manager not configured"}), 400
    
    success = git_manager.force_commit()
    if success:
        return jsonify({"message": "All changes committed to repository successfully"})
    else:
        return jsonify({"error": "Failed to commit changes to repository"}), 500

@app.route('/git-status')
def git_status():
    """Debug endpoint to check git manager status"""
    git_manager = get_git_manager()
    
    if not git_manager:
        return jsonify({
            "configured": False,
            "error": "Git manager not initialized",
            "github_token_available": bool(os.getenv("GITHUB_TOKEN")),
            "commit_interval": os.getenv("LOG_COMMIT_INTERVAL", "2700")
        })
    
    status = git_manager.get_commit_status()
    status["configured"] = True
    return jsonify(status)

@app.route('/')
def root():
    return jsonify({
        "name": "Disgram",
        "description": "Telegram to Discord messages forwarding bot",
        "health_endpoint": "/health",
        "logs_endpoint": "/logs",
        "app_logs_endpoint": "/app-logs",
        "git-status_endpoint": "/git-status",
        "channels": len(Channels),
        "status": health_status.get("status", "unknown")
    })



def run_flask_server() -> None:
    """Start the web server. Uses Waitress in production, Flask dev server otherwise."""
    port = int(os.environ.get('PORT', 5000))
    if os.environ.get('DISGRAM_ENV', '').lower() == 'production':
        logger.info(f"Starting Waitress WSGI server on port {port}...")
        from waitress import serve
        serve(app, host='0.0.0.0', port=port, threads=2)
    else:
        logger.info(f"Starting Flask dev server on port {port}...")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    import os
    
    initialize_git_manager()
    
    bot_start_time = datetime.datetime.now()
    
    # Initialize Disgram.log with channel/message links from environment
    initialize_disgram_log()
    
    # Chunk channels based on MAX_WORKERS
    channel_chunks = chunk_channels(Channels, MAX_WORKERS)
    logger.info(f"Chunked {len(Channels)} channels into {len(channel_chunks)} worker groups")
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    logger.info("Disgram bot is running with health check endpoint.")
    
    try:
        while True:
            for chunk_idx, chunk in enumerate(channel_chunks):
                channel_names = ",".join(extract_channel_name(ch) for ch in chunk)
                logger.info(f"Spawning worker {chunk_idx} for channels: {channel_names}")
                
                process = subprocess.Popen([sys.executable, "webhook.py", channel_names, str(chunk_idx)])
                process.wait()  # Wait for this chunk to finish before moving to the next
                
                exit_code = process.returncode
                if exit_code != 0:
                    logger.error(f"Worker {chunk_idx} exited with code {exit_code}")
                
            logger.info(f"Completed full cycle. Sleeping for {COOLDOWN} seconds.")
            time.sleep(COOLDOWN)
            
    except KeyboardInterrupt:
        logger.info("Shutting down bot orchestration...")
        git_manager = get_git_manager()
        if git_manager:
            logger.info("Performing final commit of all changes to repository...")
            git_manager.force_commit()
        logger.info("Shutdown complete.")
