"""
Servicio de gestión de cachés en memoria para el proyecto.
Centraliza todos los cachés que estaban dispersos en app.py.
"""

import threading
import time
from config.settings import (
    CACHE_TIMEOUT,
    GLOBAL_STATS_UPDATE_INTERVAL,
    PEAK_ELO_TTL,
    PLAYER_MATCH_HISTORY_CACHE_TIMEOUT,
    PLAYER_MATCH_HISTORY_CACHE_MAX_SIZE,
    PLAYER_MATCH_HISTORY_CACHE_MAX_MATCHES,
    PERSONAL_RECORDS_UPDATE_INTERVAL,
    LP_HISTORY_TTL,
    API_RESPONSE_CLEANUP_THRESHOLD,
    PROFILE_CACHE_TTL,
    PROFILE_CACHE_MAX_SIZE,
    PAGE_DATA_CACHE_TTL,
    PAGE_DATA_CACHE_MAX_SIZE,
    MATCH_LOOKUP_CACHE_TTL,
    MATCH_LOOKUP_CACHE_MAX_SIZE,
    PLAYER_STATS_CACHE_TTL,
    PLAYER_STATS_CACHE_MAX_SIZE,
    LIVE_GAME_CACHE_TTL,
    STORE_GLOBAL_STATS_RAW_MATCHES,
)
from utils.helpers import maybe_trim_process_memory


# ============================================================================
# CACHÉ PRINCIPAL DE JUGADORES
# ============================================================================

class PlayerCache:
    def __init__(self):
        self._cache = {
            "datos_jugadores": [],
            "timestamp": 0
        }
        self._lock = threading.Lock()
        self._timeout = CACHE_TIMEOUT

    def get(self):
        """Obtiene los datos cacheados y el timestamp."""
        with self._lock:
            return self._cache.get("datos_jugadores", []), self._cache.get("timestamp", 0)

    def set(self, data):
        """Actualiza los datos del caché."""
        with self._lock:
            self._cache["datos_jugadores"] = data
            self._cache["timestamp"] = time.time()

    def is_stale(self):
        """Verifica si el caché está desactualizado."""
        with self._lock:
            return time.time() - self._cache.get("timestamp", 0) > self._timeout

    def get_update_count(self):
        """Obtiene el contador de actualizaciones."""
        with self._lock:
            return self._cache.get("update_count", 0)

    def increment_update_count(self):
        """Incrementa el contador de actualizaciones."""
        with self._lock:
            self._cache["update_count"] = self._cache.get("update_count", 0) + 1
            return self._cache["update_count"]


# ============================================================================
# CACHÉ DE ESTADÍSTICAS GLOBALES
# ============================================================================

class GlobalStatsCache:
    def __init__(self):
        self._cache = {
            "data": None,
            "all_matches": [],
            "timestamp": 0
        }
        self._lock = threading.Lock()
        self._update_interval = GLOBAL_STATS_UPDATE_INTERVAL
        self._calculating = False

    def get(self):
        """Obtiene las estadísticas globales cacheadas."""
        with self._lock:
            return {
                "data": self._cache.get("data"),
                "all_matches": self._cache.get("all_matches", []),
                "timestamp": self._cache.get("timestamp", 0)
            }

    def set(self, data, all_matches):
        """Actualiza el caché de estadísticas globales."""
        with self._lock:
            self._cache["data"] = data
            self._cache["all_matches"] = all_matches if STORE_GLOBAL_STATS_RAW_MATCHES else []
            self._cache["timestamp"] = time.time()

    def is_stale(self):
        """Verifica si las estadísticas están desactualizadas."""
        with self._lock:
            return time.time() - self._cache.get("timestamp", 0) > self._update_interval

    def is_calculating(self):
        """Verifica si hay un cálculo en progreso."""
        with self._lock:
            return self._calculating

    def set_calculating(self, value):
        """Establece el estado de cálculo."""
        with self._lock:
            self._calculating = value

    def invalidate(self):
        """Invalida el caché de estadísticas globales."""
        with self._lock:
            self._cache["data"] = None
            self._cache["all_matches"] = []
            self._cache["timestamp"] = 0


# ============================================================================
# CACHÉ DE PEAK ELO
# ============================================================================

class PeakEloCache:
    def __init__(self):
        self._cache = {
            "data": {},
            "timestamp": 0
        }
        self._lock = threading.Lock()
        self._ttl = PEAK_ELO_TTL

    def get(self):
        """Obtiene los datos de peak ELO."""
        with self._lock:
            if self._cache["data"] and (time.time() - self._cache["timestamp"] < self._ttl):
                return True, self._cache["data"]
            return False, {}

    def set(self, data):
        """Actualiza el caché de peak ELO."""
        with self._lock:
            self._cache["data"] = data
            self._cache["timestamp"] = time.time()


# ============================================================================
# CACHÉ DE HISTORIAL DE PARTIDAS POR JUGADOR
# ============================================================================

class PlayerMatchHistoryCache:
    def __init__(self):
        self._cache = {}  # {puuid: {'data': {...}, 'timestamp': ...}}
        self._lock = threading.Lock()
        self._timeout = PLAYER_MATCH_HISTORY_CACHE_TIMEOUT
        self._max_size = PLAYER_MATCH_HISTORY_CACHE_MAX_SIZE
        self._max_matches = PLAYER_MATCH_HISTORY_CACHE_MAX_MATCHES

    def get(self, puuid):
        """Obtiene el historial de partidas de un jugador."""
        with self._lock:
            cached = self._cache.get(puuid)
            if cached and (time.time() - cached["timestamp"] < self._timeout):
                return cached["data"]
            if cached:
                self._cache.pop(puuid, None)
            return None

    def set(self, puuid, data):
        """Guarda el historial de partidas de un jugador."""
        with self._lock:
            match_count = len((data or {}).get("matches", [])) if isinstance(data, dict) else 0
            if self._max_matches and match_count > self._max_matches:
                self._cache.pop(puuid, None)
                return
            self._cleanup_if_needed()
            self._cache[puuid] = {
                "data": data,
                "timestamp": time.time()
            }

    def _cleanup_if_needed(self):
        """Limpia entradas antiguas si el caché excede el tamaño máximo."""
        if len(self._cache) >= self._max_size:
            # Ordenar por timestamp y eliminar los más antiguos
            sorted_items = sorted(self._cache.items(), key=lambda x: x[1]["timestamp"])
            to_remove = len(sorted_items) - self._max_size + 1
            for puuid, _ in sorted_items[:to_remove]:
                del self._cache[puuid]

    def clear(self):
        """Limpia todo el caché."""
        with self._lock:
            self._cache.clear()


# ============================================================================
# CACHÉ DE RÉCORDS PERSONALES
# ============================================================================

class PersonalRecordsCache:
    def __init__(self):
        self._cache = {
            "data": {},
            "timestamp": 0
        }
        self._lock = threading.Lock()
        self._update_interval = PERSONAL_RECORDS_UPDATE_INTERVAL

    def get(self, cache_key):
        """Obtiene récords personales cacheados."""
        with self._lock:
            if time.time() - self._cache["timestamp"] < self._update_interval:
                return self._cache["data"].get(cache_key)
            return None

    def set(self, cache_key, data):
        """Guarda récords personales en caché."""
        with self._lock:
            self._cache["data"][cache_key] = data
            self._cache["timestamp"] = time.time()

    def invalidate(self, puuid):
        """Invalida el caché para un jugador específico."""
        with self._lock:
            keys_to_remove = [k for k in self._cache["data"].keys() if k.startswith(f"{puuid}_")]
            for key in keys_to_remove:
                del self._cache["data"][key]


# ============================================================================
# CACHÉ DE LP HISTORY
# ============================================================================

class LpHistoryCache:
    def __init__(self):
        self._cache = {
            "data": {},
            "timestamp": 0
        }
        self._lock = threading.Lock()
        self._ttl = LP_HISTORY_TTL

    def get(self):
        """Obtiene el historial de LP."""
        with self._lock:
            if self._cache["data"] and (time.time() - self._cache["timestamp"] < self._ttl):
                return self._cache["data"]
            return None

    def set(self, data):
        """Actualiza el caché de LP history."""
        with self._lock:
            self._cache["data"] = data
            self._cache["timestamp"] = time.time()


# ============================================================================
# CACHÉ GENÉRICO CON TTL
# ============================================================================

class TimedCache:
    def __init__(self, ttl_seconds=120, max_size=128):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def get(self, key, default=None):
        """Obtiene un valor cacheado si no ha expirado."""
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return default

            if time.time() - entry["timestamp"] > self._ttl:
                del self._cache[key]
                return default

            return entry["data"]

    def set(self, key, data):
        """Guarda un valor en caché."""
        with self._lock:
            self._cleanup_expired_locked()
            if self._max_size and key not in self._cache and len(self._cache) >= self._max_size:
                oldest_key = min(self._cache.items(), key=lambda item: item[1]["timestamp"])[0]
                del self._cache[oldest_key]

            self._cache[key] = {
                "data": data,
                "timestamp": time.time()
            }

    def invalidate(self, key):
        """Invalida una clave específica."""
        with self._lock:
            self._cache.pop(key, None)

    def invalidate_prefix(self, prefix):
        """Invalida todas las claves que empiecen por el prefijo."""
        with self._lock:
            keys_to_remove = [key for key in self._cache if key.startswith(prefix)]
            for key in keys_to_remove:
                del self._cache[key]

    def clear(self):
        """Limpia todo el caché."""
        with self._lock:
            self._cache.clear()

    def _cleanup_expired_locked(self):
        now = time.time()
        keys_to_remove = [
            key for key, entry in self._cache.items()
            if now - entry["timestamp"] > self._ttl
        ]
        for key in keys_to_remove:
            del self._cache[key]


# ============================================================================
# CACHÉ DE ESTADÍSTICAS DE JUGADORES (INDEX) - NUEVO PARA RENDIMIENTO
# ============================================================================

class PlayerStatsCache:
    """
    Caché específico para las estadísticas calculadas de jugadores en el index.
    Almacena: top_champions, streaks, lp_24h, wins_24h, losses_24h, en_partida
    """
    def __init__(self):
        self._cache = {}  # {puuid_queue: {'data': {...}, 'timestamp': ...}}
        self._lock = threading.Lock()
        self._ttl = PLAYER_STATS_CACHE_TTL
        self._max_size = PLAYER_STATS_CACHE_MAX_SIZE

    def _make_key(self, puuid, queue_type):
        """Genera una clave única para puuid + queue_type."""
        return f"{puuid}_{queue_type}"

    def get(self, puuid, queue_type):
        """Obtiene las estadísticas cacheadas de un jugador para una cola específica."""
        with self._lock:
            key = self._make_key(puuid, queue_type)
            cached = self._cache.get(key)
            if cached and (time.time() - cached["timestamp"] < self._ttl):
                return cached["data"]
            return None
        
    def set(self, puuid, queue_type, data):
        """Guarda las estadísticas de un jugador."""
        with self._lock:
            key = self._make_key(puuid, queue_type)
            if self._max_size and key not in self._cache and len(self._cache) >= self._max_size:
                oldest_key = min(self._cache.items(), key=lambda item: item[1]["timestamp"])[0]
                del self._cache[oldest_key]
            self._cache[key] = {
                "data": data,
                "timestamp": time.time()
            }

    def invalidate(self, puuid):
        """Invalida todas las entradas de un jugador."""
        with self._lock:
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(f"{puuid}_")]
            for key in keys_to_remove:
                del self._cache[key]

    def clear(self):
        """Limpia todo el caché."""
        with self._lock:
            self._cache.clear()


# ============================================================================
# CACHÉ DE RESPUESTAS API (PARA RATE LIMITING)
# ============================================================================

class ApiResponseCache:
    def __init__(self):
        self._events = {}
        self._data = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._cleanup_threshold = API_RESPONSE_CLEANUP_THRESHOLD

    def create_request(self):
        """Crea una nueva petición y retorna su ID."""
        with self._lock:
            request_id = self._counter
            self._counter += 1
            self._events[request_id] = threading.Event()
            return request_id

    def set_response(self, request_id, response):
        """Establece la respuesta para una petición."""
        with self._lock:
            self._data[request_id] = response
            if request_id in self._events:
                self._events[request_id].set()

    def wait_for_response(self, request_id, timeout=120):
        """Espera la respuesta de una petición."""
        event = None
        with self._lock:
            event = self._events.get(request_id)
        
        if event and event.wait(timeout=timeout):
            with self._lock:
                response = self._data.get(request_id)
                # Limpiar
                self._events.pop(request_id, None)
                self._data.pop(request_id, None)
                return response
        return None

    def cleanup(self):
        """Limpia respuestas antiguas."""
        with self._lock:
            if len(self._events) > self._cleanup_threshold:
                keys = sorted(self._events.keys())
                keys_to_delete = keys[:-50]
                for key in keys_to_delete:
                    self._events.pop(key, None)
                    self._data.pop(key, None)


# ============================================================================
# CACHÉ DE ESTADO EN PARTIDA (Live Game)
# ============================================================================

class LiveGameCache:
    """
    Caché específico para el estado "en partida" de jugadores.
    TTL de 300 segundos (5 minutos) para mantener datos entre verificaciones del worker.
    El worker verifica cada 2 minutos, así que 5 minutos de TTL da margen de seguridad.
    """
    def __init__(self, ttl_seconds=300):
        self._cache = {}  # puuid -> {"data": game_data, "timestamp": time, "has_data": bool}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    
    def get(self, puuid):
        """Obtiene el estado en partida si está en caché y no ha expirado."""
        with self._lock:
            entry = self._cache.get(puuid)
            if not entry:
                return None
            
            # Verificar si expiró
            if time.time() - entry["timestamp"] > self._ttl:
                del self._cache[puuid]
                return None
            
            return entry["data"]
    
    def get_with_status(self, puuid):
        """
        Obtiene el estado en partida con información de si hay entrada en caché.
        Retorna: (data, has_cache_entry, age_seconds) donde has_cache_entry es True si hay datos válidos en caché
        """
        with self._lock:
            entry = self._cache.get(puuid)
            if not entry:
                return None, False, None
            
            age_seconds = time.time() - entry["timestamp"]
            
            # Verificar si expiró
            if age_seconds > self._ttl:
                del self._cache[puuid]
                return None, False, None
            
            return entry["data"], True, age_seconds
    
    def get_cache_age(self, puuid):
        """Retorna la edad en segundos de la entrada en caché, o None si no existe."""
        with self._lock:
            entry = self._cache.get(puuid)
            if not entry:
                return None
            return time.time() - entry["timestamp"]

    
    def set(self, puuid, game_data):
        """Guarda el estado en partida en el caché."""
        with self._lock:
            self._cache[puuid] = {
                "data": game_data,
                "timestamp": time.time()
            }
    
    def is_valid(self, puuid):
        """Verifica si hay datos válidos en caché para el jugador."""
        with self._lock:
            entry = self._cache.get(puuid)
            if not entry:
                return False
            return time.time() - entry["timestamp"] <= self._ttl
    
    def clear(self):
        """Limpia todo el caché."""
        with self._lock:
            self._cache.clear()



# ============================================================================
# INSTANCIAS GLOBALES
# ============================================================================


player_cache = PlayerCache()
global_stats_cache = GlobalStatsCache()
peak_elo_cache = PeakEloCache()
player_match_history_cache = PlayerMatchHistoryCache()
personal_records_cache = PersonalRecordsCache()
lp_history_cache = LpHistoryCache()
player_profile_cache = TimedCache(ttl_seconds=PROFILE_CACHE_TTL, max_size=PROFILE_CACHE_MAX_SIZE)
page_data_cache = TimedCache(ttl_seconds=PAGE_DATA_CACHE_TTL, max_size=PAGE_DATA_CACHE_MAX_SIZE)
match_lookup_cache = TimedCache(ttl_seconds=MATCH_LOOKUP_CACHE_TTL, max_size=MATCH_LOOKUP_CACHE_MAX_SIZE)
player_stats_cache = PlayerStatsCache()  # NUEVO: Caché para estadísticas de jugadores
api_response_cache = ApiResponseCache()
live_game_cache = LiveGameCache(ttl_seconds=LIVE_GAME_CACHE_TTL)  # NUEVO: Caché para estado en partida





# ============================================================================
# FUNCIONES DE UTILIDAD
# ============================================================================

def cleanup_all_caches():
    """Limpia todos los cachés de memoria."""
    player_match_history_cache.clear()
    player_profile_cache.clear()
    page_data_cache.clear()
    match_lookup_cache.clear()
    global_stats_cache.invalidate()
    player_stats_cache.clear()  # NUEVO: Limpiar también el caché de estadísticas
    api_response_cache.cleanup()
    live_game_cache.clear()
    maybe_trim_process_memory("cleanup_all_caches")
    print("[cleanup_all_caches] Todos los cachés han sido limpiados")


def invalidate_global_stats():
    """Invalida el caché de estadísticas globales."""
    global_stats_cache.invalidate()
    print("[invalidate_global_stats] Caché de estadísticas globales invalidado")


def start_cache_service():
    """
    Función de inicio para el servicio de caché.
    Se ejecuta en un thread en segundo plano para mantener
    el caché limpio y actualizado.
    """
    print("[cache_service] Servicio de caché iniciado")
    
    while True:
        try:
            # Limpiar cachés periódicamente
            time.sleep(300)  # Cada 5 minutos
            cleanup_all_caches()
            
        except Exception as e:
            print(f"[cache_service] Error: {e}")
            time.sleep(60)  # Esperar 1 minuto antes de reintentar
