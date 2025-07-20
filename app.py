from flask import Flask, render_template, redirect, url_for, request, jsonify
import requests
import os
import time
import threading
import json
import base64
from datetime import datetime
from collections import Counter
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

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
        1090: "Arena", # Ocasional, por ejemplo para eventos
        1100: "Arena", # Ocasional, por ejemplo para eventos
        1300: "Nexus Blitz",
        1400: "Ultimate Spellbook",
        1700: "Arena", # Nuevo ID de Arena
        1900: "URF (ARAM)",
        2000: "Tutorial",
        2010: "Tutorial",
        2020: "Tutorial",
        # Añadir más IDs de cola según sea necesario
    }
    return queue_names.get(int(queue_id), "Desconocido")

@app.template_filter('format_timestamp')
def format_timestamp_filter(timestamp):
    # The Riot timestamp is in milliseconds, Python datetime expects seconds
    # Divide by 1000 to convert milliseconds to seconds
    return datetime.fromtimestamp(timestamp / 1000).strftime("%d/%m/%Y %H:%M")

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")
    exit(1)

# URLs base de la API de Riot (MOVIDAS AL PRINCIPIO)
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"


# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": [],
    "timestamp": 0
}
CACHE_TIMEOUT = 130  # 2 minutos para estar seguros
cache_lock = threading.Lock()

# --- CONFIGURACIÓN DE SPLITS ---
# Define aquí los splits de la temporada 2025.
# Las fechas de los splits 2 y 3 son estimadas y deberán actualizarse
# cuando Riot Games las anuncie oficialmente.
SPLITS = {
    "s15_split1": {
        "name": "Temporada 2025 - Split 1",
        "start_date": datetime(2025, 1, 9), # Fecha oficial
    },
    "s15_split2": {
        "name": "Temporada 2025 - Split 2",
        "start_date": datetime(2025, 5, 15), # Fecha estimada
    },
    "s15_split3": {
        "name": "Temporada 2025 - Split 3",
        "start_date": datetime(2025, 9, 10), # Fecha estimada
    }
}

# Cambia esta variable para seleccionar el split activo.
ACTIVE_SPLIT_KEY = "s15_split1"
# ------------------------------------------------

# El timestamp de inicio se calcula automáticamente a partir del split activo
SEASON_START_TIMESTAMP = int(SPLITS[ACTIVE_SPLIT_KEY]["start_date"].timestamp())

API_SESSION = requests.Session() # Usar una sesión para reutilizar conexiones

def make_api_request(url, retries=3, backoff_factor=0.5):
    """
    Realiza una petición a la API de Riot con reintentos y backoff exponencial.
    Utiliza una sesión de requests para mejorar el rendimiento.
    """
    for i in range(retries):
        try:
            response = API_SESSION.get(url, timeout=10)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                print(f"Rate limit excedido. Esperando {retry_after} segundos...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"Error en la petición a {url}: {e}. Intento {i + 1}/{retries}")
            if i < retries - 1:
                time.sleep(backoff_factor * (2 ** i))
    return None

DDRAGON_VERSION = "14.9.1"  # Versión de respaldo por si falla la API

def actualizar_version_ddragon():
    """Obtiene la última versión de Data Dragon y la guarda en una variable global."""
    global DDRAGON_VERSION
    try:
        url = f"{BASE_URL_DDRAGON}/api/versions.json" # Usar BASE_URL_DDRAGON
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            DDRAGON_VERSION = response.json()[0]
            print(f"Versión de Data Dragon establecida a: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener la versión de Data Dragon: {e}. Usando versión de respaldo: {DDRAGON_VERSION}")

# Se llama una vez al inicio
actualizar_version_ddragon()

# Cache para campeones, runas y hechizos de invocador
ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    """Carga los datos de los campeones desde Data Dragon."""
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    response = make_api_request(url_campeones)
    if response:
        # DDragon champion data has "key" as numerical ID (string) and "id" as champion name (string)
        # We want to map numerical ID (from Riot API match data) to champion name (for DDragon image URL)
        return {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
    return {}

def obtener_todas_las_runas():
    """Carga los datos de las runas desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
    data = make_api_request(url)
    runes = {}
    if data:
        for tree in data.json():
            # Store the icon path for the main rune style (e.g., Precision, Domination)
            runes[tree['id']] = tree['icon']
            for slot in tree['slots']:
                for perk in slot['runes']:
                    # Store the icon path for individual perks (runes)
                    runes[perk['id']] = perk['icon']
    return runes

def obtener_todos_los_hechizos():
    """Carga los datos de los hechizos de invocador desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
    data = make_api_request(url)
    spells = {}
    if data and 'data' in data.json():
        for k, v in data.json()['data'].items():
            # Riot API match data provides summoner spell IDs as integers (e.g., 4 for Flash).
            # DDragon uses a string ID (e.g., "SummonerFlash") for the image file.
            # v['key'] is the numerical ID as a string (e.g., "4")
            # v['id'] is the DDragon image name (e.g., "SummonerFlash")
            spells[int(v['key'])] = v['id'] # Map numerical ID (int) to DDragon image ID (str)
    return spells

def actualizar_ddragon_data():
    """Actualiza todos los datos de DDragon (campeones, runas, hechizos) en las variables globales."""
    global ALL_CHAMPIONS, ALL_RUNES, ALL_SUMMONER_SPELLS
    ALL_CHAMPIONS = obtener_todos_los_campeones()
    ALL_RUNES = obtener_todas_las_runas()
    ALL_SUMMONER_SPELLS = obtener_todos_los_hechizos()
    print("DDragon champion, rune, and summoner spell data updated.")

# Cargar los datos de DDragon al inicio
actualizar_ddragon_data()


def obtener_nombre_campeon(champion_id):
    """Obtiene el nombre de un campeón dado su ID."""
    # Usamos ALL_CHAMPIONS que ya está cargado con el mapeo correcto
    return ALL_CHAMPIONS.get(champion_id, "Desconocido")

def obtener_puuid(api_key, riot_id, region):
    """Obtiene el PUUID de un jugador dado su Riot ID y región."""
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el PUUID para {riot_id} después de varios intentos.")
        return None

def obtener_id_invocador(api_key, puuid):
    """Obtiene el ID de invocador de un jugador dado su PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el ID de invocador para {puuid}.")
        return None

def obtener_elo(api_key, puuid):
    """Obtiene la información de Elo de un jugador dado su PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el Elo para {puuid}.")
        return None

def esta_en_partida(api_key, puuid):
    """Comprueba si un jugador está en una partida activa. Realiza un único intento."""
    try:
        url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"

        response = API_SESSION.get(url, timeout=5)  # Direct request, no retries

        if response.status_code == 200:  # Player is in game
            game_data = response.json()
            # Check participants for the target puuid and return champion ID
            for participant in game_data.get("participants", []):
                if participant["puuid"] == puuid:
                    return participant.get("championId")
            # This should ideally not happen if the API returns consistent data, but handle it
            print(f"Warning: Player {puuid} is in game but not found in participants list.")
            return None  
        elif response.status_code == 404:  # Player not in game (expected response)
            return None
        else:  # Unexpected error
            response.raise_for_status()  # Raises an exception for bad responses (4xx or 5xx)
    except requests.exceptions.RequestException as e:  # Handle potential request errors
        print(f"Error checking if player {puuid} is in game: {e}")  # Log the error
        return None  # Assume player is not in game in case of errors

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida,
    además del nivel, hechizos, runas y AHORA MUCHAS MÁS ESTADÍSTICAS DETALLADAS.
    """
    match_id, puuid, api_key = args
    url_match = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={api_key}"
    response_match = make_api_request(url_match)
    if not response_match:
        return None
    try:
        match_data = response_match.json()
        info = match_data.get('info', {})
        participants = info.get('participants', [])

        # La API de Riot marca las partidas 'remake' con el flag 'gameEndedInEarlySurrender'.
        # Si este flag es 'true' para cualquier participante, la partida es un remake.
        if any(p.get('gameEndedInEarlySurrender', False) for p in participants):
            print(f"Partida {match_id} marcada como remake.")
            return None

        # Se suman 2 horas (7,200,000 milisegundos) para ajustar la zona horaria.
        game_end_timestamp = info.get('gameEndTimestamp', 0) + 7200000
        game_duration = info.get('gameDuration', 0) # Duración de la partida en segundos

        for p in participants:
            if p.get('puuid') == puuid:
                # Extraer IDs de ítems, reemplazando None con 0
                items = [p.get(f'item{i}', 0) for i in range(0, 7)]

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

                # Extracción de las nuevas estadísticas
                return {
                    "match_id": match_id,
                    "champion_name": obtener_nombre_campeon(p.get('championId')),
                    "win": p.get('win', False),
                    "kills": p.get('kills', 0),
                    "deaths": p.get('deaths', 0),
                    "assists": p.get('assists', 0),
                    "items": items,
                    "game_end_timestamp": game_end_timestamp,
                    "queue_id": info.get('queueId'),
                    "champion_level": p.get('champLevel'),
                    "summoner_spell_1_id": ALL_SUMMONER_SPELLS.get(spell1_id),
                    "summoner_spell_2_id": ALL_SUMMONER_SPELLS.get(spell2_id),
                    "perk_main_id": ALL_RUNES.get(perk_main_id),
                    "perk_sub_id": ALL_RUNES.get(perk_sub_id),

                    # --- NUEVAS ESTADÍSTICAS AÑADIDAS ---
                    "total_minions_killed": p.get('totalMinionsKilled', 0),
                    "neutral_minions_killed": p.get('neutralMinionsKilled', 0),
                    "gold_earned": p.get('goldEarned', 0),
                    "gold_spent": p.get('goldSpent', 0),
                    "game_duration": game_duration, # En segundos

                    "total_damage_dealt": p.get('totalDamageDealt', 0),
                    "total_damage_dealt_to_champions": p.get('totalDamageDealtToChampions', 0),
                    "physical_damage_dealt_to_champions": p.get('physicalDamageDealtToChampions', 0),
                    "magic_damage_dealt_to_champions": p.get('magicDamageDealtToChampions', 0),
                    "true_damage_dealt_to_champions": p.get('trueDamageDealtToChampions', 0),
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
                    "penta_kills": p.get('pentaKills', 0),
                    "quadra_kills": p.get('quadra_kills', 0),
                    "triple_kills": p.get('triple_kills', 0),
                    "double_kills": p.get('double_kills', 0)
                }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error procesando los detalles de la partida {match_id}: {e}")
    return None

def leer_cuentas(url):
    """Lee las cuentas de jugadores desde un archivo de texto alojado en GitHub."""
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
            return cuentas
        else:
            print(f"Error al leer el archivo: {response.status_code}")
            return []
    except Exception as e:
        print(f"Error al leer las cuentas: {e}")
        return []

def calcular_valor_clasificacion(tier, rank, league_points):
    """
    Calcula un valor numérico para la clasificación de un jugador,
    permitiendo ordenar y comparar Elo de forma más sencilla.
    """
    tier_upper = tier.upper()
    
    # Para Master, Grandmaster y Challenger, el cálculo es más simple.
    # La base es 2800 (el valor después de Diamond I 100 LP) y se suman los LPs.
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

    # El valor de la división es un extra sobre el valor base del tier (IV=0, III=100, II=200, I=300)
    rankOrden = {"I": 3, "II": 2, "III": 1, "IV": 0}

    valor_base_tier = tierOrden.get(tier_upper, 0) * 400
    valor_division = rankOrden.get(rank, 0) * 100

    return valor_base_tier + valor_division + league_points

def leer_peak_elo():
    """Lee los datos de peak Elo desde un archivo JSON en GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/refs/heads/main/peak_elo.json"
    try:
        resp = requests.get(url)
        resp.raise_for_status()  # Lanza una excepción para códigos de error HTTP (4xx o 5xx)
        return True, resp.json()
    except Exception as e:
        print(f"Error leyendo peak elo: {e}")
    return False, {}

def leer_puuids():
    """Lee el archivo de PUUIDs desde GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/puuids.json"
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print("El archivo puuids.json no existe, se creará uno nuevo.")
            return {}
    except Exception as e:
        print(f"Error leyendo puuids.json: {e}")
    return {}

def guardar_puuids_en_github(puuid_dict):
    """Guarda o actualiza el archivo puuids.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/puuids.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado para guardar PUUIDs.")
        return

    headers = {"Authorization": f"token {token}"}
    
    # Intentar obtener el SHA del archivo si existe
    sha = None
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"No se pudo obtener el SHA de puuids.json: {e}")

    contenido_json = json.dumps(puuid_dict, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": "Actualizar PUUIDs", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    response = requests.put(url, headers=headers, json=data)
    if response.status_code in (200, 201):
        print("Archivo puuids.json actualizado correctamente en GitHub.")
    else:
        print(f"Error al actualizar puuids.json: {response.status_code} - {response.text}")

def guardar_peak_elo_en_github(peak_elo_dict):
    """Guarda o actualiza el archivo peak_elo.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado")
        return

    # Obtener el contenido actual del archivo para el SHA
    sha = None
    try:
        response = requests.get(url, headers={"Authorization": f"token {token}"})
        if response.status_code == 200:
            contenido_actual = response.json()
            sha = contenido_actual['sha']
        else:
            print(f"Error al obtener el archivo peak_elo.json para SHA: {response.status_code}")
    except Exception as e:
        print(f"Error al obtener el SHA de peak_elo.json: {e}")
        return

    # Codificar el contenido en base64 como requiere la API de GitHub
    try:
        contenido_json = json.dumps(peak_elo_dict, ensure_ascii=False, indent=2)
        contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')

        response = requests.put(
            url,
            headers={"Authorization": f"token {token}"},
            json={
                "message": "Actualizar peak elo",
                "content": contenido_b64,
                "sha": sha,
                "branch": "main"
            }
        )
        if response.status_code in (200, 201):
            print("Archivo peak_elo.json actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar peak_elo.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error al actualizar el archivo peak_elo.json: {e}")

def leer_historial_jugador_github(puuid):
    """Lee el historial de partidas de un jugador desde GitHub."""
    url = f"https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/match_history/{puuid}.json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print(f"No se encontró historial para {puuid}. Se creará uno nuevo.")
            return {}
    except Exception as e:
        print(f"Error leyendo el historial para {puuid}: {e}")
    return {}

def guardar_historial_jugador_github(puuid, historial_data):
    """Guarda o actualiza el historial de partidas de un jugador en GitHub."""
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/match_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"Token de GitHub no encontrado para guardar historial de {puuid}.")
        return

    headers = {"Authorization": f"token {token}"}
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"No se pudo obtener el SHA del historial de {puuid}: {e}")

    contenido_json = json.dumps(historial_data, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    data = {"message": f"Actualizar historial de partidas para {puuid}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code in (200, 201):
            print(f"Historial de {puuid}.json actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar historial de {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error en la petición PUT a GitHub para el historial de {puuid}: {e}")

def procesar_jugador(args_tuple):
    """
    Procesa los datos de un solo jugador.
    Implementa una lógica de actualización inteligente para reducir llamadas a la API.
    Solo actualiza el Elo si el jugador está o ha estado en partida recientemente.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list, check_in_game_this_update = args_tuple
    riot_id, jugador_nombre = cuenta

    if not puuid:
        print(f"ADVERTENCIA: Omitiendo procesamiento para {riot_id} porque no se pudo obtener su PUUID. Revisa que el Riot ID sea correcto en cuentas.txt.")
        return []

    # 1. Sondeo ligero: usar la clave secundaria para esta llamada frecuente.
    champion_id = esta_en_partida(api_key_spectator, puuid)
    is_currently_in_game = champion_id is not None

    # 2. Decisión inteligente: ¿necesitamos una actualización completa?
    # Comprobamos si el jugador estaba en partida en el ciclo anterior.
    was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
    
    # La actualización completa solo se hace si es un jugador nuevo, si está en partida ahora,
    # o si acaba de terminar una partida (estaba en partida antes pero ya no).
    needs_full_update = not old_data_list or is_currently_in_game or was_in_game_before

    if not needs_full_update:
        # Jugador inactivo, reutilizamos los datos antiguos y solo actualizamos su estado.
        print(f"Jugador {riot_id} inactivo. Omitiendo actualización de Elo.")
        for data in old_data_list:
            data['en_partida'] = False
        return old_data_list

    print(f"Actualizando datos completos para {riot_id} (estado: {'en partida' if is_currently_in_game else 'recién terminada'}).")
    # Usar la clave principal para obtener los datos de Elo, que es una operación menos frecuente pero más crítica.
    elo_info = obtener_elo(api_key_main, puuid)
    if not elo_info: # Si falla la obtención de Elo, devolvemos los datos antiguos si existen
        return old_data_list if old_data_list else []

    riot_id_modified = riot_id.replace("#", "-")
    url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
    url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"
    
    datos_jugador_list = []
    for entry in elo_info:
        nombre_campeon = obtener_nombre_campeon(champion_id) if champion_id else "Desconocido"
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
                entry.get('leaguePoints', 0)
            ),
            "nombre_campeon": nombre_campeon,
            "champion_id": champion_id if champion_id else "Desconocido"
        }
        datos_jugador_list.append(datos_jugador)
    return datos_jugador_list

def actualizar_cache():
    """
    Esta función realiza el trabajo pesado: obtiene todos los datos de la API
    y actualiza la caché global. Está diseñada para ser ejecutada en segundo plano.
    """
    print("Iniciando actualización de la caché...")
    api_key_main = os.environ.get('RIOT_API_KEY')
    # Usar la clave secundaria para el espectador, con fallback a la principal si no existe.
    api_key_spectator = os.environ.get('RIOT_API_KEY_2', api_key_main)
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
    
    if not api_key_main:
        print("ERROR CRÍTICO: La variable de entorno RIOT_API_KEY no está configurada. La aplicación no puede funcionar.")
        return
    
    with cache_lock:
        old_cache_data = cache.get('datos_jugadores', [])
    
    # Crear un mapa para acceso rápido a los datos antiguos por PUUID
    old_data_map_by_puuid = {}
    for d in old_cache_data:
        puuid = d.get('puuid')
        if puuid:
            if puuid not in old_data_map_by_puuid:
                old_data_map_by_puuid[puuid] = []
            old_data_map_by_puuid[puuid].append(d)

    cuentas = leer_cuentas(url_cuentas)

    # --- NUEVO: Controlar la frecuencia de 'esta_en_partida' ---
    with cache_lock:
        cache['update_count'] = cache.get('update_count', 0) + 1
    check_in_game_this_update = cache['update_count'] % 2 == 1

    puuid_dict = leer_puuids()
    puuids_actualizados = False

    # Paso 1: Asegurarse de que todos los jugadores tienen un PUUID en el diccionario
    for riot_id, _ in cuentas:
        if riot_id not in puuid_dict:
            print(f"No se encontró PUUID para {riot_id}. Obteniéndolo de la API...")
            game_name, tag_line = riot_id.split('#')[0], riot_id.split('#')[1]
            puuid_info = obtener_puuid(api_key_main, game_name, tag_line)
            if puuid_info and 'puuid' in puuid_info:
                puuid_dict[riot_id] = puuid_info['puuid']
                puuids_actualizados = True

    if puuids_actualizados:
        guardar_puuids_en_github(puuid_dict)

    # Paso 2: Procesar todos los jugadores en paralelo, pasando sus datos antiguos
    todos_los_datos = []
    tareas = []
    for cuenta in cuentas:
        riot_id = cuenta[0]
        puuid = puuid_dict.get(riot_id)
        old_data_for_player = old_data_map_by_puuid.get(puuid)
        tareas.append((cuenta, puuid, api_key_main, api_key_spectator, 
                      old_data_for_player, check_in_game_this_update)) # Pass the value here

    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    # Paso 3: Calcular y añadir estadísticas del top 3 campeones más jugados desde el historial
    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    for jugador in todos_los_datos:
        """Calcula las estadísticas del top 3 campeones más jugados para un jugador."""
        puuid = jugador.get('puuid')
        queue_type = jugador.get('queue_type')
        queue_id = queue_map.get(queue_type)

        jugador['top_champion_stats'] = [] # Ahora será una lista para los top 3

        if not puuid or not queue_id:
            continue
        
        historial = leer_historial_jugador_github(puuid)
        partidas_jugador = [
            p for p in historial.get('matches', []) 
            if p.get('queue_id') == queue_id and
               # Filtramos para que solo cuenten las partidas del split activo
               p.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP
        ]

        if not partidas_jugador:
            continue

        # Contar campeones para encontrar el top 3
        contador_campeones = Counter(p['champion_name'] for p in partidas_jugador)
        if not contador_campeones:
            continue
        
        top_3_campeones = contador_campeones.most_common(3)

        for campeon_nombre, _ in top_3_campeones:
            # Calcular stats para cada campeón en el top 3
            partidas_del_campeon = [p for p in partidas_jugador if p['champion_name'] == campeon_nombre]
            
            total_partidas = len(partidas_del_campeon)
            wins = sum(1 for p in partidas_del_campeon if p.get('win'))
            win_rate = (wins / total_partidas * 100) if total_partidas > 0 else 0

            total_kills = sum(p.get('kills', 0) for p in partidas_del_campeon)
            total_deaths = sum(p.get('deaths', 0) for p in partidas_del_campeon)
            total_assists = sum(p.get('assists', 0) for p in partidas_del_campeon)
            
            # Evitar división por cero para el KDA
            kda = (total_kills + total_assists) / total_deaths if total_deaths > 0 else float(total_kills + total_assists)

            jugador['top_champion_stats'].append({
                "champion_name": campeon_nombre,
                "win_rate": win_rate,
                "games_played": total_partidas,
                "kda": kda
            })

    with cache_lock:
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()
    print("Actualización de la caché completada.")

def obtener_datos_jugadores():
    """Obtiene los datos cacheados de los jugadores."""
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    """Genera una clave para el peak ELO usando el nombre del jugador y su Riot ID."""
    return f"{jugador['queue_type']}|{jugador['jugador']}|{jugador['game_name']}"

@app.route('/')
def index():
    """Renderiza la página principal con la lista de jugadores."""

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
            jugador["peak_elo"] = peak

        if actualizado:
            guardar_peak_elo_en_github(peak_elo_dict)
    else:
        print("ADVERTENCIA: No se pudo leer el archivo peak_elo.json. Se omitirá la actualización de picos.")
        for jugador in datos_jugadores:
            jugador["peak_elo"] = jugador["valor_clasificacion"] # Como fallback, mostramos el valor actual

    # Obtenemos el nombre del split activo para mostrarlo en la web
    split_activo_nombre = SPLITS[ACTIVE_SPLIT_KEY]['name']
    ultima_actualizacion = (datetime.fromtimestamp(timestamp) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    
    
    return render_template('index.html', datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION, 
                           split_activo_nombre=split_activo_nombre)

@app.route('/jugador/<path:game_name>') # Use <path:game_name> to handle '/' in Riot IDs
def perfil_jugador(game_name):
    """Muestra una página de perfil para un jugador específico."""
    todos_los_datos, _ = obtener_datos_jugadores()
    
    # Filter data for the specific player
    datos_del_jugador = [j for j in todos_los_datos if j.get('game_name') == game_name] # Usar .get para evitar KeyError si 'game_name' falta
    
    if not datos_del_jugador:
        return render_template('404.html'), 404
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid') # Get the PUUID for fetching match history

    # Fetch match history
    historial_partidas_completo = {}
    if puuid:
        historial_partidas_completo = leer_historial_jugador_github(puuid)

    # Preparar el objeto 'perfil' para la plantilla
    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'), # Usar .get con valor por defecto
        'game_name': game_name,
        'tagline': primer_perfil.get('tagline', 'N/A'),
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', 'https://ddragon.leagueoflegends.com/cdn/img/profileicon/29.png'), # Icono por defecto
        'banner_champion_splash': primer_perfil.get('banner_champion_splash', 'https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/img/default_banner.jpg'), # Banner por defecto

        # Añadir las estadísticas de clasificatoria directamente al perfil si existen
        'ranked_solo_tier': None, 'ranked_solo_rank': None, 'ranked_solo_lp': 0, 'ranked_solo_wins': 0, 'ranked_solo_losses': 0,
        'ranked_flex_tier': None, 'ranked_flex_rank': None, 'ranked_flex_lp': 0, 'ranked_flex_wins': 0, 'ranked_flex_losses': 0,
    }

    # Asignar datos de clasificatoria
    for item in datos_del_jugador:
        if item.get('queue_type') == 'RANKED_SOLO_5x5':
            perfil['ranked_solo_tier'] = item.get('tier')
            perfil['ranked_solo_rank'] = item.get('rank')
            perfil['ranked_solo_lp'] = item.get('league_points')
            perfil['ranked_solo_wins'] = item.get('wins')
            perfil['ranked_solo_losses'] = item.get('losses')
        elif item.get('queue_type') == 'RANKED_FLEX_SR':
            perfil['ranked_flex_tier'] = item.get('tier')
            perfil['ranked_flex_rank'] = item.get('rank')
            perfil['ranked_flex_lp'] = item.get('league_points')
            perfil['ranked_flex_wins'] = item.get('wins')
            perfil['ranked_flex_losses'] = item.get('losses')

    raw_matches = historial_partidas_completo.get('matches', [])
    
    processed_matches = []
    for match in raw_matches:
        # --- BLOQUE DE CÓDIGO CRÍTICO PARA EL SANEAMIENTO DE 'items' ---
        raw_items = match.get('items', []) 

        # Aseguramos que 'items' sea siempre una lista
        if not isinstance(raw_items, list):
            if callable(raw_items): # Si es una función o método (como dict.items)
                raw_items = []
            elif isinstance(raw_items, dict): # Si es un diccionario
                raw_items = list(raw_items.values())
            else: # Cualquier otro tipo inesperado
                raw_items = []

        # Convertimos cada elemento a int, default a 0 si no es convertible, y aseguramos 7 elementos
        processed_items = []
        for item_id in raw_items:
            try:
                processed_items.append(int(item_id))
            except (ValueError, TypeError):
                processed_items.append(0) # Si no es convertible, usa 0

        # Aseguramos que siempre haya 7 elementos, rellenando con 0 si es necesario
        while len(processed_items) < 7:
            processed_items.append(0)
        processed_items = processed_items[:7] # Aseguramos que no haya más de 7 (aunque es poco probable)
        # --- FIN DEL BLOQUE CRÍTICO ---

        processed_match = {
            'game_end_timestamp': match.get('game_end_timestamp', 0),
            'game_duration': match.get('game_duration', 0),
            'win': match.get('win', False),
            'queue_id': match.get('queue_id', 0),
            'champion_name': match.get('champion_name', 'Unknown'),
            'kills': match.get('kills', 0),
            'deaths': match.get('deaths', 0),
            'assists': match.get('assists', 0),
            'champion_level': match.get('champion_level', 0),
            'total_minions_killed': match.get('total_minions_killed', 0),
            'gold_earned': match.get('gold_earned', 0),
            'total_damage_dealt_to_champions': match.get('total_damage_dealt_to_champions', 0),
            'damage_dealt_to_objectives': match.get('damage_dealt_to_objectives', 0),
            'vision_score': match.get('vision_score', 0),
            'wards_placed': match.get('wards_placed', 0),
            'wards_killed': match.get('wards_killed', 0),
            'total_heal': match.get('total_heal', 0),
            'time_ccing_others': match.get('time_ccing_others', 0),
            'turret_kills': match.get('turret_kills', 0),
            'penta_kills': match.get('penta_kills', 0),
            'quadra_kills': match.get('quadra_kills', 0),
            'triple_kills': match.get('triple_kills', 0),
            'double_kills': match.get('double_kills', 0),
            'items': processed_items, # Usa la lista de ítems procesada
            'summoner_spell_1_id': match.get('summoner_spell_1_id', 'SummonerFlash'), # Asegúrate que estos son nombres de archivos de DDragon
            'summoner_spell_2_id': match.get('summoner_spell_2_id', 'SummonerHeal'), # Asegúrate que estos son nombres de archivos de DDragon
            'perk_main_id': match.get('perk_main_id', 'perk/8000/Conqueror/Conqueror.png'), # Ruta de imagen por defecto
            'perk_sub_id': match.get('perk_sub_id', 8100), # ID numérico por defecto (ej. Dominación)
        }
        processed_matches.append(processed_match)

    perfil['historial_partidas'] = processed_matches
    
    # Sort the full history by timestamp (most recent first) for consistent display
    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    return render_template('jugador.html', 
                           perfil=perfil, 
                           now=datetime.now(), 
                           ddragon_version=DDRAGON_VERSION,
                           datetime=datetime) # ¡Esta es la línea clave para Jinja2!


def actualizar_historial_partidas_en_segundo_plano():
    """
    Función que se ejecuta en un hilo separado para actualizar el historial de partidas
    de todos los jugadores de forma periódica.
    """
    print("Iniciando hilo de actualización de historial de partidas.")
    api_key = os.environ.get('RIOT_API_KEY')
    if not api_key:
        print("ERROR: RIOT_API_KEY no configurada. No se puede actualizar el historial de partidas.")
        return

    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"

    while True:
        try:
            # Asegurarse de que los datos de DDragon estén cargados
            # Esto es crucial antes de intentar mapear IDs a nombres/rutas de imagen
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                actualizar_ddragon_data()

            cuentas = leer_cuentas(url_cuentas)
            puuid_dict = leer_puuids()

            for riot_id, jugador_nombre in cuentas:
                puuid = puuid_dict.get(riot_id)
                if not puuid:
                    print(f"Saltando actualización de historial para {riot_id}: PUUID no encontrado.")
                    continue

                historial_existente = leer_historial_jugador_github(puuid)
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}
                # Mantenemos un conjunto separado de IDs de remakes para evitar re-consultarlos.
                remakes_guardados = set(historial_existente.get('remakes', []))
                
                
                # 2. Obtener TODOS los IDs de partidas de la temporada (SoloQ y Flex)
                all_match_ids_season = []
                for queue_id in queue_map.values():
                    start_index = 0
                    while True:
                        url_matches = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime={SEASON_START_TIMESTAMP}&queue={queue_id}&start={start_index}&count=100&api_key={api_key}"
                        response_matches = make_api_request(url_matches)
                        if not response_matches: break
                        match_ids_page = response_matches.json()
                        if not match_ids_page: break
                        all_match_ids_season.extend(match_ids_page)
                        if len(match_ids_page) < 100: break
                        start_index += 100
                
                # 3. Filtrar para obtener solo los IDs de partidas nuevas y no remakes
                nuevos_match_ids = [
                    mid for mid in all_match_ids_season 
                    if mid not in ids_partidas_guardadas and mid not in remakes_guardados
                ]

                if not nuevos_match_ids: # No hay nuevas partidas
                    print(f"No hay partidas nuevas para {riot_id}. Omitiendo.")
                    continue

                print(f"Se encontraron {len(nuevos_match_ids)} partidas nuevas para {riot_id}. Procesando...")

                # 4. Procesar solo las partidas nuevas en paralelo
                tareas = [(match_id, puuid, api_key) for match_id in nuevos_match_ids]
                with ThreadPoolExecutor(max_workers=10) as executor:
                    nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))

                # 5. Añadir las nuevas partidas al historial y guardar
                # Separar las partidas válidas de los remakes basándonos en el valor de retorno de obtener_info_partida.
                nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]  # No es remake
                nuevos_remakes = [
                    match_id for i, match_id in enumerate(nuevos_match_ids) # Enumerate para obtener índice
                    if nuevas_partidas_info[i] is None # None indica que fue marcado como remake
                ]

                
                if nuevas_partidas_validas:
                    historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                    # Opcional: ordenar por fecha
                    historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'], reverse=True)

                if nuevos_remakes:
                    # Añadir los IDs de los nuevos remakes al conjunto existente
                    remakes_guardados.update(nuevos_remakes)
                    # Actualizar la lista de remakes en el historial
                    historial_existente['remakes'] = list(remakes_guardados)
                
                if nuevas_partidas_validas or nuevos_remakes:
                    guardar_historial_jugador_github(puuid, historial_existente) # Guardar todo el historial, incluso si solo hay remakes
                    print(f"Historial de {riot_id} actualizado con {len(nuevas_partidas_validas)} partidas nuevas y {len(nuevos_remakes)} remakes.")

            print("Ciclo de actualización de historial completado. Próxima revisión en 5 minutos.")
            time.sleep(600) # Esperar 5 minutos para el siguiente ciclo

        except Exception as e:
            print(f"Error en el hilo de actualización de estadísticas: {e}. Reintentando en 5 minutos.")
            time.sleep(600)

def keep_alive():
    """Envía una solicitud periódica a la propia aplicación para mantenerla activa en servicios como Render."""
    while True:
        try:
            requests.get('https://soloq-cerditos-34kd.onrender.com/')
            print("Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
    """Actualiza la caché de datos de los jugadores de forma periódica."""
    while True:
        actualizar_cache()
        time.sleep(CACHE_TIMEOUT)

if __name__ == "__main__":
    # Hilo para mantener la app activa en Render
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()

    # Hilo para actualizar la caché en segundo plano
    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()

    # Hilo para la actualización del historial de partidas
    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)