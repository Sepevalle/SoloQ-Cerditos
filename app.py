from flask import Flask, render_template, redirect, url_for, request, jsonify
import requests
import os
import time
import threading
import json
import base64
from datetime import datetime, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import queue # Import for the queue

app = Flask(__name__)

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

@app.template_filter('format_timestamp')
def format_timestamp_filter(timestamp):
    return datetime.fromtimestamp(timestamp / 1000).strftime("%d/%m/%Y %H:%M")


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
    rank_map = {3: "I", 2: "II", 1: "III", 0: "IV"}

    league_points = valor % 100
    valor_without_lps = valor - league_points
    rank_value = (valor_without_lps // 100) % 4
    tier_value = (valor_without_lps // 100) // 4

    tier_name = tier_map.get(tier_value, "UNKNOWN")
    rank_name = rank_map.get(rank_value, "")
    return f"{tier_name} {rank_name} ({league_points} LPs)"

    
    try:
        valor = int(valor)
    except (ValueError, TypeError) as e:
        print(f"[format_peak_elo_filter] Error al convertir valor a int: {valor}, Error: {e}. Retornando 'N/A'.")
        return "N/A"

    # Master, Grandmaster, Challenger
    if valor >= 2800:
        lps = valor - 2800
        # Para estos tiers, no tenemos rank (I, II, III, IV) sino solo LPs.
        # No podemos reconstruir perfectamente Maestro, GM, Aspirante solo del valor de LP,
        # así que usaremos un genérico "Maestro+" y los LPs.
        # Puedes ajustar los umbrales si tienes una forma más precisa de distinguirlos.
        if valor >= 3200: # Ejemplo de umbral para Aspirante, ajustar según sea necesario
            return f"CHALLENGER ({lps} LPs)"
        elif valor >= 3000: # Ejemplo de umbral para Gran Maestro
            return f"GRANDMASTER ({lps} LPs)"
        else:
            return f"MASTER ({lps} LPs)"

    # Para tiers inferiores (Hierro a Diamante)
    tier_map = {
        6: "DIAMOND", 5: "EMERALD", 4: "PLATINUM", 3: "GOLD", 
        2: "SILVER", 1: "BRONZE", 0: "IRON"
    }
    rank_map = {"I": 3, "II": 2, "III": 1, "IV": 0}

    # Calcular LPs primero (el resto al dividir por 100)
    league_points = valor % 100
    
    # Calcular el valor sin LPs
    valor_without_lps = valor - league_points
    
    # Calcular el valor de la división (0 para IV, 1 para III, 2 para II, 3 para I)
    # Es el resto de (valor_without_lps / 100) dividido por 4
    rank_value = (valor_without_lps // 100) % 4
    
    # Calcular el valor del tier
    tier_value = (valor_without_lps // 100) // 4

    tier_name = tier_map.get(tier_value, "UNKNOWN")
    rank_name = rank_map.get(rank_value, "")

    return f"{tier_name} {rank_name} ({league_points} LPs)"

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")
    # exit(1) # Removed exit(1) to allow the app to run even without API key for testing purposes, but it will not fetch data.

# URLs base de la API de Riot
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"


# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": [],
    "timestamp": 0
}
CACHE_TIMEOUT = 130  # 2 minutos
cache_lock = threading.Lock()

# Global storage for LP tracking
# Stores { (puuid, queue_type_string): {'pre_game_valor_clasificacion': int, 'game_start_timestamp': float, 'riot_id': str, 'queue_type': str} }
player_in_game_lp = {}
player_in_game_lp_lock = threading.Lock()

# Stores { (puuid, queue_type_string): {'pre_game_valor_clasificacion': int, 'detection_timestamp': float, 'riot_id': str, 'queue_type': str} }
# This will hold LP changes detected immediately after a game, to be associated with a match later.
pending_lp_updates = {}
pending_lp_updates_lock = threading.Lock()

# --- CONFIGURACIÓN DE SPLITS ---
SPLITS = {
    "s15_split1": {
        "name": "Temporada 2025 - Split 1",
        "start_date": datetime(2025, 1, 9),
    },
    "s15_split2": {
        "name": "Temporada 2025 - Split 2",
        "start_date": datetime(2025, 5, 15),
    },
    "s15_split3": {
        "name": "Temporada 2025 - Split 3",
        "start_date": datetime(2025, 9, 10),
    }
}

ACTIVE_SPLIT_KEY = "s15_split1"
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
            if not API_REQUEST_QUEUE.empty():
                print(f"[_api_rate_limiter_worker] Tamaño de la cola de peticiones: {API_REQUEST_QUEUE.qsize()}")
            request_id, url, headers, timeout, is_spectator_api = API_REQUEST_QUEUE.get(timeout=1)
            
            # Consumir un token antes de realizar la petición
            riot_api_limiter.consume_token()

            print(f"[_api_rate_limiter_worker] Procesando petición {request_id} a: {url}")
            response = None
            for i in range(3): # Reintentos para la petición HTTP real
                try:
                    response = session.get(url, headers=headers, timeout=timeout)
                    
                    # Si es una API de espectador y devuelve 404, no reintentar
                    if is_spectator_api and response.status_code == 404:
                        print(f"[_api_rate_limiter_worker] Petición {request_id} a la API de espectador devolvió 404. No se reintentará.")
                        break # Salir del bucle de reintentos inmediatamente

                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        print(f"[_api_rate_limiter_worker] Rate limit excedido. Esperando {retry_after} segundos... (Intento {i + 1}/3)")
                        time.sleep(retry_after)
                        continue # Reintentar la petición después de esperar
                    response.raise_for_status() # Lanza una excepción para códigos de error HTTP
                    print(f"[_api_rate_limiter_worker] Petición {request_id} exitosa. Status: {response.status_code}")
                    break # Salir del bucle de reintentos si es exitoso
                except requests.exceptions.RequestException as e:
                    print(f"[_api_rate_limiter_worker] Error en petición {request_id} a {url}: {e}. Intento {i + 1}/3")
                    if i < 2: # Si no es el último intento, espera y reintenta
                        time.sleep(0.5 * (2 ** i)) # Backoff exponencial
            
            # Almacenar la respuesta y notificar al hilo que la solicitó
            with REQUEST_ID_COUNTER_LOCK:
                API_RESPONSE_DATA[request_id] = response
                if request_id in API_RESPONSE_EVENTS:
                    API_RESPONSE_EVENTS[request_id].set() # Notificar que la respuesta está lista
                else:
                    print(f"[_api_rate_limiter_worker] Advertencia: Evento para request_id {request_id} no encontrado.")

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

ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    print("[obtener_todos_los_campeones] Obteniendo datos de campeones de Data Dragon.")
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    # Esta llamada no usa make_api_request porque es una API diferente (DDragon, no Riot Games)
    response = requests.get(url_campeones, timeout=10) 
    if response and response.status_code == 200:
        return {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
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
    global ALL_CHAMPIONS, ALL_RUNES, ALL_SUMMONER_SPELLS
    print("[actualizar_ddragon_data] Iniciando actualización de todos los datos de Data Dragon.")
    ALL_CHAMPIONS = obtener_todos_los_campeones()
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
    print(f"[obtener_puuid] Intentando obtener PUUID para {riot_id} en región {region}.")
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_puuid] PUUID obtenido para {riot_id}.")
        return response.json()
    else:
        print(f"[obtener_puuid] No se pudo obtener el PUUID para {riot_id} después de varios intentos.")
        return None

def obtener_id_invocador(api_key, puuid):
    """Obtiene el ID de invocador de un jugador dado su PUUID."""
    print(f"[obtener_id_invocador] Intentando obtener ID de invocador para PUUID: {puuid}.")
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_id_invocador] ID de invocador obtenido para PUUID: {puuid}.")
        return response.json()
    else:
        print(f"[obtener_id_invocador] No se pudo obtener el ID de invocador para {puuid}.")
        return None

def obtener_elo(api_key, puuid):
    """Obtiene la información de Elo de un jugador dado su PUUID."""
    print(f"[obtener_elo] Intentando obtener Elo para PUUID: {puuid}.")
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        print(f"[obtener_elo] Elo obtenido para PUUID: {puuid}.")
        return response.json()
    else:
        print(f"[obtener_elo] No se pudo obtener el Elo para {puuid}.")
        return None

def esta_en_partida(api_key, puuid):
    """
    Comprueba si un jugador está en una partida activa.
    Retorna los datos completos de la partida si está en una, None si no.
    """
    print(f"[esta_en_partida] Verificando si el jugador {puuid} está en partida.")
    try:
        url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
        # Usar make_api_request con is_spectator_api=True para control de tasa específico si es necesario
        response = make_api_request(url, is_spectator_api=True) 

        if response and response.status_code == 200:  # Player is in game
            game_data = response.json()
            for participant in game_data.get("participants", []):
                if participant["puuid"] == puuid:
                    print(f"[esta_en_partida] Jugador {puuid} está en partida activa.")
                    return game_data
            print(f"[esta_en_partida] Advertencia: Jugador {puuid} está en partida pero no se encontró en la lista de participantes.")
            return None
        elif response and response.status_code == 404:  # Player not in game (expected response)
            print(f"[esta_en_partida] Jugador {puuid} no está en partida activa (404 Not Found).")
            return None
        elif response is None: # make_api_request returned None due to timeout or persistent error
            print(f"[esta_en_partida] make_api_request devolvió None para {puuid}. Posible timeout o error persistente.")
            return None
        else:  # Unexpected error
            print(f"[esta_en_partida] Error inesperado al verificar partida para {puuid}. Status: {response.status_code}")
            response.raise_for_status() # Esto lanzará una excepción si el status no es 2xx
    except requests.exceptions.RequestException as e:
        print(f"[esta_en_partida] Error al verificar si el jugador {puuid} está en partida: {e}")
        return None

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida,
    además del nivel, hechizos, runas y AHORA MUCHAS MÁS ESTADÍSTICAS DETALLADAS.
    """
    match_id, puuid, api_key = args
    print(f"[obtener_info_partida] Obteniendo información para la partida {match_id} del PUUID {puuid}.")
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
        team_kills = {100: 0, 200: 0}

        for p in participants:
            participant_summary = {
                "summoner_name": p.get('riotIdGameName', p.get('summonerName')),
                "champion_name": obtener_nombre_campeon(p.get('championId')),
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
            if team_id in team_kills:
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
            print(f"[obtener_info_partida] Jugador principal {puuid} no encontrado en los participantes de la partida {match_id}.")
            return None

        game_end_timestamp = info.get('gameEndTimestamp', 0) 
        game_duration = info.get('gameDuration', 0)
        
        p = main_player_data

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

        print(f"[obtener_info_partida] Información de partida {match_id} procesada para {puuid}.")
        return {
            "match_id": match_id,
            "puuid": puuid,
            "champion_name": obtener_nombre_campeon(p.get('championId')),
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
            "lp_change_this_game": None, # Initialize LP change to None for new matches

            # --- AÑADIMOS LA LISTA DE TODOS LOS PARTICIPANTES ---
            "all_participants": all_participants_details
        }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[obtener_info_partida] Error procesando los detalles de la partida {match_id}: {e}")
    return None

def leer_cuentas(url):
    """Lee las cuentas de jugadores desde un archivo de texto alojado en GitHub."""
    print(f"[leer_cuentas] Leyendo cuentas desde: {url}")
    try:
        response = requests.get(url)
        if response.status_code == 200:
            contenido = response.text.strip().split(';')
            cuentas = []
            for linea in contenido:
                partes = linea.split(',')
                if len(partes) == 2:
                    riot_id = partes[0].strip()
                    jugador = partes[1].strip()
                    cuentas.append((riot_id, jugador))
            print(f"[leer_cuentas] {len(cuentas)} cuentas leídas exitosamente.")
            return cuentas
        else:
            print(f"[leer_cuentas] Error al leer el archivo de cuentas: {response.status_code}")
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
    """Lee los datos de peak Elo desde un archivo JSON en GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/refs/heads/main/peak_elo.json"
    print(f"[leer_peak_elo] Leyendo peak elo desde: {url}")
    try:
        resp = requests.get(url, timeout=30) # Aumentado timeout
        resp.raise_for_status()
        print("[leer_peak_elo] Peak elo leído exitosamente.")
        return True, resp.json()
    except Exception as e:
        print(f"[leer_peak_elo] Error leyendo peak elo: {e}")
    return False, {}

def leer_puuids():
    """Lee el archivo de PUUIDs desde GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/puuids.json"
    print(f"[leer_puuids] Leyendo PUUIDs desde: {url}")
    try:
        resp = requests.get(url, timeout=30) # Aumentado timeout
        if resp.status_code == 200:
            print("[leer_puuids] PUUIDs leídos exitosamente.")
            return resp.json()
        elif resp.status_code == 404:
            print("[leer_puuids] El archivo puuids.json no existe, se creará uno nuevo.")
            return {}
    except Exception as e:
        print(f"[leer_puuids] Error leyendo puuids.json: {e}")
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

def guardar_peak_elo_en_github(peak_elo_dict):
    """Guarda o actualiza el archivo peak_elo.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado para guardar peak_elo. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=30) # Aumentado timeout
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_peak_elo_en_github] SHA de peak_elo.json obtenido: {sha}")
        else:
            print(f"[guardar_peak_elo_en_github] Error al obtener el archivo peak_elo.json para SHA: {response.status_code}")
    except Exception as e:
        print(f"[guardar_peak_elo_en_github] No se pudo obtener el SHA de peak_elo.json: {e}")
        return

    try:
        contenido_json = json.dumps(peak_elo_dict, ensure_ascii=False, indent=2)
        contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')

        response = requests.put(
            url,
            headers=headers,
            json={
                "message": "Actualizar peak elo",
                "content": contenido_b64,
                "sha": sha,
                "branch": "main"
            },
            timeout=30 # Aumentado timeout
        )
        if response.status_code in (200, 201):
            print("[guardar_peak_elo_en_github] Archivo peak_elo.json actualizado correctamente en GitHub.")
        else:
            print(f"[guardar_peak_elo_en_github] Error al actualizar peak_elo.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_peak_elo_en_github] Error al actualizar el archivo peak_elo.json: {e}")

def leer_historial_jugador_github(puuid):
    """Lee el historial de partidas de un jugador desde GitHub."""
    url = f"https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/match_history/{puuid}.json"
    print(f"[leer_historial_jugador_github] Leyendo historial para PUUID: {puuid} desde: {url}")
    try:
        resp = requests.get(url, timeout=30) # Aumentado timeout
        if resp.status_code == 200:
            print(f"[leer_historial_jugador_github] Historial para {puuid} leído exitosamente.")
            return resp.json()
        elif resp.status_code == 404:
            print(f"[leer_historial_jugador_github] No se encontró historial para {puuid}. Se creará uno nuevo.")
            return {}
    except Exception as e:
        print(f"[leer_historial_jugador_github] Error leyendo el historial para {puuid}: {e}")
    return {}

def guardar_historial_jugador_github(puuid, historial_data):
    """Guarda o actualiza el historial de partidas de un jugador en GitHub."""
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/match_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"[guardar_historial_jugador_github] ERROR: Token de GitHub no encontrado para guardar historial de {puuid}. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=30) # Aumentado timeout
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_historial_jugador_github] SHA del historial de {puuid} obtenido: {sha}.")
        elif response.status_code == 404:
            print(f"[guardar_historial_jugador_github] Archivo {puuid}.json no existe en GitHub, se creará uno nuevo.")
        else:
            print(f"[guardar_historial_jugador_github] Error al obtener SHA del historial de {puuid}: {response.status_code} - {response.text}")
            return # Salir si no se puede obtener el SHA
    except Exception as e:
        print(f"[guardar_historial_jugador_github] Excepción al obtener SHA del historial de {puuid}: {e}")
        return # Salir si hay una excepción

    contenido_json = json.dumps(historial_data, indent=2, ensure_ascii=False) # Añadido ensure_ascii=False
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    data = {"message": f"Actualizar historial de partidas para {puuid}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha
    try:
        print(f"[guardar_historial_jugador_github] Intentando guardar historial para {puuid} en GitHub. SHA: {sha}")
        response = requests.put(url, headers=headers, json=data, timeout=30) # Aumentado timeout
        if response.status_code in (200, 201):
            print(f"[guardar_historial_jugador_github] Historial de {puuid}.json actualizado correctamente en GitHub. Status: {response.status_code}")
        else:
            print(f"[guardar_historial_jugador_github] ERROR: Fallo al actualizar historial de {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_historial_jugador_github] ERROR: Excepción en la petición PUT a GitHub para el historial de {puuid}: {e}")

def _calculate_lp_change_for_player(puuid, queue_type_api_name, all_matches_for_player):
    """ Calcula el cambio total de LP para un jugador en una cola específica en las últimas 24 horas. """
    now_timestamp_ms = int(datetime.now().timestamp() * 1000)
    one_day_ago_timestamp_ms = now_timestamp_ms - (24 * 60 * 60 * 1000)
    lp_change_24h = 0
    queue_id_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    target_queue_id = queue_id_map.get(queue_type_api_name)
    if not target_queue_id:
        print(f"[_calculate_lp_change_for_player] Tipo de cola '{queue_type_api_name}' no reconocido. Retornando 0 LP.")
        return 0
    for match in all_matches_for_player:
        # El timestamp guardado tiene un desfase de +2h (7200000ms).
        # Lo restamos aquí para compararlo de forma consistente con el timestamp
        # del servidor, que probablemente esté en UTC.
        match_timestamp_utc = match.get('game_end_timestamp', 0) - 7200000
        if match_timestamp_utc >= one_day_ago_timestamp_ms and match.get('queue_id') == target_queue_id:
            lp_change = match.get('lp_change_this_game')
            if lp_change is not None:
                lp_change_24h += lp_change
    
    print(f"[_calculate_lp_change_for_player] Cambio de LP en 24h para {puuid} en {queue_type_api_name}: {lp_change_24h} LP.")
    return lp_change_24h

def obtener_ultimas_partidas(api_key, puuid, region_route="europe", start=0, count=20):
    """
    Obtiene las últimas partidas de un jugador.
    """
    print(f"[obtener_ultimas_partidas] Obteniendo últimas partidas para PUUID: {puuid}.")
    url = f"https://{region_route}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}&api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    return []

def obtener_historial_lp(puuid, queue_type_api_name):
    """
    Función que lee el historial de LP de un jugador desde GitHub.
    """
    url = f"https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/lp_history/{puuid}.json"
    print(f"[obtener_historial_lp] Leyendo historial de LP para PUUID: {puuid} desde: {url}")
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            historial = resp.json()
            return historial.get(queue_type_api_name, [])
        elif resp.status_code == 404:
            print(f"[obtener_historial_lp] No se encontró historial de LP para {puuid}. Se creará uno nuevo.")
            return []
    except Exception as e:
        print(f"[obtener_historial_lp] Error leyendo el historial de LP para {puuid}: {e}")
    return []

def guardar_historial_lp(puuid, queue_type_api_name, nuevo_valor_clasificacion, lp_change):
    """
    Guarda o actualiza el historial de LP de un jugador en GitHub.
    """
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/lp_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"[guardar_historial_lp] ERROR: Token de GitHub no encontrado para guardar historial de LP de {puuid}.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    historial_total = {}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            sha = response.json().get('sha')
            contenido_b64_actual = response.json().get('content')
            historial_total = json.loads(base64.b64decode(contenido_b64_actual).decode('utf-8'))
            print(f"[guardar_historial_lp] SHA y contenido actual del historial de LP de {puuid} obtenidos.")
        elif response.status_code == 404:
            print(f"[guardar_historial_lp] Archivo de historial de LP para {puuid} no existe, se creará uno nuevo.")
        else:
            print(f"[guardar_historial_lp] Error al obtener el SHA del historial de LP de {puuid}: {response.status_code} - {response.text}")
            return
    except Exception as e:
        print(f"[guardar_historial_lp] Excepción al obtener SHA/contenido del historial de LP de {puuid}: {e}")
        return

    # Añadir el nuevo punto de datos al historial de la cola
    historial_cola = historial_total.get(queue_type_api_name, [])
    # Evitar duplicados (mismo valor en el mismo momento, con el mismo cambio)
    if not historial_cola or not (historial_cola[-1]['timestamp'] == int(time.time() * 1000) and historial_cola[-1]['valor'] == nuevo_valor_clasificacion and historial_cola[-1]['change'] == lp_change):
        historial_cola.append({
            'timestamp': int(time.time() * 1000),
            'valor': nuevo_valor_clasificacion,
            'change': lp_change,
        })
    # Limitar el historial a, por ejemplo, 2000 entradas para no tener archivos gigantes
    historial_total[queue_type_api_name] = historial_cola[-2000:]
    
    contenido_json = json.dumps(historial_total, indent=2, ensure_ascii=False)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": f"Actualizar historial de LP para {puuid}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha
        
    try:
        print(f"[guardar_historial_lp] Intentando guardar historial de LP para {puuid} en GitHub.")
        response = requests.put(url, headers=headers, json=data, timeout=30)
        if response.status_code in (200, 201):
            print(f"[guardar_historial_lp] Historial de LP de {puuid}.json actualizado correctamente en GitHub. Status: {response.status_code}")
        else:
            print(f"[guardar_historial_lp] ERROR: Fallo al actualizar historial de LP de {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_historial_lp] ERROR: Excepción en la petición PUT a GitHub para el historial de LP de {puuid}: {e}")

def actualizar_peak_elo_si_es_mayor(puuid, valor_actual):
    """
    Verifica si el valor de Elo actual es mayor que el peak Elo guardado,
    y lo actualiza si es así.
    """
    exito, peak_elo = leer_peak_elo()
    if not exito:
        # Si no se puede leer, no podemos actualizar
        print("[actualizar_peak_elo_si_es_mayor] No se pudo leer peak_elo.json, no se actualizará.")
        return

    peak_valor_guardado = peak_elo.get(puuid, 0)
    
    if valor_actual > peak_valor_guardado:
        print(f"[actualizar_peak_elo_si_es_mayor] Nuevo peak Elo detectado para {puuid}: {valor_actual} > {peak_valor_guardado}. Actualizando...")
        peak_elo[puuid] = valor_actual
        guardar_peak_elo_en_github(peak_elo)
    else:
        print(f"[actualizar_peak_elo_si_es_mayor] Peak Elo de {puuid} no ha cambiado. Valor actual: {valor_actual}, Peak guardado: {peak_valor_guardado}.")

def obtener_historial_partidas_jugador(puuid, riot_api_key):
    """
    Obtiene el historial de partidas de un jugador, primero intentando leer de GitHub
    y luego, si es necesario, actualizando con nuevas partidas de la API de Riot.
    """
    historial_github = leer_historial_jugador_github(puuid)
    partidas_existentes = set(historial_github.keys())
    
    print(f"[obtener_historial_partidas_jugador] Partidas existentes en GitHub para {puuid}: {len(partidas_existentes)}")

    ultimas_partidas_ids = obtener_ultimas_partidas(riot_api_key, puuid)
    
    if not ultimas_partidas_ids:
        return historial_github

    nuevas_partidas_ids = [
        match_id for match_id in ultimas_partidas_ids
        if match_id not in partidas_existentes
    ]
    
    print(f"[obtener_historial_partidas_jugador] Se encontraron {len(nuevas_partidas_ids)} nuevas partidas para {puuid}.")
    
    if not nuevas_partidas_ids:
        return historial_github

    # Usar ThreadPoolExecutor para obtener los detalles de las nuevas partidas en paralelo
    with ThreadPoolExecutor(max_workers=min(10, len(nuevas_partidas_ids))) as executor:
        args_list = [(match_id, puuid, riot_api_key) for match_id in nuevas_partidas_ids]
        nuevas_partidas_detalles = list(executor.map(obtener_info_partida, args_list))

    nuevas_partidas_validas = {
        p["match_id"]: p for p in nuevas_partidas_detalles
        if p and p["queue_id"] in [420, 440]
    }
    
    # Combinar el historial existente con las nuevas partidas
    historial_github.update(nuevas_partidas_validas)
    
    # Guardar el historial actualizado en GitHub
    if nuevas_partidas_validas:
        print(f"[obtener_historial_partidas_jugador] Guardando {len(nuevas_partidas_validas)} nuevas partidas para {puuid} en GitHub.")
        guardar_historial_jugador_github(puuid, historial_github)
    
    return historial_github

def _worker_monitor_jugadores():
    """Hilo de trabajo que se encarga de monitorear a los jugadores."""
    print("[_worker_monitor_jugadores] Hilo de monitoreo de jugadores iniciado.")
    # El worker del RateLimiter debe estar activo antes que este hilo
    rate_limiter_thread = threading.Thread(target=_api_rate_limiter_worker, daemon=True)
    rate_limiter_thread.start()

    while True:
        try:
            # Re-leer las cuentas cada 5 minutos para detectar nuevos jugadores
            cuentas = leer_cuentas(
                "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
            )
            puuids_from_file = leer_puuids()

            # Asegurar que todos los jugadores del archivo tienen un PUUID
            cuentas_a_actualizar = []
            for riot_id, jugador in cuentas:
                if riot_id not in puuids_from_file:
                    print(f"[_worker_monitor_jugadores] PUUID no encontrado para {riot_id}. Intentando obtenerlo.")
                    try:
                        account_info = obtener_puuid(RIOT_API_KEY, riot_id, "euw")
                        if account_info and 'puuid' in account_info:
                            puuids_from_file[riot_id] = {
                                "puuid": account_info['puuid'],
                                "riot_id": riot_id,
                                "jugador": jugador
                            }
                            print(f"[_worker_monitor_jugadores] PUUID obtenido para {riot_id}.")
                            cuentas_a_actualizar.append(puuids_from_file[riot_id])
                        else:
                            print(f"[_worker_monitor_jugadores] No se pudo obtener PUUID para {riot_id}. Saltando.")
                    except Exception as e:
                        print(f"[_worker_monitor_jugadores] Error al obtener PUUID para {riot_id}: {e}")

            if cuentas_a_actualizar:
                print(f"[_worker_monitor_jugadores] Se encontraron {len(cuentas_a_actualizar)} nuevos PUUIDs. Guardando en GitHub.")
                guardar_puuids_en_github(puuids_from_file)

            # Convertir el diccionario de PUUIDs a una lista de jugadores para el monitoreo
            jugadores_para_monitorear = list(puuids_from_file.values())
            if not jugadores_para_monitorear:
                print("[_worker_monitor_jugadores] No hay jugadores para monitorear. Esperando 5 minutos.")
                time.sleep(300)
                continue

            # Realizar las llamadas de la API en paralelo
            with ThreadPoolExecutor(max_workers=10) as executor:
                # Obtener el historial completo de partidas de todos los jugadores
                historiales = {
                    jugador['puuid']: obtener_historial_partidas_jugador(jugador['puuid'], RIOT_API_KEY)
                    for jugador in jugadores_para_monitorear
                }

                # Tarea 1: Verificar si están en partida y registrar LP
                print("[_worker_monitor_jugadores] Fase 1: Verificando partidas activas y registrando LP.")
                for jugador in jugadores_para_monitorear:
                    puuid = jugador['puuid']
                    riot_id = jugador['riot_id']
                    
                    # Verificar si el jugador ya está en la tabla de partidas activas
                    # y si la partida ha terminado
                    keys_to_remove = []
                    with player_in_game_lp_lock:
                        for key, game_info in player_in_game_lp.items():
                            player_puuid, queue_type_string = key
                            if player_puuid == puuid:
                                print(f"[_worker_monitor_jugadores] Jugador {riot_id} estaba en partida de {queue_type_string}. Verificando si sigue...")
                                # Comprobar si la partida sigue activa
                                active_game = esta_en_partida(RIOT_API_KEY, puuid)
                                if not active_game or active_game.get('gameId') != game_info.get('game_id'):
                                    print(f"[_worker_monitor_jugadores] Partida de {riot_id} en {queue_type_string} ha terminado. Movilizando para seguimiento de LP.")
                                    # Mover el seguimiento a la cola de pendientes
                                    with pending_lp_updates_lock:
                                        pending_lp_updates[key] = {
                                            'pre_game_valor_clasificacion': game_info['pre_game_valor_clasificacion'],
                                            'detection_timestamp': time.time(),
                                            'riot_id': riot_id,
                                            'queue_type': queue_type_string
                                        }
                                    keys_to_remove.append(key)
                                else:
                                    print(f"[_worker_monitor_jugadores] Jugador {riot_id} sigue en partida de {queue_type_string}.")

                    with player_in_game_lp_lock:
                        for key in keys_to_remove:
                            del player_in_game_lp[key]

                    # Si el jugador no está en la tabla de activos, ver si está en una nueva partida
                    if not any(puuid in key for key in player_in_game_lp):
                        active_game = esta_en_partida(RIOT_API_KEY, puuid)
                        if active_game:
                            print(f"[_worker_monitor_jugadores] Jugador {riot_id} ha entrado en una nueva partida.")
                            elo_data = obtener_elo(RIOT_API_KEY, puuid)
                            for queue_info in elo_data:
                                queue_type_api_name = queue_info.get('queueType')
                                if queue_type_api_name in ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]:
                                    valor_clasificacion_actual = calcular_valor_clasificacion(
                                        queue_info.get('tier', 'UNRANKED'),
                                        queue_info.get('rank', 'IV'),
                                        queue_info.get('leaguePoints', 0)
                                    )
                                    key = (puuid, queue_type_api_name)
                                    with player_in_game_lp_lock:
                                        if key not in player_in_game_lp:
                                            print(f"[_worker_monitor_jugadores] Registrando LP inicial para {riot_id} en {queue_type_api_name}: {valor_clasificacion_actual}")
                                            player_in_game_lp[key] = {
                                                'pre_game_valor_clasificacion': valor_clasificacion_actual,
                                                'game_start_timestamp': active_game.get('gameStartTime'),
                                                'riot_id': riot_id,
                                                'queue_type': queue_type_api_name,
                                                'game_id': active_game.get('gameId')
                                            }
                
                # Tarea 2: Procesar la cola de LP pendientes
                print("[_worker_monitor_jugadores] Fase 2: Procesando actualizaciones de LP pendientes.")
                keys_to_process = list(pending_lp_updates.keys())
                for key in keys_to_process:
                    puuid, queue_type_api_name = key
                    pending_info = pending_lp_updates.get(key)
                    if pending_info:
                        riot_id = pending_info['riot_id']
                        pre_game_valor = pending_info['pre_game_valor_clasificacion']
                        
                        elo_data = obtener_elo(RIOT_API_KEY, puuid)
                        current_valor = None
                        for queue_info in elo_data:
                            if queue_info.get('queueType') == queue_type_api_name:
                                current_valor = calcular_valor_clasificacion(
                                    queue_info.get('tier', 'UNRANKED'),
                                    queue_info.get('rank', 'IV'),
                                    queue_info.get('leaguePoints', 0)
                                )
                                break
                        
                        if current_valor is not None:
                            lp_change = current_valor - pre_game_valor
                            print(f"[_worker_monitor_jugadores] LP change for {riot_id} in {queue_type_api_name} detected: {lp_change}")
                            
                            # Encontrar la partida recién jugada y asociar el cambio de LP
                            match_history = historiales.get(puuid, {})
                            # Buscar una partida sin cambio de LP y que haya terminado recientemente
                            matches_to_update = [
                                match for match_id, match in match_history.items()
                                if match.get('lp_change_this_game') is None
                                and match.get('queue_id') == pending_info['queue_type']
                                # Considerar solo partidas que terminaron después de que detectamos que la partida estaba activa
                                # Esto es una aproximación, podría mejorarse.
                                and match.get('game_end_timestamp', 0) >= (pending_info['detection_timestamp'] * 1000)
                            ]
                            
                            if matches_to_update:
                                # Asumir que la partida más reciente es la correcta
                                most_recent_match = max(matches_to_update, key=lambda m: m['game_end_timestamp'])
                                most_recent_match['lp_change_this_game'] = lp_change
                                
                                # Actualizar el historial de partidas en GitHub
                                print(f"[_worker_monitor_jugadores] Asociando LP change de {lp_change} a la partida {most_recent_match['match_id']} de {riot_id}.")
                                guardar_historial_jugador_github(puuid, match_history)
                                
                                # Guardar en el historial de LP para el gráfico
                                print(f"[_worker_monitor_jugadores] Guardando historial de LP para {riot_id}.")
                                guardar_historial_lp(puuid, queue_type_api_name, current_valor, lp_change)

                            # Eliminar de la cola de pendientes
                            with pending_lp_updates_lock:
                                if key in pending_lp_updates:
                                    del pending_lp_updates[key]

                # Tarea 3: Actualizar datos de jugadores y peak Elo (esto se hace cada 5 minutos)
                print("[_worker_monitor_jugadores] Fase 3: Actualizando datos de Elo y peak Elo.")
                # Obtener ELO y actualizar el peak ELO de todos los jugadores
                for jugador in jugadores_para_monitorear:
                    puuid = jugador['puuid']
                    elo_data = obtener_elo(RIOT_API_KEY, puuid)
                    for queue_info in elo_data:
                        if queue_info.get('queueType') in ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]:
                            valor_clasificacion_actual = calcular_valor_clasificacion(
                                queue_info.get('tier', 'UNRANKED'),
                                queue_info.get('rank', 'IV'),
                                queue_info.get('leaguePoints', 0)
                            )
                            # Actualizar el peak Elo si es necesario
                            actualizar_peak_elo_si_es_mayor(puuid, valor_clasificacion_actual)

            # Esperar 60 segundos antes de la próxima iteración del bucle principal
            print("[_worker_monitor_jugadores] Ciclo de monitoreo completado. Esperando 60 segundos.")
            time.sleep(60)

        except Exception as e:
            print(f"[_worker_monitor_jugadores] Error crítico en el bucle principal de monitoreo: {e}")
            time.sleep(30) # Espera antes de reintentar para no sobrecargar en caso de error repetido

# Iniciar el hilo de monitoreo en el arranque de la aplicación
# Este es el hilo principal de lógica de negocio que se ejecuta en segundo plano
monitoreo_thread = threading.Thread(target=_worker_monitor_jugadores, daemon=True)
monitoreo_thread.start()

@app.route('/')
def index():
    print("[index] Petición a la ruta raíz ('/').")
    if not RIOT_API_KEY:
        return "Error: RIOT_API_KEY no está configurada.", 500
    
    # Intenta usar la caché si es reciente
    with cache_lock:
        if (time.time() - cache["timestamp"]) < CACHE_TIMEOUT:
            print("[index] Usando datos de la caché.")
            jugadores_con_stats = cache["datos_jugadores"]
            lp_change_24h_dict = cache.get("lp_change_24h_dict", {})
            jugadores_en_partida = cache.get("jugadores_en_partida", {})
            return render_template(
                'index.html',
                jugadores=jugadores_con_stats,
                timestamp_lectura=cache["timestamp"],
                lp_change_24h_dict=lp_change_24h_dict,
                jugadores_en_partida=jugadores_en_partida
            )

    print("[index] Cache expirada o vacía. Buscando datos nuevos.")
    cuentas = leer_cuentas("https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt")
    if not cuentas:
        print("[index] No se pudieron leer las cuentas.")
        return "Error: No se pudieron leer las cuentas de jugadores.", 500

    exito_peak, peak_elo = leer_peak_elo()
    if not exito_peak:
        peak_elo = {}
        
    puuids = leer_puuids()
    if not puuids:
        print("[index] No se pudieron leer los PUUIDs.")
        return "Error: No se pudieron leer los PUUIDs.", 500

    jugadores_con_stats = []
    lp_change_24h_dict = {}
    jugadores_en_partida = {}
    
    # Usar ThreadPoolExecutor para obtener los datos de los jugadores en paralelo
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                _get_player_data, riot_id, jugador, puuids, peak_elo
            ): (riot_id, jugador) for riot_id, jugador in cuentas
        }
        for future in futures:
            try:
                result = future.result()
                if result:
                    jugadores_con_stats.append(result)
                    
                    # Recuperar lp_change_24h para la caché
                    puuid = result['puuid']
                    lp_change_24h_dict[puuid] = {}
                    lp_change_24h_dict[puuid]["RANKED_SOLO_5x5"] = result.get("solo_lp_change_24h", 0)
                    lp_change_24h_dict[puuid]["RANKED_FLEX_SR"] = result.get("flex_lp_change_24h", 0)

            except Exception as e:
                print(f"[index] Error procesando futuro para un jugador: {e}")

    # Ordenar los jugadores por Elo más alto
    jugadores_con_stats.sort(key=lambda x: x.get('valor_clasificacion_solo', 0), reverse=True)

    # Actualizar la caché antes de devolver la respuesta
    with cache_lock:
        cache["datos_jugadores"] = jugadores_con_stats
        cache["timestamp"] = time.time()
        cache["lp_change_24h_dict"] = lp_change_24h_dict
        # También actualizar el estado de los jugadores en partida en la caché
        with player_in_game_lp_lock:
            jugadores_en_partida = {key[0]: player['game_id'] for key, player in player_in_game_lp.items()}
            cache["jugadores_en_partida"] = jugadores_en_partida

    print(f"[index] Datos de {len(jugadores_con_stats)} jugadores procesados. Rendereando plantilla.")
    return render_template(
        'index.html',
        jugadores=jugadores_con_stats,
        timestamp_lectura=cache["timestamp"],
        lp_change_24h_dict=lp_change_24h_dict,
        jugadores_en_partida=jugadores_en_partida
    )

def _get_player_data(riot_id, jugador, puuids, peak_elo):
    """Función auxiliar para obtener los datos de un jugador, para ser usada en el ThreadPoolExecutor."""
    player_data = puuids.get(riot_id)
    if not player_data:
        print(f"[_get_player_data] No se encontró PUUID para {riot_id}. Saltando.")
        return None

    puuid = player_data['puuid']

    id_invocador_data = obtener_id_invocador(RIOT_API_KEY, puuid)
    if not id_invocador_data:
        return None

    elo_data = obtener_elo(RIOT_API_KEY, puuid)
    
    tier_solo = "UNRANKED"
    rank_solo = "IV"
    lp_solo = 0
    wins_solo = 0
    losses_solo = 0
    played_solo = 0
    wr_solo = 0
    valor_clasificacion_solo = 0

    tier_flex = "UNRANKED"
    rank_flex = "IV"
    lp_flex = 0
    wins_flex = 0
    losses_flex = 0
    played_flex = 0
    wr_flex = 0
    valor_clasificacion_flex = 0

    if elo_data:
        for entry in elo_data:
            if entry.get('queueType') == 'RANKED_SOLO_5x5':
                tier_solo = entry.get('tier', 'UNRANKED')
                rank_solo = entry.get('rank', 'IV')
                lp_solo = entry.get('leaguePoints', 0)
                wins_solo = entry.get('wins', 0)
                losses_solo = entry.get('losses', 0)
                played_solo = wins_solo + losses_solo
                if played_solo > 0:
                    wr_solo = round((wins_solo / played_solo) * 100)
                valor_clasificacion_solo = calcular_valor_clasificacion(tier_solo, rank_solo, lp_solo)
            elif entry.get('queueType') == 'RANKED_FLEX_SR':
                tier_flex = entry.get('tier', 'UNRANKED')
                rank_flex = entry.get('rank', 'IV')
                lp_flex = entry.get('leaguePoints', 0)
                wins_flex = entry.get('wins', 0)
                losses_flex = entry.get('losses', 0)
                played_flex = wins_flex + losses_flex
                if played_flex > 0:
                    wr_flex = round((wins_flex / played_flex) * 100)
                valor_clasificacion_flex = calcular_valor_clasificacion(tier_flex, rank_flex, lp_flex)

    # Cargar el historial de partidas
    match_history = leer_historial_jugador_github(puuid)
    solo_lp_change_24h = _calculate_lp_change_for_player(puuid, "RANKED_SOLO_5x5", match_history.values())
    flex_lp_change_24h = _calculate_lp_change_for_player(puuid, "RANKED_FLEX_SR", match_history.values())
    
    # Calcular elo actual para el peak
    valor_actual_para_peak = max(valor_clasificacion_solo, valor_clasificacion_flex)
    
    # Actualizar el peak elo si es mayor
    # Esto ya se hace en el hilo de monitoreo, pero lo mantenemos aquí por si acaso
    # se accede a la página antes de que el hilo haya corrido.
    # Esta llamada puede ser eliminada para optimizar, confiando 100% en el worker.
    # actualizar_peak_elo_si_es_mayor(puuid, valor_actual_para_peak)

    peak_elo_valor = peak_elo.get(puuid, 0)
    
    # Obtener las últimas 5 partidas
    ultimas_5_partidas = sorted(
        [p for p in match_history.values() if p['queue_id'] in [420, 440]],
        key=lambda x: x['game_end_timestamp'],
        reverse=True
    )[:5]
    
    # Contar wins y losses en las últimas 5 partidas de soloQ
    solo_q_games = [p for p in ultimas_5_partidas if p.get('queue_id') == 420]
    last_5_solo_q_wins = sum(1 for p in solo_q_games if p.get('win'))
    last_5_solo_q_losses = sum(1 for p in solo_q_games if not p.get('win'))
    
    # Encontrar la racha de victorias/derrotas de soloQ
    streak_count = 0
    current_streak_type = None
    for game in solo_q_games:
        is_win = game.get('win')
        if current_streak_type is None:
            current_streak_type = "wins" if is_win else "losses"
            streak_count = 1
        elif (is_win and current_streak_type == "wins") or (not is_win and current_streak_type == "losses"):
            streak_count += 1
        else:
            break # La racha se rompió, no seguir contando
    
    if streak_count > 0:
        if current_streak_type == "wins":
            solo_q_streak = f"{streak_count}W"
        else:
            solo_q_streak = f"{streak_count}L"
    else:
        solo_q_streak = "N/A"

    return {
        "riot_id": riot_id,
        "jugador": jugador,
        "puuid": puuid,
        "summoner_name": id_invocador_data.get('name', 'N/A'),
        "summoner_level": id_invocador_data.get('summonerLevel', 'N/A'),
        "perfil_icono": id_invocador_data.get('profileIconId', 'N/A'),

        "tier_solo": tier_solo,
        "rank_solo": rank_solo,
        "lp_solo": lp_solo,
        "wins_solo": wins_solo,
        "losses_solo": losses_solo,
        "played_solo": played_solo,
        "wr_solo": wr_solo,
        "valor_clasificacion_solo": valor_clasificacion_solo,
        "solo_lp_change_24h": solo_lp_change_24h,
        "solo_q_streak": solo_q_streak,
        "last_5_solo_q_wins": last_5_solo_q_wins,
        "last_5_solo_q_losses": last_5_solo_q_losses,

        "tier_flex": tier_flex,
        "rank_flex": rank_flex,
        "lp_flex": lp_flex,
        "wins_flex": wins_flex,
        "losses_flex": losses_flex,
        "played_flex": played_flex,
        "wr_flex": wr_flex,
        "valor_clasificacion_flex": valor_clasificacion_flex,
        "flex_lp_change_24h": flex_lp_change_24h,
        
        "peak_elo": peak_elo_valor,

        "ultimas_partidas": ultimas_5_partidas
    }

@app.route('/partida/<match_id>/<puuid>')
def ver_partida(match_id, puuid):
    print(f"[ver_partida] Petición para ver partida: {match_id} del jugador {puuid}.")
    if not RIOT_API_KEY:
        return "Error: RIOT_API_KEY no está configurada.", 500

    match_history = leer_historial_jugador_github(puuid)
    match_data = match_history.get(match_id)
    
    if not match_data:
        return "Partida no encontrada.", 404

    # Obtener el icono de los hechizos y las runas
    runa_principal_icon = ALL_RUNES.get(match_data.get('perk_main_id'))
    runa_secundaria_icon = ALL_RUNES.get(match_data.get('perk_sub_id'))
    
    return render_template(
        'partida.html',
        partida=match_data,
        ddragon_version=DDRAGON_VERSION,
        runa_principal_icon=runa_principal_icon,
        runa_secundaria_icon=runa_secundaria_icon
    )

@app.route('/lp_history/<puuid>')
def lp_history(puuid):
    print(f"[lp_history] Petición para ver historial de LP de {puuid}.")
    if not RIOT_API_KEY:
        return "Error: RIOT_API_KEY no está configurada.", 500

    historial_solo_duo = obtener_historial_lp(puuid, "RANKED_SOLO_5x5")
    historial_flex = obtener_historial_lp(puuid, "RANKED_FLEX_SR")

    return render_template(
        'lp_history.html',
        puuid=puuid,
        historial_solo_duo=historial_solo_duo,
        historial_flex=historial_flex
    )
    
@app.route('/partida_activa/<puuid>')
def partida_activa(puuid):
    print(f"[partida_activa] Petición para ver partida activa de {puuid}.")
    if not RIOT_API_KEY:
        return "Error: RIOT_API_KEY no está configurada.", 500

    game_data = esta_en_partida(RIOT_API_KEY, puuid)

    if not game_data:
        return "El jugador no está en una partida activa.", 404

    participants_data = []
    
    # Mapear los nombres de los equipos
    team_names = {100: "Equipo Azul", 200: "Equipo Rojo"}

    for participant in game_data.get('participants', []):
        champion_name = obtener_nombre_campeon(participant.get('championId'))
        spell1_icon_id = ALL_SUMMONER_SPELLS.get(participant.get('summonerSpell1Id'))
        spell2_icon_id = ALL_SUMMONER_SPELLS.get(participant.get('summonerSpell2Id'))

        perks = participant.get('perks', {})
        perk_main_id = None
        perk_sub_id = None

        if 'styles' in perks and len(perks['styles']) > 0:
            if len(perks['styles'][0]['selections']) > 0:
                perk_main_id = perks['styles'][0]['selections'][0]['perk']
            if len(perks['styles']) > 1:
                perk_sub_id = perks['styles'][1]['style']

        runa_principal_icon = ALL_RUNES.get(perk_main_id)
        runa_secundaria_icon = ALL_RUNES.get(perk_sub_id)

        participants_data.append({
            'summonerName': participant.get('summonerName'),
            'championName': champion_name,
            'teamId': participant.get('teamId'),
            'spell1_icon_id': spell1_icon_id,
            'spell2_icon_id': spell2_icon_id,
            'runa_principal_icon': runa_principal_icon,
            'runa_secundaria_icon': runa_secundaria_icon,
        })

    blue_team = [p for p in participants_data if p['teamId'] == 100]
    red_team = [p for p in participants_data if p['teamId'] == 200]

    return render_template(
        'active_game.html',
        game_data=game_data,
        blue_team=blue_team,
        red_team=red_team,
        team_names=team_names,
        ddragon_version=DDRAGON_VERSION
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)