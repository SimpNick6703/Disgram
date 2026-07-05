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
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, Response
from config import Channels, WEBHOOK_URL, THREAD_ID, COOLDOWN
from git_manager import initialize_git_manager

def get_git_manager():
    """Get the current git_log_manager instance (avoids import stale reference issue)"""
    from git_manager import git_log_manager
    return git_log_manager

# Configure logging for the main application
def setup_logging():
    """Configure logging to write to app.log and console"""
    # Only configure if not already configured
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('app.log', encoding='utf-8', mode='a'),
                logging.StreamHandler()
            ]
        )

# Setup logging
setup_logging()
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

processes = []
bot_start_time = None
last_health_check = None
health_status = {"status": "starting", "details": {}}

app = Flask(__name__)

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

def check_process_health():
    alive_processes = 0
    dead_processes = []
    
    for i, process in enumerate(processes):
        if process and process.poll() is None:
            alive_processes += 1
        else:
            dead_processes.append(i)
    
    return alive_processes, dead_processes

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
    "discord_msg": "Not checked yet"
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
            
            _ext_check_cache.update({
                "last_check_time": current_time,
                "telegram_ok": telegram_ok,
                "discord_ok": discord_ok,
                "discord_msg": discord_msg
            })
        
        return _ext_check_cache["telegram_ok"], _ext_check_cache["discord_ok"], _ext_check_cache["discord_msg"]

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
        
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_mb": round(memory.available / 1024 / 1024, 2),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2)
        }
    except Exception as e:
        return {"error": str(e)}

def restart_all_processes():
    """Restart all bot processes when they appear to be zombified"""
    global processes, bot_start_time
    
    print("⚠️  Log freshness check failed - restarting all bot processes...")
    
    # Terminate existing processes
    for process in processes:
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception as e:
                print(f"Error terminating process: {e}")
    
    # Clear the processes list
    processes.clear()
    
    # Restart all processes
    start_bot_processes()
    
    print(f"✅ Restarted {len(processes)} bot processes due to stale logs")

@app.route('/health')
def health_check():
    global last_health_check
    last_health_check = datetime.datetime.now()
    
    # Define wrapper functions for async execution
    def get_process_health():
        return check_process_health()
    
    def get_external_status():
        return get_cached_external_checks()
    
    def get_log_status():
        return check_log_freshness()
    
    def get_sys_stats():
        return get_system_stats()
    
    def get_git_status():
        git_manager = get_git_manager()
        return git_manager.get_commit_status() if git_manager else {"git_available": False}
    
    # Execute all checks in parallel using ThreadPoolExecutor (max_workers=5)
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all tasks
        future_process_health = executor.submit(get_process_health)
        future_external = executor.submit(get_external_status)
        future_log = executor.submit(get_log_status)
        future_system = executor.submit(get_sys_stats)
        future_git = executor.submit(get_git_status)
        
        # Collect results
        alive_count, dead_processes = future_process_health.result()
        telegram_ok, discord_ok, discord_msg = future_external.result()
        log_fresh, log_msg, log_last_modified = future_log.result()
        system_stats = future_system.result()
        git_commit_status = future_git.result()
    
    total_processes = len(processes)
    uptime_seconds = (datetime.datetime.now() - bot_start_time).total_seconds() if bot_start_time else 0
    uptime_minutes = round(uptime_seconds / 60, 2)
    
    is_healthy = (
        alive_count == total_processes and
        alive_count > 0 and
        telegram_ok and
        discord_ok and
        log_fresh
    )
    
    status_code = 200 if is_healthy else 503
    
    health_data = {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": last_health_check.isoformat(),
        "uptime_minutes": uptime_minutes,
        "processes": {
            "total": total_processes,
            "running": alive_count,
            "dead": dead_processes,
            "channels": [extract_channel_name(ch) for ch in Channels]
        },
        "external_services": {
            "telegram_reachable": telegram_ok,
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
    """Clear the contents of Disgram.log while preserving latest message links for each channel"""
    try:
        log_file_path = "Disgram.log"
        
        if not os.path.exists(log_file_path):
            return jsonify({
                "status": "error",
                "message": "Disgram.log file not found"
            }), 404
        
        # Read the file and extract the latest message link for each channel
        header_line = None
        latest_messages = {}  # Dictionary to store latest message per channel
        
        with open(log_file_path, 'r', encoding='utf-8') as log_file:
            for line in log_file:
                line_stripped = line.strip()
                # Preserve the header line
                if "Add your message links" in line_stripped:
                    header_line = line_stripped
                
                # Extract all message links from the line (from log entries)
                matches = re.findall(r'https://t\.me/([^/\s]+)/(\d+)', line)
                for channel, msg_num in matches:
                    msg_num = int(msg_num)
                    # Keep only the latest message number for each channel
                    if channel not in latest_messages or msg_num > latest_messages[channel]:
                        latest_messages[channel] = msg_num
        
        # Convert to sorted list of full URLs
        channel_links = [f"https://t.me/{channel}/{msg_num}" 
                        for channel, msg_num in sorted(latest_messages.items())]
        
        # Write back only the header and latest channel links
        with open(log_file_path, 'w', encoding='utf-8') as log_file:
            if header_line:
                log_file.write(f"{header_line}\n")
            for link in channel_links:
                log_file.write(f"{link}\n")
        
        logger.info(f"Disgram.log cleared successfully, preserving {len(channel_links)} latest channel links")
        
        return jsonify({
            "status": "success",
            "message": "Disgram.log cleared successfully",
            "preserved_links": len(channel_links),
            "latest_messages": channel_links
        })
        
    except Exception as e:
        logger.error(f"Error clearing Disgram.log: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error clearing log file: {str(e)}"
        }), 500

@app.route('/app-logs')
def view_app_logs():
    """View application logs (app.log)"""
    try:
        log_file_path = "app.log"
        
        if not os.path.exists(log_file_path):
            return Response(
                "app.log file not found",
                status=404,
                mimetype='text/plain'
            )
        
        with open(log_file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
        # Show last 500 lines for application logs
        last_lines = lines[-500:] if len(lines) > 500 else lines
        
        # Sanitize log content to remove sensitive information
        log_content = sanitize_log_content(''.join(last_lines))
        
        total_lines = len(lines)
        showing_lines = len(last_lines)
        header = f"Disgram Application Log Viewer\n"
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
            f"Error reading application log file: {str(e)}",
            status=500,
            mimetype='text/plain'
        )

@app.route('/force-commit', methods=['POST'])
def force_commit():
    """Endpoint to force immediate commit of all changes to repository"""
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

def start_bot_processes():
    global bot_start_time, processes
    bot_start_time = datetime.datetime.now()
    
    # Initialize Disgram.log with channel/message links from environment
    initialize_disgram_log()
    
    print(f"Starting Disgram bot with {len(Channels)} channels...")
    
    try:
        for channel in Channels:
            print(f"Starting bot for {channel}...")
            channel_name = extract_channel_name(channel)
            process = subprocess.Popen(
                [sys.executable, "webhook.py", channel_name]
            )
            processes.append(process)
        
        print(f"Started {len(processes)} bot processes successfully.")
        
    except Exception as e:
        print(f"Error starting bot processes: {e}")

def run_flask_server():
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting health check server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    import os
    
    # Initialize Git manager first
    initialize_git_manager()
    
    start_bot_processes()
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    logger.info("Disgram bot is running with health check endpoint.")
    logger.info("Health check available at: /health")
    logger.info("Message log viewer available at: /logs")
    logger.info("Application log viewer available at: /app-logs") 
    logger.info("Force commit (all changes) available at: /force-commit")
    
    try:
        while True:
            time.sleep(30)
            
            alive_count, dead_processes = check_process_health()
            if dead_processes:
                print(f"Detected {len(dead_processes)} dead processes, restarting...")
                for dead_idx in dead_processes:
                    if dead_idx < len(processes) and dead_idx < len(Channels):
                        channel = Channels[dead_idx]
                        channel_name = extract_channel_name(channel)
                        print(f"Restarting process for {channel}...")
                        
                        new_process = subprocess.Popen(
                            [sys.executable, "webhook.py", channel_name]
                        )
                        processes[dead_idx] = new_process
            
            # Check if logs are stale (indicates zombie processes)
            log_fresh, log_msg, log_last_modified = check_log_freshness()
            if not log_fresh and alive_count > 0:  # Only restart if we have processes that should be working
                print(f"🚨 ZOMBIE PROCESSES DETECTED: {log_msg}")
                print("All processes appear alive but logs are stale - restarting all processes...")
                restart_all_processes()
            
    except KeyboardInterrupt:
        print("\nShutting down all bots...")
        
        # Force final commit before shutdown
        git_manager = get_git_manager()
        if git_manager:
            logger.info("Performing final commit of all changes to repository...")
            git_manager.force_commit()
        
        for process in processes:
            if process and process.poll() is None:
                process.terminate()
        
        for process in processes:
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        

        print("All bots have been stopped.")
