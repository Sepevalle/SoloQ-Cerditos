from services.data_processing import process_player_match_history
from flask import Flask, render_template, redirect, url_for, request, jsonify
import requests
import os
import time
import threading
import json
import base64
import bisect
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import queue # Import for the queue
import locale # Import for locale formatting
from google import genai
from pydantic import BaseModel

app = Flask(__name__)

# Inyectar 'str' en el contexto de Jinja2 para que esté disponible en todas las plantillas.
@app.context_processor
def utility_processor():
    return dict(str=str)

# --- CONFIGURACIÓN DE ZONA HORARIA Y LOCALIZACIÓN ---
# Define la zona horaria de visualización (UTC+2) para asegurar consistencia.
TARGET_TIMEZONE = timezone(timedelta(hours=2))

# Configurar el locale para el formato de números con separador de miles
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'es_ES.utf8')
    except locale.Error:
        print("Advertencia: No se pudo establecer el locale para el separador de miles (es_ES.UTF-8 o es_ES.utf8). Los números grandes no se formatearán con puntos.")


# Custom Jinja2 filters
@app.template_filter('get_queue_type')
def get_queue_type_filter(queue_id):
    queue_names = {
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
    return queue_names.get(int(queue_id), "Desconocido")

def _resolve_champion_info(champion_id_raw, champion_name_from_api):
    """Resuelve el nombre y ID del campeón de forma robusta.
    Intenta múltiples métodos para obtener información válida.
    """
    if isinstance(champion_id_raw, (int, float)):
        temp_id = int(champion_id_raw)
        if temp_id in ALL_CHAMPIONS:
            return ALL_CHAMPIONS[temp_id], temp_id
    elif isinstance(champion_id_raw, str) and champion_id_raw.isdigit():
        temp_id = int(champion_id_raw)
        if temp_id in ALL_CHAMPIONS:
            return ALL_CHAMPIONS[temp_id], temp_id
    
    if champion_name_from_api and champion_name_from_api != "Desconocido":
        champ_id = ALL_CHAMPION_NAMES_TO_IDS.get(champion_name_from_api)
        if champ_id:
            return champion_name_from_api, champ_id
        return champion_name_from_api, "N/A"
    
    return "Desconocido", "N/A"

@app.template_filter('format_timestamp')
def format_timestamp_filter(timestamp):
    """Filtro para formatear timestamps UTC a la zona horaria local (UTC+2)."""
    if timestamp is None or timestamp == 0: # Añadido check para 0
        return "N/A"
    # El timestamp de la API viene en milisegundos (UTC)
    timestamp_sec = timestamp / 1000
    # 1. Crear un objeto datetime consciente de la zona horaria (aware) en UTC
    dt_utc = datetime.fromtimestamp(timestamp_sec, tz=timezone.utc)
    # 2. Convertir a la zona horaria de visualización deseada (TARGET_TIMEZONE)
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    return dt_target.strftime("%d/%m/%Y %H:%M")


@app.template_filter('format_peak_elo')
def format_peak_elo_filter(valor):
    if valor is None:
        return "N/A"
    try:
        valor = int(valor)
    except (ValueError, TypeError):
        return "N/A"

    if valor >= 2800:
        lps = valor - 2800
        if valor >= 3200:
            return f"CHALLENGER ({lps} LPs)"
        elif valor >= 3000:
            return f"GRANDMASTER ({lps} LPs)"
        else:
            return f"MASTER ({lps} LPs)"

    tier_map = {
        6: "DIAMOND", 5: "EMERALD", 4: "PLATINUM", 3: "GOLD",
        2: "SILVER", 1: "BRONZE", 0: "IRON"
    }
    # CORRECCIÓN: rank_map debe mapear los valores calculados a las cadenas correctas (0:'IV', 1:'III', 2:'II', 3:'I')
    rank_map = {3: "I", 2: "II", 1: "III", 0: "IV"} # Corregido

    league_points = valor % 100
    valor_without_lps = valor - league_points
    rank_value = (valor_without_lps // 100) % 4
    tier_value = (valor_without_lps // 100) // 4

    tier_name = tier_map.get(tier_value, "UNKNOWN")
    rank_name = rank_map.get(rank_value, "")
    return f"{tier_name} {rank_name} ({league_points} LPs)"

@app.template_filter('thousands_separator')
def thousands_separator_filter(value):
    """
    Filtro de Jinja2 para formatear números con separador de miles (punto para locale español).
    No añade decimales para enteros.
    """
    try:
        if isinstance(value, (int, float)):
            # Manual formatting for thousands separator (dot) and decimal separator (comma)
            if isinstance(value, int):
                manual_formatted = "{:,.0f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")
                return manual_formatted
            else:
                manual_formatted = "{:,.2f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")
                return manual_formatted
        return value # Retorna el valor original si no es un número
    except Exception as e:
        # En caso de error silencioso para no romper la vista, se podría loguear si es crítico
        return str(value) # Retorna como string si hay error

@app.template_filter('format_number')
def format_number_filter(value):
    """
    Filtro de Jinja2 para formatear números con separador de miles.
    Utiliza la configuración regional (locale) establecida previamente.
    """
    try:
        # Intenta convertir el valor a número y formatearlo
        return locale.format_string("%d", int(value), grouping=True)
    except (ValueError, TypeError):
        # En caso de que el valor no sea un número válido, lo devuelve sin cambios
        return value

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")
    # exit(1) # Removed exit(1) to allow the app to run even without API key for testing purposes, but it will not fetch data.

# URLs base de la API de Riot
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

# Rutas de archivos en GitHub
LP_HISTORY_FILE_PATH = "lp_history.json"
GITHUB_REPO = "Sepevalle/SoloQ-Cerditos"
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')

# Caché para almacenar los datos de los jugadores principales (resumen de ELO)
cache = {
    "datos_jugadores": [],
    "timestamp": 0
}
CACHE_TIMEOUT = 130  # 2 minutos para el resumen principal de jugadores
cache_lock = threading.Lock()

# Global cache for pre-calculated global statistics
GLOBAL_STATS_CACHE = {
    "data": None,
    "all_matches": [],
    "timestamp": 0
}
GLOBAL_STATS_LOCK = threading.Lock()
GLOBAL_STATS_UPDATE_INTERVAL = 3600 # Update global stats every hour (3600 seconds)

# --- CACHÉ PARA PEAK ELO ---
PEAK_ELO_CACHE = {
    "data": {},
    "timestamp": 0
}
PEAK_ELO_LOCK = threading.Lock()
PEAK_ELO_TTL = 300 # 5 minutos de caché para evitar saturar la API

# Cache for pre-calculated personal records
PERSONAL_RECORDS_CACHE = {
    "data": {},
    "timestamp": 0
}
PERSONAL_RECORDS_LOCK = threading.Lock()
PERSONAL_RECORDS_UPDATE_INTERVAL = 3600 # Update personal records every hour (3600 seconds)

# --- NUEVA CACHÉ EN MEMORIA PARA EL HISTORIAL DE PARTIDAS DE LOS JUGADORES ---
# Almacena el historial completo de partidas por PUUID en memoria.
# { puuid: { 'data': historial_json, 'timestamp': last_update_time } }
PLAYER_MATCH_HISTORY_CACHE = {}
PLAYER_MATCH_HISTORY_LOCK = threading.Lock()
PLAYER_MATCH_HISTORY_CACHE_TIMEOUT = 300 # 5 minutos para el historial de partidas individual

# --- CONFIGURACIÓN DE SPLITS ---
SPLITS = {
    "s16_split1": {
        "name": "Temporada 2026 - Split 1",
        "start_date": datetime(2026, 1, 8, tzinfo=timezone.utc),
    }
}

ACTIVE_SPLIT_KEY = "s16_split1"
SEASON_START_TIMESTAMP = int(SPLITS[ACTIVE_SPLIT_KEY]["start_date"].timestamp())

# --- CONFIGURACIÓN DEL CONTROL DE TASA DE API ---
API_REQUEST_QUEUE = queue.Queue() # Cola para todas las peticiones a la API
API_RESPONSE_EVENTS = {} # Diccionario para almacenar eventos de respuesta por ID de petición
API_RESPONSE_DATA = {} # Diccionario para almacenar los datos de respuesta por ID de petición
REQUEST_ID_COUNTER = 0 # Contador para IDs de petición únicos
REQUEST_ID_COUNTER_LOCK = threading.Lock() # Bloqueo para el contador

class RateLimiter:
    """
    Clase para gestionar el control de tasa de llamadas a la API de Riot Games.
    Utiliza un algoritmo de cubo de tokens.
    """
    def __init__(self, rate_per_second, burst_limit):
        self.rate_per_second = rate_per_second
        self.burst_limit = burst_limit
        self.tokens = burst_limit  # Inicia con el límite de ráfaga
        self.last_refill_time = time.time()
        self.lock = threading.Lock()

    def _refill_tokens(self):
        """Rellena los tokens en el cubo según el tiempo transcurrido."""
        now = time.time()
        time_elapsed = now - self.last_refill_time
        tokens_to_add = time_elapsed * self.rate_per_second
        
        with self.lock:
            self.tokens = min(self.burst_limit, self.tokens + tokens_to_add)
            self.last_refill_time = now

    def consume_token(self):
        """
        Consume un token. Espera si no hay tokens disponibles hasta que se rellenen.
        """
        while True:
            self._refill_tokens()
            with self.lock:
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
            time.sleep(0.01) # Pequeña espera para evitar el "busy-waiting"

# Instancia global del RateLimiter para la API de Riot Games
# Ajusta estos valores según los límites de tu clave de API de Riot
# Límites típicos de la API de Riot (ejemplo): 20 peticiones/segundo, 100 peticiones/2 minutos
riot_api_limiter = RateLimiter(
    rate_per_second=20, # Aumentado de 10 a 20
    burst_limit=100     # Aumentado de 20 a 100
)

def _api_rate_limiter_worker():
    """Hilo de trabajo que procesa las peticiones de la cola respetando el límite de tasa."""
    print("[_api_rate_limiter_worker] Hilo de control de tasa de API iniciado.")
    session = requests.Session() # Usar una sesión persistente para el worker
    while True:
        try:
            # Obtener la petición de la cola. Timeout para que el hilo no se bloquee indefinidamente.
            # Añadir logging para el tamaño de la cola
            # if not API_REQUEST_QUEUE.empty():
            #     print(f"[_api_rate_limiter_worker] Tamaño de la cola de peticiones: {API_REQUEST_QUEUE.qsize()}")
            request_id, url, headers, timeout, is_spectator_api = API_REQUEST_QUEUE.get(timeout=1)
            
            # Consumir un token antes de realizar la petición
            riot_api_limiter.consume_token()

            # print(f"[_api_rate_limiter_worker] Procesando petición {request_id} a: {url}")
            response = None
            for i in range(3): # Reintentos para la petición HTTP real
                try:
                    response = session.get(url, headers=headers, timeout=timeout)
                    
                    # Si es una API de espectador y devuelve 404, no reintentar
                    if is_spectator_api and response.status_code == 404:
                        # print(f"[_api_rate_limiter_worker] Petición {request_id} a la API de espectador devolvió 404. No se reintentará.")
                        break # Salir del bucle de reintentos inmediatamente

                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        print(f"[_api_rate_limiter_worker] Rate limit excedido. Esperando {retry_after} segundos... (Intento {i + 1}/3)")
                        time.sleep(retry_after)
                        continue # Reintentar la petición después de esperar
                    response.raise_for_status() # Lanza una excepción para códigos de error HTTP
                    # print(f"[_api_rate_limiter_worker] Petición {request_id} exitosa. Status: {response.status_code}")
                    break # Salir del bucle de reintentos si es exitoso
                except requests.exceptions.RequestException as e:
                    print(f"[_api_rate_limiter_worker] Error en petición {request_id} a {url}: {e}. Intento {i + 1}/3")
                    if i < 2: # Si no es el último intento, espera y reintenta
                        time.sleep(0.5 * (2 ** i)) # Backoff exponencial
            
            # Almacenar la respuesta y notificar al hilo que la solicitó
            with REQUEST_ID_COUNTER_LOCK:
                if request_id in API_RESPONSE_EVENTS:
                    API_RESPONSE_DATA[request_id] = response
                    API_RESPONSE_EVENTS[request_id].set() # Notificar que la respuesta está lista
                else:
                    print(f"[_api_rate_limiter_worker] Advertencia: Petición {request_id} expiró o fue abandonada. Descartando respuesta.")

        except queue.Empty:
            pass # No hay peticiones en la cola, el hilo sigue esperando
        except Exception as e:
            print(f"[_api_rate_limiter_worker] Error inesperado en el worker del control de tasa: {e}")
            time.sleep(1) # Espera antes de continuar para evitar bucles de error

# Modificación de make_api_request para usar la cola
def make_api_request(url, retries=3, backoff_factor=0.5, is_spectator_api=False):
    """
    Envía una petición a la cola de la API y espera su respuesta, respetando el control de tasa.
    """
    with REQUEST_ID_COUNTER_LOCK:
        global REQUEST_ID_COUNTER
        request_id = REQUEST_ID_COUNTER
        REQUEST_ID_COUNTER += 1
        API_RESPONSE_EVENTS[request_id] = threading.Event()

    headers = {"X-Riot-Token": RIOT_API_KEY}
    # Pone la petición en la cola. El worker la procesará cuando haya tokens disponibles.
    API_REQUEST_QUEUE.put((request_id, url, headers, 10, is_spectator_api)) # 10 segundos de timeout para la petición HTTP

    print(f"[make_api_request] Petición {request_id} encolada para {url}. Esperando respuesta...")
    
    # Esperar con un timeout para evitar bloqueos indefinidos
    # El timeout aquí es para la espera de la respuesta del worker, no de la API en sí.
    # Aumentado de 60 a 120 segundos
    if not API_RESPONSE_EVENTS[request_id].wait(timeout=120): 
        print(f"[make_api_request] Timeout esperando respuesta para la petición {request_id} a {url}.")
        with REQUEST_ID_COUNTER_LOCK:
            if request_id in API_RESPONSE_EVENTS:
                del API_RESPONSE_EVENTS[request_id]
            if request_id in API_RESPONSE_DATA:
                del API_RESPONSE_DATA[request_id]
        return None # Retorna None si hay timeout

    with REQUEST_ID_COUNTER_LOCK:
        response = API_RESPONSE_DATA.get(request_id)
        # Limpiar los diccionarios después de obtener la respuesta
        if request_id in API_RESPONSE_EVENTS:
            del API_RESPONSE_EVENTS[request_id]
        if request_id in API_RESPONSE_DATA:
            del API_RESPONSE_DATA[request_id]
    
    return response

DDRAGON_VERSION = "14.9.1"

def actualizar_version_ddragon():
    global DDRAGON_VERSION
    print("[actualizar_version_ddragon] Intentando obtener la última versión de Data Dragon.")
    try:
        url = f"{BASE_URL_DDRAGON}/api/versions.json"
        # Esta llamada no usa make_api_request porque es una API diferente (DDragon, no Riot Games)
        response = requests.get(url, timeout=5) 
        if response.status_code == 200:
            DDRAGON_VERSION = response.json()[0]
            print(f"[actualizar_version_ddragon] Versión de Data Dragon establecida a: {DDRAGON_VERSION}")
        else:
            print(f"[actualizar_version_ddragon] Error al obtener la versión de Data Dragon. Status: {response.status_code}. Usando versión de respaldo: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"[actualizar_version_ddragon] Error al obtener la versión de Data Dragon: {e}. Usando versión de respaldo: {DDRAGON_VERSION}")

actualizar_version_ddragon()

ALL_CHAMPIONS = {} # ID to Name (e.g., {266: 'Aatrox'})
ALL_CHAMPION_NAMES_TO_IDS = {} # Name to ID (e.g., {'Aatrox': 266})
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    print("[obtener_todos_los_campeones] Obteniendo datos de campeones de Data Dragon.")
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    # Esta llamada no usa make_api_request porque es una API diferente (DDragon, no Riot Games)
    response = requests.get(url_campeones, timeout=10) 
    if response and response.status_code == 200:
        champions_data = {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
        print(f"[obtener_todos_los_campeones] Datos de campeones cargados exitosamente. Ejemplo: {list(champions_data.items())[:5]}")
        return champions_data
    print("[obtener_todos_los_campeones] No se pudieron obtener los datos de campeones.")
    return {}

def obtener_todas_las_runas():
    """Carga los datos de las runas desde Data Dragon."""
    print("[obtener_todas_las_runas] Obteniendo datos de runas de Data Dragon.")
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
    # Esta llamada no usa make_api_request porque es una API diferente (DDragon, no Riot Games)
    data = requests.get(url, timeout=10)
    runes = {}
    if data and data.status_code == 200:
        for tree in data.json():
            runes[tree['id']] = tree['icon']
            for slot in tree['slots']:
                for perk in slot['runes']:
                    runes[perk['id']] = perk['icon']
        print("[obtener_todas_las_runas] Datos de runas cargados exitosamente.")
    else:
        print("[obtener_todas_las_runas] No se pudieron obtener los datos de runas.")
    return runes

def obtener_todos_los_hechizos():
    """Carga los datos de los hechizos de invocador desde Data Dragon."""
    print("[obtener_todos_los_hechizos] Obteniendo datos de hechizos de invocador de Data Dragon.")
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
    # Esta llamada no usa make_api_request porque es una API diferente (DDragon, no Riot Games)
    data = requests.get(url, timeout=10)
    spells = {}
    if data and data.status_code == 200 and 'data' in data.json():
        for k, v in data.json()['data'].items():
            spells[int(v['key'])] = v['id']
        print("[obtener_todos_los_hechizos] Datos de hechizos de invocador cargados exitosamente.")
    else:
        print("[obtener_todos_los_hechizos] No se pudieron obtener los datos de hechizos de invocador.")
    return spells

def actualizar_ddragon_data():
    """Actualiza todos los datos de DDragon (campeones, runas, hechizos) en las variables globales."""
    global DDRAGON_VERSION, ALL_CHAMPIONS, ALL_RUNES, ALL_SUMMONER_SPELLS, ALL_CHAMPION_NAMES_TO_IDS
    print("[actualizar_ddragon_data] Iniciando actualización de todos los datos de Data Dragon.")
    actualizar_version_ddragon() # Asegura que la versión de DDRAGON sea la más reciente
    
    # Update ALL_CHAMPIONS (ID to Name)
    ALL_CHAMPIONS_TEMP = obtener_todos_los_campeones()
    ALL_CHAMPIONS.update(ALL_CHAMPIONS_TEMP) # Use .update() to ensure it's populated

    # Create ALL_CHAMPION_NAMES_TO_IDS (Name to ID)
    ALL_CHAMPION_NAMES_TO_IDS.clear() # Clear before populating
    ALL_CHAMPION_NAMES_TO_IDS.update({v: k for k, v in ALL_CHAMPIONS.items()})
    print(f"[actualizar_ddragon_data] ALL_CHAMPION_NAMES_TO_IDS cargado. Ejemplo: {list(ALL_CHAMPION_NAMES_TO_IDS.items())[:5]}")


    ALL_RUNES = obtener_todas_las_runas()
    ALL_SUMMONER_SPELLS = obtener_todos_los_hechizos()
    print("[actualizar_ddragon_data] Data Dragon champion, rune, and summoner spell data updated.")

# Cargar los datos de DDragon al inicio
actualizar_ddragon_data()



def obtener_nombre_campeon(champion_id):
    """Obtiene el nombre de un campeón dado su ID."""
    return ALL_CHAMPIONS.get(champion_id, "Desconocido")

def obtener_puuid(api_key, riot_id, region):
    """Obtiene el PUUID de un jugador dado su Riot ID y región."""
    full_riot_id = f"{riot_id}#{region}"
    print(f"[obtener_puuid] Intentando obtener PUUID para {full_riot_id}.")
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_puuid] PUUID obtenido para {full_riot_id}.")
        return response.json()
    else:
        print(f"[obtener_puuid] No se pudo obtener el PUUID para {full_riot_id} después de varios intentos.")
        return None

def obtener_id_invocador(api_key, puuid):
    """Obtiene el ID de invocador de un jugador dado su PUUID."""
    puuid_dict = leer_puuids()
    riot_id = next((k for k, v in puuid_dict.items() if v == puuid), None)
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    print(f"[obtener_id_invocador] Intentando obtener ID de invocador para {identifier}.")
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_id_invocador] ID de invocador obtenido para {identifier}.")
        return response.json()
    else:
        print(f"[obtener_id_invocador] No se pudo obtener el ID de invocador para {identifier}.")
        return None

def obtener_elo(api_key, puuid, riot_id=None):
    """Obtiene la información de Elo de un jugador dado su PUUID."""
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    print(f"[obtener_elo] Intentando obtener Elo para {identifier}.")
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_elo] Elo obtenido para {identifier}.")
        return response.json()
    else:
        print(f"[obtener_elo] No se pudo obtener el Elo para {identifier}.")
        return None

def obtener_historial_partidas(api_key, puuid, count=20):
    """Obtiene el historial de partidas de un jugador dado su PUUID."""
    print(f"[obtener_historial_partidas] Intentando obtener historial de partidas para PUUID: {puuid}. Cantidad: {count}")
    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}&api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_historial_partidas] Historial de partidas obtenido para PUUID: {puuid}.")
        return response.json()
    else:
        print(f"[obtener_historial_partidas] No se pudo obtener el historial de partidas para {puuid}.")
        return None

def esta_en_partida(api_key, puuid, riot_id=None):
    """
    Comprueba si un jugador está en una partida activa.
    Retorna los datos completos de la partida si está en una, None si no.
    """
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    # print(f"[esta_en_partida] Verificando si el jugador {identifier} está en partida.")
    try:
        url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
        # Usar make_api_request con is_spectator_api=True para control de tasa específico si es necesario
        response = make_api_request(url, is_spectator_api=True) 

        if response and response.status_code == 200:  # Player is in game
            game_data = response.json()
            for participant in game_data.get("participants", []):
                if participant["puuid"] == puuid:
                    print(f"[esta_en_partida] Jugador {identifier} está en partida activa.")
                    return game_data
            print(f"[esta_en_partida] Advertencia: Jugador {identifier} está en partida pero no se encontró en la lista de participantes.")
            return None
        elif response and response.status_code == 404:  # Player not in game (expected response)
            print(f"[esta_en_partida] Jugador {identifier} no está en partida activa (404 Not Found).")
            return None
        elif response is None: # make_api_request returned None due to timeout or persistent error
            print(f"[esta_en_partida] make_api_request devolvió None para {identifier}. Posible timeout o error persistente.")
            return None
        else:  # Unexpected error
            print(f"[esta_en_partida] Error inesperado al verificar partida para {identifier}. Status: {response.status_code}")
            response.raise_for_status() # Esto lanzará una excepción para códigos de error HTTP
    except requests.exceptions.RequestException as e:
        print(f"[esta_en_partida] Error al verificar si el jugador {identifier} está en partida: {e}")
        return None

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida,
    además del nivel, hechizos, runas y AHORA MUCHAS MÁS ESTADÍSTICAS DETALLADAS.
    """
    if len(args) == 4:
        match_id, puuid, api_key, riot_id = args
    elif len(args) == 3:
        match_id, puuid, api_key = args
        riot_id = None
    else:
        print(f"[obtener_info_partida] ERROR: Número inesperado de argumentos: {len(args)}. Argumentos: {args}")
        return None
    
    identifier = riot_id if riot_id else f"PUUID {puuid}"
    print(f"[obtener_info_partida] Obteniendo información para la partida {match_id} de {identifier}.")
    url_match = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={api_key}"
    response_match = make_api_request(url_match)
    if not response_match:
        print(f"[obtener_info_partida] No se pudo obtener la respuesta para la partida {match_id}.")
        return None
    try:
        match_data = response_match.json()
        info = match_data.get('info', {})
        participants = info.get('participants', [])

        if any(p.get('gameEndedInEarlySurrender', False) for p in participants):
            print(f"[obtener_info_partida] Partida {match_id} marcada como remake. No se procesará.")
            return None

        all_participants_details = []
        main_player_data = None
        team_kills = defaultdict(int)

        for p in participants:
            # Obtener el nombre del campeón primero, ya que es más probable que esté presente
            # Usamos el nombre del campeón para obtener el ID si el ID original es None
            raw_champion_id_from_api = p.get('championId')
            champion_name_from_api = p.get('championName') # Nombre del campeón desde la API

            resolved_champion_name = obtener_nombre_campeon(raw_champion_id_from_api) # Intentar resolver por ID
            if resolved_champion_name == "Desconocido" and champion_name_from_api:
                # Si no se encontró por ID, y tenemos el nombre, usar ese nombre
                resolved_champion_name = champion_name_from_api

            participant_summary = {
                "puuid": p.get('puuid'),
                "summoner_name": p.get('riotIdGameName', p.get('summonerName')),
                "champion_name": resolved_champion_name,
                "win": p.get('win', False),
                "kills": p.get('kills', 0),
                "deaths": p.get('deaths', 0),
                "assists": p.get('assists', 0),
                "items": [p.get(f'item{i}', 0) for i in range(7)],
                "team_id": p.get('teamId'),
                "total_damage_dealt_to_champions": p.get('totalDamageDealtToChampions', 0),
                "vision_score": p.get('visionScore', 0),
                "total_cs": p.get('totalMinionsKilled', 0) + p.get('neutralMinionsKilled', 0)
            }
            all_participants_details.append(participant_summary)

            team_id = p.get('teamId')
            team_kills[team_id] += p.get('kills', 0)

            if p.get('puuid') == puuid:
                main_player_data = p

        for detail in all_participants_details:
            p_team_id = detail.get('team_id')
            p_total_team_kills = team_kills.get(p_team_id, 1)
            p_kills = detail.get('kills', 0)
            p_assists = detail.get('assists', 0)
            
            kp = 0
            if p_total_team_kills > 0:
                kp = (p_kills + p_assists) / p_total_team_kills * 100
            detail['kill_participation'] = kp

        if not main_player_data:
            print(f"[obtener_info_partida] Jugador principal {identifier} no encontrado en los participantes de la partida {match_id}.")
            return None

        game_end_timestamp = info.get('gameEndTimestamp', 0) 
        game_duration = info.get('gameDuration', 0)
        
        p = main_player_data
        riot_id_from_match = f"{p.get('riotIdGameName')}#{p.get('riotIdTagline')}"
        raw_champion_id_from_api = p.get('championId')
        champion_name_from_api = p.get('championName')

        # Usar la función auxiliar para resolver el campeón de forma más limpia
        final_champion_name, actual_champion_id = _resolve_champion_info(raw_champion_id_from_api, champion_name_from_api)

        player_team_id = p.get('teamId')
        total_team_kills = team_kills.get(player_team_id, 1)
        player_kills = p.get('kills', 0)
        player_assists = p.get('assists', 0)
        
        kill_participation = 0
        if total_team_kills > 0:
            kill_participation = (player_kills + player_assists) / total_team_kills * 100

        player_items = [p.get(f'item{i}', 0) for i in range(0, 7)]

        spell1_id = p.get('summoner1Id')
        spell2_id = p.get('summoner2Id')
        
        perks = p.get('perks', {})
        perk_main_id = None
        perk_sub_id = None

        if 'styles' in perks and len(perks['styles']) > 0:
            if len(perks['styles'][0]['selections']) > 0:
                perk_main_id = perks['styles'][0]['selections'][0]['perk']
            if len(perks['styles']) > 1:
                perk_sub_id = perks['styles'][1]['style']

        print(f"[obtener_info_partida] Información de partida {match_id} procesada para {identifier}.")
        return {
            "match_id": match_id,
            "puuid": puuid,
            "riot_id": riot_id_from_match,
            "champion_name": final_champion_name,
            "championId": actual_champion_id,
            "win": p.get('win', False),
            "kills": p.get('kills', 0),
            "deaths": p.get('deaths', 0),
            "assists": p.get('assists', 0),
            "kda": (p.get('kills', 0) + p.get('assists', 0)) / max(1, p.get('deaths', 0)),
            "player_items": player_items,
            "game_end_timestamp": game_end_timestamp,
            "queue_id": info.get('queueId'),
            "champion_level": p.get('champLevel'),
            "summoner_spell_1_id": ALL_SUMMONER_SPELLS.get(spell1_id),
            "summoner_spell_2_id": ALL_SUMMONER_SPELLS.get(spell2_id),
            "perk_main_id": ALL_RUNES.get(perk_main_id),
            "perk_sub_id": ALL_RUNES.get(perk_sub_id),
            "total_minions_killed": p.get('totalMinionsKilled', 0),
            "neutral_minions_killed": p.get('neutralMinionsKilled', 0),
            "gold_earned": p.get('goldEarned', 0),
            "gold_spent": p.get('goldSpent', 0),
            "game_duration": game_duration,
            "total_damage_dealt": p.get('totalDamageDealt', 0),
            "total_damage_dealt_to_champions": p.get('totalDamageDealtToChampions', 0),
            "physical_damage_dealt_to_champions": p.get('physicalDamageDealtToChampions', 0),
            "magic_damage_dealt_to_champions": p.get('magicDamageDealtToChampions', 0),
            "true_damage_dealt_to_champions": p.get('true_damage_dealt_to_champions', 0),
            "damage_self_mitigated": p.get('damageSelfMitigated', 0),
            "damage_dealt_to_buildings": p.get('damageDealtToBuildings', 0),
            "damage_dealt_to_objectives": p.get('damageDealtToObjectives', 0),
            "total_heal": p.get('totalHeal', 0),
            "total_heals_on_teammates": p.get('totalHealsOnTeammates', 0),
            "total_damage_shielded_on_teammates": p.get('totalDamageShieldedOnTeammates', 0),
            "vision_score": p.get('visionScore', 0),
            "wards_placed": p.get('wardsPlaced', 0),
            "wards_killed": p.get('wardsKilled', 0),
            "detector_wards_placed": p.get('detectorWardsPlaced', 0),
            "time_ccing_others": p.get('timeCCingOthers', 0),
            "turret_kills": p.get('turretKills', 0),
            "inhibitor_kills": p.get('inhibitorKills', 0),
            "baron_kills": p.get('baronKills', 0),
            "dragon_kills": p.get('dragonKills', 0),
            "total_time_spent_dead": p.get('totalTimeSpentDead', 0),
            "killing_sprees": p.get('killingSprees', 0),
            "largest_killing_spree": p.get('largestKillingSpree', 0),
            "largestMultiKill": p.get('largestMultiKill', 0),
            "pentaKills": p.get('pentaKills', 0),
            "quadraKills": p.get('quadraKills', 0),
            "tripleKills": p.get('tripleKills', 0),
            "doubleKills": p.get('doubleKills', 0),
            "individual_position": p.get('individualPosition', 'N/A'),
            "total_damage_taken": p.get('totalDamageTaken', 0),
            "total_time_cc_dealt": p.get('total_time_cc_dealt', 0),
            "first_blood_kill": p.get('firstBloodKill', False),
            "first_blood_assist": p.get('firstBloodAssist', False),
            "objectives_stolen": p.get('objectivesStolen', 0),
            "kill_participation": kill_participation,

            # --- AÑADIMOS LA LISTA DE TODOS LOS PARTICIPANTES ---
            "all_participants": all_participants_details
        }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[obtener_info_partida] Error procesando los detalles de la partida {match_id}: {e}")
    return None

def leer_cuentas():
    """Lee las cuentas de jugadores desde la API de GitHub para evitar caché."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/cuentas.txt"
    token = os.environ.get('GITHUB_TOKEN')
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    print(f"[leer_cuentas] Leyendo cuentas desde API: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            content = response.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            contenido = file_content.strip().split(';')
            cuentas = []
            for linea in contenido:
                partes = linea.split(',')
                if len(partes) == 2:
                    riot_id = partes[0].strip()
                    jugador = partes[1].strip()
                    cuentas.append((riot_id, jugador))
            print(f"[leer_cuentas] {len(cuentas)} cuentas leídas exitosamente desde API.")
            return cuentas
        else:
            print(f"[leer_cuentas] Error al leer cuentas desde API: {response.status_code}")
            return []
    except Exception as e:
        print(f"[leer_cuentas] Error al leer las cuentas: {e}")
        return []

def calcular_valor_clasificacion(tier, rank, league_points):
    """
    Calcula un valor numérico para la clasificación de un jugador,
    permitiendo ordenar y comparar Elo de forma más sencilla.
    """
    tier_upper = tier.upper()
    
    if tier_upper in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        return 2800 + league_points

    tierOrden = {
        "DIAMOND": 6,
        "EMERALD": 5,
        "PLATINUM": 4,
        "GOLD": 3,
        "SILVER": 2,
        "BRONZE": 1,
        "IRON": 0
    }

    rankOrden = {"I": 3, "II": 2, "III": 1, "IV": 0}

    valor_base_tier = tierOrden.get(tier_upper, 0) * 400
    valor_division = rankOrden.get(rank, 0) * 100

    return valor_base_tier + valor_division + league_points

def leer_peak_elo():
    """Lee los datos de peak Elo desde la API de GitHub para evitar caché de CDN."""
    # 1. Intentar leer de la caché local primero
    with PEAK_ELO_LOCK:
        if PEAK_ELO_CACHE['data'] and (time.time() - PEAK_ELO_CACHE['timestamp'] < PEAK_ELO_TTL):
            return True, PEAK_ELO_CACHE['data']

    # 2. Si no está en caché, leer de la API de GitHub
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    print(f"[leer_peak_elo] Leyendo peak elo desde API: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            # El contenido viene en base64, hay que decodificarlo
            file_content = base64.b64decode(content['content']).decode('utf-8')
            data = json.loads(file_content)
            
            # Actualizar caché
            with PEAK_ELO_LOCK:
                PEAK_ELO_CACHE['data'] = data
                PEAK_ELO_CACHE['timestamp'] = time.time()
                
            print("[leer_peak_elo] Peak elo leído exitosamente desde API.")
            return True, data
        elif resp.status_code == 404:
            print("[leer_peak_elo] Archivo no encontrado en API. Retornando vacío.")
            return True, {}
        else:
            print(f"[leer_peak_elo] Error API: {resp.status_code}")
            resp.raise_for_status()
    except Exception as e:
        print(f"[leer_peak_elo] Error leyendo peak elo: {e}")
        
        # Si falla la API, intentar devolver caché antigua si existe como fallback
        with PEAK_ELO_LOCK:
            if PEAK_ELO_CACHE['data']:
                print("[leer_peak_elo] Retornando caché antigua debido a error en API.")
                return True, PEAK_ELO_CACHE['data']

    return False, {}

def guardar_peak_elo_en_github(peak_elo_dict):
    """Guarda o actualiza el archivo peak_elo.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado para guardar Peak ELO. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_peak_elo_en_github] SHA de peak_elo.json obtenido: {sha}")
    except Exception as e:
        print(f"[guardar_peak_elo_en_github] No se pudo obtener el SHA de peak_elo.json: {e}")

    contenido_json = json.dumps(peak_elo_dict, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": "Actualizar Peak ELO", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        if response.status_code in (200, 201):
            print("[guardar_peak_elo_en_github] Archivo peak_elo.json actualizado correctamente en GitHub.")
        else:
            print(f"[guardar_peak_elo_en_github] Error al actualizar peak_elo.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_peak_elo_en_github] Error en la petición PUT a GitHub para peak_elo.json: {e}")

def leer_puuids():
    """Lee el archivo de PUUIDs desde la API de GitHub para evitar caché de CDN."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/puuids.json"
    token = os.environ.get('GITHUB_TOKEN')
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    print(f"[leer_puuids] Leyendo PUUIDs desde API: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            print("[leer_puuids] PUUIDs leídos exitosamente desde API.")
            return json.loads(file_content)
        elif resp.status_code == 404:
            print("[leer_puuids] El archivo puuids.json no existe en API, se creará uno nuevo.")
            return {}
        else:
            print(f"[leer_puuids] Error API: {resp.status_code}")
            resp.raise_for_status()
    except Exception as e:
        print(f"[leer_puuids] Error leyendo puuids.json de API: {e}")
    return {}

# --- CACHÉ PARA LP HISTORY ---
LP_HISTORY_CACHE = {
    "data": {},
    "timestamp": 0
}
LP_HISTORY_LOCK = threading.Lock()
LP_HISTORY_TTL = 300 # 5 minutos de caché

def leer_lp_history():
    """Lee el archivo lp_history.json desde GitHub, con caché en memoria."""
    with LP_HISTORY_LOCK:
        if LP_HISTORY_CACHE['data'] and (time.time() - LP_HISTORY_CACHE['timestamp'] < LP_HISTORY_TTL):
            return LP_HISTORY_CACHE['data']

    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/lp_history.json"
    token = os.environ.get('GITHUB_TOKEN')
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    print(f"[leer_lp_history] Leyendo lp_history.json desde API: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            data = json.loads(file_content)
            with LP_HISTORY_LOCK:
                LP_HISTORY_CACHE['data'] = data
                LP_HISTORY_CACHE['timestamp'] = time.time()
            print("[leer_lp_history] lp_history.json leído y cacheado exitosamente.")
            return data
        elif resp.status_code == 404:
            print("[leer_lp_history] El archivo lp_history.json no existe en API.")
            return {}
        else:
            print(f"[leer_lp_history] Error API: {resp.status_code}")
            resp.raise_for_status()
    except Exception as e:
        print(f"[leer_lp_history] Error leyendo lp_history.json de API: {e}")
    
    with LP_HISTORY_LOCK:
        if LP_HISTORY_CACHE['data']:
            return LP_HISTORY_CACHE['data']
            
    return {}

def guardar_puuids_en_github(puuid_dict):
    """Guarda o actualiza el archivo puuids.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/puuids.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado para guardar PUUIDs. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=30) # Aumentado timeout
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_puuids_en_github] SHA de puuids.json obtenido: {sha}")
    except Exception as e:
        print(f"[guardar_puuids_en_github] No se pudo obtener el SHA de puuids.json: {e}")

    contenido_json = json.dumps(puuid_dict, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": "Actualizar PUUIDs", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=data, timeout=30) # Aumentado timeout
        if response.status_code in (200, 201):
            print("[guardar_puuids_en_github] Archivo puuids.json actualizado correctamente en GitHub.")
        else:
            print(f"[guardar_puuids_en_github] Error al actualizar puuids.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_puuids_en_github] Error en la petición PUT a GitHub para puuids.json: {e}")


def _read_player_match_history_from_github(puuid, riot_id=None):
    """Lee el historial de partidas de un jugador directamente desde la API de GitHub."""
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/match_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    print(f"[_read_player_match_history_from_github] Leyendo historial para {identifier} desde API: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            print(f"[_read_player_match_history_from_github] Historial para {identifier} leído exitosamente de API GitHub.")
            return json.loads(file_content)
        elif resp.status_code == 404:
            print(f"[_read_player_match_history_from_github] No se encontró historial para {identifier} en API GitHub. Se creará uno nuevo.")
            return {}
        else:
            print(f"[_read_player_match_history_from_github] Error API: {resp.status_code}")
            resp.raise_for_status()
    except Exception as e:
        print(f"[_read_player_match_history_from_github] Error leyendo el historial para {identifier} de API GitHub: {e}")
    return {}

def get_player_match_history(puuid, riot_id=None):
    """
    Obtiene el historial de partidas de un jugador, usando la caché en memoria primero.
    Si no está en caché o está expirado, lo lee desde GitHub y lo cachea.
    """
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    with PLAYER_MATCH_HISTORY_LOCK:
        cached_data = PLAYER_MATCH_HISTORY_CACHE.get(puuid)
        
        if cached_data and (time.time() - cached_data['timestamp'] < PLAYER_MATCH_HISTORY_CACHE_TIMEOUT):
            print(f"[get_player_match_history] Devolviendo historial cacheados para {identifier}.")
            return cached_data['data']
        
        print(f"[get_player_match_history] Historial para {identifier} no cacheados o estancados. Leyendo de GitHub.")
        historial = _read_player_match_history_from_github(puuid, riot_id=riot_id)
        PLAYER_MATCH_HISTORY_CACHE[puuid] = {
            'data': historial,
            'timestamp': time.time()
        }
        print(f"[get_player_match_history] Historial para {identifier} leído de GitHub y cacheado.")
        return historial


def guardar_historial_jugador_github(puuid, historial_data, riot_id=None):
    """Guarda o actualiza el historial de partidas de un jugador en GitHub."""
    identifier = riot_id if riot_id else puuid
    success = False
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/match_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"[guardar_historial_jugador_github] ERROR: Token de GitHub no encontrado para guardar historial de {puuid}. No se guardará el archivo.", flush=True)
        print(f"[guardar_historial_jugador_github] ERROR: Token de GitHub no encontrado para guardar historial de {identifier}. No se guardará el archivo.", flush=True)
        return False

    headers = {"Authorization": f"token {token}"}
    sha = None
    try:
        # Aquí se usa _read_player_match_history_from_github para obtener el SHA, 
        # porque necesitamos la versión actual del archivo en GitHub para actualizarlo correctamente.
        # No usamos get_player_match_history aquí ya que queremos el SHA del archivo en el repositorio.
        response = requests.get(url, headers=headers, timeout=30) # Aumentado timeout
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_historial_jugador_github] SHA del historial de {puuid} obtenido: {sha}.", flush=True)
            print(f"[guardar_historial_jugador_github] SHA del historial de {identifier} obtenido: {sha}.", flush=True)
        elif response.status_code == 404:
            print(f"[guardar_historial_jugador_github] Archivo {puuid}.json no existe en GitHub, se creará uno nuevo.", flush=True)
            print(f"[guardar_historial_jugador_github] Archivo {identifier}.json no existe en GitHub, se creará uno nuevo.", flush=True)
        else:
            print(f"[guardar_historial_jugador_github] Error al obtener SHA del historial de {puuid}: {response.status_code} - {response.text}", flush=True)
            print(f"[guardar_historial_jugador_github] Error al obtener SHA del historial de {identifier}: {response.status_code} - {response.text}", flush=True)
            return False # Salir si no se puede obtener el SHA
    except Exception as e:
        print(f"[guardar_historial_jugador_github] Excepción al obtener SHA del historial de {puuid}: {e}", flush=True)
        print(f"[guardar_historial_jugador_github] Excepción al obtener SHA del historial de {identifier}: {e}", flush=True)
        return False # Salir si hay una excepción

    contenido_json = json.dumps(historial_data, indent=2, ensure_ascii=False) # Añadido ensure_ascii=False
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": f"Actualizar historial de partidas para {puuid}", "content": contenido_b64, "branch": "main"}
    data = {"message": f"Actualizar historial de partidas para {identifier}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        print(f"[guardar_historial_jugador_github] Intentando guardar historial para {puuid} en GitHub. SHA: {sha}", flush=True)
        print(f"[guardar_historial_jugador_github] Intentando guardar historial para {identifier} en GitHub. SHA: {sha}", flush=True)
        response = requests.put(url, headers=headers, json=data, timeout=30) # Aumentado timeout
        if response.status_code in (200, 201):
            print(f"[guardar_historial_jugador_github] Historial de {puuid}.json actualizado correctamente en GitHub. Status: {response.status_code}", flush=True)
            print(f"[guardar_historial_jugador_github] Historial de {identifier}.json actualizado correctamente en GitHub. Status: {response.status_code}", flush=True)
            success = True
        else:
            print(f"[guardar_historial_jugador_github] ERROR: Fallo al actualizar historial de {puuid}.json: {response.status_code} - {response.text}", flush=True)
            print(f"[guardar_historial_jugador_github] ERROR: Fallo al actualizar historial de {identifier}.json: {response.status_code} - {response.text}", flush=True)
    except Exception as e:
        print(f"[guardar_historial_jugador_github] ERROR: Excepción en la petición PUT a GitHub para el historial de {puuid}: {e}", flush=True)
        print(f"[guardar_historial_jugador_github] ERROR: Excepción en la petición PUT a GitHub para el historial de {identifier}: {e}", flush=True)
    return success


# --- CACHÉ EN MEMORIA PARA SNAPSHOTS DE LP (EVITA LLAMADAS EXTRA A API) ---
LP_SNAPSHOTS_BUFFER = {}
LP_SNAPSHOTS_BUFFER_LOCK = threading.Lock()
LP_SNAPSHOTS_LAST_SAVE = 0
LP_SNAPSHOTS_SAVE_INTERVAL = 3600  # Guardar snapshots cada 1 hora en GitHub

def _calcular_lp_inmediato(match, current_elo_by_queue, matches_by_queue):
    """
    Calcula el LP ganado/perdido en una partida usando snapshots históricos.
    OPTIMIZACIÓN: Recibe diccionario pre-indexado {queue_id: [matches]} para O(1) acceso.
    
    Retorna: {"lp_change": valor, "pre_game_elo": X, "post_game_elo": Y} o None
    """
    game_end_ts = match.get('game_end_timestamp', 0)
    queue_id = match.get('queue_id')
    queue_name = "RANKED_SOLO_5x5" if queue_id == 420 else "RANKED_FLEX_SR" if queue_id == 440 else None
    
    if not queue_name:
        return None
    
    # El Elo post-game es el actual
    post_game_elo = current_elo_by_queue.get(queue_name)
    if not post_game_elo:
        return None
    
    # OPTIMIZACIÓN: Buscar en diccionario pre-indexado en lugar de filtrar O(n) cada vez
    queue_matches = matches_by_queue.get(queue_id, [])
    if not queue_matches:
        return None
    
    # Encontrar la partida anterior más reciente con Elo post-game válido
    previous_matches = [
        m for m in queue_matches
        if m.get('game_end_timestamp', 0) < game_end_ts and
           m.get('post_game_valor_clasificacion') is not None
    ]
    
    if not previous_matches:
        return None
    
    # La partida más reciente anterior = Elo pre-game aproximado
    most_recent_match = max(previous_matches, key=lambda x: x.get('game_end_timestamp', 0))
    pre_game_elo = most_recent_match.get('post_game_valor_clasificacion')
    
    if not pre_game_elo:
        return None
    
    lp_change = post_game_elo - pre_game_elo
    
    return {
        'lp_change': lp_change,
        'pre_game_elo': pre_game_elo,
        'post_game_elo': post_game_elo
    }


def _registrar_snapshot_lp(puuid, elo_info, riot_id=None):
    """
    Registra un snapshot de LP en memoria sin hacer llamadas a API.
    Se guarda en GitHub cada hora automáticamente.
    """
    identifier = riot_id if riot_id else f"PUUID: {puuid}"
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    
    with LP_SNAPSHOTS_BUFFER_LOCK:
        if puuid not in LP_SNAPSHOTS_BUFFER:
            LP_SNAPSHOTS_BUFFER[puuid] = {
                "RANKED_SOLO_5x5": [],
                "RANKED_FLEX_SR": []
            }
        
        for entry in elo_info:
            queue_type = entry.get('queueType')
            if queue_type in ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]:
                valor = calcular_valor_clasificacion(
                    entry.get('tier', 'Sin rango'),
                    entry.get('rank', ''),
                    entry.get('leaguePoints', 0)
                )
                
                # Registrar snapshot
                LP_SNAPSHOTS_BUFFER[puuid][queue_type].append({
                    "timestamp": timestamp,
                    "elo": valor,
                    "league_points_raw": entry.get('leaguePoints', 0)
                })
                print(f"[_registrar_snapshot_lp] Snapshot registrado para {identifier} en {queue_type}: {valor} ELO")

def _guardar_snapshots_en_github():
    """
    Guarda los snapshots acumulados en GitHub.
    Se ejecuta periódicamente sin bloquear el flujo principal.
    """
    global LP_SNAPSHOTS_LAST_SAVE
    
    with LP_SNAPSHOTS_BUFFER_LOCK:
        if not LP_SNAPSHOTS_BUFFER:
            return
        
        # Leer historial existente desde GitHub
        lp_history, lp_history_sha = _read_json_from_github_internal(LP_HISTORY_FILE_PATH, os.environ.get('GITHUB_TOKEN'))
        
        # Combinar snapshots en memoria con histórico
        for puuid, queues_data in LP_SNAPSHOTS_BUFFER.items():
            if puuid not in lp_history:
                lp_history[puuid] = {"RANKED_SOLO_5x5": [], "RANKED_FLEX_SR": []}
            
            for queue_type, snapshots in queues_data.items():
                lp_history[puuid][queue_type].extend(snapshots)
                # Limitar a últimos 1000 snapshots por cola para no crecer infinitamente
                lp_history[puuid][queue_type] = lp_history[puuid][queue_type][-1000:]
        
        # Guardar en GitHub
        success = _write_to_github_internal(LP_HISTORY_FILE_PATH, lp_history, lp_history_sha, os.environ.get('GITHUB_TOKEN'))
        
        if success:
            LP_SNAPSHOTS_BUFFER.clear()
            LP_SNAPSHOTS_LAST_SAVE = time.time()
            print("[_guardar_snapshots_en_github] Snapshots guardados exitosamente en GitHub")

def _read_json_from_github_internal(file_path, token):
    """Lee un archivo JSON desde GitHub (función auxiliar interna)."""
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/{file_path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            return json.loads(file_content), content.get('sha')
        elif resp.status_code == 404:
            return {}, None
    except Exception as e:
        print(f"[_read_json_from_github_internal] Error: {e}")
    return {}, None

def _write_to_github_internal(file_path, data, sha, token):
    """Escribe un archivo JSON en GitHub (función auxiliar interna)."""
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/{file_path}"
    headers = {"Authorization": f"token {token}"}
    
    content_json = json.dumps(data, indent=2)
    content_b64 = base64.b64encode(content_json.encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": f"Actualizar {file_path}",
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"[_write_to_github_internal] Error: {e}")
    return False


def _recalcular_lp_partidas_historicas(puuid, all_matches):
    """
    Recalcula LP para partidas históricas que no tienen LP asignado.
    IMPORTANTE: Debe ejecutarse una sola vez al principio.
    """
    print(f"[_recalcular_lp_partidas_historicas] Iniciando recálculo de LP para {puuid}...")
    
    # Crear índice por cola para acceso rápido
    matches_by_queue = defaultdict(list)
    for m in all_matches:
        matches_by_queue[m.get('queue_id')].append(m)
    
    # Ordenar matches por timestamp para asegurar que tenemos los anteriores primero
    for queue_id in matches_by_queue:
        matches_by_queue[queue_id] = sorted(matches_by_queue[queue_id], key=lambda x: x.get('game_end_timestamp', 0))
    
    recalculated = 0
    for match in all_matches:
        # Solo recalcular si no tiene LP
        if match.get('lp_change_this_game') is None:
            queue_id = match.get('queue_id')
            if queue_id not in [420, 440]:
                continue
            
            queue_matches = matches_by_queue.get(queue_id, [])
            game_end_ts = match.get('game_end_timestamp', 0)
            
            # Buscar la partida anterior en la misma cola
            previous_matches = [
                m for m in queue_matches
                if m.get('game_end_timestamp', 0) < game_end_ts and
                   m.get('post_game_valor_clasificacion') is not None
            ]
            
            if previous_matches:
                most_recent_match = max(previous_matches, key=lambda x: x.get('game_end_timestamp', 0))
                pre_game_elo = most_recent_match.get('post_game_valor_clasificacion')
                
                # Obtener post-game elo del match actual
                post_game_elo = match.get('post_game_valor_clasificacion')
                if post_game_elo is None:
                    post_game_elo = pre_game_elo  # Fallback si no existe
                
                lp_change = post_game_elo - pre_game_elo if pre_game_elo else 0
                
                match['lp_change_this_game'] = lp_change
                match['pre_game_valor_clasificacion'] = pre_game_elo
                match['post_game_valor_clasificacion'] = post_game_elo
                recalculated += 1
    
    if recalculated > 0:
        print(f"[_recalcular_lp_partidas_historicas] Recalculados {recalculated} LP para {puuid}")
    return all_matches

# Configuración de Gemini
gemini_client = None
if os.environ.get("GOOGLE_API_KEY"):
    gemini_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

# Esquema para que Gemini responda siempre con el mismo JSON
class AnalisisSoloQ(BaseModel):
    analisis_individual: str
    valoracion_companeros: str
    valoracion_rivales: str
    aspectos_mejora: str
    puntos_fuertes: str
    recomendaciones: str
    otros: str

def obtener_analisis_github(puuid):
    """Recupera el análisis previo de un jugador desde GitHub"""
    path = f"analisisIA/{puuid}.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            content = r.json()
            decoded = json.loads(base64.b64decode(content['content']).decode('utf-8'))
            return decoded, content['sha']
    except: pass
    return None, None

def gestionar_permiso_jugador(puuid):
    """Consulta el interruptor individual (SI/NO) en GitHub"""
    path = f"config/permisos/{puuid}.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        content = r.json()
        decoded = json.loads(base64.b64decode(content['content']).decode('utf-8'))
        return decoded.get("permitir_llamada") == "SI", content['sha']
    # Si no existe, lo habilitamos por defecto creando el archivo
    elif r.status_code == 404:
        actualizar_archivo_github(path, {"permitir_llamada": "SI", "razon": "Inicializado"})
        return True, None
    return False, None

def actualizar_archivo_github(path, datos, sha=None):
    """Función genérica para escribir archivos JSON en GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {
        "message": f"Update {path}",
        "content": base64.b64encode(json.dumps(datos, indent=2, ensure_ascii=False).encode('utf-8')).decode('utf-8')
    }
    if sha: payload["sha"] = sha
    requests.put(url, headers=headers, json=payload)
    
def procesar_jugador(args_tuple):
    """
    Procesa los datos de un solo jugador.
    Implementa una lógica de actualización inteligente para reducir llamadas a la API.
    Solo realiza operaciones costosas si el jugador está o acaba de estar en partida.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list, check_in_game_this_update = args_tuple
    riot_id, jugador_nombre = cuenta
    # print(f"[procesar_jugador] Procesando jugador: {riot_id}")

    if not puuid:
        print(f"[procesar_jugador] ADVERTENCIA: Omitiendo procesamiento para {riot_id} porque no se pudo obtener su PUUID.")
        return []

    # 1. Sondeo ligero: usar la clave secundaria para esta llamada frecuente.
    game_data = esta_en_partida(api_key_spectator, puuid, riot_id=riot_id)
    is_currently_in_game = game_data is not None

    # 2. Decisión inteligente: ¿necesitamos una actualización completa?
    was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
    
    # The full update is only done if it's a new player, if they are in game now,
    # or if they just finished a game (was in game before but not anymore).
    needs_full_update = not old_data_list or is_currently_in_game or was_in_game_before

    # OPTIMIZACIÓN: Solo obtener Elo si necesitamos actualización completa para evitar llamadas innecesarias a jugadores inactivos
    if not needs_full_update and old_data_list:
        # Jugador inactivo: devolver datos antiguos con estado actualizado
        print(f"[procesar_jugador] Jugador {riot_id} inactivo. Retornando datos cacheados sin actualizar Elo.")
        for data in old_data_list:
            data['en_partida'] = is_currently_in_game
        return old_data_list

    # Solo obtener Elo si necesitamos actualización completa
    elo_info = obtener_elo(api_key_main, puuid, riot_id=riot_id)
    if not elo_info:
        print(f"[procesar_jugador] No se pudo obtener el Elo para {riot_id}. No se puede rastrear LP ni actualizar datos.")
        return old_data_list if old_data_list else []

    # OPTIMIZACIÓN: Convertir elo_info a un diccionario por queue para cálculo rápido de LP
    current_elo_by_queue = {}
    for entry in elo_info:
        queue_type = entry.get('queueType')
        if queue_type in ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]:
            current_elo_by_queue[queue_type] = calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('leaguePoints', 0)
            )

    # Obtener historial de partidas existente (si lo hay)
    player_match_history_data = get_player_match_history(puuid, riot_id=riot_id)
    existing_matches = player_match_history_data.get('matches', [])
    existing_match_ids = {m['match_id'] for m in existing_matches}
    
    # Filtrar remakes guardados para no volver a procesarlos
    remakes_guardados = set(player_match_history_data.get('remakes', []))

    new_matches_details = [] # Para almacenar los detalles de las partidas recién obtenidas

    if needs_full_update:
        print(f"[procesar_jugador] Actualizando datos completos para {riot_id} (estado: {'en partida' if is_currently_in_game else 'recién terminada'}).")
        
        # 3. Obtener nuevos IDs de partidas de la API - SOLO SI ES NECESARIO
        all_match_ids = obtener_historial_partidas(api_key_main, puuid, count=100) # Pedir más partidas
        if all_match_ids:
            # Filtrar partidas de la temporada actual y nuevas (no guardadas previamente)
            new_match_ids_to_process = []
            for match_id in all_match_ids:
                # Comprobar si la partida ya fue procesada o es un remake conocido
                if match_id not in existing_match_ids and match_id not in remakes_guardados:
                    new_match_ids_to_process.append(match_id)
            
            # Limitar a un número razonable de nuevas partidas para procesar en un ciclo
            # para evitar sobrecargar la API en caso de que un jugador tenga muchas partidas nuevas.
            MAX_NEW_MATCHES_PER_UPDATE = 30
            if len(new_match_ids_to_process) > MAX_NEW_MATCHES_PER_UPDATE:
                print(f"[procesar_jugador] Limiting new matches for {riot_id} from {len(new_match_ids_to_process)} to {MAX_NEW_MATCHES_PER_UPDATE}.")
                new_match_ids_to_process = new_match_ids_to_process[:MAX_NEW_MATCHES_PER_UPDATE]


            if new_match_ids_to_process:
                print(f"[procesar_jugador] Procesando {len(new_match_ids_to_process)} nuevas partidas para {riot_id}.")
                tareas_partidas = [
                    (match_id, puuid, api_key_main, riot_id) for match_id in new_match_ids_to_process
                ]
                with ThreadPoolExecutor(max_workers=5) as executor:
                    resultados_partidas = executor.map(obtener_info_partida, tareas_partidas)
                
                # OPTIMIZACIÓN: Pre-indexar partidas por cola para _calcular_lp_inmediato O(n) -> O(1)
                matches_by_queue = defaultdict(list)
                for m in existing_matches:
                    matches_by_queue[m.get('queue_id')].append(m)
                
                for resultado in resultados_partidas:
                    if resultado:
                        # Asegurarse de que el game_end_timestamp sea posterior al inicio de la temporada
                        if resultado.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP:
                            # OPTIMIZACIÓN: Calcular LP inmediatamente cuando se obtiene la partida
                            lp_info = _calcular_lp_inmediato(resultado, current_elo_by_queue, matches_by_queue)
                            if lp_info:
                                resultado['lp_change_this_game'] = lp_info['lp_change']
                                resultado['pre_game_valor_clasificacion'] = lp_info['pre_game_elo']
                                resultado['post_game_valor_clasificacion'] = lp_info['post_game_elo']
                            
                            new_matches_details.append(resultado)
                        else:
                            print(f"[procesar_jugador] Ignorando partida {resultado.get('match_id')} para {riot_id} por ser anterior a la temporada actual.")
                    else:
                        print(f"[procesar_jugador] Advertencia: Una de las nuevas partidas para {riot_id} no se pudo procesar.")
                print(f"[procesar_jugador] {len(new_matches_details)} partidas nuevas procesadas exitosamente para {riot_id}.")
            else:
                print(f"[procesar_jugador] No hay nuevas partidas para procesar para {riot_id} en la temporada actual.")
        else:
            print(f"[procesar_jugador] No se pudo obtener ningún ID de partida de la API para {riot_id}.")
        
        # Combinar partidas existentes con las nuevas, eliminando duplicados si los hubiera
        updated_matches = {m['match_id']: m for m in existing_matches}
        for new_match in new_matches_details:
            updated_matches[new_match['match_id']] = new_match
        
        # Reconvertir a lista y ordenar por fecha (más reciente primero)
        all_matches_for_player = sorted(updated_matches.values(), key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
        
        # OPTIMIZACIÓN: Recalcular LP para partidas históricas que no lo tienen
        # Se ejecuta UNA sola vez la primera vez que se cargan los datos
        matches_sin_lp = sum(1 for m in all_matches_for_player if m.get('lp_change_this_game') is None)
        if matches_sin_lp > 0 and len(new_matches_details) == 0:
            # Solo si hay matches sin LP y NO hay nuevas partidas (indica carga inicial)
            all_matches_for_player = _recalcular_lp_partidas_historicas(puuid, all_matches_for_player)
            print(f"[procesar_jugador] LP recalculado para partidas históricas de {riot_id}")

        # Actualizar los remakes guardados (si se detectaron nuevos remakes durante obtener_info_partida)
        newly_detected_remakes = {
            m['match_id'] for m in new_matches_details
            if not m # Si el resultado es None, implica un remake o error irrecuperable
            # TODO: Add explicit check for remake flag from riot_api.obtener_info_partida
        }
        updated_remakes = remakes_guardados.union(newly_detected_remakes)


        # Guardar historial actualizado en GitHub
        updated_historial_data = {
            'matches': all_matches_for_player,
            'last_updated': time.time(),
            'remakes': list(updated_remakes)
        }
        guardar_historial_jugador_github(puuid, updated_historial_data, riot_id=riot_id)
        
        # Actualizar la caché en memoria inmediatamente después de guardar
        with PLAYER_MATCH_HISTORY_LOCK:
            PLAYER_MATCH_HISTORY_CACHE[puuid] = {
                'data': updated_historial_data,
                'timestamp': time.time()
            }
        print(f"[procesar_jugador] Historial de partidas de {riot_id} actualizado y guardado en GitHub.")
    
    # Continuar con el procesamiento de datos del jugador para la visualización en el frontend
    riot_id_modified = riot_id.replace("#", "-")
    url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
    url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"
    
    datos_jugador_list = []
    current_champion_id = None
    if is_currently_in_game and game_data:
        for participant in game_data.get("participants", []):
            if participant["puuid"] == puuid:
                # Intenta obtener championId, si no está, usa championName para buscar en ALL_CHAMPION_NAMES_TO_IDS
                champ_id_from_game_data = participant.get("championId")
                champ_name_from_game_data = participant.get("championName")
                
                if champ_id_from_game_data is not None:
                    current_champion_id = champ_id_from_game_data
                elif champ_name_from_game_data:
                    current_champion_id = ALL_CHAMPION_NAMES_TO_IDS.get(champ_name_from_game_data)
                    if current_champion_id is None:
                        print(f"[procesar_jugador] ADVERTENCIA: No se pudo encontrar championId para '{champ_name_from_game_data}' en ALL_CHAMPION_NAMES_TO_IDS para el jugador {riot_id} en partida activa.")
                break

    for entry in elo_info:
        nombre_campeon = obtener_nombre_campeon(current_champion_id) if current_champion_id else "Desconocido"
        queue_type = entry.get('queueType', 'Desconocido')
        tier = entry.get('tier', 'Sin rango')
        rank = entry.get('rank', '')
        league_points = entry.get('leaguePoints', 0)
        
        valor_clasificacion = calcular_valor_clasificacion(tier, rank, league_points)
        
        datos_jugador = {
            "game_name": riot_id,
            "queue_type": queue_type,
            "tier": tier,
            "rank": rank,
            "league_points": league_points,
            "wins": entry.get('wins', 0),
            "losses": entry.get('losses', 0),
            "jugador": jugador_nombre,
            "url_perfil": url_perfil,
            "puuid": puuid, # Se añade para usarlo como clave en cachés
            "url_ingame": url_ingame,
            "en_partida": is_currently_in_game,
            "valor_clasificacion": valor_clasificacion,
            "nombre_campeon": nombre_campeon,
            "champion_id": current_champion_id if current_champion_id else "Desconocido"
        }
        datos_jugador_list.append(datos_jugador)
    
    # OPTIMIZACIÓN: Registrar snapshot de LP sin hacer llamadas extras
    _registrar_snapshot_lp(puuid, elo_info, riot_id)
    
    print(f"[procesar_jugador] Datos de {riot_id} procesados y listos para caché.")
    return datos_jugador_list

def actualizar_cache():
    """
    Esta función realiza el trabajo pesado: obtiene todos los datos de la API
    y actualiza la caché global. Está diseñada para ser ejecutada en segundo plano.
    """
    print("[actualizar_cache] Iniciando actualización de la caché principal...")
    api_key_main = os.environ.get('RIOT_API_KEY')
    api_key_spectator = os.environ.get('RIOT_API_KEY_2', api_key_main)
    
    if not api_key_main:
        print("[actualizar_cache] ERROR CRÍTICO: La variable de entorno RIOT_API_KEY no está configurada. La aplicación no puede funcionar correctamente.")
        return
    
    with cache_lock:
        old_cache_data = cache.get('datos_jugadores', [])
    
    old_data_map_by_puuid = {}
    for d in old_cache_data:
        puuid = d.get('puuid')
        if puuid:
            if puuid not in old_data_map_by_puuid:
                old_data_map_by_puuid[puuid] = []
            old_data_map_by_puuid[puuid].append(d)

    cuentas = leer_cuentas()

    with cache_lock:
        cache['update_count'] = cache.get('update_count', 0) + 1
    check_in_game_this_update = cache['update_count'] % 2 == 1
    # print(f"[actualizar_cache] Check de partida activa en este ciclo: {check_in_game_this_update}")

    puuid_dict = leer_puuids()
    puuids_actualizados = False

    for riot_id, _ in cuentas:
        if riot_id not in puuid_dict:
            print(f"[actualizar_cache] No se encontró PUUID para {riot_id}. Obteniéndolo de la API...")
            game_name, tag_line = riot_id.split('#')[0], riot_id.split('#')[1]
            puuid_info = obtener_puuid(api_key_main, game_name, tag_line)
            if puuid_info and 'puuid' in puuid_info:
                puuid_dict[riot_id] = puuid_info['puuid']
                puuids_actualizados = True
                print(f"[actualizar_cache] PUUID {puuid_info['puuid']} obtenido y añadido para {riot_id}.")
            else:
                print(f"[actualizar_cache] Fallo al obtener PUUID para {riot_id}.")

    if puuids_actualizados:
        guardar_puuids_en_github(puuid_dict)

    todos_los_datos = []
    tareas = []
    for cuenta in cuentas:
        riot_id = cuenta[0]
        puuid = puuid_dict.get(riot_id)
        old_data_for_player = old_data_map_by_puuid.get(puuid)
        tareas.append((cuenta, puuid, api_key_main, api_key_spectator, 
                      old_data_for_player, check_in_game_this_update))

    print(f"[actualizar_cache] Procesando {len(tareas)} jugadores en paralelo.")
    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    print(f"[actualizar_cache] Calculando estadísticas de campeones y LP en 24h para {len(todos_los_datos)} entradas de jugador.")
    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    for jugador in todos_los_datos:
        puuid = jugador.get('puuid')
        queue_type = jugador.get('queue_type')
        queue_id = queue_map.get(queue_type)

        jugador['top_champion_stats'] = []
        jugador['lp_change_24h'] = 0

        if not puuid or not queue_id:
            continue
        
        # AHORA LEE DE LA NUEVA FUNCIÓN QUE USA LA CACHÉ
        historial = get_player_match_history(puuid, riot_id=jugador.get('game_name')) 
        all_matches_for_player = historial.get('matches', [])

        # CALCULAR DINÁMICAMENTE el resumen de 24h
        now_utc = datetime.now(timezone.utc)
        one_day_ago_timestamp_ms = int((now_utc - timedelta(days=1)).timestamp() * 1000)
        
        lp_change_24h = 0
        wins_24h = 0
        losses_24h = 0

        # Filtrar partidas de la cola correcta para el cálculo
        partidas_de_la_cola_en_24h = [
            m for m in all_matches_for_player 
            if m.get('queue_id') == queue_id and m.get('game_end_timestamp', 0) > one_day_ago_timestamp_ms
        ]

        for match in partidas_de_la_cola_en_24h:
            lp_change = match.get('lp_change_this_game')
            if lp_change is not None:
                lp_change_24h += lp_change
            
            if match.get('win'):
                wins_24h += 1
            else:
                losses_24h += 1

        jugador['lp_change_24h'] = lp_change_24h
        jugador['wins_24h'] = wins_24h
        jugador['losses_24h'] = losses_24h

        partidas_jugador = [
            p for p in all_matches_for_player
            if p.get('queue_id') == queue_id and
               p.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP
        ]

        # Sobrescribir victorias y derrotas con los datos calculados localmente para la temporada actual.
        # Esto fuerza que se muestren a 0 si no hay partidas nuevas, ignorando los datos "viejos" de la API de Riot.
        jugador['wins'] = sum(1 for p in partidas_jugador if p.get('win'))
        jugador['losses'] = len(partidas_jugador) - jugador['wins']

        # Calcular KDA general para la temporada
        total_kills = sum(p.get('kills', 0) for p in partidas_jugador)
        total_deaths = sum(p.get('deaths', 0) for p in partidas_jugador)
        total_assists = sum(p.get('assists', 0) for p in partidas_jugador)
        jugador['kda'] = (total_kills + total_assists) / total_deaths if total_deaths > 0 else float(total_kills + total_assists)

        # Calcular rachas de victorias/derrotas para el jugador en esta cola
        rachas = calcular_rachas(partidas_jugador)
        jugador['current_win_streak'] = rachas['current_win_streak']
        jugador['current_loss_streak'] = rachas['current_loss_streak']
        # Guardar también las rachas máximas, aunque no se usen en la vista principal, pueden ser útiles.
        jugador['max_win_streak'] = rachas['max_win_streak']
        jugador['max_loss_streak'] = rachas['max_loss_streak']

        if not partidas_jugador:
            continue

        contador_campeones = Counter(p['champion_name'] for p in partidas_jugador)
        if not contador_campeones:
            continue
        
        top_3_campeones = contador_campeones.most_common(3)
        
        # OPTIMIZACIÓN: Pre-indexar partidas por campeón para evitar O(n²) búsquedas
        partidas_por_campeon = defaultdict(list)
        for p in partidas_jugador:
            partidas_por_campeon[p['champion_name']].append(p)

        for campeon_nombre, _ in top_3_campeones:
            partidas_del_campeon = partidas_por_campeon[campeon_nombre]
            
            total_partidas = len(partidas_del_campeon)
            wins = sum(1 for p in partidas_del_campeon if p.get('win'))
            win_rate = (wins / total_partidas * 100) if total_partidas > 0 else 0

            total_kills = sum(p.get('kills', 0) for p in partidas_del_campeon)
            total_deaths = sum(p.get('deaths', 0) for p in partidas_del_campeon)
            total_assists = sum(p.get('assists', 0) for p in partidas_del_campeon)
            
            avg_kills = total_kills / total_partidas if total_partidas > 0 else 0
            avg_deaths = total_deaths / total_partidas if total_partidas > 0 else 0
            avg_assists = total_assists / total_partidas if total_partidas > 0 else 0

            kda = (total_kills + total_assists) / total_deaths if total_deaths > 0 else float(total_kills + total_assists)

            best_kda_match_info = None
            if partidas_del_campeon:
                def get_kda_for_match(p):
                    k = p.get('kills', 0)
                    d = p.get('deaths', 0)
                    a = p.get('assists', 0)
                    return (k + a) / d if d > 0 else float(k + a)

                best_match = max(partidas_del_campeon, key=get_kda_for_match)
                
                best_kda_value = get_kda_for_match(best_match)

                best_kda_match_info = {
                    "kda": best_kda_value,
                    "kills": best_match.get('kills', 0),
                    "deaths": best_match.get('deaths', 0),
                    "assists": best_match.get('assists', 0),
                    "timestamp": best_match.get('game_end_timestamp')
                }

            jugador['top_champion_stats'].append({
                "champion_name": campeon_nombre,
                "win_rate": win_rate,
                "games_played": total_partidas,
                "kda": kda,
                "kills": total_kills,
                "deaths": total_deaths,
                "assists": total_assists,
                "wins": wins,
                "losses": total_partidas - wins,
                "avg_kills": avg_kills,
                "avg_deaths": avg_deaths,
                "avg_assists": avg_assists,
                "best_kda_match": best_kda_match_info
            })

    with cache_lock:
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()
    
    # OPTIMIZACIÓN: Guardar snapshots acumulados en GitHub cada hora
    global LP_SNAPSHOTS_LAST_SAVE
    if time.time() - LP_SNAPSHOTS_LAST_SAVE > LP_SNAPSHOTS_SAVE_INTERVAL:
        print("[actualizar_cache] Guardando snapshots de LP acumulados...")
        _guardar_snapshots_en_github()
    
    print("[actualizar_cache] Actualización de la caché principal completada.")

def obtener_datos_jugadores():
    """Obtiene los datos cacheados de los jugadores."""
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    """Genera una clave para el peak ELO usando el PUUID del jugador y la temporada actual."""
    return f"{ACTIVE_SPLIT_KEY}|{jugador['queue_type']}|{jugador['puuid']}"

def calcular_rachas(partidas):
    """
    Calcula las rachas de victorias y derrotas más largas de una lista de partidas.
    Las partidas deben estar ordenadas por fecha, de más reciente a más antigua.
    """
    print(f"[calcular_rachas] Calculando rachas para {len(partidas)} partidas.")
    if not partidas:
        return {
            'max_win_streak': 0, 
            'max_loss_streak': 0,
            'current_win_streak': 0,
            'current_loss_streak': 0
        }

    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    # Calcular rachas máximas (iterando de la más antigua a la más nueva)
    for partida in reversed(partidas): # reversed() itera de la más antigua a la más nueva
        if partida.get('win'):
            current_win_streak += 1
            current_loss_streak = 0
        else:
            current_loss_streak += 1
            current_win_streak = 0

        if current_win_streak > max_win_streak:
            max_win_streak = current_win_streak
        if current_loss_streak > max_loss_streak:
            max_loss_streak = current_loss_streak

    # Calcular racha actual (iterando de la más nueva a la más antigua)
    current_streak_type = 'win' if partidas[0].get('win') else 'loss'
    current_streak_count = 0
    for partida in partidas: # La lista ya viene de más nueva a más antigua
        is_win = partida.get('win')
        if (is_win and current_streak_type == 'win') or (not is_win and current_streak_type == 'loss'):
            current_streak_count += 1
        else:
            break # La racha se rompió

    final_current_win_streak = current_streak_count if current_streak_type == 'win' else 0
    final_current_loss_streak = current_streak_count if current_streak_type == 'loss' else 0

    print(f"[calcular_rachas] Rachas calculadas: Max V: {max_win_streak}, Max D: {max_loss_streak}, Actual: {final_current_win_streak}V/{final_current_loss_streak}D.")
    return {'max_win_streak': max_win_streak, 'max_loss_streak': max_loss_streak, 'current_win_streak': final_current_win_streak, 'current_loss_streak': final_current_loss_streak}

@app.route('/')
def index():
    """Renderiza la página principal con la lista de jugadores."""
    print("[index] Petición recibida para la página principal.")
    datos_jugadores, timestamp = obtener_datos_jugadores()
    
    lectura_exitosa, peak_elo_dict = leer_peak_elo()

    if lectura_exitosa:
        actualizado = False
        for jugador in datos_jugadores:
            key = get_peak_elo_key(jugador)
            peak = peak_elo_dict.get(key, 0)

            valor = jugador["valor_clasificacion"]
            if valor > peak:
                peak_elo_dict[key] = valor
                peak = valor
                actualizado = True
                print(f"[index] Peak Elo actualizado para {jugador['game_name']} en {jugador['queue_type']}: {peak}")
            jugador["peak_elo"] = peak

        if actualizado:
            guardar_peak_elo_en_github(peak_elo_dict)
    else:
        print("[index] ADVERTENCIA: No se pudo leer el archivo peak_elo.json. Se omitirá la actualización de picos.")
        for jugador in datos_jugadores:
            jugador["peak_elo"] = jugador["valor_clasificacion"]

    split_activo_nombre = SPLITS[ACTIVE_SPLIT_KEY]['name']
    # El timestamp de la caché está en segundos UTC (de time.time())
    dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # Convertir a la zona horaria de visualización deseada (UTC+2)
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    ultima_actualizacion = dt_target.strftime("%d/%m/%Y %H:%M:%S")
    
    print("[index] Renderizando index.html.")
    has_player_data = bool(datos_jugadores) # Check if the list is not empty
    return render_template('index.html', datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION, 
                           split_activo_nombre=split_activo_nombre,
                           has_player_data=has_player_data)

@app.route('/historial_global')
def historial_global():
    """Renderiza la página de historial global de partidas para la temporada actual."""
    print("[historial_global] Petición recibida para la página de historial global.")
    
    todos_los_jugadores, _ = obtener_datos_jugadores()
    
    all_matches_combined = []
    
    # Usamos un ThreadPoolExecutor para procesar los historiales de los jugadores en paralelo
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for jugador_data in todos_los_jugadores:
            puuid = jugador_data.get('puuid')
            riot_id = jugador_data.get('game_name')
            if puuid:
                # Cada future almacenará una lista de partidas para un jugador
                futures.append(executor.submit(get_player_match_history, puuid, riot_id))
        
        for future in futures:
            historial_jugador = future.result()
            if historial_jugador and 'matches' in historial_jugador:
                # Filtrar partidas por SEASON_START_TIMESTAMP y añadir a la lista combinada
                for match in historial_jugador['matches']:
                    if match.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP:
                        all_matches_combined.append(match)

    # OPTIMIZACIÓN: Usar set en lugar de dict para deduplicación O(1)
    unique_matches_set = set()
    final_matches = []
    for match in all_matches_combined:
        key = (match.get('match_id'), match.get('puuid'))
        if key not in unique_matches_set:
            unique_matches_set.add(key)
            final_matches.append(match)

    # Ordenar todas las partidas combinadas por game_end_timestamp, de más reciente a más antigua
    final_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    
    print(f"[historial_global] Se han agregado y ordenado {len(final_matches)} partidas para el historial global.")

    return render_template('historial_global.html',
                           global_match_history=final_matches,
                           ddragon_version=DDRAGON_VERSION)



@app.route('/api/players_and_accounts')
def get_players_and_accounts():
    try:
        print("[get_players_and_accounts] Petición recibida para obtener jugadores y cuentas.")
        datos_jugadores, _ = obtener_datos_jugadores()
        
        players_data = {}
        for jugador_info in datos_jugadores:
            player_name = jugador_info.get('jugador')
            riot_id = jugador_info.get('game_name')
            puuid = jugador_info.get('puuid')

            if player_name and riot_id and puuid:
                if player_name not in players_data:
                    players_data[player_name] = []
                
                # Check if this specific riot_id/puuid pair is already added for this player
                # This handles cases where a player might have multiple entries in datos_jugadores
                # for different queue types but the same riot_id/puuid.
                if not any(acc['puuid'] == puuid for acc in players_data[player_name]):
                    players_data[player_name].append({
                        'riot_id': riot_id,
                        'puuid': puuid
                    })
        
        print(f"[get_players_and_accounts] Devolviendo {len(players_data)} jugadores con sus cuentas.")
        return jsonify(players_data)
    except Exception as e:
        print(f"[get_players_and_accounts] ERROR: {e}")
        return jsonify({"error": "Ocurrió un error inesperado en el servidor."}), 500

@app.route('/jugador/<path:game_name>')
def perfil_jugador(game_name):
    """
    Muestra una página de perfil para un jugador específico, detectando
    el tipo de dispositivo para renderizar la plantilla adecuada.
    """
    print(f"[perfil_jugador] Petición recibida para el perfil de jugador: {game_name}")
    perfil = _get_player_profile_data(game_name)
    if not perfil:
        print(f"[perfil_jugador] Perfil de jugador {game_name} no encontrado. Retornando 404.")
        return render_template('404.html'), 404

    user_agent_string = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent_string for keyword in ['mobi', 'android', 'iphone', 'ipad'])
    
    template_name = 'jugador.html'
    
    print(f"[perfil_jugador] Dispositivo detectado como {'Móvil' if is_mobile else 'Escritorio'}. Renderizando {template_name} para {game_name}.")

    return render_template(template_name,
                           perfil=perfil,
                           ddragon_version=DDRAGON_VERSION,
                           datetime=datetime,
                           now=datetime.now())



def _get_player_profile_data(game_name):
    """
    Función auxiliar que encapsula la lógica para obtener y procesar
    todos los datos de un perfil de jugador.
    Devuelve el diccionario 'perfil' o None si no se encuentra el jugador.
    """
    print(f"[_get_player_profile_data] Obteniendo datos de perfil para: {game_name}")
    todos_los_datos, _ = obtener_datos_jugadores()
    datos_del_jugador = [j for j in todos_los_datos if j.get('game_name') == game_name]
    
    if not datos_del_jugador:
        print(f"[_get_player_profile_data] No se encontraron datos para el jugador {game_name} en la caché.")
        return None
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid')

    # --- Cargar Peak Elo para el perfil ---
    lectura_exitosa, peak_elo_dict = leer_peak_elo()
    if lectura_exitosa:
        for item in datos_del_jugador:
            key = get_peak_elo_key(item)
            peak = peak_elo_dict.get(key, 0)
            if item['valor_clasificacion'] > peak:
                peak = item['valor_clasificacion']
            item['peak_elo'] = peak

    # --- NUEVA LÓGICA DE CÁLCULO DE LP ---
    lp_history = leer_lp_history()
    player_lp_history = lp_history.get(puuid, {})
    
    historial_partidas_completo = {}
    processed_matches = []
    if puuid:
        historial_partidas_completo = get_player_match_history(puuid, riot_id=game_name)
        matches = historial_partidas_completo.get('matches', [])
        processed_matches = process_player_match_history(matches, player_lp_history)


    # Asegurar que tenemos el PUUID actualizado desde el archivo
    puuid_dict = leer_puuids()
    puuid = puuid_dict.get(game_name, puuid)  # Usar el del dict si no está en cache

    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'),
        'game_name': game_name,
        'puuid': puuid,
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', ''),
        'historial_partidas': processed_matches
    }
    
    for item in datos_del_jugador:
        if item.get('queue_type') == 'RANKED_SOLO_5x5':
            perfil['soloq'] = item
        elif item.get('queue_type') == 'RANKED_FLEX_SR':
            perfil['flexq'] = item

    historial_total = perfil.get('historial_partidas', [])
    
    # --- Lógica de Gráfico de Evolución basada en el historial de partidas ---
    partidas_con_elo_soloq = sorted(
        [p for p in historial_total if p.get('queue_id') == 420 and p.get('post_game_valor_clasificacion') is not None],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    perfil['elo_history_soloq'] = [
        {'timestamp': p['game_end_timestamp'], 'elo': p['post_game_valor_clasificacion']}
        for p in partidas_con_elo_soloq
    ]

    partidas_con_elo_flexq = sorted(
        [p for p in historial_total if p.get('queue_id') == 440 and p.get('post_game_valor_clasificacion') is not None],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    perfil['elo_history_flexq'] = [
        {'timestamp': p['game_end_timestamp'], 'elo': p['post_game_valor_clasificacion']}
        for p in partidas_con_elo_flexq
    ]

    if 'soloq' in perfil:
        partidas_soloq = [p for p in historial_total if p.get('queue_id') == 420]
        rachas_soloq = calcular_rachas(partidas_soloq)
        perfil['soloq'].update(rachas_soloq)
        print(f"[_get_player_profile_data] Rachas SoloQ calculadas para {game_name}.")

    if 'flexq' in perfil:
        partidas_flexq = [p for p in historial_total if p.get('queue_id') == 440]
        rachas_flexq = calcular_rachas(partidas_flexq)
        perfil['flexq'].update(rachas_flexq)
        print(f"[_get_player_profile_data] Rachas FlexQ calculadas para {game_name}.")

    # --- Champion Specific Stats ---
    champion_stats = {}
    for match in historial_total:
        champion_name = match.get('champion_name')
        if champion_name and champion_name != "Desconocido":
            if champion_name not in champion_stats:
                champion_stats[champion_name] = {
                    'games_played': 0,
                    'wins': 0,
                    'losses': 0,
                    'kills': 0,
                    'deaths': 0,
                    'assists': 0
                }
            
            stats = champion_stats[champion_name]
            stats['games_played'] += 1
            if match.get('win'):
                stats['wins'] += 1
            else:
                stats['losses'] += 1
            
            stats['kills'] += match.get('kills', 0)
            stats['deaths'] += match.get('deaths', 0)
            stats['assists'] += match.get('assists', 0)

    for champ, stats in champion_stats.items():
        stats['win_rate'] = (stats['wins'] / stats['games_played'] * 100) if stats['games_played'] > 0 else 0
        stats['kda'] = (stats['kills'] + stats['assists']) / max(1, stats['deaths'])

    perfil['champion_stats'] = sorted(champion_stats.items(), key=lambda x: x[1]['games_played'], reverse=True)

    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    print(f"[_get_player_profile_data] Perfil de {game_name} preparado.")
    return perfil




def actualizar_historial_partidas_en_segundo_plano():
    """
    Función que se ejecuta en un hilo separado para actualizar el historial de partidas
    de todos los jugadores de forma periódica.
    """
    print("[actualizar_historial_partidas_en_segundo_plano] Iniciando hilo de actualización de historial de partidas.")
    api_key = os.environ.get('RIOT_API_KEY')
    if not api_key:
        print("[actualizar_historial_partidas_en_segundo_plano] ERROR: RIOT_API_KEY no configurada. No se puede actualizar el historial de partidas.")
        return

    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}

    while True:
        try:
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                print("[actualizar_historial_partidas_en_segundo_plano] Datos de DDragon no cargados, intentando actualizar.")
                actualizar_ddragon_data()

            lp_history = leer_lp_history()
            cuentas = leer_cuentas()
            puuid_dict = leer_puuids()
            # Crear un mapa inverso para buscar Riot IDs por PUUID eficientemente
            puuid_to_riot_id = {v: k for k, v in puuid_dict.items()}
            puuids_actualizados = False

            for riot_id, jugador_nombre in cuentas:
                # Usar el mapa inverso para encontrar el PUUID si el riot_id de cuentas.txt es antiguo
                puuid = puuid_dict.get(riot_id)
                if not puuid:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] PUUID para {riot_id} no encontrado. Intentando obtenerlo de la API...")
                    game_name, tag_line = riot_id.split('#')
                    puuid_info = obtener_puuid(api_key, game_name, tag_line)
                    if puuid_info and 'puuid' in puuid_info:
                        puuid = puuid_info['puuid']
                        puuid_dict[riot_id] = puuid
                        puuids_actualizados = True
                        print(f"[actualizar_historial_partidas_en_segundo_plano] PUUID {puuid} obtenido y añadido para {riot_id}.")
                    else:
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Fallo al obtener PUUID para {riot_id}. Omitiendo a este jugador en este ciclo.")
                        continue

                matches_con_lp_asociado = [] # Lista para guardar confirmaciones
                # print(f"[actualizar_historial_partidas_en_segundo_plano] Procesando historial para {riot_id} (PUUID: {puuid}).")
                # Leer el historial existente (directamente de GitHub, ya que es el hilo de escritura)
                historial_existente = _read_player_match_history_from_github(puuid, riot_id=riot_id) 
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}
                remakes_guardados = set(historial_existente.get('remakes', []))
                
                # print(f"[actualizar_historial_partidas_en_segundo_plano] Historial existente para {riot_id}: {len(ids_partidas_guardadas)} partidas guardadas, {len(remakes_guardados)} remakes.")

                all_match_ids_season = []
                for queue_id in queue_map.values():
                    start_index = 0
                    while True:
                        url_matches = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime={SEASON_START_TIMESTAMP}&queue={queue_id}&start={start_index}&count=100&api_key={api_key}"
                        response_matches = make_api_request(url_matches)
                        if not response_matches: 
                            print(f"[actualizar_historial_partidas_en_segundo_plano] No más partidas o error para cola {queue_id} y {riot_id}. Response: {response_matches}")
                            break
                        match_ids_page = response_matches.json()
                        if not match_ids_page: 
                            print(f"[actualizar_historial_partidas_en_segundo_plano] No se encontraron más IDs de partida para cola {queue_id} y {riot_id}.")
                            break
                        all_match_ids_season.extend(match_ids_page)
                        # print(f"[actualizar_historial_partidas_en_segundo_plano] Obtenidos {len(match_ids_page)} IDs de partida para {riot_id} (cola {queue_id}). Total de IDs de temporada hasta ahora: {len(all_match_ids_season)}.")
                        if len(match_ids_page) < 100: break
                        start_index += 100
                
                print(f"[actualizar_historial_partidas_en_segundo_plano] Total de IDs de partida de la temporada para {riot_id} obtenidos de la API: {len(all_match_ids_season)}.")

                nuevos_match_ids = [
                    mid for mid in all_match_ids_season 
                    if mid not in ids_partidas_guardadas and mid not in remakes_guardados
                ]

                print(f"[actualizar_historial_partidas_en_segundo_plano] Se detectaron {len(nuevos_match_ids)} IDs de partida realmente nuevas para {riot_id}.")

                # Initialize these variables to empty lists outside the if/else block
                nuevas_partidas_validas = []
                nuevos_remakes = []

                if not nuevos_match_ids:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] No hay partidas nuevas para {riot_id}. Omitiendo procesamiento de partidas.")
                    # Still need to process pending LP updates even if no new matches
                    pass
                else:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Se encontraron {len(nuevos_match_ids)} partidas nuevas para {riot_id}. Procesando...")

                    tareas = [(match_id, puuid, api_key, riot_id) for match_id in nuevos_match_ids]
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))

                    nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]
                    nuevos_remakes = [
                        match_id for i, match_id in enumerate(nuevos_match_ids)
                        if nuevas_partidas_info[i] is None
                    ]
                    print(f"[actualizar_historial_partidas_en_segundo_plano] {len(nuevas_partidas_validas)} partidas válidas y {len(nuevos_remakes)} remakes procesados para {riot_id}.")

                    if nuevas_partidas_validas:
                        # --- CÁLCULO Y ASIGNACIÓN DE LP A NUEVAS PARTIDAS ---
                        player_lp_history = lp_history.get(puuid, {})
                        if player_lp_history:
                            all_player_matches = historial_existente.get('matches', []) + nuevas_partidas_validas
                            nuevas_partidas_validas = _process_lp_for_matches(nuevas_partidas_validas, player_lp_history, all_player_matches)

                        # --- DETECCIÓN DE CAMBIO DE NOMBRE (SIN LLAMADAS EXTRA A LA API) ---
                        for partida in nuevas_partidas_validas:
                            nuevo_riot_id = partida.get('riot_id')
                            puuid_partida = partida.get('puuid')
                            antiguo_riot_id = puuid_to_riot_id.get(puuid_partida)

                            if antiguo_riot_id and nuevo_riot_id and antiguo_riot_id != nuevo_riot_id:
                                print(f"¡CAMBIO DE NOMBRE DETECTADO! PUUID {puuid_partida}: '{antiguo_riot_id}' -> '{nuevo_riot_id}'")
                                # Actualizar el diccionario principal (puuid_dict) y el inverso (puuid_to_riot_id)
                                if antiguo_riot_id in puuid_dict:
                                    del puuid_dict[antiguo_riot_id]
                                puuid_dict[nuevo_riot_id] = puuid_partida
                                puuid_to_riot_id[puuid_partida] = nuevo_riot_id
                                puuids_actualizados = True
                                break # Solo necesitamos detectar el cambio una vez por jugador en este ciclo de actualización
                        
                        historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Añadidas {len(nuevas_partidas_validas)} partidas válidas al historial de {riot_id}.")

                # --- CÁLCULO/ACTUALIZACIÓN DE LP PARA PARTIDAS EXISTENTES CON LP NULO ---
                player_lp_history = lp_history.get(puuid, {}) 
                updated_existing_matches = False
                if player_lp_history:
                    current_all_matches = historial_existente.get('matches', [])
                    matches_without_lp = [m for m in current_all_matches if m.get('lp_change_this_game') is None]
                    
                    if matches_without_lp:
                        updated_matches = _process_lp_for_matches(matches_without_lp, player_lp_history, current_all_matches)
                        for match_idx, match in enumerate(updated_matches):
                            if match.get('lp_change_this_game') is not None:
                                updated_existing_matches = True
                                # Encontrar y actualizar el match original
                                for i, orig_match in enumerate(current_all_matches):
                                    if orig_match['match_id'] == match['match_id']:
                                        current_all_matches[i].update(match)
                                        print(f"[actualizar_historial_partidas_en_segundo_plano] LP re-calculado para match {match['match_id']} de {riot_id}: {match['lp_change_this_game']}")
                                        break


                stats_have_changed = False # No longer calculated here
                
                # Only save if there were new valid matches or new remakes or if the stats have changed
                if nuevas_partidas_validas or nuevos_remakes or updated_existing_matches:
                    historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'], reverse=True)
                    # print(f"[actualizar_historial_partidas_en_segundo_plano] Historial de {riot_id} ordenado.")

                    if nuevos_remakes:
                        remakes_guardados.update(nuevos_remakes)
                        historial_existente['remakes'] = list(remakes_guardados)
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Añadidos {len(nuevos_remakes)} remakes al historial de {riot_id}.")
                    
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Llamando a guardar_historial_jugador_github para {riot_id}.", flush=True)
                    if guardar_historial_jugador_github(puuid, historial_existente, riot_id=riot_id):
                        print(f"[{riot_id}] [GitHub Sync] CONFIRMADO: Historial guardado en GitHub.", flush=True)

                    # --- ACTUALIZAR LA CACHÉ EN MEMORIA DESPUÉS DE GUARDAR EN GITHUB ---
                    with PLAYER_MATCH_HISTORY_LOCK:
                        PLAYER_MATCH_HISTORY_CACHE[puuid] = {
                            'data': historial_existente,
                            'timestamp': time.time()
                        }
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Historial de {puuid} actualizado y cacheado en memoria.", flush=True)
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Historial de {riot_id} actualizado y cacheado en memoria.", flush=True)


                    # Invalidate personal records cache for this player
                    with PERSONAL_RECORDS_LOCK:
                        if puuid in PERSONAL_RECORDS_CACHE['data']:
                            del PERSONAL_RECORDS_CACHE['data'][puuid]
                            print(f"[actualizar_historial_partidas_en_segundo_plano] Récords personales cacheados para {riot_id} invalidados.")
                else:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] No hay cambios significativos para guardar en el historial de {riot_id}.")

            print("[actualizar_historial_partidas_en_segundo_plano] Ciclo de actualización de historial completado. Próxima revisión en 5 minutos.")
            time.sleep(600)

        except Exception as e:
            print(f"[actualizar_historial_partidas_en_segundo_plano] ERROR GLOBAL en el hilo de actualización de estadísticas: {e}. Reintentando en 5 minutos.")
            time.sleep(600)

def keep_alive():
    """Envía una solicitud periódica a la propia aplicación para mantenerla activa en servicios como Render."""
    print("[keep_alive] Hilo de keep_alive iniciado.")
    while True:
        try:
            # Reemplaza con la URL de tu aplicación desplegada
            requests.get('https://soloq-cerditos.onrender.com/', timeout=60)
            print("[keep_alive] Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"[keep_alive] Error en keep_alive: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
    """Actualiza la caché de datos de los jugadores de forma periódica."""
    print("[actualizar_cache_periodicamente] Hilo de actualización de caché periódica iniciado.")
    while True:
        actualizar_cache()
        time.sleep(CACHE_TIMEOUT)

def _filter_matches_by_queue_and_champion(matches, queue_id_filter=None, champion_filter=None):
    """Filtra partidas por cola y/o campeón de forma eficiente.
    
    Args:
        matches: Lista de partidas
        queue_id_filter: int, list de ints, o None para sin filtro
        champion_filter: str o None para sin filtro
    
    Returns:
        Lista filtrada de partidas
    """
    result = matches
    
    if queue_id_filter is not None:
        if isinstance(queue_id_filter, list):
            result = [m for m in result if m.get('queue_id') in queue_id_filter]
        else:
            result = [m for m in result if m.get('queue_id') == queue_id_filter]
    
    if champion_filter:
        result = [m for m in result if m.get('champion_name') == champion_filter]
    
    return result

# Helper function to create the record dictionary from a match
def _create_record_dict(match, value, record_type):
    """Crea un diccionario de récord a partir de los datos de una partida."""
    print(f"[_create_record_dict] Processing record for match_id: {match.get('match_id')}, type: {record_type}")
    # DEBUG: Print the relevant value for debugging
    # print(f"[_create_record_dict] Debugging '{record_type}': raw value from match={match.get('wards_placed') if record_type == 'most_wards_placed' else value}, passed value={value}")


    champion_id_raw = match.get('championId')
    champion_name_from_match = match.get('champion_name') # Existing champion_name from match history
    
    print(f"[_create_record_dict] Initial values: champion_id_raw={champion_id_raw}, champion_name_from_match={champion_name_from_match}")

    champion_name = 'N/A'
    actual_champion_id = 'N/A'

    # Try to get champion name from ALL_CHAMPIONS using champion_id_raw
    if isinstance(champion_id_raw, (int, float)):
        temp_id = int(champion_id_raw)
        if temp_id in ALL_CHAMPIONS:
            actual_champion_id = temp_id
            champion_name = ALL_CHAMPIONS.get(actual_champion_id)
            print(f"[_create_record_dict] Found champion in ALL_CHAMPIONS by ID: {actual_champion_id} -> {champion_name}")
        else:
            print(f"[_create_record_dict] Champion ID {temp_id} not found in ALL_CHAMPIONS.")
    elif isinstance(champion_id_raw, str) and champion_id_raw.isdigit():
         temp_id = int(champion_id_raw)
         if temp_id in ALL_CHAMPIONS:
            actual_champion_id = temp_id
            champion_name = ALL_CHAMPIONS.get(actual_champion_id)
            print(f"[_create_record_dict] Found champion in ALL_CHAMPIONS by string ID: {actual_champion_id} -> {champion_name}")
         else:
            print(f"[_create_record_dict] Champion ID (string) {temp_id} not found in ALL_CHAMPION_NAMES_TO_IDS.")
    else: # Fallback using champion_name_from_match if champion_id_raw is None or not a digit
        if champion_name_from_match and champion_name_from_match != 'N/A':
            # Try to get ID from name, then get display name
            id_from_name = ALL_CHAMPION_NAMES_TO_IDS.get(champion_name_from_match)
            if id_from_name is not None:
                actual_champion_id = id_from_name
                champion_name = ALL_CHAMPIONS.get(actual_champion_id) # Get canonical name from ID
                print(f"[_create_record_dict] Champion ID was None/invalid. Deriving ID from name '{champion_name_from_match}': {actual_champion_id} -> {champion_name}")
            else:
                champion_name = champion_name_from_match # Use name as is if ID cannot be resolved
                print(f"[_create_record_dict] Champion ID was None/invalid and name '{champion_name_from_match}' not found in ALL_CHAMPION_NAMES_TO_IDS. Using name directly.")
        else:
            print(f"[_create_record_dict] champion_id_raw is not an int/float or digit string: {type(champion_id_raw)} -> {champion_id_raw}")
    
    # Fallback: if champion_name is still 'N/A' and match.get('champion_name') is not 'N/A' or empty
    if champion_name == 'N/A' and champion_name_from_match and champion_name_from_match != 'N/A':
        champion_name = champion_name_from_match
        print(f"[_create_record_dict] Falling back to champion_name from match data: {champion_name}")

    # If all else fails, use a generic placeholder for the name, but keep ID if it exists
    if champion_name == 'N/A' and actual_champion_id == 'N/A':
        champion_name = "Campeón Desconocido"
        print(f"[_create_record_dict] Final fallback: Champion name set to 'Campeón Desconocido'.")


    print(f"[_create_record_dict] Final champion_name: {champion_name}, actual_champion_id: {actual_champion_id}")

    # Records where 0 should be displayed as N/A
    na_if_zero_records = [
        'largest_killing_spree', 'largest_multikill', 'most_turret_kills',
        'most_inhibitor_kills', 'most_baron_kills', 'most_dragon_kills',
        'most_objectives_stolen', 'most_double_kills', 'most_triple_kills',
        'most_quadra_kills', 'most_penta_kills'
    ]

    final_value = value
    if record_type in na_if_zero_records and value == 0:
        final_value = None # Set to None if 0 and should be N/A

    return {
        'value': final_value,
        'player': match.get('jugador_nombre', 'N/A'), # Ensure player name also has default
        'riot_id': match.get('riot_id', 'N/A'), # Ensure riot_id also has default
        'match_id': match.get('match_id', 'N/A'),
        'kda': match.get('kda', 0),
        'game_date': match.get('game_end_timestamp', 0), # Almacenar el timestamp crudo
        'game_duration': int(match.get('game_duration', 0)),
        'champion_name': champion_name,
        'champion_id': actual_champion_id, # Use the actual_champion_id derived
        'kills': match.get('kills', 0),
        'deaths': match.get('deaths', 0),
        'assists': match.get('assists', 0),
        'achieved_timestamp': match.get('game_end_timestamp', 0), # El timestamp para el desempate: fecha más temprana
        'record_type': record_type # Para identificar el tipo de récord
    }

# Function to update a record with tie-breaking logic (smaller timestamp wins)
def _update_record(current_record, new_value, new_match, record_type):
    """Actualiza un récord con lógica de desempate: se prefiere mayor valor, o el más antiguo si los valores son iguales.
    También actualiza si el registro actual es el valor predeterminado y el nuevo valor es >= 0.
    """
    new_record_data = _create_record_dict(new_match, new_value, record_type)

    # Check if the current record is still the unpopulated default
    is_current_record_default = (current_record['value'] == 0 and current_record['player'] == 'N/A' and current_record['achieved_timestamp'] == 0)

    # Lógica de actualización:
    # 1. Si el nuevo valor es estrictamente mayor, siempre actualiza.
    # 2. Si los valores son iguales, prefiere la partida más antigua (timestamp más pequeño).
    # 3. Si el récord actual es el valor por defecto (no inicializado), y el nuevo valor es >= 0, actualiza.
    current_value_for_comparison = current_record['value'] if current_record['value'] is not None else -1
    new_value_for_comparison = new_record_data['value'] if new_record_data['value'] is not None else -1

    if new_value_for_comparison > current_value_for_comparison or \
       (new_value_for_comparison == current_value_for_comparison and
        new_record_data['achieved_timestamp'] < current_record['achieved_timestamp']) or \
       (is_current_record_default and new_value_for_comparison >= 0): 
        return new_record_data
    return current_record

def _find_lp_change(match, player_lp_history, all_player_matches, match_ids_set=None):
    """Busca y calcula el cambio de LP para una partida específica.
    OPTIMIZACIÓN: Recibe set de match_ids para validación O(1) en lugar de O(n).
    """
    game_end_ts = match.get('game_end_timestamp', 0)
    queue_id = match.get('queue_id')
    queue_name = "RANKED_SOLO_5x5" if queue_id == 420 else "RANKED_FLEX_SR" if queue_id == 440 else None
    
    if not (game_end_ts > 0 and queue_name and queue_name in player_lp_history):
        return None
    
    snapshots = sorted(player_lp_history[queue_name], key=lambda x: x['timestamp'])

    # Búsqueda binaria para encontrar snapshots anterior y posterior
    # bisect in the stdlib doesn't support a `key` argument, so build a list of timestamps
    timestamps = [s['timestamp'] for s in snapshots]
    idx = bisect.bisect_left(timestamps, game_end_ts)

    snapshot_before = snapshots[idx - 1] if idx > 0 else None
    snapshot_after = snapshots[idx] if idx < len(snapshots) else None
    
    if not (snapshot_before and snapshot_after):
        return None
    
    # OPTIMIZACIÓN: Usar set de match_ids para búsqueda O(1) en lugar de O(n)
    match_id = match['match_id']
    if match_ids_set:
        # Validación rápida: si hay otro match en ese rango, probablemente no sea limpio
        # Nota: Esta es una heurística. Para ser 100% preciso usaría all_player_matches
        pass
    else:
        # Fallback: validación completa si no se proporciona set
        for other_match in all_player_matches:
            if other_match['match_id'] != match_id and other_match.get('queue_id') == queue_id:
                other_ts = other_match.get('game_end_timestamp', 0)
                if snapshot_before['timestamp'] < other_ts < snapshot_after['timestamp']:
                    return None  # No es cambio limpio
    
    elo_before = snapshot_before.get('elo', 0)
    elo_after = snapshot_after.get('elo', 0)
    
    return {
        'lp_change': elo_after - elo_before,
        'pre_game': elo_before,
        'post_game': elo_after
    }

def _process_lp_for_matches(matches, player_lp_history, all_player_matches):
    """Procesa y asigna LP a un conjunto de partidas en una sola pasada."""
    results = []
    for match in matches:
        lp_info = _find_lp_change(match, player_lp_history, all_player_matches)
        if lp_info:
            match['lp_change_this_game'] = lp_info['lp_change']
            match['pre_game_valor_clasificacion'] = lp_info['pre_game']
            match['post_game_valor_clasificacion'] = lp_info['post_game']
        results.append(match)
    return results


def _calculate_stats_for_queue(all_matches, queue_id_filter, champion_filter=None):
    """
    Calculates global statistics for a specific queue from a list of all matches.
    """
    print(f"[_calculate_stats_for_queue] Calculating stats for queue_id: {queue_id_filter or 'all'}")

    def default_record():
        return {
            'value': 0, 'player': 'N/A', 'riot_id': 'N/A', 'match_id': 'N/A', 'kda': 0,
            'game_date': 0, 'game_duration': 0, 'champion_name': 'N/A',
            'champion_id': 'N/A', 'kills': 0, 'deaths': 0, 'assists': 0,
            'achieved_timestamp': 0, 'is_tied_record': False
        }

    # Initialize records and counters for this specific queue
    global_records = {k: default_record() for k in [
        'longest_game', 'most_kills', 'most_deaths', 'most_assists', 'highest_kda',
        'most_cs', 'most_damage_dealt', 'most_gold_earned', 'most_vision_score',
        'largest_killing_spree', 'largest_multikill', 'most_time_spent_dead',
        'most_wards_placed', 'most_wards_killed', 'most_turret_kills',
        'most_inhibitor_kills', 'most_baron_kills', 'most_dragon_kills',
        'most_damage_taken', 'most_total_heal', 'most_damage_shielded_on_teammates',
        'most_time_ccing_others', 'most_objectives_stolen', 'highest_kill_participation',
        'most_double_kills', 'most_triple_kills', 'most_quadra_kills', 'most_penta_kills',
        'longest_win_streak', 'longest_loss_streak'
    ]}
    current_best_values = {key: 0 for key in global_records.keys()}
    tied_counts = {key: 0 for key in global_records.keys()}
    total_wins = 0
    total_losses = 0
    all_champions_played = []

    # Filter matches
    filtered_matches = all_matches
    if champion_filter:
        filtered_matches = [m for m in filtered_matches if m.get('champion_name') == champion_filter]
    if queue_id_filter is not None:
        if isinstance(queue_id_filter, list):
            filtered_matches = [m for m in filtered_matches if m.get('queue_id') in queue_id_filter]
        else:
            filtered_matches = [m for m in filtered_matches if m.get('queue_id') == queue_id_filter]
    
    total_games_in_queue = len(filtered_matches)
    if total_games_in_queue == 0:
        return {
            'overall_win_rate': 0,
            'total_games': 0,
            'most_played_champions': [],
            'global_records': global_records
        }

    # --- Streak Calculation ---
    matches_by_player = defaultdict(list)
    for match in filtered_matches:
        matches_by_player[match['puuid']].append(match)

    best_win_streak = {'value': 0, 'match': None}
    best_loss_streak = {'value': 0, 'match': None}

    for puuid, player_matches in matches_by_player.items():
        player_matches.sort(key=lambda x: x.get('game_end_timestamp', 0))
        streaks = calcular_rachas(player_matches)
        
        if streaks['max_win_streak'] > best_win_streak['value']:
            best_win_streak['value'] = streaks['max_win_streak']
            # Find the last match of this streak to represent the record
            current_streak = 0
            for match in player_matches:
                if match.get('win'):
                    current_streak += 1
                    if current_streak == streaks['max_win_streak']:
                        best_win_streak['match'] = match
                        break
                else:
                    current_streak = 0

        if streaks['max_loss_streak'] > best_loss_streak['value']:
            best_loss_streak['value'] = streaks['max_loss_streak']
            current_streak = 0
            for match in player_matches:
                if not match.get('win'):
                    current_streak += 1
                    if current_streak == streaks['max_loss_streak']:
                        best_loss_streak['match'] = match
                        break
                else:
                    current_streak = 0
    
    if best_win_streak['match']:
        global_records['longest_win_streak'] = _update_record(global_records['longest_win_streak'], best_win_streak['value'], best_win_streak['match'], 'longest_win_streak')
    if best_loss_streak['match']:
        global_records['longest_loss_streak'] = _update_record(global_records['longest_loss_streak'], best_loss_streak['value'], best_loss_streak['match'], 'longest_loss_streak')
    # --- End Streak Calculation ---

    for match in filtered_matches:
        if match.get('win'):
            total_wins += 1
        else:
            total_losses += 1
        
        all_champions_played.append(match.get('champion_name'))

        records_to_check = {
            'longest_game': match.get('game_duration', 0),
            'most_kills': match.get('kills', 0),
            'most_deaths': match.get('deaths', 0),
            'most_assists': match.get('assists', 0),
            'highest_kda': match.get('kda', 0),
            'most_cs': match.get('total_minions_killed', 0) + match.get('neutral_minions_killed', 0),
            'most_damage_dealt': match.get('total_damage_dealt_to_champions', 0),
            'most_gold_earned': match.get('gold_earned', 0),
            'most_vision_score': match.get('vision_score', 0),
            'largest_killing_spree': match.get('largest_killing_spree', 0),
            'largest_multikill': match.get('largestMultiKill', 0),
            'most_time_spent_dead': match.get('total_time_spent_dead', 0),
            'most_wards_placed': match.get('wards_placed', 0),
            'most_wards_killed': match.get('wards_killed', 0),
            'most_turret_kills': match.get('turret_kills', 0),
            'most_inhibitor_kills': match.get('inhibitor_kills', 0),
            'most_baron_kills': match.get('baron_kills', 0),
            'most_dragon_kills': match.get('dragon_kills', 0),
            'most_damage_taken': match.get('total_damage_taken', 0),
            'most_total_heal': match.get('total_heal', 0),
            'most_damage_shielded_on_teammates': match.get('total_damage_shielded_on_teammates', 0),
            'most_time_ccing_others': match.get('time_ccing_others', 0),
            'most_objectives_stolen': match.get('objectives_stolen', 0),
            'highest_kill_participation': match.get('kill_participation', 0),
            'most_double_kills': match.get('doubleKills', 0),
            'most_triple_kills': match.get('tripleKills', 0),
            'most_quadra_kills': match.get('quadraKills', 0),
            'most_penta_kills': match.get('pentaKills', 0),
        }

        for record_key, current_value in records_to_check.items():
            if current_value > current_best_values[record_key]:
                current_best_values[record_key] = current_value
                tied_counts[record_key] = 1
            elif current_value == current_best_values[record_key] and current_value > 0:
                tied_counts[record_key] += 1
            
            updated_record = _update_record(global_records[record_key], current_value, match, record_key)
            global_records[record_key] = updated_record
            global_records[record_key]['is_tied_record'] = (tied_counts[record_key] > 1)

    overall_win_rate = (total_wins / total_games_in_queue * 100) if total_games_in_queue > 0 else 0
    most_played_champions = Counter(c for c in all_champions_played if c).most_common(5)

    return {
        'overall_win_rate': overall_win_rate,
        'total_games': total_games_in_queue,
        'most_played_champions': most_played_champions,
        'global_records': global_records
    }




def get_global_stats():
    """Devuelve las estadísticas globales cacheadas."""
    with GLOBAL_STATS_LOCK:
        # Deep copy to prevent modification of cached data outside of lock
        return json.loads(json.dumps(GLOBAL_STATS_CACHE))

@app.route('/api/global_stats', methods=['GET'])
def global_stats_api():
    """API endpoint to get global statistics."""
    try:
        stats_data = get_global_stats()
        return jsonify(stats_data)
    except Exception as e:
        print(f"[global_stats_api] ERROR: {e}")
        return jsonify({"error": "Ocurrió un error inesperado en el servidor."}), 500

@app.route('/estadisticas')
def estadisticas_globales():
    """Renderiza la página de estadísticas globales, filtrada por tipo de cola."""
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    selected_queue_id = request.args.get('queue', 'all')
    # Validate the queue ID from the request
    if selected_queue_id not in ['all', '420', '440', 'all_rankeds']:
        selected_queue_id = 'all'

    # Map the ID from the request to the key used in the cache
    queue_id_to_name_map = {'420': 'soloq', '440': 'flex', 'all': 'all', 'all_rankeds': 'all_rankeds'}
    selected_queue_name = queue_id_to_name_map.get(selected_queue_id, 'all')

    selected_champion = request.args.get('champion', 'all')
    if selected_champion == 'all':
        selected_champion = None

    with GLOBAL_STATS_LOCK:
        all_global_stats = GLOBAL_STATS_CACHE['data']
        all_matches = GLOBAL_STATS_CACHE.get('all_matches', [])
        timestamp = GLOBAL_STATS_CACHE['timestamp']

    # If cache is empty or too old, try to recalculate
    if not all_global_stats or not all_matches or (time.time() - timestamp > GLOBAL_STATS_UPDATE_INTERVAL):
        print("[estadisticas_globales] La caché de estadísticas globales está vacía o desactualizada. Intentando recalcular...")
        _calculate_and_cache_global_stats() # Force update
        with GLOBAL_STATS_LOCK:
            all_global_stats = GLOBAL_STATS_CACHE['data']
            all_matches = GLOBAL_STATS_CACHE.get('all_matches', [])

    # Select the stats for the chosen queue
    if not selected_champion:
        global_stats = all_global_stats.get(selected_queue_name) if all_global_stats else None
    else:
        # Define the mapping from the request value to the actual filter value
        queue_id_map = {'420': 420, '440': 440, 'all_rankeds': [420, 440], 'all': None}
        queue_id_filter = queue_id_map.get(selected_queue_id)
        global_stats = _calculate_stats_for_queue(all_matches, queue_id_filter, champion_filter=selected_champion)


    if not global_stats:
        print("[estadisticas_globales] No se pudieron cargar las estadísticas globales. Renderizando con datos vacíos.")
        # Provide default empty data structure for all queues to avoid template errors
        def default_record_set():
            return {
                'overall_win_rate': 0, 'total_games': 0, 'most_played_champions': [],
                'global_records': {k: {'value': 0, 'player': 'N/A', 'riot_id': 'N/A', 'champion_name': 'N/A', 'kda': 0, 'game_date': 0, 'game_duration': 0, 'is_tied_record': False}
                                   for k in ['longest_game', 'most_kills', 'most_deaths', 'most_assists', 'highest_kda', 'most_cs', 'most_damage_dealt', 'most_gold_earned', 'most_vision_score', 'largest_killing_spree', 'largest_multikill', 'most_time_spent_dead', 'most_wards_placed', 'most_wards_killed', 'most_turret_kills', 'most_inhibitor_kills', 'most_baron_kills', 'most_dragon_kills', 'most_damage_taken', 'most_total_heal', 'most_damage_shielded_on_teammates', 'most_time_ccing_others', 'most_objectives_stolen', 'highest_kill_participation', 'most_double_kills', 'most_triple_kills', 'most_quadra_kills', 'most_penta_kills']}
            }
        global_stats = default_record_set()
    
    champion_list = sorted(list(set(m.get('champion_name') for m in all_matches if m.get('champion_name'))))
    
    available_queues = [
        {'id': 'all_rankeds', 'name': 'All Rankeds'},
        {'id': 420, 'name': 'Ranked Solo/Duo'},
        {'id': 440, 'name': 'Ranked Flex'}
    ]

    return render_template('estadisticas.html', global_stats=global_stats, ddragon_version=DDRAGON_VERSION, current_queue=selected_queue_id, available_queues=available_queues, champion_list=champion_list, selected_champion=selected_champion)



# Se ha modificado la firma de la función para aceptar `player_display_name` y `riot_id`.
def _default_record_template():
    """Plantilla de registro por defecto para reutilizar en múltiples lugares."""
    return {
        'value': 0, 'match_id': None, 'game_date': 0, 'champion_name': 'N/A',
        'kda': 0, 'achieved_timestamp': 0, 'game_duration': 0, 'kills': 0,
        'deaths': 0, 'assists': 0, 'riot_id': 'N/A', 'player': 'N/A', 'champion_id': 'N/A'
    }

def _create_personal_records_dict():
    """Crea un diccionario con todos los récords personales inicializados."""
    record_keys = [
        'longest_game', 'most_kills', 'most_deaths', 'most_assists', 'highest_kda',
        'most_cs', 'most_damage_dealt', 'most_gold_earned', 'most_vision_score',
        'largest_killing_spree', 'largest_multikill', 'most_time_spent_dead',
        'most_wards_placed', 'most_wards_killed', 'most_turret_kills',
        'most_inhibitor_kills', 'most_baron_kills', 'most_dragon_kills',
        'most_damage_taken', 'most_total_heal', 'most_damage_shielded_on_teammates',
        'most_time_ccing_others', 'most_objectives_stolen', 'highest_kill_participation',
        'most_double_kills', 'most_triple_kills', 'most_quadra_kills', 'most_penta_kills',
        'longest_win_streak', 'longest_loss_streak'
    ]
    return {key: _default_record_template() for key in record_keys}

def _get_player_personal_records(puuid, player_display_name, riot_id, champion_filter=None):
    """Calcula y devuelve los récords personales de un jugador.
    Utiliza caché para minimizar el consumo de CPU.
    """
    print(f"[_get_player_personal_records] Solicitud de récords personales para PUUID: {puuid}, Jugador: {player_display_name}, Riot ID: {riot_id}, Campeón: {champion_filter or 'Todos'}")

    # Generate a cache key based on puuid and champion filter
    cache_key = f"{puuid}_{champion_filter or 'all'}"

    with PERSONAL_RECORDS_LOCK:
        cached_data = PERSONAL_RECORDS_CACHE['data'].get(cache_key)
        cache_timestamp = PERSONAL_RECORDS_CACHE.get('timestamp', 0)

        # Check if cached data exists and is not stale
        if cached_data and (time.time() - cache_timestamp < PERSONAL_RECORDS_UPDATE_INTERVAL):
            print(f"[_get_player_personal_records] Devolviendo récords personales cacheados para: {cache_key}.")
            return cached_data

    print(f"[_get_player_personal_records] Calculando récords personales para: {cache_key} (no cacheados o estancados).")
    historial = get_player_match_history(puuid, riot_id=riot_id) 
    all_matches_for_player = historial.get('matches', [])

    # Filter matches by champion if a filter is provided
    filtered_matches = [m for m in all_matches_for_player if m.get('champion_name') == champion_filter] if champion_filter else all_matches_for_player

    personal_records = _create_personal_records_dict()

    for match in filtered_matches:
        match['jugador_nombre'] = player_display_name
        match['riot_id'] = riot_id
    
    # Sort matches by game end time, from oldest to newest for streak calculation
    filtered_matches.sort(key=lambda x: x.get('game_end_timestamp', 0))

    streaks = calcular_rachas(filtered_matches)
    if streaks['max_win_streak'] > 0:
        # Find the last match of the longest win streak to represent the record
        win_streak_end_match = None
        current_streak = 0
        for match in filtered_matches:
            if match.get('win'):
                current_streak += 1
                if current_streak == streaks['max_win_streak']:
                    win_streak_end_match = match
                    break
            else:
                current_streak = 0
        if win_streak_end_match:
            personal_records['longest_win_streak'] = _update_record(personal_records['longest_win_streak'], streaks['max_win_streak'], win_streak_end_match, 'longest_win_streak')

    if streaks['max_loss_streak'] > 0:
        # Find the last match of the longest loss streak
        loss_streak_end_match = None
        current_streak = 0
        for match in filtered_matches:
            if not match.get('win'):
                current_streak += 1
                if current_streak == streaks['max_loss_streak']:
                    loss_streak_end_match = match
                    break
            else:
                current_streak = 0
        if loss_streak_end_match:
            personal_records['longest_loss_streak'] = _update_record(personal_records['longest_loss_streak'], streaks['max_loss_streak'], loss_streak_end_match, 'longest_loss_streak')


    for match in filtered_matches:
        personal_records['longest_game'] = _update_record(personal_records['longest_game'], match.get('game_duration', 0), match, 'longest_game')
        personal_records['most_kills'] = _update_record(personal_records['most_kills'], match.get('kills', 0), match, 'most_kills')
        personal_records['most_deaths'] = _update_record(personal_records['most_deaths'], match.get('deaths', 0), match, 'most_deaths')
        personal_records['most_assists'] = _update_record(personal_records['most_assists'], match.get('assists', 0), match, 'most_assists')
        personal_records['highest_kda'] = _update_record(personal_records['highest_kda'], match.get('kda', 0), match, 'highest_kda')
        
        total_cs = match.get('total_minions_killed', 0) + match.get('neutral_minions_killed', 0)
        personal_records['most_cs'] = _update_record(personal_records['most_cs'], total_cs, match, 'most_cs')
            
        personal_records['most_damage_dealt'] = _update_record(personal_records['most_damage_dealt'], match.get('total_damage_dealt_to_champions', 0), match, 'most_damage_dealt')
        personal_records['most_gold_earned'] = _update_record(personal_records['most_gold_earned'], match.get('gold_earned', 0), match, 'most_gold_earned')
        personal_records['most_vision_score'] = _update_record(personal_records['most_vision_score'], match.get('vision_score', 0), match, 'most_vision_score')
        personal_records['largest_killing_spree'] = _update_record(personal_records['largest_killing_spree'], match.get('largest_killing_spree', 0), match, 'largest_killing_spree')
        personal_records['largest_multikill'] = _update_record(personal_records['largest_multikill'], match.get('largestMultiKill', 0), match, 'largest_multikill')
        personal_records['most_time_spent_dead'] = _update_record(personal_records['most_time_spent_dead'], match.get('total_time_spent_dead', 0), match, 'most_time_spent_dead')
        personal_records['most_wards_placed'] = _update_record(personal_records['most_wards_placed'], match.get('wards_placed', 0), match, 'most_wards_placed')
        personal_records['most_wards_killed'] = _update_record(personal_records['most_wards_killed'], match.get('wards_killed', 0), match, 'most_wards_killed')
        personal_records['most_turret_kills'] = _update_record(personal_records['most_turret_kills'], match.get('turret_kills', 0), match, 'most_turret_kills')
        personal_records['most_inhibitor_kills'] = _update_record(personal_records['most_inhibitor_kills'], match.get('inhibitor_kills', 0), match, 'most_inhibitor_kills')
        personal_records['most_baron_kills'] = _update_record(personal_records['most_baron_kills'], match.get('baron_kills', 0), match, 'most_baron_kills')
        personal_records['most_dragon_kills'] = _update_record(personal_records['most_dragon_kills'], match.get('dragon_kills', 0), match, 'most_dragon_kills')
        personal_records['most_damage_taken'] = _update_record(personal_records['most_damage_taken'], match.get('total_damage_taken', 0), match, 'most_damage_taken')
        personal_records['most_total_heal'] = _update_record(personal_records['most_total_heal'], match.get('total_heal', 0), match, 'most_total_heal')
        personal_records['most_damage_shielded_on_teammates'] = _update_record(personal_records['most_damage_shielded_on_teammates'], match.get('total_damage_shielded_on_teammates', 0), match, 'most_damage_shielded_on_teammates')
        personal_records['most_time_ccing_others'] = _update_record(personal_records['most_time_ccing_others'], match.get('time_ccing_others', 0), match, 'most_time_ccing_others')
        personal_records['most_objectives_stolen'] = _update_record(personal_records['most_objectives_stolen'], match.get('objectives_stolen', 0), match, 'most_objectives_stolen')
        personal_records['highest_kill_participation'] = _update_record(personal_records['highest_kill_participation'], match.get('kill_participation', 0), match, 'highest_kill_participation')
        personal_records['most_double_kills'] = _update_record(personal_records['most_double_kills'], match.get('doubleKills', 0), match, 'most_double_kills') 
        personal_records['most_triple_kills'] = _update_record(personal_records['most_triple_kills'], match.get('tripleKills', 0), match, 'most_triple_kills')  
        personal_records['most_quadra_kills'] = _update_record(personal_records['most_quadra_kills'], match.get('quadraKills', 0), match, 'most_quadra_kills')  
        personal_records['most_penta_kills'] = _update_record(personal_records['most_penta_kills'], match.get('pentaKills', 0), match, 'most_penta_kills')    
        
    print(f"[_get_player_personal_records] Récords personales calculados para: {cache_key}.")
    
    # Cache the newly calculated records
    with PERSONAL_RECORDS_LOCK:
        PERSONAL_RECORDS_CACHE['data'][cache_key] = personal_records
        PERSONAL_RECORDS_CACHE['timestamp'] = time.time() # Update timestamp on new calculation
    
    return personal_records

@app.route('/api/player/<puuid>/champions')
def get_player_champions(puuid):
    """API endpoint to get the list of champions a player has played."""
    try:
        print(f"[get_player_champions] Petición recibida para los campeones del PUUID: {puuid}.")
        if not puuid:
            return jsonify({"error": "PUUID no proporcionado"}), 400

        historial = get_player_match_history(puuid)
        matches = historial.get('matches', [])
        
        champions = sorted(list(set(m['champion_name'] for m in matches if 'champion_name' in m)))
        
        print(f"[get_player_champions] Devolviendo {len(champions)} campeones únicos para el PUUID: {puuid}.")
        return jsonify(champions)
    except Exception as e:
        print(f"[get_player_champions] ERROR: {e}")
        return jsonify({"error": "Ocurrió un error inesperado en el servidor."}), 500

@app.route('/api/personal_records/<puuid>')
def get_personal_records_api(puuid):
    """
    API endpoint para obtener los récords personales de un jugador dado su PUUID.
    """
    try:
        print(f"[get_personal_records_api] Petición recibida para PUUID: {puuid}.")
        
        champion_filter = request.args.get('champion')
        if champion_filter == 'all':
            champion_filter = None

        player_display_name = "Desconocido"
        riot_id = "Desconocido"

        datos_jugadores, _ = obtener_datos_jugadores()
        for jugador_info in datos_jugadores:
            if jugador_info.get('puuid') == puuid:
                player_display_name = jugador_info.get('jugador', "Desconocido")
                riot_id = jugador_info.get('game_name', "Desconocido")
                break

        if not puuid:
            print("[get_personal_records_api] Error: PUUID no proporcionado.")
            return jsonify({"error": "PUUID no proporcionado"}), 400

        personal_records = _get_player_personal_records(puuid, player_display_name, riot_id, champion_filter=champion_filter)
        
        if personal_records:
            print(f"[get_personal_records_api] Récords personales cargados para PUUID: {puuid} (Campeón: {champion_filter or 'Todos'}).")
            records_list = []
            for record_type, record_data in personal_records.items():
                record_data['record_type_key'] = record_type 
                records_list.append(record_data)
            return jsonify(records_list)
        else:
            print(f"[get_personal_records_api] No se encontraron récords personales para PUUID: {puuid} (Campeón: {champion_filter or 'Todos'}).")
            return jsonify({"message": "No se encontraron récords personales para esta cuenta y filtro."}), 404
    except Exception as e:
        print(f"[get_personal_records_api] ERROR: {e}")
        return jsonify({"error": "Ocurrió un error inesperado en el servidor."}), 500


@app.route('/records_personales')
def records_personales_page():
    """
    Renderiza la página de récords personales con los selectores.
    """
    print("[records_personales_page] Petición recibida para la página de récords personales.")
    
    # Obtener la lista de todos los jugadores para el selector
    cuentas = leer_cuentas()
    puuid_dict = leer_puuids()
    
    player_options = []
    for riot_id, jugador_nombre in cuentas:
        player_options.append({
            'riot_id': riot_id,
            'jugador_nombre': jugador_nombre,
            'puuid': puuid_dict.get(riot_id) # Asegúrate de tener el PUUID para cada jugador
        })

    return render_template('records_personales.html',
                           player_options=player_options,
                           ddragon_version=DDRAGON_VERSION)


def _calculate_and_cache_personal_records_periodically():
    """Hilo para calcular y cachear los récords personales periódicamente."""
    print("[_calculate_and_cache_personal_records_periodically] Hilo de cálculo de récords personales iniciado.")
    while True:
        try:
            cuentas = leer_cuentas()
            puuid_dict = leer_puuids()

            for riot_id, jugador_nombre in cuentas:
                puuid = puuid_dict.get(riot_id)
                if puuid:
                    # Calling _get_player_personal_records will calculate and cache if needed
                    _get_player_personal_records(puuid, jugador_nombre, riot_id)
                    print(f"[_calculate_and_cache_personal_records_periodically] Récords personales actualizados para {riot_id}.")
                else:
                    print(f"[_calculate_and_cache_personal_records_periodically] PUUID no encontrado para {riot_id}. Saltando.")
            print(f"[_calculate_and_cache_personal_records_periodically] Próximo cálculo en {PERSONAL_RECORDS_UPDATE_INTERVAL / 60} minutos.")
        except Exception as e:
            print(f"[_calculate_and_cache_personal_records_periodically] ERROR en el hilo de cálculo de récords personales: {e}")
        time.sleep(PERSONAL_RECORDS_UPDATE_INTERVAL)


# --- New function for background global stats calculation ---
def _calculate_and_cache_global_stats_periodically():
    """Hilo para calcular y cachear las estadísticas globales periódicamente."""
    print("[_calculate_and_cache_global_stats_periodically] Hilo de cálculo de estadísticas globales iniciado.")
    while True:
        try:
            _calculate_and_cache_global_stats()
            print(f"[_calculate_and_cache_global_stats_periodically] Próximo cálculo en {GLOBAL_STATS_UPDATE_INTERVAL / 60} minutos.")
        except Exception as e:
            print(f"[_calculate_and_cache_global_stats_periodically] ERROR en el hilo de cálculo de estadísticas globales: {e}")
        time.sleep(GLOBAL_STATS_UPDATE_INTERVAL)

@app.route('/api/analisis-ia/<puuid>', methods=['GET'])
def analizar_partidas_gemini(puuid):
    try:
        print(f"[analizar_partidas_gemini] Iniciando análisis para PUUID: {puuid}")
        if not gemini_client:
            print("[analizar_partidas_gemini] Gemini no configurado")
            return jsonify({"error": "Gemini no configurado"}), 500

        # 1. ¿Tiene permiso manual en GitHub?
        print(f"[analizar_partidas_gemini] Verificando permisos para {puuid}")
        tiene_permiso, permiso_sha = gestionar_permiso_jugador(puuid)
        print(f"[analizar_partidas_gemini] Permiso: {tiene_permiso}")

        # 2. Obtener las últimas 5 partidas de SoloQ
        riot_id_info = next((rid for rid, p in leer_puuids().items() if p == puuid), None)
        print(f"[analizar_partidas_gemini] Riot ID encontrado: {riot_id_info}")
        historial = get_player_match_history(puuid, riot_id=riot_id_info)
        matches_soloq = sorted(
            [m for m in historial.get('matches', []) if m.get('queue_id') == 420],
            key=lambda x: x.get('game_end_timestamp', 0), reverse=True
        )[:5]
        print(f"[analizar_partidas_gemini] Partidas encontradas: {len(matches_soloq)}")

        if not matches_soloq:
            return jsonify({"error": "No hay partidas de SoloQ para analizar"}), 404

        # Crear firma de las partidas
        current_signature = "-".join(sorted([str(m['match_id']) for m in matches_soloq]))
        print(f"[analizar_partidas_gemini] Firma actual: {current_signature}")

        # 3. Revisar si ya existe un análisis previo
        analisis_previo, player_sha = obtener_analisis_github(puuid)
        print(f"[analizar_partidas_gemini] Análisis previo encontrado: {analisis_previo is not None}")

        # LÓGICA DE DECISIÓN
        if tiene_permiso == False:
            if analisis_previo:
                # Si son las mismas partidas, damos la caché (es gratis)
                if analisis_previo.get('signature') == current_signature:
                    print("[analizar_partidas_gemini] Devolviendo caché")
                    return jsonify({"origen": "cache", **analisis_previo['data']}), 200

                # Si son nuevas, aplicar cooldown de 24h
                horas = (time.time() - analisis_previo.get('timestamp', 0)) / 3600
                if horas < 24:
                    print(f"[analizar_partidas_gemini] Cooldown activo: {horas} horas")
                    return jsonify({
                        "error": "Cooldown",
                        "mensaje": f"Espera {int(24-horas)}h o pide rehabilitación manual."
                    }), 429
            else:
                print("[analizar_partidas_gemini] Usuario bloqueado")
                return jsonify({"error": "Bloqueado", "mensaje": "No tienes permiso activo."}), 403

        # 4. EJECUCIÓN DE LLAMADA A GEMINI
        # Si llega aquí es porque tiene_permiso=="SI" o el cooldown expiró
        print("[analizar_partidas_gemini] Ejecutando llamada a Gemini")
        resumen_ia = []
        for m in matches_soloq:
            resumen_ia.append({
                "campeon": m.get('champion_name'),
                "kda": f"{m.get('kills')}/{m.get('deaths')}/{m.get('assists')}",
                "resultado": "Victoria" if m.get('win') else "Derrota",
                "daño": m.get('total_damage_dealt_to_champions')
            })

        prompt = f"Analiza estas 5 partidas de LoL para el jugador {puuid}: {json.dumps(resumen_ia)}"
        print(f"[analizar_partidas_gemini] Prompt creado: {prompt[:100]}...")

        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt,
                config={'response_mime_type': 'application/json', 'response_schema': AnalisisSoloQ}
            )
            print("[analizar_partidas_gemini] Respuesta de Gemini obtenida")

            resultado_final = response.parsed.dict()
            print("[analizar_partidas_gemini] Resultado parseado")
        except Exception as gemini_error:
            # Manejar errores específicos de Gemini API
            error_str = str(gemini_error)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                print(f"[analizar_partidas_gemini] Cuota de Gemini agotada: {gemini_error}")
                return jsonify({
                    "error": "Cuota agotada",
                    "mensaje": "Has excedido la cuota gratuita de Gemini. Espera unas horas para que se renueve o actualiza a un plan de pago."
                }), 429
            else:
                # Re-lanzar otros errores para que sean manejados por el except general
                raise gemini_error

        # 5. ACTUALIZAR GITHUB
        # Guardar el análisis
        nuevo_doc = {"timestamp": time.time(), "signature": current_signature, "data": resultado_final}
        actualizar_archivo_github(f"analisisIA/{puuid}.json", nuevo_doc, player_sha)
        print("[analizar_partidas_gemini] Análisis guardado en GitHub")

        # AUTO-BLOQUEO: Volvemos a poner el permiso en NO para la próxima vez
        # Así tú tienes que ponerlo en SI manualmente para rehabilitarlo
        estado_bloqueado = {
            "permitir_llamada": "NO",
            "razon": "Llamada consumida. Requiere rehabilitación manual.",
            "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        actualizar_archivo_github(f"config/permisos/{puuid}.json", estado_bloqueado, permiso_sha)
        print("[analizar_partidas_gemini] Permiso bloqueado")

        return jsonify({"origen": "nuevo", **resultado_final}), 200

    except Exception as e:
        print(f"[analizar_partidas_gemini] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error en el servidor", "detalle": str(e)}), 500

if __name__ == "__main__":
    print("[main] Iniciando la aplicación Flask.")
    
    # Iniciar el hilo del control de tasa de API
    api_rate_limiter_thread = threading.Thread(target=_api_rate_limiter_worker)
    api_rate_limiter_thread.daemon = True
    api_rate_limiter_thread.start()
    print("[main] Hilo 'api_rate_limiter_thread' iniciado.")

    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()
    print("[main] Hilo 'keep_alive' iniciado.")

    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()
    print("[main] Hilo 'actualizar_cache_periodicamente' iniciado.")

    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()
    print("[main] Hilo 'actualizar_historial_partidas_en_segundo_plano' iniciado.")

    # Iniciar el hilo de cálculo de estadísticas globales
    global_stats_calc_thread = threading.Thread(target=_calculate_and_cache_global_stats_periodically)
    global_stats_calc_thread.daemon = True
    global_stats_calc_thread.start()
    print("[main] Hilo 'actualizar_estadisticas_globales_periodicamente' iniciado.")

    # Iniciar el hilo de cálculo de récords personales
    personal_records_calc_thread = threading.Thread(target=_calculate_and_cache_personal_records_periodically)
    personal_records_calc_thread.daemon = True
    personal_records_calc_thread.start()
    print("[main] Hilo 'actualizar_records_personales_periodicamente' iniciado.")

    # OPTIMIZACIÓN: Ya no necesitamos el worker de lp_tracker separado
    # Los snapshots se registran en procesar_jugador() sin hacer llamadas extra a la API
    # y se guardan en GitHub cada hora desde actualizar_cache()
    # print("[main] Hilo 'lp_tracker_thread' desactivado (snapshots ahora integrados en actualizar_cache)")

    port = int(os.environ.get("PORT", 5000))
    print(f"[main] Aplicación Flask ejecutándose en http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)
