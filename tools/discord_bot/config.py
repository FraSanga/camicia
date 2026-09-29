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

DISCORD_VOLUNTEER_ROLE_ID_RAW = os.getenv("DISCORD_VOLUNTEER_ROLE_ID", "").strip()
DISCORD_VOLUNTEER_ROLE_ID = (
    int(DISCORD_VOLUNTEER_ROLE_ID_RAW) if DISCORD_VOLUNTEER_ROLE_ID_RAW.isdigit() else 0
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

# MariaDB configuration
DB_HOST = os.getenv("MARIADB_HOST", os.getenv("DATABASE_CONTAINER_NAME", "database"))
DB_PORT = int(os.getenv("MARIADB_PORT", "3306"))
DB_USER = os.getenv("MARIADB_USER", "")
DB_PASSWD = os.getenv("MARIADB_PASSWORD", "")
DB_NAME = os.getenv("MARIADB_DATABASE", "camicia")

# Fallback 1: Read database credentials directly from project config.xml if available
config_xml = CAMICIA_PROJECT_DIR / "config.xml"
if config_xml.exists():
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(config_xml)
        cfg = tree.getroot().find("config")
        if cfg is not None:
            xml_host = cfg.findtext("db_host")
            xml_user = cfg.findtext("db_user")
            xml_pass = cfg.findtext("db_passwd")
            xml_name = cfg.findtext("db_name")
            if not DB_PASSWD and xml_pass:
                DB_PASSWD = xml_pass
            if not DB_USER and xml_user:
                DB_USER = xml_user
            if (not DB_HOST or DB_HOST in ("127.0.0.1", "localhost")) and xml_host:
                DB_HOST = xml_host
            if (not DB_NAME or DB_NAME == "camicia") and xml_name:
                DB_NAME = xml_name
    except Exception:
        pass

# Fallback 2: Check MARIADB_ROOT_PASSWORD from environment
if not DB_PASSWD:
    DB_PASSWD = os.getenv("MARIADB_ROOT_PASSWORD", "")
    if DB_PASSWD and not DB_USER:
        DB_USER = "root"

if not DB_USER:
    DB_USER = "root"
