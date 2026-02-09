import requests
import os
import time
import threading
import json
from datetime import datetime, timedelta, timezone
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import queue

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")

# URLs base de la API de Riot
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

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
            request_id, url, headers, timeout, is_spectator_api = API_REQUEST_QUEUE.get(timeout=1)
            
            riot_api_limiter.consume_token()

            print(f"[_api_rate_limiter_worker] Procesando petición {request_id} a: {url}")
            response = None
            for i in range(3): # Reintentos para la petición HTTP real
                try:
                    response = session.get(url, headers=headers, timeout=timeout)
                    
                    if is_spectator_api and response.status_code == 404:
                        print(f"[_api_rate_limiter_worker] Petición {request_id} a la API de espectador devolvió 404. No se reintentará.")
                        break

                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        print(f"[_api_rate_limiter_worker] Rate limit excedido. Esperando {retry_after} segundos... (Intento {i + 1}/3)")
                        time.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    print(f"[_api_rate_limiter_worker] Petición {request_id} exitosa. Status: {response.status_code}")
                    break
                except requests.exceptions.RequestException as e:
                    print(f"[_api_rate_limiter_worker] Error en petición {request_id} a {url}: {e}. Intento {i + 1}/3")
                    if i < 2:
                        time.sleep(0.5 * (2 ** i))
            
            with REQUEST_ID_COUNTER_LOCK:
                API_RESPONSE_DATA[request_id] = response
                if request_id in API_RESPONSE_EVENTS:
                    API_RESPONSE_EVENTS[request_id].set()
                else:
                    print(f"[_api_rate_limiter_worker] Advertencia: Evento para request_id {request_id} no encontrado.")

        except queue.Empty:
            pass
        except Exception as e:
            print(f"[_api_rate_limiter_worker] Error inesperado en el worker del control de tasa: {e}")
            time.sleep(1)

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
    API_REQUEST_QUEUE.put((request_id, url, headers, 10, is_spectator_api))

    print(f"[make_api_request] Petición {request_id} encolada para {url}. Esperando respuesta...")
    
    if not API_RESPONSE_EVENTS[request_id].wait(timeout=120):
        print(f"[make_api_request] Timeout esperando respuesta para la petición {request_id} a {url}.")
        with REQUEST_ID_COUNTER_LOCK:
            if request_id in API_RESPONSE_EVENTS:
                del API_RESPONSE_EVENTS[request_id]
            if request_id in API_RESPONSE_DATA:
                del API_RESPONSE_DATA[request_id]
        return None

    with REQUEST_ID_COUNTER_LOCK:
        response = API_RESPONSE_DATA.get(request_id)
        if request_id in API_RESPONSE_EVENTS:
            del API_RESPONSE_EVENTS[request_id]
        if request_id in API_RESPONSE_DATA:
            del API_RESPONSE_DATA[request_id]
    
    return response

# Import config.settings to update the version there too
import config.settings as settings

DDRAGON_VERSION = settings.DDRAGON_VERSION

def get_ddragon_version():
    """
    Retorna la versión actual de Data Dragon.
    Esta función siempre retorna el valor actualizado de settings.DDRAGON_VERSION,
    a diferencia de importar DDRAGON_VERSION directamente que puede quedar desactualizado.
    """
    return settings.DDRAGON_VERSION

def actualizar_version_ddragon():
    global DDRAGON_VERSION
    print("[actualizar_version_ddragon] Intentando obtener la última versión de Data Dragon.")
    try:
        url = f"{BASE_URL_DDRAGON}/api/versions.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            new_version = response.json()[0]
            DDRAGON_VERSION = new_version
            # Also update the version in config.settings so blueprints see the new version
            settings.DDRAGON_VERSION = new_version
            print(f"[actualizar_version_ddragon] Versión de Data Dragon establecida a: {DDRAGON_VERSION}")
        else:
            print(f"[actualizar_version_ddragon] Error al obtener la versión de Data Dragon. Status: {response.status_code}. Usando versión: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"[actualizar_version_ddragon] Error al obtener la versión de Data Dragon: {e}. Usando versión: {DDRAGON_VERSION}")



ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    print("[obtener_todos_los_campeones] Obteniendo datos de campeones de Data Dragon.")
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    response = requests.get(url_campeones, timeout=10)
    if response and response.status_code == 200:
        return {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
    print("[obtener_todos_los_campeones] No se pudieron obtener los datos de campeones.")
    return {}

def obtener_todas_las_runas():
    """Carga los datos de las runas desde Data Dragon."""
    print("[obtener_todas_las_runas] Obteniendo datos de runas de Data Dragon.")
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
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
        response = make_api_request(url, is_spectator_api=True)

        if response and response.status_code == 200:
            game_data = response.json()
            for participant in game_data.get("participants", []):
                if participant["puuid"] == puuid:
                    print(f"[esta_en_partida] Jugador {puuid} está en partida activa.")
                    return game_data
            print(f"[esta_en_partida] Advertencia: Jugador {puuid} está en partida pero no se encontró en la lista de participantes.")
            return None
        elif response and response.status_code == 404:
            print(f"[esta_en_partida] Jugador {puuid} no está en partida activa (404 Not Found).")
            return None
        elif response is None:
            print(f"[esta_en_partida] make_api_request devolvió None para {puuid}. Posible timeout o error persistente.")
            return None
        else:
            print(f"[esta_en_partida] Error inesperado al verificar partida para {puuid}. Status: {response.status_code}")
            response.raise_for_status()
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
            "post_game_valor_clasificacion": None, # Initialize post-game ELO to None
            "pre_game_valor_clasificacion": None, # Initialize pre-game ELO to None

            # --- AÑADIMOS LA LISTA DE TODOS LOS PARTICIPANTES ---
            "all_participants": all_participants_details
        }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[obtener_info_partida] Error procesando los detalles de la partida {match_id}: {e}")
    return None


def start_rate_limiter():
    """
    Función de inicio para el servicio de rate limiting.
    Inicia el worker del rate limiter en un thread en segundo plano.
    """
    print("[riot_api] Iniciando servicio de rate limiting...")
    
    # Iniciar el worker en un thread daemon
    rate_limiter_thread = threading.Thread(target=_api_rate_limiter_worker, daemon=True)
    rate_limiter_thread.start()
    print("[riot_api] ✓ Rate limiter iniciado correctamente")
