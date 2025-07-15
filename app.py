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
    return queue_names.get(queue_id, "Tipo de Cola Desconocido")

@app.template_filter('format_timestamp')
def format_timestamp_filter(timestamp_ms):
    # Convierte el timestamp de milisegundos a segundos
    dt_object = datetime.fromtimestamp(timestamp_ms / 1000)
    # Formatea la fecha y hora a un formato legible
    return dt_object.strftime('%Y-%m-%d %H:%M')

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")
    exit(1)

# URLs base de la API de Riot
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

# Versión de DDragon (se actualizará al inicio)
DDRAGON_VERSION = "14.10.1" # Versión por defecto, se actualizará dinámicamente

# Cache para los datos de los jugadores y el historial de partidas
# Estructura: {puuid: {datos_invocador, datos_soloq, datos_flex, historial_partidas}}
CACHE_JUGADORES = {}
CACHE_TIMEOUT = 300  # 5 minutos en segundos

# Lista de jugadores a seguir
# Formato: {"nombre_interno": {"gameName": "NombreInvocador", "tagLine": "TAG"}}
JUGADORES_SEGUIDOS = {
    "Sepevalle": {"gameName": "Sepevalle", "tagLine": "EUW"},
    "Autopsy GRU": {"gameName": "Autopsy GRU", "tagLine": "7549"},
    "AuronPlay": {"gameName": "AuronPlay", "tagLine": "EUW"},
    "Rubius": {"gameName": "Rubius", "tagLine": "EUW"},
    "TheGrefg": {"gameName": "TheGrefg", "tagLine": "EUW"},
    "Ibai": {"gameName": "Ibai", "tagLine": "EUW"},
    "ElXokas": {"gameName": "ElXokas", "tagLine": "EUW"},
    "Reven": {"gameName": "Reven", "tagLine": "EUW"},
    "Werlyb": {"gameName": "Werlyb", "tagLine": "EUW"},
    "Caps": {"gameName": "Caps", "tagLine": "EUW"},
    "Rekkles": {"gameName": "Rekkles", "tagLine": "EUW"},
    "Perkz": {"gameName": "Perkz", "tagLine": "EUW"},
    "Jankos": {"gameName": "Jankos", "tagLine": "EUW"},
    "Humanoid": {"gameName": "Humanoid", "tagLine": "EUW"},
    "Elyoya": {"gameName": "Elyoya", "tagLine": "EUW"},
    "Razork": {"gameName": "Razork", "tagLine": "EUW"},
    "Carzzy": {"gameName": "Carzzy", "tagLine": "EUW"},
    "Hans Sama": {"gameName": "Hans Sama", "tagLine": "EUW"},
    "Mikyx": {"gameName": "Mikyx", "tagLine": "EUW"},
    "Hylissang": {"gameName": "Hylissang", "tagLine": "EUW"},
    "Larssen": {"gameName": "Larssen", "tagLine": "EUW"},
    "Comp": {"gameName": "Comp", "tagLine": "EUW"},
    "Trymbi": {"gameName": "Trymbi", "tagName": "EUW"},
    "Vetheo": {"gameName": "Vetheo", "tagName": "EUW"},
    "Inspired": {"gameName": "Inspired", "tagName": "EUW"},
    "Upset": {"gameName": "Upset", "tagName": "EUW"},
    "Vulcan": {"gameName": "Vulcan", "tagName": "EUW"},
    "Chovy": {"gameName": "Chovy", "tagName": "KR"},
    "Faker": {"gameName": "Faker", "tagName": "KR"},
    "Deft": {"gameName": "Deft", "tagName": "KR"},
    "Keria": {"gameName": "Keria", "tagName": "KR"},
    "ShowMaker": {"gameName": "ShowMaker", "tagName": "KR"},
    "Canyon": {"gameName": "Canyon", "tagName": "KR"},
    "Viper": {"gameName": "Viper", "tagName": "KR"},
    "Ruler": {"gameName": "Ruler", "tagName": "KR"},
    "Gen.G Chovy": {"gameName": "Gen.G Chovy", "tagName": "KR"},
    "T1 Faker": {"gameName": "T1 Faker", "tagName": "KR"},
    "DK ShowMaker": {"gameName": "DK ShowMaker", "tagName": "KR"},
    "JDG Knight": {"gameName": "JDG Knight", "tagName": "CN"},
    "BLG Elk": {"gameName": "BLG Elk", "tagName": "CN"},
    "RNG GALA": {"gameName": "RNG GALA", "tagName": "CN"},
    "EDG Flandre": {"gameName": "EDG Flandre", "tagName": "CN"},
    "TES JackeyLove": {"gameName": "TES JackeyLove", "tagName": "CN"},
    "LNG Tarzan": {"gameName": "LNG Tarzan", "tagName": "CN"},
    "G2 Caps": {"gameName": "G2 Caps", "tagName": "EUW"},
    "FNC Rekkles": {"gameName": "FNC Rekkles", "tagName": "EUW"},
    "MAD Elyoya": {"gameName": "MAD Elyoya", "tagName": "EUW"},
    "VIT Perkz": {"gameName": "VIT Perkz", "tagName": "EUW"},
    "KOI Larssen": {"gameName": "KOI Larssen", "tagName": "EUW"},
    "C9 Fudge": {"gameName": "C9 Fudge", "tagName": "NA1"},
    "TL CoreJJ": {"gameName": "TL CoreJJ", "tagName": "NA1"},
    "EG Jojopyun": {"gameName": "EG Jojopyun", "tagName": "NA1"},
    "100T Closer": {"gameName": "100T Closer", "tagName": "NA1"},
    "TSM Spica": {"gameName": "TSM Spica", "tagName": "NA1"},
    "FLY Prince": {"gameName": "FLY Prince", "tagName": "NA1"},
    "CLG Dhokla": {"gameName": "CLG Dhokla", "tagName": "NA1"},
    "DIG River": {"gameName": "DIG River", "tagName": "NA1"},
    "IMT Tactical": {"gameName": "IMT Tactical", "tagName": "NA1"},
    "GG Licorice": {"gameName": "GG Licorice", "tagName": "NA1"},
    "LOUD Robo": {"gameName": "LOUD Robo", "tagName": "BR1"},
    "PNG tinowns": {"gameName": "PNG tinowns", "tagName": "BR1"},
    "RED Jojo": {"gameName": "RED Jojo", "tagName": "BR1"},
    "FLA Brance": {"gameName": "FLA Brance", "tagName": "BR1"},
    "ITZ Envy": {"gameName": "ITZ Envy", "tagName": "BR1"},
    "R7 Ceo": {"gameName": "R7 Ceo", "tagName": "LA1"},
    "ISG Seiya": {"gameName": "ISG Seiya", "tagName": "LA1"},
    "EST Oddie": {"gameName": "EST Oddie", "tagName": "LA1"},
    "AKL Genthix": {"gameName": "AKL Genthix", "tagName": "LA1"},
    "INF Future": {"gameName": "INF Future", "tagName": "LA1"},
    "PSG Unified": {"gameName": "PSG Unified", "tagName": "TW2"},
    "CFO Shunn": {"gameName": "CFO Shunn", "tagName": "TW2"},
    "JT Lilv": {"gameName": "JT Lilv", "tagName": "TW2"},
    "BYG Liang": {"gameName": "BYG Liang", "tagName": "TW2"},
    "DC Yursan": {"gameName": "DC Yursan", "tagName": "TW2"},
    "DFM Evi": {"gameName": "DFM Evi", "tagName": "JP1"},
    "SHG Yutapon": {"gameName": "SHG Yutapon", "tagName": "JP1"},
    "V3 Paz": {"gameName": "V3 Paz", "tagName": "JP1"},
    "RJ Kazu": {"gameName": "RJ Kazu", "tagName": "JP1"},
    "AXZ Hachamecha": {"gameName": "AXZ Hachamecha", "tagName": "JP1"},
    "GAM Levi": {"gameName": "GAM Levi", "tagName": "VN2"},
    "SGB Taki": {"gameName": "SGB Taki", "tagName": "VN2"},
    "TS Nper": {"gameName": "TS Nper", "tagName": "VN2"},
    "CES Pyshiro": {"gameName": "CES Pyshiro", "tagName": "VN2"},
    "SE Zyro": {"gameName": "SE Zyro", "tagName": "VN2"},
    "CBLOL": {"gameName": "CBLOL", "tagName": "BR1"},
    "LLA": {"gameName": "LLA", "tagName": "LA1"},
    "PCS": {"gameName": "PCS", "tagTag": "TW2"},
    "LJL": {"gameName": "LJL", "tagTag": "JP1"},
    "VCS": {"gameName": "VCS", "tagTag": "VN2"},
    "LCS": {"gameName": "LCS", "tagTag": "NA1"},
    "LEC": {"gameName": "LEC", "tagTag": "EUW"},
    "LCK": {"gameName": "LCK", "tagTag": "KR"},
    "LPL": {"gameName": "LPL", "tagTag": "CN"},
}

# Diccionario para almacenar el historial de elo de los jugadores
# {puuid: {queue_type: {timestamp: valor_clasificacion}}}
HISTORIAL_ELO = {}

# Ruta del archivo JSON para guardar el historial de elo
ELO_HISTORY_FILE = "elo_history.json"
PEAK_ELO_FILE = "peak_elo.json"

# Cargar historial de elo al inicio
def cargar_historial_elo():
    global HISTORIAL_ELO
    if os.path.exists(ELO_HISTORY_FILE):
        with open(ELO_HISTORY_FILE, 'r') as f:
            HISTORIAL_ELO = json.load(f)
    else:
        HISTORIAL_ELO = {}

# Guardar historial de elo
def guardar_historial_elo():
    with open(ELO_HISTORY_FILE, 'w') as f:
        json.dump(HISTORIAL_ELO, f, indent=4)

# Cargar peak elo al inicio
def cargar_peak_elo():
    global CACHE_JUGADORES
    if os.path.exists(PEAK_ELO_FILE):
        with open(PEAK_ELO_FILE, 'r') as f:
            peak_data = json.load(f)
            for puuid, data in peak_data.items():
                if puuid in CACHE_JUGADORES:
                    if 'soloq' in data:
                        CACHE_JUGADORES[puuid]['soloq']['peak_elo'] = data['soloq'].get('peak_elo', 0)
                    if 'flex' in data:
                        CACHE_JUGADORES[puuid]['flex']['peak_elo'] = data['flex'].get('peak_elo', 0)
    else:
        # Inicializar peak_elo a 0 si el archivo no existe
        for puuid in CACHE_JUGADORES:
            if 'soloq' in CACHE_JUGADORES[puuid]:
                CACHE_JUGADORES[puuid]['soloq']['peak_elo'] = 0
            if 'flex' in CACHE_JUGADORES[puuid]:
                CACHE_JUGADORES[puuid]['flex']['peak_elo'] = 0

# Guardar peak elo
def guardar_peak_elo():
    peak_data = {}
    for puuid, data in CACHE_JUGADORES.items():
        player_peak_data = {}
        if 'soloq' in data and 'peak_elo' in data['soloq']:
            player_peak_data['soloq'] = {'peak_elo': data['soloq']['peak_elo']}
        if 'flex' in data and 'peak_elo' in data['flex']:
            player_peak_data['flex'] = {'peak_elo': data['flex']['peak_elo']}
        if player_peak_data:
            peak_data[puuid] = player_peak_data
    with open(PEAK_ELO_FILE, 'w') as f:
        json.dump(peak_data, f, indent=4)

# Función para obtener la última versión de DDragon
def obtener_ddragon_version():
    global DDRAGON_VERSION
    try:
        response = requests.get(f"{BASE_URL_DDRAGON}/api/versions.json")
        response.raise_for_status()
        versions = response.json()
        if versions:
            DDRAGON_VERSION = versions[0]
            print(f"DDragon version updated to: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching DDragon version: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decoding DDragon version JSON: {e}")

# Funciones de la API de Riot
def call_riot_api(url):
    headers = {"X-Riot-Token": RIOT_API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Lanza una excepción para códigos de estado de error (4xx o 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Riot API: {url} - {e}")
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 5))
            print(f"Rate limit exceeded. Retrying after {retry_after} seconds.")
            time.sleep(retry_after)
            return call_riot_api(url) # Reintentar la llamada
        return None

def obtener_puuid_y_id(game_name, tag_line):
    url = f"{BASE_URL_ASIA}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    data = call_riot_api(url)
    if data:
        return data.get('puuid'), data.get('gameName'), data.get('tagLine')
    return None, None, None

def obtener_datos_invocador(puuid):
    url = f"{BASE_URL_EUW}/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return call_riot_api(url)

def obtener_datos_ligas(summoner_id):
    url = f"{BASE_URL_EUW}/lol/league/v4/entries/by-summoner/{summoner_id}"
    return call_riot_api(url)

def obtener_estado_partida(summoner_id):
    url = f"{BASE_URL_EUW}/lol/spectator/v4/active-games/by-summoner/{summoner_id}"
    return call_riot_api(url)

def obtener_match_history_ids(puuid, count=10):
    url = f"{BASE_URL_ASIA}/lol/match/v5/matches/by-puuid/{puuid}/ids?count={count}"
    return call_riot_api(url)

def obtener_match_details(match_id):
    url = f"{BASE_URL_ASIA}/lol/match/v5/matches/{match_id}"
    return call_riot_api(url)

def obtener_todos_los_campeones():
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    data = call_riot_api(url)
    if data and 'data' in data:
        return {v['key']: k for k, v in data['data'].items()}
    return {}

def obtener_todas_las_runas():
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
    data = call_riot_api(url)
    runes = {}
    if data:
        for tree in data:
            runes[tree['id']] = tree['icon'] # Store the style icon for the main tree
            for slot in tree['slots']:
                for perk in slot['runes']:
                    runes[perk['id']] = perk['icon'] # Store individual rune icons
    return runes

def obtener_todos_los_hechizos():
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
    data = call_riot_api(url)
    spells = {}
    if data and 'data' in data:
        for k, v in data['data'].items():
            spells[v['key']] = v['id'] # Store spell ID to name mapping
    return spells


# Cache para campeones y runas
ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def actualizar_ddragon_data():
    global ALL_CHAMPIONS, ALL_RUNES, ALL_SUMMONER_SPELLS
    ALL_CHAMPIONS = obtener_todos_los_campeones()
    ALL_RUNES = obtener_todas_las_runas()
    ALL_SUMMONER_SPELLS = obtener_todos_los_hechizos()
    print("DDragon champion, rune, and summoner spell data updated.")

# Función para obtener los datos de un jugador
def obtener_datos_jugador(nombre_interno, game_name, tag_line):
    puuid, riot_game_name, riot_tag_line = obtener_puuid_y_id(game_name, tag_line)
    if not puuid:
        return {"nombre": nombre_interno, "game_name": f"{game_name}#{tag_line}", "error": "PUUID no encontrado"}

    summoner_data = obtener_datos_invocador(puuid)
    if not summoner_data:
        return {"nombre": nombre_interno, "game_name": f"{game_name}#{tag_line}", "error": "Datos de invocador no encontrados"}

    summoner_id = summoner_data['id']
    profile_icon_id = summoner_data['profileIconId']
    summoner_level = summoner_data['summonerLevel']

    league_data = obtener_datos_ligas(summoner_id)
    soloq_data = None
    flex_data = None

    for entry in league_data:
        if entry['queueType'] == "RANKED_SOLO_5x5":
            soloq_data = {
                "tier": entry['tier'],
                "rank": entry['rank'],
                "league_points": entry['leaguePoints'],
                "wins": entry['wins'],
                "losses": entry['losses'],
                "valor_clasificacion": calculate_elo(entry['tier'], entry['rank'], entry['leaguePoints'])
            }
        elif entry['queueType'] == "RANKED_FLEX_SR":
            flex_data = {
                "tier": entry['tier'],
                "rank": entry['rank'],
                "league_points": entry['leaguePoints'],
                "wins": entry['wins'],
                "losses": entry:['losses'],
                "valor_clasificacion": calculate_elo(entry['tier'], entry['rank'], entry['leaguePoints'])
            }

    # Actualizar peak elo
    current_timestamp = int(datetime.now().timestamp())
    if puuid not in HISTORIAL_ELO:
        HISTORIAL_ELO[puuid] = {'RANKED_SOLO_5x5': {}, 'RANKED_FLEX_SR': {}}

    if soloq_data:
        if puuid not in CACHE_JUGADORES or 'soloq' not in CACHE_JUGADORES[puuid]:
            soloq_data['peak_elo'] = soloq_data['valor_clasificacion']
        else:
            soloq_data['peak_elo'] = max(soloq_data['valor_clasificacion'], CACHE_JUGADORES[puuid]['soloq'].get('peak_elo', 0))
        HISTORIAL_ELO[puuid]['RANKED_SOLO_5x5'][current_timestamp] = soloq_data['valor_clasificacion']

    if flex_data:
        if puuid not in CACHE_JUGADORES or 'flex' not in CACHE_JUGADORES[puuid]:
            flex_data['peak_elo'] = flex_data['valor_clasificacion']
        else:
            flex_data['peak_elo'] = max(flex_data['valor_clasificacion'], CACHE_JUGADORES[puuid]['flex'].get('peak_elo', 0))
        HISTORIAL_ELO[puuid]['RANKED_FLEX_SR'][current_timestamp] = flex_data['valor_clasificacion']

    # Obtener estado en partida
    en_partida = False
    nombre_campeon_ingame = None
    try:
        spectator_data = obtener_estado_partida(summoner_id)
        if spectator_data and 'gameId' in spectator_data:
            en_partida = True
            for participant in spectator_data['participants']:
                if participant['summonerId'] == summoner_id:
                    # Obtener el nombre del campeón usando el ID
                    champion_id_ingame = str(participant['championId'])
                    nombre_campeon_ingame = ALL_CHAMPIONS.get(champion_id_ingame, "Desconocido")
                    break
    except Exception as e:
        print(f"Error al obtener estado de partida para {nombre_interno}: {e}")
        en_partida = False
        nombre_campeon_ingame = None

    # Obtener historial de partidas
    match_ids = obtener_match_history_ids(puuid, count=20) # Obtener más partidas para el historial detallado
    recent_matches = []
    if match_ids:
        for match_id in match_ids:
            match_details = obtener_match_details(match_id)
            if match_details:
                for participant_data in match_details['info']['participants']:
                    if participant_data['puuid'] == puuid:
                        # Extraer IDs de ítems, reemplazando None con 0
                        items = [
                            participant_data.get(f'item{i}', 0) for i in range(0, 7)
                        ]

                        # Obtener IDs de hechizos y runas
                        spell1_id = participant_data.get('summoner1Id')
                        spell2_id = participant_data.get('summoner2Id')
                        
                        # Obtener runas (perks)
                        perks = participant_data.get('perks', {})
                        perk_main_id = None
                        perk_sub_id = None

                        if 'styles' in perks and len(perks['styles']) > 0:
                            # Runa principal (keystone)
                            if len(perks['styles'][0]['selections']) > 0:
                                perk_main_id = perks['styles'][0]['selections'][0]['perk']
                            # Estilo de runa secundaria
                            if len(perks['styles']) > 1:
                                perk_sub_id = perks['styles'][1]['style']

                        recent_matches.append({
                            'match_id': match_id,
                            'champion_name': ALL_CHAMPIONS.get(str(participant_data['championId']), "Desconocido"),
                            'kills': participant_data['kills'],
                            'deaths': participant_data['deaths'],
                            'assists': participant_data['assists'],
                            'win': participant_data['win'],
                            'queue_id': match_details['info']['queueId'],
                            'game_end_timestamp': match_details['info']['gameEndTimestamp'],
                            'items': items,
                            'champion_level': participant_data.get('champLevel'), # Nuevo: Nivel del campeón
                            'summoner_spell_1_id': ALL_SUMMONER_SPELLS.get(str(spell1_id)), # Nuevo: Hechizo 1
                            'summoner_spell_2_id': ALL_SUMMONER_SPELLS.get(str(spell2_id)), # Nuevo: Hechizo 2
                            'perk_main_id': ALL_RUNES.get(perk_main_id), # Nuevo: Runa principal
                            'perk_sub_id': ALL_RUNES.get(perk_sub_id) # Nuevo: Runa secundaria
                        })
                        break # Salir del bucle de participantes una vez que encontramos al jugador

    player_data = {
        "puuid": puuid,
        "nombre": nombre_interno,
        "game_name": f"{riot_game_name}#{riot_tag_line}",
        "profile_icon_id": profile_icon_id,
        "summoner_level": summoner_level,
        "soloq": soloq_data,
        "flex": flex_data,
        "en_partida": en_partida,
        "nombre_campeon_ingame": nombre_campeon_ingame,
        "recent_matches": recent_matches,
        "last_updated": datetime.now().isoformat()
    }
    return player_data

# Calcular elo basado en tier, rank y league points
def calculate_elo(tier, rank, lp):
    elo_map = {
        "IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200,
        "PLATINUM": 1600, "EMERALD": 2000, "DIAMOND": 2400,
        "MASTER": 2800, "GRANDMASTER": 2900, "CHALLENGER": 3000
    }
    rank_map = {"IV": 0, "III": 100, "II": 200, "I": 300}
    
    base_elo = elo_map.get(tier.upper(), 0)
    rank_elo = rank_map.get(rank.upper(), 0) if tier.upper() not in ["MASTER", "GRANDMASTER", "CHALLENGER"] else 0
    
    return base_elo + rank_elo + lp

# Función para actualizar la caché de datos de los jugadores
def actualizar_cache():
    global CACHE_JUGADORES
    print("Actualizando caché de jugadores...")
    start_time = time.time()
    
    # Obtener la última versión de DDragon y datos de campeones/runas/hechizos
    obtener_ddragon_version()
    actualizar_ddragon_data()

    jugadores_a_procesar = list(JUGADORES_SEGUIDOS.items())
    
    def fetch_player_data(item):
        nombre_interno, datos = item
        return obtener_datos_jugador(nombre_interno, datos['gameName'], datos['tagLine'])

    with ThreadPoolExecutor(max_workers=5) as executor: # Limitar a 5 hilos para evitar saturar la API
        resultados = list(executor.map(fetch_player_data, jugadores_a_procesar))

    for player_data in resultados:
        if player_data and "puuid" in player_data:
            puuid = player_data['puuid']
            CACHE_JUGADORES[puuid] = player_data
            # Asegurarse de que el peak_elo se inicialice si el jugador es nuevo en la caché
            if 'soloq' in CACHE_JUGADORES[puuid] and 'peak_elo' not in CACHE_JUGADORES[puuid]['soloq']:
                CACHE_JUGADORES[puuid]['soloq']['peak_elo'] = CACHE_JUGADORES[puuid]['soloq']['valor_clasificacion']
            if 'flex' in CACHE_JUGADORES[puuid] and 'peak_elo' not in CACHE_JUGADORES[puuid]['flex']:
                CACHE_JUGADORES[puuid]['flex']['peak_elo'] = CACHE_JUGADORES[puuid]['flex']['valor_clasificacion']
            
            # Actualizar peak elo si el valor actual es mayor
            if 'soloq' in CACHE_JUGADORES[puuid] and CACHE_JUGADORES[puuid]['soloq']['valor_clasificacion'] > CACHE_JUGADORES[puuid]['soloq']['peak_elo']:
                CACHE_JUGADORES[puuid]['soloq']['peak_elo'] = CACHE_JUGADORES[puuid]['soloq']['valor_clasificacion']
            if 'flex' in CACHE_JUGADORES[puuid] and CACHE_JUGADORES[puuid]['flex']['valor_clasificacion'] > CACHE_JUGADORES[puuid]['flex']['peak_elo']:
                CACHE_JUGADORES[puuid]['flex']['peak_elo'] = CACHE_JUGADORES[puuid]['flex']['valor_clasificacion']

    guardar_peak_elo() # Guardar los picos actualizados
    guardar_historial_elo() # Guardar el historial de elo
    
    end_time = time.time()
    print(f"Caché de jugadores actualizada en {end_time - start_time:.2f} segundos.")


# Rutas de la aplicación Flask
@app.route('/')
def index():
    # Asegurarse de que la caché esté actualizada o cargarla si está vacía
    if not CACHE_JUGADORES:
        actualizar_cache()
    
    # Convertir el diccionario de jugadores a una lista para Jinja2
    datos_jugadores_lista = []
    for puuid, data in CACHE_JUGADORES.items():
        # Crear entradas para SoloQ y Flex si existen datos
        if data.get('soloq'):
            soloq_entry = data['soloq'].copy()
            soloq_entry['nombre'] = data['nombre']
            soloq_entry['game_name'] = data['game_name']
            soloq_entry['queue_type'] = "RANKED_SOLO_5x5"
            soloq_entry['en_partida'] = data.get('en_partida', False)
            soloq_entry['nombre_campeon'] = data.get('nombre_campeon_ingame')
            soloq_entry['puuid'] = puuid
            soloq_entry['profile_icon_id'] = data['profile_icon_id']
            soloq_entry['summoner_level'] = data['summoner_level']
            # Asegurarse de que peak_elo esté presente
            soloq_entry['peak_elo'] = soloq_entry.get('peak_elo', soloq_entry['valor_clasificacion'])
            
            # Añadir top_champion_stats para SoloQ
            if 'recent_matches' in data:
                soloq_matches = [m for m in data['recent_matches'] if m['queue_id'] == 420]
                if soloq_matches:
                    champion_counts = Counter(m['champion_name'] for m in soloq_matches)
                    top_champions_raw = champion_counts.most_common(3) # Get top 3 champions
                    top_champion_stats = []
                    for champ_name, games_played in top_champions_raw:
                        champ_matches = [m for m in soloq_matches if m['champion_name'] == champ_name]
                        wins = sum(1 for m in champ_matches if m['win'])
                        losses = games_played - wins
                        win_rate = (wins / games_played * 100) if games_played > 0 else 0

                        # Calculate KDA for the champion
                        total_kills = sum(m['kills'] for m in champ_matches)
                        total_deaths = sum(m['deaths'] for m in champ_matches)
                        total_assists = sum(m['assists'] for m in champ_matches)
                        kda = (total_kills + total_assists) / max(1, total_deaths) # Avoid division by zero

                        top_champion_stats.append({
                            'champion_name': champ_name,
                            'games_played': games_played,
                            'win_rate': win_rate,
                            'kda': kda
                        })
                    soloq_entry['top_champion_stats'] = top_champion_stats
                else:
                    soloq_entry['top_champion_stats'] = [] # No soloq matches found
            else:
                soloq_entry['top_champion_stats'] = [] # No recent matches data

            datos_jugadores_lista.append(soloq_entry)

        if data.get('flex'):
            flex_entry = data['flex'].copy()
            flex_entry['nombre'] = data['nombre']
            flex_entry['game_name'] = data['game_name']
            flex_entry['queue_type'] = "RANKED_FLEX_SR"
            flex_entry['en_partida'] = data.get('en_partida', False)
            flex_entry['nombre_campeon'] = data.get('nombre_campeon_ingame')
            flex_entry['puuid'] = puuid
            flex_entry['profile_icon_id'] = data['profile_icon_id']
            flex_entry['summoner_level'] = data['summoner_level']
            # Asegurarse de que peak_elo esté presente
            flex_entry['peak_elo'] = flex_entry.get('peak_elo', flex_entry['valor_clasificacion'])

            # Añadir top_champion_stats para Flex
            if 'recent_matches' in data:
                flex_matches = [m for m in data['recent_matches'] if m['queue_id'] == 440]
                if flex_matches:
                    champion_counts = Counter(m['champion_name'] for m in flex_matches)
                    top_champions_raw = champion_counts.most_common(3) # Get top 3 champions
                    top_champion_stats = []
                    for champ_name, games_played in top_champions_raw:
                        champ_matches = [m for m in flex_matches if m['champion_name'] == champ_name]
                        wins = sum(1 for m in champ_matches if m['win'])
                        losses = games_played - wins
                        win_rate = (wins / games_played * 100) if games_played > 0 else 0

                        # Calculate KDA for the champion
                        total_kills = sum(m['kills'] for m in champ_matches)
                        total_deaths = sum(m['deaths'] for m in champ_matches)
                        total_assists = sum(m['assists'] for m in champ_matches)
                        kda = (total_kills + total_assists) / max(1, total_deaths) # Avoid division by zero

                        top_champion_stats.append({
                            'champion_name': champ_name,
                            'games_played': games_played,
                            'win_rate': win_rate,
                            'kda': kda
                        })
                    flex_entry['top_champion_stats'] = top_champion_stats
                else:
                    flex_entry['top_champion_stats'] = [] # No flex matches found
            else:
                flex_entry['top_champion_stats'] = [] # No recent matches data

            datos_jugadores_lista.append(flex_entry)

    # Obtener la última hora de actualización
    ultima_actualizacion = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if CACHE_JUGADORES:
        any_player_puuid = next(iter(CACHE_JUGADORES))
        if 'last_updated' in CACHE_JUGADORES[any_player_puuid]:
            ultima_actualizacion = datetime.fromisoformat(CACHE_JUGADORES[any_player_puuid]['last_updated']).strftime('%Y-%m-%d %H:%M:%S')

    return render_template('index.html', datos_jugadores=datos_jugadores_lista, ultima_actualizacion=ultima_actualizacion, ddragon_version=DDRAGON_VERSION)

@app.route('/jugador/<game_name_tag>', methods=['GET'])
def perfil_jugador(game_name_tag):
    # Decodificar el game_name_tag para manejar el '#'
    game_name, tag_line = game_name_tag.rsplit('#', 1) if '#' in game_name_tag else (game_name_tag, "")

    # Buscar el jugador en la caché por su game_name y tag_line
    perfil = None
    for puuid, data in CACHE_JUGADORES.items():
        if data.get('game_name') == f"{game_name}#{tag_line}":
            perfil = data
            break
    
    if perfil:
        return render_template('jugador.html', perfil=perfil, ddragon_version=DDRAGON_VERSION)
    else:
        return "Jugador no encontrado o datos no disponibles.", 404

# Hilo para la actualización de estadísticas en segundo plano
def actualizar_historial_partidas_en_segundo_plano():
    while True:
        try:
            print("Iniciando actualización de historial de partidas en segundo plano...")
            start_time = time.time()
            
            # Asegurarse de que los datos de DDragon estén cargados
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                obtener_ddragon_version()
                actualizar_ddragon_data()

            for nombre_interno, datos_jugador in JUGADORES_SEGUIDOS.items():
                puuid, _, _ = obtener_puuid_y_id(datos_jugador['gameName'], datos_jugador['tagLine'])
                if puuid and puuid in CACHE_JUGADORES:
                    # Solo actualizamos el historial de partidas para los jugadores ya en caché
                    # La lógica de obtener_datos_jugador ya incluye el historial
                    updated_player_data = obtener_datos_jugador(nombre_interno, datos_jugador['gameName'], datos_jugador['tagLine'])
                    if updated_player_data:
                        CACHE_JUGADORES[puuid] = updated_player_data
                        print(f"Historial de partidas actualizado para {nombre_interno}")
                    else:
                        print(f"No se pudo actualizar el historial de partidas para {nombre_interno}")
                time.sleep(1) # Pequeña pausa para evitar saturar la API
            
            guardar_peak_elo()
            guardar_historial_elo()
            end_time = time.time()
            print(f"Actualización de historial de partidas completada en {end_time - start_time:.2f} segundos.")
            time.sleep(900) # Esperar 15 minutos antes del siguiente ciclo

        except Exception as e:
            print(f"Error en el hilo de actualización de estadísticas: {e}. Reintentando en 5 minutos.")
            time.sleep(300)

def keep_alive():
    """Envía una solicitud periódica a la propia aplicación para mantenerla activa en servicios como Render."""
    while True:
        try:
            # Asegúrate de reemplazar 'https://soloq-cerditos.onrender.com/' con la URL real de tu aplicación si es diferente
            requests.get('https://soloq-cerditos.onrender.com/')
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
    # Cargar historial de elo y peak elo al inicio
    cargar_historial_elo()
    cargar_peak_elo()

    # Hilo para mantener la app activa en Render
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()

    # Hilo para actualizar la caché en segundo plano
    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()

    # Hilo para la actualización del historial de partidas (puede ser el mismo que el de la caché si se desea)
    # Si quieres que el historial de partidas se actualice con una frecuencia diferente, puedes crear un hilo separado.
    # Por ahora, la lógica de obtener_datos_jugador ya se encarga de obtener el historial.
    # Si la frecuencia de actualización de 'recent_matches' es más alta que CACHE_TIMEOUT,
    # entonces necesitarías un hilo separado solo para eso o ajustar CACHE_TIMEOUT.
    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()

    app.run(debug=True, host='0.0.0.0', port=os.environ.get('PORT', 5000))