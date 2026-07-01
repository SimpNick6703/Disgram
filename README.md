# Disgram

A Python-based application that scrapes public Telegram channel preview pages (`https://t.me/s/{channel}`) and forwards messages—including text, images, and videos—to Discord channels using Discord's modern **Components V2 (Layouts)** system.

It features native process management for scraping multiple channels concurrently, a Flask health/status server, and built-in automatic Git log persistence using either Personal Access Tokens (PATs) or GitHub Apps.

---

## Core Architecture

Disgram uses a master-worker process structure. The master process runs a status watchdog and a Flask API server, while spawning isolated worker subprocesses for each scraped Telegram channel.

### 1.1 Process Management & API Server

The master process manages lifecycle supervision for all channel subprocesses and exposes administrative API routes.

```mermaid
graph TD
    Start[Start: main.py] --> InitGit[Initialize GitLogManager]
    InitGit --> SpawnBots[Spawn Subprocesses: webhook.py per channel]
    InitGit --> SpawnFlask[Spawn Flask Server Thread]
    
    SpawnBots --> WatchdogLoop[Watchdog Loop: Every 30s]
    WatchdogLoop --> CheckHealth{Subprocesses Alive?}
    CheckHealth -- No --> RestartDead[Restart Dead Subprocess]
    RestartDead --> WatchdogLoop
    CheckHealth -- Yes --> CheckZombies{Logs Fresh?}
    CheckZombies -- No (Zombie detected) --> RestartAll[Restart All Subprocesses]
    RestartAll --> WatchdogLoop
    CheckZombies -- Yes --> WatchdogLoop

    SpawnFlask --> FlaskThread[Flask Thread: Listen on PORT]
    FlaskThread --> ServeAPI[Serve Endpoints]
    ServeAPI --> E1["GET / (Root Info Page)"]
    ServeAPI --> E2["GET /health"]
    ServeAPI --> E3["GET /logs"]
    ServeAPI --> E4["GET /app-logs"]
    ServeAPI --> E5["GET /git-status"]
    ServeAPI --> E6["POST /force-commit"]
    ServeAPI --> E7["POST /logs/clear"]

    classDef proc fill:#3b82f6,stroke:#1d4ed8,color:#fff
    classDef decis fill:#f59e0b,stroke:#b45309,color:#fff
    classDef api fill:#8b5cf6,stroke:#6d28d9,color:#fff
    class Start,InitGit,SpawnBots,SpawnFlask,WatchdogLoop,RestartDead,RestartAll,FlaskThread proc
    class CheckHealth,CheckZombies decis
    class ServeAPI,E1,E2,E3,E4,E5,E6,E7 api
```

### 1.2 Scraper & Message Forwarding Pipeline

Each channel worker scrapes content from Telegram and forwards it via Discord Layouts (with built-in failover capabilities).

```mermaid
graph TD
    Startbot[Start Subprocess] --> ScraperLoop[Scraper Loop]
    ScraperLoop --> FetchTG[Fetch Public Telegram Preview via HTTP]
    FetchTG --> ParseTG{New Message Found?}
    ParseTG -- No --> Cooldown[Wait Cooldown]
    Cooldown --> FetchTG
    
    ParseTG -- Yes --> GetMedia[Download Media Concurrently <max 8 workers>]
    GetMedia --> BuildLayout[Build Discord Component V2 Layout]
    BuildLayout --> SendWebhook[Send Webhook Message]
    
    SendWebhook --> Success{Send Successful?}
    Success -- Yes --> UpdateLog[Update Disgram.log with Marker]
    UpdateLog --> Cooldown
    
    Success -- No (Payload Too Large 413) --> ApplyFallback[Targeted Video Fallback]
    ApplyFallback --> LoopMedia[Iterate Downloaded Media]
    LoopMedia --> ExcludeVideos{Is Video & Size > 10MB?}
    ExcludeVideos -- Yes --> ReplaceWithCDN[Replace with Telegram CDN URL]
    ExcludeVideos -- No --> KeepAttachment[Keep as File Attachment]
    ReplaceWithCDN --> BuildFallbackLayout[Build Fallback Layout]
    KeepAttachment --> BuildFallbackLayout
    BuildFallbackLayout --> RetryWebhook[Retry Send Webhook]
    
    RetryWebhook --> SuccessFallback{Retry Successful?}
    SuccessFallback -- Yes --> UpdateLog
    SuccessFallback -- No --> PlainTextFallback[Final Plain Text Fallback]
    PlainTextFallback --> SuccessFinal{Final Send Successful?}
    SuccessFinal -- Yes --> UpdateLog
    SuccessFinal -- No --> LogError[Log Error]
    LogError --> Cooldown

    classDef loop fill:#10b981,stroke:#047857,color:#fff
    classDef decis fill:#f59e0b,stroke:#b45309,color:#fff
    classDef fallback fill:#ef4444,stroke:#b91c1c,color:#fff
    class Startbot,ScraperLoop,FetchTG,Cooldown,GetMedia,BuildLayout,SendWebhook,UpdateLog,BuildFallbackLayout,RetryWebhook,PlainTextFallback,LogError loop
    class ParseTG,Success,ExcludeVideos,SuccessFallback,SuccessFinal decis
    class ApplyFallback,LoopMedia,ReplaceWithCDN,KeepAttachment fallback
```

---

## Features

- **No Bot Accounts Required**: Operates using public Telegram preview pages.
- **Discord Components V2**: Utilizes the layout model (`Container`, `TextDisplay`, and `MediaGallery`) rather than legacy embeds for cleaner rendering.
- **Grouped Media Processing**: Support for up to 10 images and videos inside a single gallery component, preserved in their original visual order of appearance.
- **Unsupported Large Videos**: Automatically detects videos that are too large to play or download inline in the Telegram web preview. It extracts their durations and thumbnails, forwarding them in the gallery with a descriptive label (e.g. `Media is too big (0:17)`).
- **Document & File Placeholders**: Identifies attached documents and files from the preview, extracting their filenames and appending them as clean formatted placeholders (e.g., `-# Attached file(s): `README.md``) above the message link.
- **Collision-free Parallel Downloading**: Uses a thread pool capped at 8 workers to fetch media assets concurrently, utilizing unique filenames (timestamp, index, and random UUID chunks) to prevent concurrent download collisions in the webhook attachments.
- **Targeted Video Fallback**: If an upload fails with `413 Payload Too Large`, the bot filters out video files larger than 10MB, replacing them with their Telegram CDN links, while still uploading smaller videos and images as attachments.
- **Process Isolation**: Each channel scrapes in its own subprocess. If a subprocess crashes or hangs (zombie detection), the main watchdog restarts it.
- **Git Log Persistence**: Automatically commits and pushes log files back to GitHub periodically using **PAT** or **GitHub App** authorization.
- **Monitoring endpoints**: Health checks, real-time log viewers, git status debug tools, and log clearing via HTTP.

---

## Prerequisites

- Python 3.8 or higher
- Git (configured on host system or running environment)
- A Discord Webhook URL

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/SimpNick6703/Disgram.git
   cd Disgram
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

Duplicate `.env.example` to `.env` and fill out your variables:
```bash
cp .env.example .env
```

### 1. General & Scraper Settings
* `DISCORD_WEBHOOK_URL`: Your Discord webhook.
* `DISCORD_THREAD_ID` (Optional): ID of a specific Discord thread to forward messages into.
* `TELEGRAM_CHANNELS`: Comma-separated list of Telegram channel links.
* `EMBED_COLOR`: Color hex (e.g., `89a7d9`) for Discord layout container borders.

> [!TIP]
> **Starting from specific messages**: To begin scraping from a specific message number, append the message ID to the channel URL: `https://t.me/channel_name/1234`.

---

### 2. Git Persistence Settings
Disgram automatically commits and pushes its state and application logs back to GitHub. You can authenticate using one of two options:

#### Option A: Personal Access Token (PAT)
* `GITHUB_TOKEN`: Your personal access token with repository write scope.

#### Option B: GitHub App (Preferred)
* `GITHUB_APP_ID` or `GITHUB_APP_CLIENT_ID`: Identifies the registered GitHub App.
* `GITHUB_APP_INSTALLATION_ID`: The installation ID of the App on the target repository.
* `GITHUB_APP_PRIVATE_KEY_PATH`: Local path to the private key (`.pem` file), or...
* `GITHUB_APP_PRIVATE_KEY`: Raw multiline PEM string (e.g., `"-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"`). Ideal for cloud deployments like Azure.

* `GITHUB_REPO_URL`: The repository Git clone URL (`https://github.com/username/repo.git`).
* `GITHUB_DEPLOY_BRANCH`: The branch to push logs to (e.g., `azure-prod`).

---

### 3. Log Commits & Scheduling

You can customize how log pushes to GitHub are scheduled using two mutually exclusive modes:

#### 3.1 Scheduled Commits Flow (`COMMIT_MODE=scheduled`)

```mermaid
graph TD
    Start[Daemon Thread Check] --> CheckMode{Is COMMIT_MODE == scheduled?}
    CheckMode -- No --> Skip[Skip Scheduled Logic]
    CheckMode -- Yes --> CheckTime{Is Scheduled UTC Hour?}
    
    CheckTime -- No --> WaitNext[Wait 60s]
    WaitNext --> Start
    
    CheckTime -- Yes --> CheckGrace{Time since last commit >= 45m?}
    CheckGrace -- No --> LogCooldown[Log Cooldown Active & Skip]
    LogCooldown --> WaitNext
    
    CheckGrace -- Yes --> CheckChanges{Has Log Changes?}
    CheckChanges -- No --> LogNoChanges[Log 'No Changes' & Skip]
    LogNoChanges --> WaitNext
    
    CheckChanges -- Yes --> AuthApp[Get/Refresh GitHub Token]
    AuthApp --> GitCommit[Git Commit & Push logs to deployment branch]
    GitCommit --> UpdateTime[Update last_commit_time]
    UpdateTime --> WaitNext

    classDef proc fill:#3b82f6,stroke:#1d4ed8,color:#fff
    classDef decis fill:#f59e0b,stroke:#b45309,color:#fff
    classDef git fill:#10b981,stroke:#047857,color:#fff
    class Start,Skip,WaitNext,LogCooldown,LogNoChanges,UpdateTime proc
    class CheckMode,CheckTime,CheckGrace,CheckChanges decis
    class AuthApp,GitCommit git
```

#### 3.2 Interval Commits Flow (`COMMIT_MODE=interval`)

```mermaid
graph TD
    Start[Daemon Thread Check] --> CheckMode{Is COMMIT_MODE == interval?}
    CheckMode -- No --> Skip[Skip Interval Logic]
    CheckMode -- Yes --> CheckInterval{Time since last commit >= LOG_COMMIT_INTERVAL?}
    
    CheckInterval -- No --> WaitNext[Wait 60s]
    WaitNext --> Start
    
    CheckInterval -- Yes --> CheckChanges{Has Log Changes?}
    CheckChanges -- No --> LogNoChanges[Log 'No Changes' & Reset timer]
    LogNoChanges --> WaitNext
    
    CheckChanges -- Yes --> AuthApp[Get/Refresh GitHub Token]
    AuthApp --> GitCommit[Git Commit & Push logs to deployment branch]
    GitCommit --> UpdateTime[Update last_commit_time]
    UpdateTime --> WaitNext

    classDef proc fill:#3b82f6,stroke:#1d4ed8,color:#fff
    classDef decis fill:#f59e0b,stroke:#b45309,color:#fff
    classDef git fill:#10b981,stroke:#047857,color:#fff
    class Start,Skip,WaitNext,LogNoChanges,UpdateTime proc
    class CheckMode,CheckInterval,CheckChanges decis
    class AuthApp,GitCommit git
```

* `COMMIT_MODE`: The scheduling strategy. Options: `"interval"` (default) or `"scheduled"`.
* **Interval Settings** (if `COMMIT_MODE=interval`):
  * `LOG_COMMIT_INTERVAL`: Time in seconds between pushes (e.g. `3600` for 1 hour).
* **Scheduled Settings** (if `COMMIT_MODE=scheduled`):
  * `COMMIT_SCHEDULE`: Preset execution time (`"hourly"`, `"every_2h"`, `"custom"`).
  * `COMMIT_CUSTOM_HOURS`: Comma-separated list of UTC hours (e.g., `"0,6,12,18"` for midnight, 6 AM, noon, and 6 PM UTC).
* `STARTUP_GRACE_PERIOD`: Period in seconds (default `600` = 10 minutes) during which background commits are blocked on startup. This acts as a cooldown buffer to avoid rapid redeployment loops when deploying via a CI/CD pipeline triggered by code pushes to the same branch.

---

## Usage

1. Start the bot manually:
   ```bash
   python main.py
   ```
2. Start the bot via Azure startup script:
   ```bash
   bash start.sh
   ```
   *The startup script verifies and configures git credentials globally, sets the default branch, configures credential helpers, and launches the application.*

3. Stop the bot: Press `Ctrl + C` (which triggers a final log push to git and terminates all worker processes).

---

## Flask Server API Endpoints

The Flask server listens on `PORT` (default: `5000`):

* `GET /`: Displays repository description, status, and list of endpoints.
* `GET /health`: Health diagnostic response indicating if processes are alive and returns Discord webhook rate limit statuses.
* `GET /logs`: Displays contents of `Disgram.log` (scraped messages history).
* `GET /app-logs`: Displays `app.log` (internal system, Flask, and process manager activity).
* `GET /git-status`: Status diagnostics for git persistence (commit mode, last/next scheduled commits, elapsed time, hash, and status).
* `POST /force-commit`: Bypasses schedule cooldowns to immediately commit and push logs to the remote repository.
* `POST /logs/clear`: Wipes historical entries from `Disgram.log` but automatically extracts and preserves the latest processed message URLs for each channel as the new markers to prevent forwarding duplicate messages.

---

## Known Constraints & Issues

- **Scraping Limits**: Since logs are fetched from public preview pages (`/s/{channel}`), only public Telegram channels are supported.
- **Compressed Media Quality**: Telegram scales down image quality on its preview web server; higher-resolution assets must be extracted natively.
- **Media Exclusions**: The scraper does not support uncompressed file attachments, documents, or message replies.

---

## License

This project is licensed under the MIT License - see the [LICENSE](Disgram/LICENSE) file for details.