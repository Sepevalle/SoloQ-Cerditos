"""
Configuracion centralizada del proyecto SoloQ-Cerditos.
Todas las variables de entorno y configuraciones globales se definen aqui.
"""

import os
from datetime import datetime, timedelta, timezone


def _env_flag(name, default=False):
    """Lee flags booleanas desde variables de entorno."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "si")


# ============================================================================
# CONFIGURACION DE API KEYS
# ============================================================================

RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
RIOT_API_KEY_2 = os.environ.get("RIOT_API_KEY_2", RIOT_API_KEY)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no esta configurada en las variables de entorno.")


# ============================================================================
# URLS BASE DE APIs
# ============================================================================

BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_EUROPE = "https://europe.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"


# ============================================================================
# CONFIGURACION DE GITHUB
# ============================================================================

GITHUB_REPO = "Sepevalle/SoloQ-Cerditos"
LP_HISTORY_FILE_PATH = "lp_history.json"
ACHIEVEMENTS_CONFIG_PATH = "config/logros/achievements_config.json"


# ============================================================================
# CONFIGURACION DE ENTORNO / RENDER
# ============================================================================

IS_RENDER = any(
    os.environ.get(var)
    for var in (
        "RENDER",
        "RENDER_SERVICE_ID",
        "RENDER_INSTANCE_ID",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
    )
)
LOW_MEMORY_MODE = _env_flag("LOW_MEMORY_MODE", default=IS_RENDER)


# ============================================================================
# CONFIGURACION DE ZONA HORARIA
# ============================================================================

TARGET_TIMEZONE = timezone(timedelta(hours=2))


# ============================================================================
# CONFIGURACION DE SPLITS/TEMPORADAS
# ============================================================================

SPLITS = {
    "s16_split1": {
        "name": "Temporada 2026 - Split 1",
        "start_date": datetime(2026, 1, 8, tzinfo=timezone.utc),
    }
}

ACTIVE_SPLIT_KEY = "s16_split1"
SEASON_START_TIMESTAMP = int(SPLITS[ACTIVE_SPLIT_KEY]["start_date"].timestamp())


# ============================================================================
# CONFIGURACION DE CACHES
# ============================================================================

# Cache principal de jugadores
CACHE_TIMEOUT = 300

# Cache de estadisticas globales
GLOBAL_STATS_UPDATE_INTERVAL = 86400

# Cache de Peak ELO
PEAK_ELO_TTL = 300

# Cache de historial de partidas
PLAYER_MATCH_HISTORY_CACHE_TIMEOUT = 180 if LOW_MEMORY_MODE else 300
PLAYER_MATCH_HISTORY_CACHE_MAX_SIZE = 4 if LOW_MEMORY_MODE else 15
PLAYER_MATCH_HISTORY_CACHE_MAX_MATCHES = 120 if LOW_MEMORY_MODE else 400

# Cache de records personales
PERSONAL_RECORDS_UPDATE_INTERVAL = 3600

# Cache de LP history
LP_HISTORY_TTL = 300

# Caches genericos / perfiles / paginas
PROFILE_CACHE_TTL = 45 if LOW_MEMORY_MODE else 120
PROFILE_CACHE_MAX_SIZE = 2 if LOW_MEMORY_MODE else 64
PAGE_DATA_CACHE_TTL = 60 if LOW_MEMORY_MODE else 180
PAGE_DATA_CACHE_MAX_SIZE = 4 if LOW_MEMORY_MODE else 32
MATCH_LOOKUP_CACHE_TTL = 300 if LOW_MEMORY_MODE else 900
MATCH_LOOKUP_CACHE_MAX_SIZE = 1000 if LOW_MEMORY_MODE else 5000
PLAYER_STATS_CACHE_TTL = 180 if LOW_MEMORY_MODE else 300
PLAYER_STATS_CACHE_MAX_SIZE = 64 if LOW_MEMORY_MODE else 256
LIVE_GAME_CACHE_TTL = 180 if LOW_MEMORY_MODE else 300


# ============================================================================
# CONFIGURACION DE RATE LIMITING
# ============================================================================

API_RATE_PER_SECOND = 20
API_BURST_LIMIT = 100
API_RESPONSE_CLEANUP_THRESHOLD = 50


# ============================================================================
# CONFIGURACION DE SNAPSHOTS LP
# ============================================================================

LP_SNAPSHOTS_SAVE_INTERVAL = 3600


# ============================================================================
# CONFIGURACION DE DATA DRAGON
# ============================================================================

# Esta version se actualiza automaticamente al iniciar la aplicacion
# mediante la funcion actualizar_version_ddragon() en services/riot_api.py
DDRAGON_VERSION = "16.3.1"


# ============================================================================
# CONFIGURACION DEL SERVIDOR
# ============================================================================

PORT = int(os.environ.get("PORT", 5000))
HOST = "0.0.0.0"
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")
SECRET_KEY = os.environ.get("SECRET_KEY", "soloq-cerditos-dev-key-change-in-production")


# ============================================================================
# MAPEO DE COLAS
# ============================================================================

QUEUE_NAMES = {
    400: "Normal (Blind Pick)",
    420: "Clasificatoria Solo/Duo",
    430: "Normal (Draft Pick)",
    440: "Clasificatoria Flexible",
    450: "ARAM",
    700: "Clash",
    800: "Co-op vs. AI (Beginner)",
    810: "Co-op vs. AI (Intermediate)",
    820: "Co-op vs. AI (Intro)",
    830: "Co-op vs. AI (Twisted Treeline)",
    840: "Co-op vs. AI (Summoner's Rift)",
    850: "Co-op vs. AI (ARAM)",
    900: "URF",
    1020: "One For All",
    1090: "Arena",
    1100: "Arena",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1900: "URF (ARAM)",
    2000: "Tutorial",
    2010: "Tutorial",
    2020: "Tutorial",
}

QUEUE_TYPE_MAP = {
    420: "RANKED_SOLO_5x5",
    440: "RANKED_FLEX_SR",
}


# ============================================================================
# MAPEO DE RANGOS ELO
# ============================================================================

TIER_ORDER = {
    "DIAMOND": 6,
    "EMERALD": 5,
    "PLATINUM": 4,
    "GOLD": 3,
    "SILVER": 2,
    "BRONZE": 1,
    "IRON": 0,
}

RANK_ORDER = {
    "I": 3,
    "II": 2,
    "III": 1,
    "IV": 0,
}


# ============================================================================
# CONFIGURACION DE GEMINI AI
# ============================================================================

GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
GEMINI_MODEL = GEMINI_MODELS[0]


# ============================================================================
# INTERVALOS DE ACTUALIZACION (para hilos de background)
# ============================================================================

CACHE_UPDATE_INTERVAL = 130
LP_TRACKER_INTERVAL = 300


# ============================================================================
# CONFIGURACION DE ACTUALIZACION DE HISTORIAL DE PARTIDAS
# ============================================================================

FULL_HISTORY_UPDATE_INTERVAL = 48 * 60 * 60
LIVE_GAME_CHECK_INTERVAL = 120
FULL_HISTORY_INITIAL_DELAY = int(os.environ.get("FULL_HISTORY_INITIAL_DELAY", "600" if LOW_MEMORY_MODE else "0"))


# ============================================================================
# FLAGS DE RENDIMIENTO / MEMORIA
# ============================================================================

ENABLE_PROFILE_CACHE = _env_flag("ENABLE_PROFILE_CACHE", default=not LOW_MEMORY_MODE)
ENABLE_HEAVY_PAGE_CACHE = _env_flag("ENABLE_HEAVY_PAGE_CACHE", default=not LOW_MEMORY_MODE)
STORE_GLOBAL_STATS_RAW_MATCHES = _env_flag("STORE_GLOBAL_STATS_RAW_MATCHES", default=not LOW_MEMORY_MODE)
ENABLE_KEEP_ALIVE = _env_flag("ENABLE_KEEP_ALIVE", default=False)
ENABLE_STATS_CALCULATOR_THREAD = _env_flag("ENABLE_STATS_CALCULATOR_THREAD", default=not LOW_MEMORY_MODE)
ENABLE_GLOBAL_STATS_BACKGROUND_CACHE = _env_flag("ENABLE_GLOBAL_STATS_BACKGROUND_CACHE", default=not LOW_MEMORY_MODE)
ENABLE_PERSONAL_RECORDS_BACKGROUND_CACHE = _env_flag("ENABLE_PERSONAL_RECORDS_BACKGROUND_CACHE", default=not LOW_MEMORY_MODE)
ENABLE_LP_RECALC_WORKER = _env_flag("ENABLE_LP_RECALC_WORKER", default=not LOW_MEMORY_MODE)
ENABLE_DEDICATED_JSON_GENERATOR_THREAD = _env_flag("ENABLE_DEDICATED_JSON_GENERATOR_THREAD", default=False)
ENABLE_DDRAGON_PERIODIC_REFRESH = _env_flag("ENABLE_DDRAGON_PERIODIC_REFRESH", default=not LOW_MEMORY_MODE)
ENABLE_BOOT_INDEX_WARMUP = _env_flag("ENABLE_BOOT_INDEX_WARMUP", default=not LOW_MEMORY_MODE)
ENABLE_ASYNC_STALE_INDEX_REGEN = _env_flag("ENABLE_ASYNC_STALE_INDEX_REGEN", default=not LOW_MEMORY_MODE)
DDRAGON_REFRESH_INTERVAL = int(os.environ.get("DDRAGON_REFRESH_INTERVAL", "21600"))
