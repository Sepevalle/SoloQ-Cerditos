"""
Configuración centralizada del proyecto SoloQ-Cerditos.
Todas las variables de entorno y configuraciones globales se definen aquí.
"""

import os
from datetime import datetime, timezone, timedelta

# ============================================================================
# CONFIGURACIÓN DE API KEYS
# ============================================================================

RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
RIOT_API_KEY_2 = os.environ.get("RIOT_API_KEY_2", RIOT_API_KEY)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")

# ============================================================================
# URLS BASE DE APIs
# ============================================================================

BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_EUROPE = "https://europe.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

# ============================================================================
# CONFIGURACIÓN DE GITHUB
# ============================================================================

GITHUB_REPO = "Sepevalle/SoloQ-Cerditos"
LP_HISTORY_FILE_PATH = "lp_history.json"

# ============================================================================
# CONFIGURACIÓN DE ZONA HORARIA
# ============================================================================

TARGET_TIMEZONE = timezone(timedelta(hours=2))

# ============================================================================
# CONFIGURACIÓN DE SPLITS/TEMPORADAS
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
# CONFIGURACIÓN DE CACHÉS
# ============================================================================

# Caché principal de jugadores
CACHE_TIMEOUT = 300  # 5 minutos

# Caché de estadísticas globales
GLOBAL_STATS_UPDATE_INTERVAL = 86400  # 24 horas

# Caché de Peak ELO
PEAK_ELO_TTL = 300  # 5 minutos

# Caché de historial de partidas
PLAYER_MATCH_HISTORY_CACHE_TIMEOUT = 300  # 5 minutos
PLAYER_MATCH_HISTORY_CACHE_MAX_SIZE = 15  # Máximo 15 jugadores en caché

# Caché de récords personales
PERSONAL_RECORDS_UPDATE_INTERVAL = 3600  # 1 hora

# Caché de LP history
LP_HISTORY_TTL = 300  # 5 minutos

# ============================================================================
# CONFIGURACIÓN DE RATE LIMITING
# ============================================================================

API_RATE_PER_SECOND = 20
API_BURST_LIMIT = 100
API_RESPONSE_CLEANUP_THRESHOLD = 50

# ============================================================================
# CONFIGURACIÓN DE SNAPSHOTS LP
# ============================================================================

LP_SNAPSHOTS_SAVE_INTERVAL = 3600  # 1 hora

# ============================================================================
# CONFIGURACIÓN DE DATA DRAGON
# ============================================================================

# Esta versión se actualiza automáticamente al iniciar la aplicación
# mediante la función actualizar_version_ddragon() en services/riot_api.py
DDRAGON_VERSION = "16.3.1"


# ============================================================================
# CONFIGURACIÓN DEL SERVIDOR
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
# CONFIGURACIÓN DE GEMINI AI
# ============================================================================

GEMINI_MODEL = "gemini-3-flash-preview"

# ============================================================================
# INTERVALOS DE ACTUALIZACIÓN (para hilos de background)
# ============================================================================

CACHE_UPDATE_INTERVAL = 130  # segundos - actualización de caché de jugadores
LP_TRACKER_INTERVAL = 300    # segundos - snapshots de LP
