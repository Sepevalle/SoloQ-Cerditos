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
    PERSONAL_RECORDS_UPDATE_INTERVAL,
    LP_HISTORY_TTL,
    API_RESPONSE_CLEANUP_THRESHOLD,
)


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
            self._cache["all_matches"] = all_matches
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

    def get(self, puuid):
        """Obtiene el historial de partidas de un jugador."""
        with self._lock:
            cached = self._cache.get(puuid)
            if cached and (time.time() - cached["timestamp"] < self._timeout):
                return cached["data"]
            return None

    def set(self, puuid, data):
        """Guarda el historial de partidas de un jugador."""
        with self._lock:
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
# INSTANCIAS GLOBALES
# ============================================================================

player_cache = PlayerCache()
global_stats_cache = GlobalStatsCache()
peak_elo_cache = PeakEloCache()
player_match_history_cache = PlayerMatchHistoryCache()
personal_records_cache = PersonalRecordsCache()
lp_history_cache = LpHistoryCache()
api_response_cache = ApiResponseCache()


# ============================================================================
# FUNCIONES DE UTILIDAD
# ============================================================================

def cleanup_all_caches():
    """Limpia todos los cachés de memoria."""
    player_match_history_cache.clear()
    api_response_cache.cleanup()
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
