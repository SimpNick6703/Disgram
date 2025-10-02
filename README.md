# Disgram API

A scalable FastAPI-based Telegram-to-Discord message forwarding service

Disgram is a multi-job message forwarding bot that monitors Telegram channels and forwards messages to Discord webhooks through a RESTful API. This production-ready version supports multiple concurrent forwarding jobs, comprehensive health monitoring, and Discord-compliant rate limiting.

**Production API Branch**: [https://github.com/SimpNick6703/Disgram/tree/prod-api](https://github.com/SimpNick6703/Disgram/tree/prod-api)

## Features

- **RESTful API**: Full CRUD operations for managing forwarding jobs
- **Multi-Job Support**: Run multiple Telegram-to-Discord forwarders simultaneously
- **Authentication**: Bearer token authentication for all API endpoints
- **Health Monitoring**: Comprehensive health checks and system monitoring
- **Rate Limiting**: Discord-compliant rate limiting with bucket tracking
- **Auto-Recovery**: Automatic process restart and zombie detection
- **Thread Support**: Optional Discord thread forwarding
- **Custom Embeds**: Configurable embed colors per job
- **Detailed Logging**: Structured logging with job-specific filtering
- **Auto Documentation**: Interactive API docs with Swagger UI

## Workflow
```mermaid
flowchart TD
    A[Start: python main.py] --> B[Load FastAPI Server]
    B --> C[Initialize Job Manager]
    C --> D[Load Existing Jobs]
    D --> E[Start Health Monitoring]
    E --> F[FastAPI Server Running on Port 5000]
    
    %% API Workflow
    F --> G{API Request Received}
    G --> |POST /job| H[Create New Job]
    G --> |PATCH /job| I[Update Existing Job]
    G --> |DELETE /job| J[Delete Job]
    G --> |GET /health| K[Health Check]
    G --> |GET /logs| L[View Logs]
    
    %% Job Creation Workflow
    H --> M[Validate Job Configuration]
    M --> N[Save Job to jobs.json]
    N --> O[Start Job Processes]
    O --> P{Thread ID Configured?}
    P --> |Yes| Q[Launch threadhook.py Process]
    P --> |No| R[Launch webhook.py Process]
    
    %% Process Management
    Q --> S[Process per Channel]
    R --> S
    S --> T[Monitor Process Health Every 30s]
    T --> U{Process Alive?}
    U --> |No| V[Restart Dead Process]
    V --> S
    U --> |Yes| W[Continue Monitoring]
    W --> T
    
    %% Message Processing Workflow (webhook.py/threadhook.py)
    S --> X[Read Disgram.log for Last Message]
    X --> Y[Start Main Loop]
    Y --> Z[Scrape Telegram Preview Page]
    Z --> AA{New Message Found?}
    AA --> |No| BB[Wait for Cooldown Period]
    BB --> Z
    AA --> |Yes| CC[Parse Message Content]
    CC --> DD[Extract Text Content]
    CC --> EE[Extract Media Content]
    CC --> FF[Extract Grouped Media]
    DD --> GG[Apply Rate Limiting]
    EE --> GG
    FF --> GG
    GG --> HH[Format Content for Discord]
    HH --> II[Create Discord Embed]
    II --> JJ[Add Source Credits]
    JJ --> KK{Using Thread?}
    KK --> |Yes| LL[Send to Discord Thread]
    KK --> |No| MM[Send to Discord Channel]
    LL --> NN{Send Successful?}
    MM --> NN
    NN --> |No| OO[Apply Error Handling]
    OO --> PP[Log Error to Disgram.log]
    PP --> QQ{Retry Available?}
    QQ --> |Yes| RR[Wait and Retry]
    RR --> GG
    QQ --> |No| SS[Skip Message]
    NN --> |Yes| TT[Update Disgram.log]
    TT --> UU[Continue to Next Message]
    SS --> UU
    UU --> VV[Wait for Cooldown]
    VV --> Z
    
    %% Health Monitoring Workflow
    K --> WW[Check Process Health]
    WW --> XX[Check External Services]
    XX --> YY[Check Log Freshness]
    YY --> ZZ{All Systems Healthy?}
    ZZ --> |Yes| AAA[Return Healthy Status]
    ZZ --> |No| BBB[Return Unhealthy Status]
    BBB --> CCC{Zombie Processes Detected?}
    CCC --> |Yes| DDD[Restart All Processes]
    DDD --> S
    
    %% Job Update/Delete Workflow
    I --> EEE[Stop Existing Processes]
    EEE --> FFF[Update Job Configuration]
    FFF --> GGG[Restart Processes with New Config]
    GGG --> S
    
    J --> HHH[Stop All Job Processes]
    HHH --> III[Remove Job from Storage]
    III --> JJJ[Return Success Response]
    
    %% Shutdown Workflow
    KKK[Shutdown Signal: Ctrl+C] --> LLL[Stop All Job Processes]
    LLL --> MMM[Wait for Process Termination]
    MMM --> NNN[Cleanup Resources]
    NNN --> OOO[Exit Application]
    
    %% Styling
    classDef startEnd fill:#2E8B57,stroke:#000,stroke-width:3px,color:#fff
    classDef process fill:#4169E1,stroke:#000,stroke-width:2px,color:#fff
    classDef decision fill:#FF6B35,stroke:#000,stroke-width:3px,color:#fff
    classDef error fill:#DC143C,stroke:#000,stroke-width:3px,color:#fff
    classDef api fill:#9370DB,stroke:#000,stroke-width:2px,color:#fff
    classDef health fill:#20B2AA,stroke:#000,stroke-width:2px,color:#fff
    classDef message fill:#FFD700,stroke:#000,stroke-width:2px,color:#000
    
    class A,F,OOO startEnd
    class B,C,D,E,M,N,O,S,X,Y,Z,CC,DD,EE,FF,GG,HH,II,JJ,TT,UU,VV process
    class G,P,U,AA,KK,NN,QQ,ZZ,CCC decision
    class OO,PP,RR,SS,BBB,DDD error
    class H,I,J,EEE,FFF,GGG,HHH,III,JJJ api
    class K,WW,XX,YY,AAA health
    class LL,MM,BB,V,W,T message
```

## Quick Start

### Prerequisites

- Python 3.8+
- Discord webhook URL
- Public Telegram channels to monitor

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/SimpNick6703/Disgram.git
   cd Disgram
   git checkout prod-api
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   # Create .env file
   echo "AUTH_PASSWORD=your_secure_password_here" > .env
   echo "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook" >> .env
   ```

4. **Start the server**
   ```bash
   python main.py
   ```

The API will be available at:
- **Service**: `http://localhost:5000`
- **Interactive Docs**: `http://localhost:5000/docs`
- **Alternative Docs**: `http://localhost:5000/redoc`

## API Reference

### Authentication

All endpoints (except `/health`, `/logs`, `/`, `/jobs`) require Bearer token authentication:

```bash
Authorization: Bearer your_secure_password_here
```

### Core Endpoints

#### **GET /** - API Information
Get basic API information and current status.

```bash
curl http://localhost:5000/
```

#### **GET /health** - Health Check
Monitor system health and process status.

```bash
# System health
curl http://localhost:5000/health

# Job-specific health  
curl -H "JOB_NAME: my-job" http://localhost:5000/health
```

**Response includes:**
- Process status (running/dead processes)
- External connectivity (Telegram, Discord)
- Log freshness (zombie process detection)
- System resources (CPU, memory, disk)
- Rate limiting status

#### **GET /logs** - View Logs
Access application logs with optional job filtering.

```bash
# System logs
curl http://localhost:5000/logs

# Job-specific logs
curl -H "JOB_NAME: my-job" http://localhost:5000/logs
```

#### **GET /jobs** - List All Jobs
Get all configured forwarding jobs.

```bash
curl http://localhost:5000/jobs
```

### Job Management

#### **POST /job** - Create Job
Create a new message forwarding job.

```bash
curl -X POST http://localhost:5000/job \
  -H "Authorization: Bearer your_password" \
  -H "JOB_NAME: my-telegram-job" \
  -H "DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/123/abc" \
  -H "TELEGRAM_CHANNELS: https://t.me/channel1,https://t.me/channel2" \
  -H "EMBED_COLOR: 89a7d9" \
  -H "DISCORD_THREAD_ID: 1234567890"
```

**Required Headers:**
- `JOB_NAME`: Unique job identifier
- `DISCORD_WEBHOOK_URL`: Target Discord webhook
- `TELEGRAM_CHANNELS`: Comma-separated Telegram channels

**Optional Headers:**
- `EMBED_COLOR`: Hex color code (without #)
- `DISCORD_THREAD_ID`: Discord thread ID for threaded messages

#### **PATCH /job** - Update Job
Update an existing job configuration.

```bash
curl -X PATCH http://localhost:5000/job \
  -H "Authorization: Bearer your_password" \
  -H "JOB_NAME: my-telegram-job" \
  -H "EMBED_COLOR: ff5733"
```

**Note**: Job processes are automatically restarted after updates.

#### **DELETE /job** - Delete Job
Remove a job and stop all its processes.

```bash
curl -X DELETE http://localhost:5000/job \
  -H "Authorization: Bearer your_password" \
  -H "JOB_NAME: my-telegram-job"
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTH_PASSWORD` | API authentication token | `disgram_default_password_change_me` |
| `DISCORD_WEBHOOK_URL` | Fallback Discord webhook URL | None |
| `EMBED_COLOR` | Default embed color (hex) | Random |
| `PORT` | Server port | `5000` |

### Configuration Files

#### `config.py`
- `COOLDOWN`: Message processing interval (300 seconds recommended)
- Rate limiting and authentication settings

#### `jobs.json`
Automatically created to persist job configurations.

## Process Management

### Automatic Process Management

The system includes sophisticated process management:

- **Health Monitoring**: Checks every 30 seconds for dead processes
- **Auto-Restart**: Dead processes are automatically restarted
- **Zombie Detection**: Monitors log freshness to detect unresponsive processes
- **Graceful Shutdown**: Proper cleanup on application termination

### Process Types

1. **webhook.py**: Standard Telegram-to-Discord forwarding
2. **threadhook.py**: Thread-specific Discord forwarding

Each job spawns separate processes for each configured Telegram channel.

## Rate Limiting

### Discord Compliance

- **Per-Route Limits**: Bucket-based rate limiting per webhook
- **Global Limits**: 50 requests/second for bots
- **429 Handling**: Automatic retry with proper backoff
- **Header Parsing**: Respects Discord rate limit headers

### Telegram Compliance

- **COOLDOWN**: 5-minute interval between requests (configurable)
- **Connection Monitoring**: Automatic Telegram connectivity checks

## Advanced Usage

### Multiple Job Example

```bash
# Create job for crypto news
curl -X POST http://localhost:5000/job \
  -H "Authorization: Bearer your_password" \
  -H "JOB_NAME: crypto-news" \
  -H "DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/crypto/webhook" \
  -H "TELEGRAM_CHANNELS: https://t.me/cryptonews,https://t.me/bitcoinnews" \
  -H "EMBED_COLOR: f7931a"

# Create job for tech updates  
curl -X POST http://localhost:5000/job \
  -H "Authorization: Bearer your_password" \
  -H "JOB_NAME: tech-updates" \
  -H "DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/tech/webhook" \
  -H "TELEGRAM_CHANNELS: https://t.me/technews" \
  -H "EMBED_COLOR: 5865f2" \
  -H "DISCORD_THREAD_ID: 1234567890"
```

### Health Monitoring Script

```bash
#!/bin/bash
# health_check.sh
response=$(curl -s http://localhost:5000/health)
status=$(echo $response | jq -r '.status')

if [ "$status" != "healthy" ]; then
    echo "Alert: Disgram is unhealthy!"
    echo $response | jq .
fi
```

### Telegram Channel Formats

The system supports multiple Telegram URL formats:

- **Channel**: `https://t.me/channelname`
- **Specific Message**: `https://t.me/channelname/123` (starts from message 123)
- **Private Channel**: `https://t.me/c/channelid/123`

## Monitoring & Logging

### Log Levels

- **INFO**: General operation messages
- **ERROR**: Error conditions and failures  
- **NEW_MESSAGE**: Successfully forwarded messages
- **STATUS**: System status changes
- **SYSTEM**: Job management events

### Health Check Response

```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T12:00:00",
  "uptime_minutes": 120.5,
  "processes": {
    "total": 4,
    "running": 4
  },
  "jobs": {
    "total_jobs": 2,
    "job_names": ["crypto-news", "tech-updates"]
  },
  "external_services": {
    "telegram_reachable": true
  },
  "log_freshness": {
    "is_fresh": true,
    "message": "Log is fresh (last updated 0.5 minutes ago)",
    "age_minutes": 0.5
  },
  "system": {
    "cpu_percent": 15.2,
    "memory_percent": 45.8,
    "disk_percent": 25.1
  },
  "rate_limiting": {
    "active_buckets": 2,
    "global_requests_per_second": 1.2
  }
}
```

## Troubleshooting

### Common Issues

**1. Job Creation Fails**
```bash
# Check if job name already exists
curl http://localhost:5000/jobs

# Verify webhook URL is accessible
curl -X GET "your_webhook_url"
```

**2. Processes Not Starting**
```bash
# Check health status
curl http://localhost:5000/health

# View system logs  
curl http://localhost:5000/logs
```

**3. Rate Limiting Issues**
```bash
# Check rate limit status
curl http://localhost:5000/health | jq '.rate_limiting'

# Increase COOLDOWN in config.py
```

**4. Authentication Errors**
```bash
# Verify password in .env file
# Ensure proper Bearer token format
Authorization: Bearer your_password
```

### Log Analysis

```bash
# View recent errors
curl http://localhost:5000/logs | grep ERROR

# Monitor specific job
curl -H "JOB_NAME: your-job" http://localhost:5000/logs

# Check process health
curl http://localhost:5000/health | jq '.processes'
```

## Security Considerations

- Change default `AUTH_PASSWORD` immediately
- Use HTTPS in production environments  
- Regularly rotate authentication tokens
- Monitor logs for unauthorized access attempts
- Keep Discord webhook URLs secure

## Deployment

### Production Setup

1. **Use Process Manager**
   ```bash
   # Using systemd
   sudo systemctl enable disgram
   sudo systemctl start disgram
   
   # Using PM2
   pm2 start main.py --name disgram
   pm2 save
   ```

2. **Reverse Proxy (nginx)**
   ```nginx
   location /disgram/ {
       proxy_pass http://localhost:5000/;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
   }
   ```

3. **Environment Configuration**
   ```bash
   export PORT=5000
   export AUTH_PASSWORD="your_secure_production_password"
   ```

## Performance & Scaling

### Resource Requirements

- **CPU**: 1 core minimum, scales with number of jobs
- **Memory**: 512MB minimum, ~100MB per job
- **Disk**: 1GB for logs and job storage
- **Network**: Stable internet for Telegram/Discord APIs

### Scaling Considerations

- Each job spawns processes per Telegram channel
- Rate limiting prevents overwhelming Discord APIs
- Log rotation recommended for long-running deployments
- Monitor system resources through `/health` endpoint

## Core Components

### 1. **main.py** - FastAPI Application & Process Manager
- **FastAPI Web Server**: Serves REST API on configurable port (default: 5000)
- **Process Management**: Spawns and monitors subprocess for each Telegram channel
- **Health Monitoring**: Comprehensive system health checks and auto-recovery
- **Job Orchestration**: Coordinates job creation, updates, and deletion
- **API Endpoints**: Full CRUD operations for job management

**Key Functions:**
- `start_job_processes()`: Spawns webhook.py/threadhook.py processes per channel
- `check_process_health()`: Monitors process status and detects dead processes
- `restart_all_processes()`: Handles zombie process recovery
- `health_check()`: Provides detailed system health information

### 2. **jobs.py** - Job Management System
- **Job Storage**: Thread-safe job persistence in JSON format
- **CRUD Operations**: Create, read, update, delete forwarding jobs
- **Data Validation**: Ensures job configuration integrity

**Key Classes:**
- `Job`: Dataclass representing a forwarding job configuration
- `JobManager`: Thread-safe job management with file persistence

### 3. **auth.py** - Authentication System
- **Bearer Token Auth**: FastAPI dependency for endpoint security
- **Token Validation**: Validates Authorization headers against configured password
- **Error Handling**: Proper HTTP status codes for auth failures

### 4. **webhook.py** - Standard Message Forwarding
- **Telegram Scraping**: Monitors public Telegram channels via web scraping
- **Message Processing**: Extracts text, media, and formatting from Telegram messages
- **Discord Integration**: Sends formatted messages to Discord webhooks
- **Media Handling**: Downloads and forwards images, videos, and grouped media

**Key Functions:**
- `parse_channel_param()`: Handles different Telegram URL formats
- `get_last_message_number()`: Tracks message processing progress
- `send_message_to_discord()`: Sends formatted messages with embeds

### 5. **threadhook.py** - Thread-Specific Forwarding
- **Thread Support**: Specialized version of webhook.py for Discord threads
- **Thread Parameters**: Handles webhook URLs with thread_id parameters
- **Same Core Logic**: Inherits all scraping and processing logic from webhook.py

### 6. **rate_limiter.py** - Discord API Compliance
- **Bucket-Based Rate Limiting**: Tracks per-route rate limits using Discord's bucket system
- **Global Rate Limits**: Enforces Discord's 50 requests/second global limit
- **429 Response Handling**: Automatic retry with proper wait times
- **Header Parsing**: Extracts and respects Discord rate limit headers

**Key Classes:**
- `RateLimitBucket`: Tracks individual webhook endpoint limits
- `DiscordRateLimiter`: Comprehensive rate limiting with retry logic

### 7. **config.py** - Configuration Management
- **Environment Loading**: Uses python-dotenv for environment variable management
- **Default Values**: Provides fallback values for all configuration options
- **Rate Limiting**: Configures message processing intervals (COOLDOWN)

**Key Variables:**
- `COOLDOWN`: Telegram request interval (300 seconds recommended)
- `AUTH_PASSWORD`: API authentication token
- `EMBED_COLOR`: Default Discord embed color

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Links

- **Production API**: [https://github.com/SimpNick6703/Disgram/tree/prod-api](https://github.com/SimpNick6703/Disgram/tree/prod-api)
- **Issues**: [GitHub Issues](https://github.com/SimpNick6703/Disgram/issues)
- **Discussions**: [GitHub Discussions](https://github.com/SimpNick6703/Disgram/discussions)

---

**Made for seamless Telegram-Discord integration**