import os
from pathlib import Path
from dotenv import load_dotenv

# Search for .env in current dir, or repo root
BOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = BOT_DIR.parent.parent

# Load environment
if (REPO_ROOT / ".env").exists():
    load_dotenv(REPO_ROOT / ".env")
elif (BOT_DIR / ".env").exists():
    load_dotenv(BOT_DIR / ".env")
else:
    load_dotenv()

# Discord configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID_RAW = os.getenv("DISCORD_GUILD_ID", "").strip()
DISCORD_GUILD_ID = int(DISCORD_GUILD_ID_RAW) if DISCORD_GUILD_ID_RAW.isdigit() else None

DISCORD_RECORDS_CHANNEL_ID_RAW = os.getenv("DISCORD_RECORDS_CHANNEL_ID", "").strip()
DISCORD_RECORDS_CHANNEL_ID = (
    int(DISCORD_RECORDS_CHANNEL_ID_RAW) if DISCORD_RECORDS_CHANNEL_ID_RAW.isdigit() else 0
)

DISCORD_BOT_COMMANDS_CHANNEL_ID_RAW = os.getenv("DISCORD_BOT_COMMANDS_CHANNEL_ID", "").strip()
DISCORD_BOT_COMMANDS_CHANNEL_ID = (
    int(DISCORD_BOT_COMMANDS_CHANNEL_ID_RAW) if DISCORD_BOT_COMMANDS_CHANNEL_ID_RAW.isdigit() else 0
)

# Project path resolution (where records_longest_history.txt / records_loops.txt reside)
candidate_paths = [
    os.getenv("CAMICIA_PROJECT_DIR", "").strip(),
    "/data/project",  # Inside Docker container
    str(REPO_ROOT / "projects" / "camicia"),
    str(REPO_ROOT / "projects"),
    str(BOT_DIR.parent.parent / "projects" / "camicia"),
]

CAMICIA_PROJECT_DIR = Path(".")
for cand in candidate_paths:
    if cand and Path(cand).exists():
        CAMICIA_PROJECT_DIR = Path(cand)
        break

# Poll interval in seconds
POLL_INTERVAL_SECONDS = float(os.getenv("WATCHER_POLL_INTERVAL_SECONDS", "5.0"))

# State file location
state_file_env = os.getenv("WATCHER_STATE_FILE", "").strip()
if state_file_env:
    WATCHER_STATE_FILE = Path(state_file_env)
else:
    WATCHER_STATE_FILE = BOT_DIR / "watcher_state.json"

lucky_state_env = os.getenv("LUCKY_STATE_FILE", "").strip()
if lucky_state_env:
    LUCKY_STATE_FILE = Path(lucky_state_env)
else:
    LUCKY_STATE_FILE = WATCHER_STATE_FILE.parent / "lucky_state.json"

# Real-world record benchmark (Nessler 2022, 8344 cards / 1164 tricks)
REAL_WORLD_RECORD_CARDS = 8344

# Project domain & branding
PROJECT_DOMAIN = os.getenv("PROJECT_DOMAIN", os.getenv("DOMAIN", "camicia.dev")).strip()
if not PROJECT_DOMAIN or PROJECT_DOMAIN in ("127.0.0.1", "localhost"):
    PROJECT_DOMAIN = "camicia.dev"
PROJECT_ICON_URL = f"https://{PROJECT_DOMAIN}/favicon_round.png"

# MariaDB (optional user lookup)
DB_HOST = os.getenv("MARIADB_HOST", os.getenv("DATABASE_CONTAINER_NAME", "127.0.0.1"))
DB_PORT = int(os.getenv("MARIADB_PORT", "3306"))
DB_USER = os.getenv("MARIADB_USER", "boincadm")
DB_PASSWD = os.getenv("MARIADB_PASSWORD", "")
DB_NAME = os.getenv("MARIADB_DATABASE", "camicia")
