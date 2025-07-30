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
    """
    Convierte un valor de clasificación numérico de nuevo a un formato legible
    como 'TIER RANK (LP LPs)'.
    """
    if valor is None:
        print(f"[format_peak_elo_filter] Valor de entrada es None. Retornando 'N/A'.")
        return "N/A"
    
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

        game_end_timestamp = info.get('gameEndTimestamp', 0) + 7200000
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
        resp = requests.get(url)
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
        resp = requests.get(url)
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
        response = requests.get(url, headers=headers)
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
        response = requests.put(url, headers=headers, json=data)
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
        response = requests.get(url, headers=headers)
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
            }
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
        resp = requests.get(url, timeout=10)
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
        print(f"Token de GitHub no encontrado para guardar historial de {puuid}. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_historial_jugador_github] SHA del historial de {puuid} obtenido: {sha}")
    except Exception as e:
        print(f"[guardar_historial_jugador_github] No se pudo obtener el SHA del historial de {puuid}: {e}")

    contenido_json = json.dumps(historial_data, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    data = {"message": f"Actualizar historial de partidas para {puuid}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code in (200, 201):
            print(f"[guardar_historial_jugador_github] Historial de {puuid}.json actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar historial de {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_historial_jugador_github] Error en la petición PUT a GitHub para el historial de {puuid}: {e}")

def _calculate_lp_change_for_player(puuid, queue_type_api_name, all_matches_for_player):
    """
    Calcula el cambio total de LP para un jugador en una cola específica en las últimas 24 horas.
    """
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
        # Ensure match is for the correct queue and within the last 24 hours
        if match_timestamp_utc >= one_day_ago_timestamp_ms and match.get('queue_id') == target_queue_id:
            if match.get('lp_change_this_game') is not None:
                lp_change_24h += match['lp_change_this_game']
    print(f"[_calculate_lp_change_for_player] Cambio de LP en 24h para {puuid} en {queue_type_api_name}: {lp_change_24h} LP.")
    return lp_change_24h


def procesar_jugador(args_tuple):
    """
    Procesa los datos de un solo jugador.
    Implementa una lógica de actualización inteligente para reducir llamadas a la API.
    Solo actualiza el Elo si el jugador está o ha estado en partida recientemente.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list, check_in_game_this_update = args_tuple
    riot_id, jugador_nombre = cuenta
    print(f"[procesar_jugador] Procesando jugador: {riot_id}")

    if not puuid:
        print(f"[procesar_jugador] ADVERTENCIA: Omitiendo procesamiento para {riot_id} porque no se pudo obtener su PUUID.")
        return []

    # Obtener la información de Elo actual del jugador (used for general display and 'needs_full_update' logic)
    elo_info = obtener_elo(api_key_main, puuid)
    if not elo_info:
        print(f"[procesar_jugador] No se pudo obtener el Elo para {riot_id}. No se puede rastrear LP ni actualizar datos.")
        return old_data_list if old_data_list else []

    # 1. Sondeo ligero: usar la clave secundaria para esta llamada frecuente.
    game_data = esta_en_partida(api_key_spectator, puuid)
    is_currently_in_game = game_data is not None

    # --- LP Tracking Logic ---
    with player_in_game_lp_lock:
        if is_currently_in_game:
            # El jugador está en partida. Almacenar su LP actual si no está ya almacenado.
            active_game_queue_id = game_data.get('gameQueueConfigId')
            
            queue_type_api_name = None
            if active_game_queue_id == 420:
                queue_type_api_name = "RANKED_SOLO_5x5"
            elif active_game_queue_id == 440:
                queue_type_api_name = "RANKED_FLEX_SR"
            
            if queue_type_api_name:
                elo_entry_for_active_queue = next((entry for entry in elo_info if entry.get('queueType') == queue_type_api_name), None)
                if elo_entry_for_active_queue:
                    pre_game_valor = calcular_valor_clasificacion(
                        elo_entry_for_active_queue.get('tier', 'Sin rango'),
                        elo_entry_for_active_queue.get('rank', ''),
                        elo_entry_for_active_queue.get('leaguePoints', 0)
                    )
                    lp_tracking_key = (puuid, queue_type_api_name)

                    if lp_tracking_key not in player_in_game_lp:
                        player_in_game_lp[lp_tracking_key] = {
                            'pre_game_valor_clasificacion': pre_game_valor,
                            'game_start_timestamp': time.time(),
                            'riot_id': riot_id,
                            'queue_type': queue_type_api_name
                        }
                        print(f"[{riot_id}] [LP Tracker] Jugador entró en partida de {get_queue_type_filter(active_game_queue_id)}. Valor pre-partida almacenado: {pre_game_valor}")
                else:
                    print(f"[{riot_id}] [LP Tracker] Jugador en partida de {get_queue_type_filter(active_game_queue_id)} pero no se encontró información de Elo para esa cola.")
            else:
                print(f"[{riot_id}] [LP Tracker] Jugador en partida de cola no clasificatoria ({get_queue_type_filter(active_game_queue_id)}). No se rastrea LP.")

        # If the player is NOT in game, check if they were being tracked
        # and move them to pending_lp_updates.
        keys_to_remove_from_in_game = []
        for lp_tracking_key, pre_game_data in player_in_game_lp.items():
            tracked_puuid, tracked_queue_type = lp_tracking_key
            if tracked_puuid == puuid and not is_currently_in_game: # This player was tracked, and now is not in game
                print(f"[{riot_id}] [LP Tracker] Jugador {riot_id} (cola {tracked_queue_type}) terminó una partida. Moviendo a actualizaciones pendientes.")
                with pending_lp_updates_lock:
                    pending_lp_updates[lp_tracking_key] = {
                        'pre_game_valor_clasificacion': pre_game_data['pre_game_valor_clasificacion'],
                        'detection_timestamp': time.time(), # Timestamp when we detected they finished the game
                        'riot_id': riot_id,
                        'queue_type': tracked_queue_type
                    }
                keys_to_remove_from_in_game.append(lp_tracking_key)
        
        for key in keys_to_remove_from_in_game:
            del player_in_game_lp[key]

    # --- End LP Tracking Logic ---

    # 2. Decisión inteligente: ¿necesitamos una actualización completa?
    was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
    
    # The full update is only done if it's a new player, if they are in game now,
    # or if they just finished a game (was in game before but not anymore).
    needs_full_update = not old_data_list or is_currently_in_game or was_in_game_before

    if not needs_full_update:
        print(f"[procesar_jugador] Jugador {riot_id} inactivo. Omitiendo actualización de Elo.")
        for data in old_data_list:
            data['en_partida'] = False
        return old_data_list

    print(f"[procesar_jugador] Actualizando datos completos para {riot_id} (estado: {'en partida' if is_currently_in_game else 'recién terminada'}).")
    
    riot_id_modified = riot_id.replace("#", "-")
    url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
    url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"
    
    datos_jugador_list = []
    current_champion_id = None
    if is_currently_in_game and game_data:
        for participant in game_data.get("participants", []):
            if participant["puuid"] == puuid:
                current_champion_id = participant.get("championId")
                break

    for entry in elo_info:
        nombre_campeon = obtener_nombre_campeon(current_champion_id) if current_champion_id else "Desconocido"
        datos_jugador = {
            "game_name": riot_id,
            "queue_type": entry.get('queueType', 'Desconocido'),
            "tier": entry.get('tier', 'Sin rango'),
            "rank": entry.get('rank', ''),
            "league_points": entry.get('leaguePoints', 0),
            "wins": entry.get('wins', 0),
            "losses": entry.get('losses', 0),
            "jugador": jugador_nombre,
            "url_perfil": url_perfil,
            "puuid": puuid, # Se añade para usarlo como clave en cachés
            "url_ingame": url_ingame,
            "en_partida": is_currently_in_game,
            "valor_clasificacion": calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('league_points', 0)
            ),
            "nombre_campeon": nombre_campeon,
            "champion_id": current_champion_id if current_champion_id else "Desconocido"
        }
        datos_jugador_list.append(datos_jugador)
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
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
    
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

    cuentas = leer_cuentas(url_cuentas)

    with cache_lock:
        cache['update_count'] = cache.get('update_count', 0) + 1
    check_in_game_this_update = cache['update_count'] % 2 == 1
    print(f"[actualizar_cache] Check de partida activa en este ciclo: {check_in_game_this_update}")

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
        
        historial = leer_historial_jugador_github(puuid)
        all_matches_for_player = historial.get('matches', [])

        jugador['lp_change_24h'] = _calculate_lp_change_for_player(
            puuid, queue_type, all_matches_for_player
        )

        partidas_jugador = [
            p for p in all_matches_for_player
            if p.get('queue_id') == queue_id and
               p.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP
        ]

        if not partidas_jugador:
            continue

        contador_campeones = Counter(p['champion_name'] for p in partidas_jugador)
        if not contador_campeones:
            continue
        
        top_3_campeones = contador_campeones.most_common(3)

        for campeon_nombre, _ in top_3_campeones:
            partidas_del_campeon = [p for p in partidas_jugador if p['champion_name'] == campeon_nombre]
            
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
    print("[actualizar_cache] Actualización de la caché principal completada.")

def obtener_datos_jugadores():
    """Obtiene los datos cacheados de los jugadores."""
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    """Genera una clave para el peak ELO usando el nombre del jugador y su Riot ID."""
    return f"{jugador['queue_type']}|{jugador['jugador']}|{jugador['game_name']}"

def calcular_rachas(partidas):
    """
    Calcula las rachas de victorias y derrotas más largas de una lista de partidas.
    Las partidas deben estar ordenadas por fecha, de más reciente a más antigua.
    """
    print(f"[calcular_rachas] Calculando rachas para {len(partidas)} partidas.")
    if not partidas:
        return {'max_win_streak': 0, 'max_loss_streak': 0}

    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    for partida in reversed(partidas):
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
            
    print(f"[calcular_rachas] Rachas calculadas: Max V: {max_win_streak}, Max D: {max_loss_streak}.")
    return {'max_win_streak': max_win_streak, 'max_loss_streak': max_loss_streak}

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
    ultima_actualizacion = (datetime.fromtimestamp(timestamp) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    
    print("[index] Renderizando index.html.")
    return render_template('index.html', datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION, 
                           split_activo_nombre=split_activo_nombre)

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
    
    template_name = 'jugador_2.html' if is_mobile else 'jugador.html'
    
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

    historial_partidas_completo = {}
    if puuid:
        historial_partidas_completo = leer_historial_jugador_github(puuid)
        for match in historial_partidas_completo.get('matches', []):
            if 'lp_change_this_game' not in match:
                match['lp_change_this_game'] = None
                print(f"[_get_player_profile_data] Inicializando 'lp_change_this_game' a None para la partida {match.get('match_id')} del jugador {puuid}.")

    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'),
        'game_name': game_name,
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', ''),
        'historial_partidas': historial_partidas_completo.get('matches', [])
    }
    
    lp_change_soloq_24h = _calculate_lp_change_for_player(
        puuid, "RANKED_SOLO_5x5", perfil['historial_partidas']
    )
    lp_change_flexq_24h = _calculate_lp_change_for_player(
        puuid, "RANKED_FLEX_SR", perfil['historial_partidas']
    )

    for item in datos_del_jugador:
        if item.get('queue_type') == 'RANKED_SOLO_5x5':
            perfil['soloq'] = item
            perfil['soloq']['lp_change_24h'] = lp_change_soloq_24h
        elif item.get('queue_type') == 'RANKED_FLEX_SR':
            perfil['flexq'] = item
            perfil['flexq']['lp_change_24h'] = lp_change_flexq_24h

    historial_total = perfil.get('historial_partidas', [])
    
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

    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    print(f"[_get_player_profile_data] Perfil de {game_name} preparado.")
    return perfil


@app.route('/jugador_original/<path:game_name>')
def perfil_jugador_original(game_name):
    """
    Muestra una página de perfil para un jugador específico.
    Esta es la versión CORREGIDA Y MEJORADA de tu función original.
    """
    print(f"[perfil_jugador_original] Petición recibida para el perfil original de jugador: {game_name}")
    todos_los_datos, _ = obtener_datos_jugadores()
    
    datos_del_jugador = [j for j in todos_los_datos if j.get('game_name') == game_name]
    
    if not datos_del_jugador:
        print(f"[perfil_jugador_original] Perfil de jugador original {game_name} no encontrado. Retornando 404.")
        return render_template('404.html'), 404
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid')

    historial_partidas_completo = {}
    if puuid:
        historial_partidas_completo = leer_historial_jugador_github(puuid)
        for match in historial_partidas_completo.get('matches', []):
            if 'lp_change_this_game' not in match:
                match['lp_change_this_game'] = None
                print(f"[perfil_jugador_original] Inicializando 'lp_change_this_game' a None para la partida {match.get('match_id')} del jugador {puuid}.")


    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'),
        'game_name': game_name,
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', ''),
        'historial_partidas': historial_partidas_completo.get('matches', [])
    }
    
    for item in datos_del_jugador:
        if item.get('queue_type') == 'RANKED_SOLO_5x5':
            perfil['soloq'] = item
        elif item.get('queue_type') == 'RANKED_FLEX_SR':
            perfil['flexq'] = item

    historial_total = perfil.get('historial_partidas', [])
    
    if 'soloq' in perfil:
        partidas_soloq = [p for p in historial_total if p.get('queue_id') == 420]
        rachas_soloq = calcular_rachas(partidas_soloq)
        perfil['soloq'].update(rachas_soloq)
        print(f"[perfil_jugador_original] Rachas SoloQ calculadas para {game_name}.")

    if 'flexq' in perfil:
        partidas_flexq = [p for p in historial_total if p.get('queue_id') == 440]
        rachas_flexq = calcular_rachas(partidas_flexq)
        perfil['flexq'].update(rachas_flexq)
        print(f"[perfil_jugador_original] Rachas FlexQ calculadas para {game_name}.")

    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    print(f"[perfil_jugador_original] Perfil original de {game_name} preparado.")
    return render_template('jugador.html',
                           perfil=perfil,
                           ddragon_version=DDRAGON_VERSION,
                           datetime=datetime,
                           now=datetime.now())

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
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"

    while True:
        try:
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                print("[actualizar_historial_partidas_en_segundo_plano] Datos de DDragon no cargados, intentando actualizar.")
                actualizar_ddragon_data()

            cuentas = leer_cuentas(url_cuentas)
            puuid_dict = leer_puuids()

            for riot_id, jugador_nombre in cuentas:
                puuid = puuid_dict.get(riot_id)
                if not puuid:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Saltando actualización de historial para {riot_id}: PUUID no encontrado.")
                    continue

                print(f"[actualizar_historial_partidas_en_segundo_plano] Procesando historial para {riot_id} (PUUID: {puuid}).")
                historial_existente = leer_historial_jugador_github(puuid)
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}
                remakes_guardados = set(historial_existente.get('remakes', []))
                
                all_match_ids_season = []
                for queue_id in queue_map.values():
                    start_index = 0
                    while True:
                        url_matches = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime={SEASON_START_TIMESTAMP}&queue={queue_id}&start={start_index}&count=100&api_key={api_key}"
                        response_matches = make_api_request(url_matches)
                        if not response_matches: 
                            print(f"[actualizar_historial_partidas_en_segundo_plano] No más partidas o error para cola {queue_id} y PUUID {puuid}.")
                            break
                        match_ids_page = response_matches.json()
                        if not match_ids_page: 
                            print(f"[actualizar_historial_partidas_en_segundo_plano] No se encontraron más IDs de partida para cola {queue_id} y PUUID {puuid}.")
                            break
                        all_match_ids_season.extend(match_ids_page)
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Obtenidos {len(match_ids_page)} IDs de partida para {riot_id} (cola {queue_id}).")
                        if len(match_ids_page) < 100: break
                        start_index += 100
                
                nuevos_match_ids = [
                    mid for mid in all_match_ids_season 
                    if mid not in ids_partidas_guardadas and mid not in remakes_guardados
                ]

                if not nuevos_match_ids:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] No hay partidas nuevas para {riot_id}. Omitiendo procesamiento de partidas.")
                    # Still need to process pending LP updates even if no new matches
                    pass
                else:
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Se encontraron {len(nuevos_match_ids)} partidas nuevas para {riot_id}. Procesando...")

                    tareas = [(match_id, puuid, api_key) for match_id in nuevos_match_ids]
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))

                    nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]
                    nuevos_remakes = [
                        match_id for i, match_id in enumerate(nuevos_match_ids)
                        if nuevas_partidas_info[i] is None
                    ]
                    print(f"[actualizar_historial_partidas_en_segundo_plano] {len(nuevas_partidas_validas)} partidas válidas y {len(nuevos_remakes)} remakes procesados para {riot_id}.")

                    if nuevas_partidas_validas:
                        historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Añadidas {len(nuevas_partidas_validas)} partidas válidas al historial de {riot_id}.")

                # --- LÓGICA DE ASOCIACIÓN DE LP MEJORADA ---
                with pending_lp_updates_lock:
                    keys_to_clear_from_pending = []
                    # Iterate over a copy to allow modification of the original dictionary
                    for lp_update_key, lp_update_data in list(pending_lp_updates.items()):
                        update_puuid, update_queue_type = lp_update_key

                        if update_puuid != puuid: # Only process updates for the current player being handled in this loop iteration
                            continue

                        print(f"[{lp_update_data['riot_id']}] [LP Associator] Intentando asociar LP pendiente para cola {update_queue_type}.")
                        
                        # Fetch the LATEST Elo info for this specific player to get the post-game LP
                        latest_elo_info = obtener_elo(api_key, update_puuid)
                        if not latest_elo_info:
                            print(f"[{lp_update_data['riot_id']}] [LP Associator] No se pudo obtener el Elo más reciente para {update_puuid}. Saltando asociación de LP.")
                            continue

                        post_game_elo_entry = next((entry for entry in latest_elo_info if entry.get('queueType') == update_queue_type), None)
                        if not post_game_elo_entry:
                            print(f"[{lp_update_data['riot_id']}] [LP Associator] No se encontró entrada de Elo para la cola {update_queue_type} en el Elo más reciente. Saltando asociación de LP.")
                            continue

                        pre_game_valor = lp_update_data['pre_game_valor_clasificacion']
                        post_game_valor = calcular_valor_clasificacion(
                            post_game_elo_entry.get('tier', 'Sin rango'),
                            post_game_elo_entry.get('rank', ''),
                            post_game_elo_entry.get('leaguePoints', 0)
                        )
                        lp_change = post_game_valor - pre_game_valor
                        print(f"[{lp_update_data['riot_id']}] [LP Associator] LP calculado: {pre_game_valor} -> {post_game_valor} ({lp_change:+d} LP).")

                        potential_match = None
                        smallest_time_diff = float('inf')
                        detection_ts_sec = lp_update_data['detection_timestamp']

                        # Find the most recent match for this player and queue that doesn't have LP yet
                        # and ended just before the LP change was detected.
                        for match in historial_existente.get('matches', []):
                            is_candidate = (
                                match.get('puuid') == update_puuid and
                                (match.get('queue_id') == 420 and update_queue_type == "RANKED_SOLO_5x5" or
                                 match.get('queue_id') == 440 and update_queue_type == "RANKED_FLEX_SR") and
                                match.get('lp_change_this_game') is None # Ensure it hasn't been assigned yet
                            )

                            if is_candidate:
                                # Calculate time difference in seconds
                                # The match timestamp is already adjusted by +2h, so no need to adjust again here.
                                match_end_ts_sec = match.get('game_end_timestamp', 0) / 1000 
                                time_diff = detection_ts_sec - match_end_ts_sec

                                # Look for matches that ended within a reasonable window (e.g., 5 minutes) BEFORE the LP detection
                                if 0 < time_diff < 300 and time_diff < smallest_time_diff: # 300 seconds = 5 minutes
                                    smallest_time_diff = time_diff
                                    potential_match = match

                        if potential_match:
                            potential_match['lp_change_this_game'] = lp_change
                            print(f"[{lp_update_data['riot_id']}] [LP Associator] Cambio de LP {lp_change} asociado a la partida {potential_match['match_id']}.")
                            keys_to_clear_from_pending.append(lp_update_key)
                        else:
                            print(f"[{lp_update_data['riot_id']}] [LP Associator] No se encontró una partida adecuada para asociar el cambio de LP {lp_change} (cola: {update_queue_type}).")

                    for key in keys_to_clear_from_pending:
                        if key in pending_lp_updates:
                            del pending_lp_updates[key]
                            print(f"[{puuid}] [LP Associator] Actualización de LP pendiente eliminada para {key}.")
                
                # Ensure all matches have the 'lp_change_this_game' field before saving
                for match in historial_existente.get('matches', []):
                    if 'lp_change_this_game' not in match:
                        match['lp_change_this_game'] = None
                        print(f"[actualizar_historial_partidas_en_segundo_plano] Inicializando 'lp_change_this_game' a None para la partida {match.get('match_id')} antes de guardar.")

                if nuevas_partidas_validas or nuevos_remakes or keys_to_clear_from_pending:
                    historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'], reverse=True)
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Historial de {riot_id} ordenado.")

                if nuevos_remakes:
                    remakes_guardados.update(nuevos_remakes)
                    historial_existente['remakes'] = list(remakes_guardados)
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Añadidos {len(nuevos_remakes)} remakes al historial de {riot_id}.")
                
                if nuevas_partidas_validas or nuevos_remakes or keys_to_clear_from_pending:
                    guardar_historial_jugador_github(puuid, historial_existente)
                    print(f"[actualizar_historial_partidas_en_segundo_plano] Historial de {riot_id} guardado en GitHub.")
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
            requests.get('https://soloq-cerditos-34kd.onrender.com/')
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

    port = int(os.environ.get("PORT", 5000))
    print(f"[main] Aplicación Flask ejecutándose en http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)