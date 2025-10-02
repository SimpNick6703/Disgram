import subprocess
import time
import threading
import datetime
import os
import psutil
import requests
import re
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Depends, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from config import COOLDOWN
from jobs import job_manager, Job
from auth import get_current_user

# Process tracking: job_name -> list of subprocess objects
job_processes = {}
bot_start_time = None
last_health_check = None
health_status = {"status": "starting", "details": {}}

# No more legacy support - API-only approach

# Pydantic models for request/response validation
class JobResponse(BaseModel):
    jobName: str
    webhookUrl: str
    telegramChannels: list[str]
    threadId: Optional[str] = None
    embedColor: Optional[str] = None

class JobCreateResponse(BaseModel):
    message: str
    job: JobResponse

class HealthResponse(BaseModel):
    status: str
    timestamp: str
    uptime_minutes: Optional[float] = None
    job_name: Optional[str] = None
    processes: Dict[str, Any]
    jobs: Optional[Dict[str, Any]] = None
    external_services: Optional[Dict[str, Any]] = None
    log_freshness: Optional[Dict[str, Any]] = None
    system: Optional[Dict[str, Any]] = None
    rate_limiting: Optional[Dict[str, Any]] = None
    configuration: Optional[Dict[str, Any]] = None

class MessageResponse(BaseModel):
    message: str

class ErrorResponse(BaseModel):
    error: str
    message: Optional[str] = None

app = FastAPI(
    title="Disgram Bot API",
    description="Multi-job Telegram to Discord message forwarding bot",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

def initialize_disgram_log():
    """Initialize log file with message links from all jobs"""
    existing_links = set()
    if os.path.exists("Disgram.log"):
        with open("Disgram.log", "r", encoding="utf-8") as log_file:
            for line in log_file:
                line = line.strip()
                if line.startswith("https://t.me/"):
                    existing_links.add(line)
    
    new_links = []
    
    # Add message links from all jobs
    jobs = job_manager.get_all_jobs()
    for job in jobs.values():
        for channel_url in job.telegramChannels:
            if channel_url not in existing_links and channel_url not in new_links:
                new_links.append(channel_url)
    
    if new_links:
        with open("Disgram.log", "a", encoding="utf-8") as log_file:
            for channel_url in new_links:
                log_file.write(f"{channel_url}\n")


def extract_channel_name(channel_url):
    """Extract channel name from Telegram URL"""
    if channel_url.startswith("https://t.me/"):
        # Handle both channel URLs and message URLs
        path = channel_url[13:]  # Remove "https://t.me/"
        channel_name = path.split("/")[0]  # Get first part (channel name)
        return channel_name
    else:
        return channel_url

def check_process_health(job_name=None):
    """
    Check process health
    If job_name is provided, check only that job's processes
    Otherwise, check all processes
    """
    if job_name:
        # Check specific job
        if job_name not in job_processes:
            return 0, [], 0
        
        alive_count = 0
        dead_indices = []
        total = len(job_processes[job_name])
        
        for i, process in enumerate(job_processes[job_name]):
            if process and process.poll() is None:
                alive_count += 1
            else:
                dead_indices.append(i)
        
        return alive_count, dead_indices, total
    else:
        # Check all job processes
        total_alive = 0
        all_dead_indices = []
        total_processes = 0
        
        for job_name, procs in job_processes.items():
            for i, process in enumerate(procs):
                total_processes += 1
                if process and process.poll() is None:
                    total_alive += 1
                else:
                    all_dead_indices.append((job_name, i))
        
        return total_alive, all_dead_indices, total_processes

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
    global job_processes, bot_start_time
    
    print("⚠️  Log freshness check failed - restarting all bot processes...")
    
    # Terminate existing job processes
    for job_name, procs in list(job_processes.items()):
        for process in procs:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception as e:
                    print(f"Error terminating process: {e}")
    
    # Clear the job processes
    job_processes.clear()
    
    # Restart all processes
    start_bot_processes()
    
    total_job_processes = sum(len(procs) for procs in job_processes.values())
    print(f"✅ Restarted {total_job_processes} bot processes due to stale logs")

@app.get("/health", response_model=HealthResponse, summary="Health Check")
def health_check(
    job_name: Optional[str] = Header(None, alias="JOB_NAME", description="Get health for specific job (optional)")
):
    """
    Get system or job-specific health status
    
    Optional headers:
    - JOB_NAME: Get health for a specific job instead of general system health
    
    Returns detailed information about:
    - Process status (running/dead)
    - External service connectivity  
    - Log freshness
    - System resources
    - Rate limiting status
    """
    global last_health_check
    last_health_check = datetime.datetime.now()
    
    if job_name:
        # Return health for specific job
        if not job_manager.job_exists(job_name):
            raise HTTPException(status_code=404, detail=f'Job "{job_name}" not found')
        
        job = job_manager.get_job(job_name)
        alive_count, dead_indices, total_processes = check_process_health(job_name)
        
        is_healthy = alive_count == total_processes and alive_count > 0
        
        return HealthResponse(
            status="healthy" if is_healthy else "unhealthy",
            timestamp=last_health_check.isoformat(),
            job_name=job_name,
            processes={
                "total": total_processes,
                "running": alive_count,
                "dead_count": len(dead_indices),
                "channels": [extract_channel_name(ch) for ch in job.telegramChannels]
            },
            configuration={
                "webhook_url": job.webhookUrl[:50] + "..." if len(job.webhookUrl) > 50 else job.webhookUrl,
                "thread_id_configured": job.threadId is not None,
                "embed_color": job.embedColor,
                "channels_count": len(job.telegramChannels)
            }
        )
    
    # Return general system health
    alive_count, dead_processes_list, total_processes = check_process_health()
    
    telegram_ok = check_telegram_connectivity()
    
    # Check log freshness (critical for detecting zombie processes)
    log_fresh, log_msg, log_last_modified = check_log_freshness()
    
    system_stats = get_system_stats()
    
    uptime_seconds = (datetime.datetime.now() - bot_start_time).total_seconds() if bot_start_time else 0
    uptime_minutes = round(uptime_seconds / 60, 2)
    
    # Get all jobs info
    all_jobs = job_manager.get_all_jobs()
    jobs_summary = {
        "total_jobs": len(all_jobs),
        "job_names": list(all_jobs.keys())
    }
    
    is_healthy = (
        alive_count == total_processes and
        alive_count > 0 and
        telegram_ok and
        log_fresh
    )
    
    # Get rate limiting status
    try:
        from rate_limiter import discord_rate_limiter
        rate_limit_status = discord_rate_limiter.get_rate_limit_status()
    except Exception as e:
        rate_limit_status = {"error": f"Failed to get rate limit status: {str(e)}"}
    
    health_data = HealthResponse(
        status="healthy" if is_healthy else "unhealthy",
        timestamp=last_health_check.isoformat(),
        uptime_minutes=uptime_minutes,
        processes={
            "total": total_processes,
            "running": alive_count
        },
        jobs=jobs_summary,
        external_services={
            "telegram_reachable": telegram_ok
        },
        log_freshness={
            "is_fresh": log_fresh,
            "message": log_msg,
            "last_modified": log_last_modified.isoformat() if log_last_modified else None,
            "age_minutes": round((datetime.datetime.now() - log_last_modified).total_seconds() / 60, 1) if log_last_modified else None
        },
        system=system_stats,
        rate_limiting=rate_limit_status
    )
    
    global health_status
    health_status = health_data.dict()
    
    return health_data

@app.get("/logs", response_class=PlainTextResponse, summary="View Logs")
def view_logs(
    job_name: Optional[str] = Header(None, alias="JOB_NAME", description="Get logs for specific job (optional)")
):
    """
    View application logs
    
    Optional headers:
    - JOB_NAME: Filter logs for a specific job
    
    Without JOB_NAME: Shows system logs (job creation/updates/deletions)
    With JOB_NAME: Shows job-specific logs (message forwarding, errors, etc.)
    
    Returns plain text log content with headers showing file info and filtering applied.
    """
    try:
        log_file_path = "Disgram.log"
        
        if not os.path.exists(log_file_path):
            raise HTTPException(status_code=404, detail="Disgram.log file not found")
        
        with open(log_file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        
        if job_name:
            # Filter logs for specific job
            if not job_manager.job_exists(job_name):
                raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found")
            
            # Filter lines that contain the job name
            filtered_lines = [line for line in lines if f"[{job_name}]" in line]
            
            # Also include channel-specific lines for this job
            job = job_manager.get_job(job_name)
            if job:
                for channel in job.telegramChannels:
                    channel_name = extract_channel_name(channel)
                    filtered_lines.extend([line for line in lines if channel_name in line and line not in filtered_lines])
            
            last_lines = filtered_lines[-1000:] if len(filtered_lines) > 1000 else filtered_lines
            
            total_lines = len(filtered_lines)
            showing_lines = len(last_lines)
            header = f"Disgram Log Viewer - Job: {job_name}\n"
            header += f"Total lines for job: {total_lines}\n"
            header += f"Showing last {showing_lines} lines\n"
            header += f"Log file: {os.path.abspath(log_file_path)}\n"
            header += f"Last modified: {datetime.datetime.fromtimestamp(os.path.getmtime(log_file_path)).isoformat()}\n"
            header += "=" * 80 + "\n\n"
            
            log_content = ''.join(last_lines)
        else:
            # Show system-level logs (job management events)
            system_lines = [line for line in lines if "[SYSTEM]" in line]
            
            last_lines = system_lines[-1000:] if len(system_lines) > 1000 else system_lines
            
            total_lines = len(system_lines)
            showing_lines = len(last_lines)
            header = f"Disgram Log Viewer - System Logs\n"
            header += f"Total system log lines: {total_lines}\n"
            header += f"Showing last {showing_lines} lines\n"
            header += f"Log file: {os.path.abspath(log_file_path)}\n"
            header += f"Last modified: {datetime.datetime.fromtimestamp(os.path.getmtime(log_file_path)).isoformat()}\n"
            header += f"Tip: Use JOB_NAME header to view job-specific logs\n"
            header += f"Available at: /docs for interactive API documentation\n"
            header += "=" * 80 + "\n\n"
            
            log_content = ''.join(last_lines)
        
        return PlainTextResponse(
            content=header + log_content,
            headers={'Cache-Control': 'no-cache'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log file: {str(e)}")

@app.get("/", summary="API Information")
def root():
    """Get basic information about the Disgram bot API"""
    return {
        "name": "Disgram",
        "description": "Multi-job Telegram to Discord message forwarding bot",
        "version": "2.0.0",
        "documentation": "/docs",
        "health_endpoint": "/health",
        "logs_endpoint": "/logs", 
        "job_endpoints": {
            "create": "POST /job",
            "update": "PATCH /job", 
            "delete": "DELETE /job"
        },
        "jobs_count": len(job_manager.get_all_jobs()),
        "status": health_status.get("status", "unknown")
    }

@app.post("/job", response_model=JobCreateResponse, status_code=201, summary="Create Job")
def create_job(
    job_name: str = Header(..., alias="JOB_NAME", description="Unique name for the job"),
    webhook_url: str = Header(..., alias="DISCORD_WEBHOOK_URL", description="Discord webhook URL"),
    telegram_channels: str = Header(..., alias="TELEGRAM_CHANNELS", description="Comma-separated Telegram channels"),
    embed_color: Optional[str] = Header(None, alias="EMBED_COLOR", description="Hex color code (e.g., 89a7d9)"),
    thread_id: Optional[str] = Header(None, alias="DISCORD_THREAD_ID", description="Discord thread ID"),
    authenticated: bool = Depends(get_current_user)
):
    """
    Create a new forwarding job
    
    Required headers:
    - Authorization: Bearer <token>
    - JOB_NAME: Unique identifier for the job
    - DISCORD_WEBHOOK_URL: Discord webhook URL
    - TELEGRAM_CHANNELS: Comma-separated list of Telegram channels/links
    
    Optional headers:
    - EMBED_COLOR: Hex color code without # (e.g., "89a7d9")
    - DISCORD_THREAD_ID: Discord thread ID for threaded messages
    """
    try:
        # Parse telegram channels (comma-separated)
        channels_list = [ch.strip() for ch in telegram_channels.split(',') if ch.strip()]
        if not channels_list:
            raise HTTPException(status_code=400, detail="At least one Telegram channel is required")
        
        # Create job object
        job = Job(
            jobName=job_name,
            webhookUrl=webhook_url,
            telegramChannels=channels_list,
            threadId=thread_id if thread_id else None,
            embedColor=embed_color if embed_color else None
        )
        
        # Add job
        success, message = job_manager.add_job(job)
        
        if success:
            # Start processes for this job
            start_job_processes(job)
            return JobCreateResponse(
                message=message,
                job=JobResponse(**job.to_dict())
            )
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to create job: {str(e)}')

@app.patch("/job", response_model=JobCreateResponse, summary="Update Job")  
def update_job(
    job_name: str = Header(..., alias="JOB_NAME", description="Name of the job to update"),
    webhook_url: Optional[str] = Header(None, alias="DISCORD_WEBHOOK_URL", description="New Discord webhook URL"),
    telegram_channels: Optional[str] = Header(None, alias="TELEGRAM_CHANNELS", description="New comma-separated Telegram channels"),
    embed_color: Optional[str] = Header(None, alias="EMBED_COLOR", description="New hex color code (empty to clear)"),
    thread_id: Optional[str] = Header(None, alias="DISCORD_THREAD_ID", description="New Discord thread ID (empty to clear)"),
    authenticated: bool = Depends(get_current_user)
):
    """
    Update an existing job
    
    Required headers:
    - Authorization: Bearer <token>
    - JOB_NAME: Name of the job to update
    
    Optional headers (at least one required):
    - DISCORD_WEBHOOK_URL: New webhook URL
    - TELEGRAM_CHANNELS: New comma-separated channels
    - EMBED_COLOR: New color (empty string to clear)
    - DISCORD_THREAD_ID: New thread ID (empty string to clear)
    
    The job's processes will be automatically restarted.
    """
    try:
        # Check if job exists
        if not job_manager.job_exists(job_name):
            raise HTTPException(status_code=404, detail=f'Job "{job_name}" not found')
        
        # Collect updates
        updates = {}
        
        if webhook_url is not None:
            updates['webhookUrl'] = webhook_url
        
        if telegram_channels is not None:
            channels_list = [ch.strip() for ch in telegram_channels.split(',') if ch.strip()]
            if channels_list:
                updates['telegramChannels'] = channels_list
        
        if embed_color is not None:  # Allow empty string to clear
            updates['embedColor'] = embed_color if embed_color else None
        
        if thread_id is not None:  # Allow empty string to clear
            updates['threadId'] = thread_id if thread_id else None
        
        if not updates:
            raise HTTPException(status_code=400, detail='No valid updates provided')
        
        # Update job
        success, message = job_manager.update_job(job_name, updates)
        
        if success:
            # Restart processes for this job
            stop_job_processes(job_name)
            job = job_manager.get_job(job_name)
            if job:
                start_job_processes(job)
            
            return JobCreateResponse(
                message=message,
                job=JobResponse(**job.to_dict()) if job else None
            )
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to update job: {str(e)}')

@app.delete("/job", response_model=MessageResponse, summary="Delete Job")
def delete_job(
    job_name: str = Header(..., alias="JOB_NAME", description="Name of the job to delete"),
    authenticated: bool = Depends(get_current_user)
):
    """
    Delete a job and stop all its processes
    
    Required headers:
    - Authorization: Bearer <token>
    - JOB_NAME: Name of the job to delete
    
    This will immediately stop all processes associated with the job
    and remove it from the job storage.
    """
    try:
        # Check if job exists
        if not job_manager.job_exists(job_name):
            raise HTTPException(status_code=404, detail=f'Job "{job_name}" not found')
        
        # Stop processes for this job
        stop_job_processes(job_name)
        
        # Delete job
        success, message = job_manager.delete_job(job_name)
        
        if success:
            return MessageResponse(message=message)
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to delete job: {str(e)}')

@app.get("/jobs", response_model=Dict[str, JobResponse], summary="List All Jobs")
def list_jobs():
    """
    Get a list of all configured jobs
    
    Returns a dictionary mapping job names to their configurations.
    Useful for seeing all active forwarding jobs at a glance.
    """
    all_jobs = job_manager.get_all_jobs()
    return {name: JobResponse(**job.to_dict()) for name, job in all_jobs.items()}

def start_job_processes(job):
    """Start processes for a specific job"""
    global job_processes
    
    if job.jobName in job_processes:
        print(f"Warning: Job '{job.jobName}' already has running processes")
        return
    
    job_processes[job.jobName] = []
    
    print(f"Starting processes for job '{job.jobName}' with {len(job.telegramChannels)} channels...")
    
    try:
        for channel_url in job.telegramChannels:
            # For message links like https://t.me/channel/123, pass the full URL
            # For channel links like https://t.me/channel, extract just the channel name
            if "/s/" in channel_url or (channel_url.count("/") == 4 and channel_url.split("/")[-1].isdigit()):
                # This is a specific message link - pass the full URL
                channel_param = channel_url
                print(f"Starting process for message link: {channel_url}")
            else:
                # This is a channel URL - extract channel name
                channel_param = extract_channel_name(channel_url)
                print(f"Starting process for channel: {channel_param}")
            
            # Determine which script to use
            if job.threadId:
                # Use threadhook for thread-based forwarding
                webhook_with_thread = f"{job.webhookUrl}?thread_id={job.threadId}"
                process = subprocess.Popen(
                    ["python", "threadhook.py", channel_param, webhook_with_thread, 
                     job.embedColor or "", job.jobName],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            else:
                # Use webhook for regular forwarding
                process = subprocess.Popen(
                    ["python", "webhook.py", channel_param, job.webhookUrl,
                     job.embedColor or "", job.jobName],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
            job_processes[job.jobName].append(process)
        
        print(f"Started {len(job_processes[job.jobName])} processes for job '{job.jobName}'")
        
    except Exception as e:
        print(f"Error starting processes for job '{job.jobName}': {e}")

def stop_job_processes(job_name):
    """Stop all processes for a specific job"""
    global job_processes
    
    if job_name not in job_processes:
        return
    
    print(f"Stopping processes for job '{job_name}'...")
    
    for process in job_processes[job_name]:
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception as e:
                print(f"Error stopping process: {e}")
    
    del job_processes[job_name]
    print(f"Stopped all processes for job '{job_name}'")

def start_bot_processes():
    global bot_start_time, job_processes
    bot_start_time = datetime.datetime.now()
    
    # Initialize Disgram.log with message links from all jobs
    initialize_disgram_log()
    
    # Start processes for all configured jobs
    jobs = job_manager.get_all_jobs()
    if jobs:
        print(f"Starting Disgram bot with {len(jobs)} job(s)...")
        for job in jobs.values():
            start_job_processes(job)
        print(f"Started all job processes successfully.")
    else:
        print("No jobs configured. Use the API at /docs to create jobs.")
        print("FastAPI server will start for job management.")

def run_fastapi_server():
    import uvicorn
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting FastAPI server on port {port}...")
    print(f"API documentation available at: http://localhost:{port}/docs")
    print(f"Alternative docs at: http://localhost:{port}/redoc")
    uvicorn.run(app, host='0.0.0.0', port=port, log_level="info")

if __name__ == "__main__":
    import os
    
    start_bot_processes()
    
    fastapi_thread = threading.Thread(target=run_fastapi_server, daemon=True)
    fastapi_thread.start()
    
    print("Disgram bot is running with FastAPI server.")
    print("Interactive API docs available at: /docs")
    print("Health check available at: /health")
    print("Log viewer available at: /logs")
    
    try:
        while True:
            time.sleep(30)
            
            # Check and restart dead processes for each job
            for job_name in list(job_processes.keys()):
                job = job_manager.get_job(job_name)
                if not job:
                    # Job was deleted, processes should already be stopped
                    if job_name in job_processes:
                        del job_processes[job_name]
                    continue
                
                alive_count, dead_indices, total = check_process_health(job_name)
                
                if dead_indices:
                    print(f"Detected {len(dead_indices)} dead process(es) for job '{job_name}', restarting...")
                    
                    for dead_idx in dead_indices:
                        if dead_idx < len(job_processes[job_name]) and dead_idx < len(job.telegramChannels):
                            channel = job.telegramChannels[dead_idx]
                            channel_name = extract_channel_name(channel)
                            print(f"Restarting process for channel {channel_name} in job '{job_name}'...")
                            
                            if job.threadId:
                                webhook_with_thread = f"{job.webhookUrl}?thread_id={job.threadId}"
                                new_process = subprocess.Popen(
                                    ["python", "threadhook.py", channel_name, webhook_with_thread,
                                     job.embedColor or "", job_name],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE
                                )
                            else:
                                new_process = subprocess.Popen(
                                    ["python", "webhook.py", channel_name, job.webhookUrl,
                                     job.embedColor or "", job_name],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE
                                )
                            
                            job_processes[job_name][dead_idx] = new_process
            
            # Get overall health for zombie detection
            alive_count, _, total = check_process_health()
            
            # Check if logs are stale (indicates zombie processes)
            log_fresh, log_msg, log_last_modified = check_log_freshness()
            if not log_fresh and alive_count > 0:  # Only restart if we have processes that should be working
                print(f"🚨 ZOMBIE PROCESSES DETECTED: {log_msg}")
                print("All processes appear alive but logs are stale - restarting all processes...")
                restart_all_processes()
            
    except KeyboardInterrupt:
        print("\nShutting down all bots...")
        
        # Shutdown job processes
        for job_name, procs in job_processes.items():
            for process in procs:
                if process and process.poll() is None:
                    process.terminate()
        
        for job_name, procs in job_processes.items():
            for process in procs:
                if process:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
        
        print("All bots have been stopped.")