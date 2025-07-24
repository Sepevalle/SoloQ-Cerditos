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
    }
    # The queue_id passed here should now always be an integer due to changes in procesar_jugador
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
        return "N/A"
    
    try:
        valor = int(valor)
    except (ValueError, TypeError):
        return "N/A"

    # Master, Grandmaster, Challenger
    if valor >= 2800:
        lps = valor - 2800
        # No podemos distinguir entre Master, GM, Challenger solo con el valor.
        # Mostramos un genérico para estos tiers.
        if valor >= 3000: # Arbitrary threshold for Challenger
            return f"CHALLENGER ({lps} LPs)"
        elif valor >= 2900: # Arbitrary threshold for Grandmaster
            return f"GRANDMASTER ({lps} LPs)"
        else: # Master
            return f"MASTER ({lps} LPs)"

    tier_map_reverse = {
        6: "DIAMOND", 5: "EMERALD", 4: "PLATINUM", 3: "GOLD", 
        2: "SILVER", 1: "BRONZE", 0: "IRON"
    }
    rank_map_reverse = {3: "I", 2: "II", 1: "III", 0: "IV"}

    tier_value_numeric = valor // 400
    remainder_after_tier = valor % 400
    rank_value_numeric = remainder_after_tier // 100
    lps = remainder_after_tier % 100
    
    tier_str = tier_map_reverse.get(tier_value_numeric, "UNKNOWN")
    rank_str = rank_map_reverse.get(rank_value_numeric, "")
    return f"{tier_str.capitalize()} {rank_str} ({lps} LPs)"

# Configuración de la API de Riot Games
RIOT_API_KEY = os.environ.get("RIOT_API_KEY")
if not RIOT_API_KEY:
    print("Error: RIOT_API_KEY no está configurada en las variables de entorno.")
    exit(1)

# URLs base de la API de Riot
BASE_URL_ASIA = "https://asia.api.riotgames.com"
BASE_URL_EUW = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

# Global map for queue type strings to their numeric IDs from League-V4 API
QUEUE_TYPE_TO_ID_MAP = {
    "RANKED_SOLO_5x5": 420,
    "RANKED_FLEX_SR": 440,
    # Add other ranked queue types if they appear and need mapping from League-V4
}

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": [],
    "timestamp": 0,
    "update_count": 0 # Contador para controlar la frecuencia de llamadas a esta_en_partida
}
CACHE_TIMEOUT = 130  # 2 minutos
cache_lock = threading.Lock()

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
API_SESSION = requests.Session()

def make_api_request(url, retries=3, backoff_factor=0.5):
    """
    Realiza una petición a la API de Riot con reintentos y manejo de Rate Limit (código 429).
    """
    for i in range(retries):
        try:
            # Añadimos la clave de la API en la cabecera de cada petición
            headers = {"X-Riot-Token": RIOT_API_KEY}
            response = API_SESSION.get(url, headers=headers, timeout=10)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                print(f"Rate limit exceeded. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status() # Lanza una excepción para códigos de error HTTP (4xx o 5xx)
            return response
        except requests.exceptions.RequestException as e:
            print(f"Error in request to {url}: {e}. Attempt {i + 1}/{retries}")
            if i < retries - 1:
                time.sleep(backoff_factor * (2 ** i))
    return None

DDRAGON_VERSION = "14.9.1"

def actualizar_version_ddragon():
    """Obtiene la última versión de Data Dragon."""
    global DDRAGON_VERSION
    try:
        url = f"{BASE_URL_DDRAGON}/api/versions.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            DDRAGON_VERSION = response.json()[0]
            print(f"Data Dragon version set to: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"Error getting Data Dragon version: {e}. Using fallback version: {DDRAGON_VERSION}")

actualizar_version_ddragon()

ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    """Carga los datos de todos los campeones desde Data Dragon."""
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    response = make_api_request(url_campeones)
    if response:
        return {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
    return {}

def obtener_todas_las_runas():
    """Carga los datos de las runas desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
    data = make_api_request(url)
    runes = {}
    if data:
        for tree in data.json():
            runes[tree['id']] = tree['icon']
            for slot in tree['slots']:
                for perk in slot['runes']:
                    runes[perk['id']] = perk['icon']
    return runes

def obtener_todos_los_hechizos():
    """Carga los datos de los hechizos de invocador desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
    data = make_api_request(url)
    spells = {}
    if data and 'data' in data.json():
        for k, v in data.json()['data'].items():
            spells[int(v['key'])] = v['id']
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
    return ALL_CHAMPIONS.get(champion_id, "Desconocido")

def obtener_puuid(api_key, riot_id, region):
    """Obtiene el PUUID de un jugador dado su Riot ID y región."""
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"Could not get PUUID for {riot_id} after several attempts.")
        return None

def obtener_id_invocador(api_key, puuid):
    """Obtiene el ID de invocador y el profileIconId de un jugador dado su PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"Could not get summoner ID for {puuid}.")
        return None

def obtener_elo(api_key, puuid):
    """Obtiene la información de Elo de un jugador dado su PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"Could not get Elo for {puuid}.")
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
            print(f"Warning: Player {puuid} is in game but not found in participants list.")
            return None  
        elif response.status_code == 404:  # Player not in game (expected response)
            return None
        else:  # Unexpected error
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error checking if player {puuid} is in game: {e}")
        return None

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida,
    además del nivel, hechizos, runas y MUCHAS MÁS ESTADÍSTICAS DETALLADAS para análisis exhaustivo.
    Ahora también incluye el ELO del jugador en el momento de la consulta.
    """
    match_id, puuid, api_key, player_elo_data_at_fetch = args # player_elo_data_at_fetch now contains both soloq and flexq data
    url_match = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={api_key}"
    response_match = make_api_request(url_match)
    if not response_match:
        return None
    try:
        match_data = response_match.json()
        info = match_data.get('info', {})
        participants = info.get('participants', [])

        if any(p.get('gameEndedInEarlySurrender', False) for p in participants):
            print(f"Match {match_id} marked as remake.")
            return None

        main_player_data = None
        
        # Calculate team totals for share metrics
        team_totals = {
            100: {'kills': 0, 'damage': 0, 'gold': 0, 'cs': 0},
            200: {'kills': 0, 'damage': 0, 'gold': 0, 'cs': 0}
        }
        all_participants_details = [] # To store details for all participants

        for p in participants:
            team_id = p.get('teamId')
            if team_id in team_totals:
                team_totals[team_id]['kills'] += p.get('kills', 0)
                team_totals[team_id]['damage'] += p.get('totalDamageDealtToChampions', 0)
                team_totals[team_id]['gold'] += p.get('goldEarned', 0)
                team_totals[team_id]['cs'] += p.get('totalMinionsKilled', 0) + p.get('neutralMinionsKilled', 0)

            if p.get('puuid') == puuid:
                main_player_data = p
            
            # Collect details for all participants for the expanded view
            all_participants_details.append({
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
            })


        if not main_player_data:
            return None

        game_end_timestamp = info.get('gameEndTimestamp', 0) + 7200000 # Convert to local time if needed, assuming it's UTC
        game_duration = info.get('gameDuration', 0) # In seconds

        p = main_player_data
        player_team_id = p.get('teamId')
        
        # Calculate player's kill participation (KP)
        total_team_kills = team_totals[player_team_id]['kills']
        player_kills = p.get('kills', 0)
        player_assists = p.get('assists', 0)
        kill_participation = 0
        if total_team_kills > 0:
            kill_participation = (player_kills + player_assists) / total_team_kills * 100

        # Calculate player's damage share
        total_team_damage = team_totals[player_team_id]['damage']
        player_damage = p.get('totalDamageDealtToChampions', 0)
        damage_share = 0
        if total_team_damage > 0:
            damage_share = (player_damage / total_team_damage) * 100

        # Calculate player's gold share
        total_team_gold = team_totals[player_team_id]['gold']
        player_gold = p.get('goldEarned', 0)
        gold_share = 0
        if total_team_gold > 0:
            gold_share = (player_gold / total_team_gold) * 100
        
        # Calculate player's CS share
        total_team_cs = team_totals[player_team_id]['cs']
        player_cs = p.get('totalMinionsKilled', 0) + p.get('neutralMinionsKilled', 0)
        cs_share = 0
        if total_team_cs > 0:
            cs_share = (player_cs / total_team_cs) * 100

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

        # Add KP% to all participants in all_participants_details
        for participant_detail in all_participants_details:
            p_team_id = participant_detail.get('team_id')
            p_total_team_kills = team_totals.get(p_team_id, {'kills': 0})['kills']
            p_kills = participant_detail.get('kills', 0)
            p_assists = participant_detail.get('assists', 0)
            
            kp = 0
            if p_total_team_kills > 0:
                kp = (p_kills + p_assists) / p_total_team_kills * 100
            participant_detail['kill_participation'] = kp


        return {
            "match_id": match_id,
            "champion_name": obtener_nombre_campeon(p.get('championId')),
            "win": p.get('win', False),
            "kills": player_kills,
            "deaths": p.get('deaths', 0),
            "assists": p.get('assists', 0),
            "kda": (player_kills + player_assists) / max(1, p.get('deaths', 0)),
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
            "gold_earned": player_gold,
            "gold_spent": p.get('goldSpent', 0),
            "game_duration": game_duration, # In seconds
            "total_damage_dealt": p.get('totalDamageDealt', 0),
            "total_damage_dealt_to_champions": player_damage,
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
            "largestMultiKill": p.get('largestMultiKill', 0),
            "pentaKills": p.get('pentaKills', 0),
            "quadraKills": p.get('quadraKills', 0),
            "tripleKills": p.get('tripleKills', 0),
            "doubleKills": p.get('doubleKills', 0),
            "individual_position": p.get('individualPosition', 'N/A'),
            "team_position": p.get('teamPosition', 'N/A'), # New: Specific role within the team
            "total_damage_taken": p.get('totalDamageTaken', 0),
            "total_time_cc_dealt": p.get('totalTimeCCDealt', 0),
            "first_blood_kill": p.get('firstBloodKill', False),
            "first_blood_assist": p.get('firstBloodAssist', False),
            "objectives_stolen": p.get('objectivesStolen', 0),
            "kill_participation": kill_participation,
            "damage_share": damage_share, # New: Player's damage share of team's total damage to champions
            "gold_share": gold_share,     # New: Player's gold share of team's total gold
            "cs_share": cs_share,         # New: Player's CS share of team's total CS
            "total_team_kills": total_team_kills, # New: Total team kills for context
            "total_team_damage": total_team_damage, # New: Total team damage to champions for context
            "total_team_gold": total_team_gold, # New: Total team gold for context
            "total_team_cs": total_team_cs, # New: Total team CS for context
            "all_participants": all_participants_details, # All participants data
            "player_elo_at_match_time": player_elo_data_at_fetch # Elo data at the time this match was fetched
        }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error processing match details {match_id}: {e}")
    return None

def leer_cuentas(url):
    """Reads player accounts from a text file hosted on GitHub."""
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
            print(f"Error reading file: {response.status_code}")
            return []
    except Exception as e:
        print(f"Error reading accounts: {e}")
        return []

def calcular_valor_clasificacion(tier, rank, league_points):
    """
    Calculates a numeric value for a player's rank,
    allowing for easier sorting and comparison of Elo.
    """
    tier_upper = tier.upper()
    
    # For Master, Grandmaster, and Challenger, the calculation is simpler.
    # The base is 2800 (the value after Diamond I 100 LP) and LPs are added.
    if tier_upper in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        # Assign a higher base value for GM/Challenger to ensure they sort above Master
        if tier_upper == "CHALLENGER":
            return 3000 + league_points
        elif tier_upper == "GRANDMASTER":
            return 2900 + league_points
        else: # Master
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
    # Corrected tierOrden for consistency with string keys
    tierOrden = {
        "DIAMOND": 6,
        "EMERALD": 5,
        "PLATINUM": 4,
        "GOLD": 3,
        "SILVER": 2,
        "BRONZE": 1,
        "IRON": 0
    }


    # The division value is an extra on top of the base tier value (IV=0, III=100, II=200, I=300)
    rankOrden = {"I": 3, "II": 2, "III": 1, "IV": 0}

    valor_base_tier = tierOrden.get(tier_upper, 0) * 400
    valor_division = rankOrden.get(rank, 0) * 100

    return valor_base_tier + valor_division + league_points

def leer_peak_elo():
    """Reads peak Elo data from a JSON file on GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/Full-IA/peak_elo.json"
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        return True, resp.json()
    except Exception as e:
        print(f"Error reading peak elo: {e}")
    return False, {}

def leer_puuids():
    """Reads the PUUIDs file from GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/Full-IA/puuids.json"
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print("The puuids.json file does not exist, a new one will be created.")
            return {}
    except Exception as e:
        print(f"Error reading puuids.json: {e}")
        return {}

def guardar_puuids_en_github(puuid_dict):
    """Saves or updates the puuids.json file on GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/puuids.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("GitHub token not found to save PUUIDs.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"Could not get SHA for puuids.json: {e}")

    contenido_json = json.dumps(puuid_dict, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": "Update PUUIDs", "content": contenido_b64, "branch": "Full-IA"}
    if sha:
        data["sha"] = sha

    response = requests.put(url, headers=headers, json=data)
    if response.status_code in (200, 201):
        print("puuids.json file updated successfully on GitHub.")
    else:
        print(f"Error updating puuids.json: {response.status_code} - {response.text}")

def guardar_peak_elo_en_github(peak_elo_dict):
    """Saves or updates the peak_elo.json file on GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("GitHub token not found")
        return

    sha = None
    try:
        response = requests.get(url, headers={"Authorization": f"token {token}"})
        if response.status_code == 200:
            contenido_actual = response.json()
            sha = contenido_actual['sha']
        else:
            print(f"Error getting peak_elo.json file for SHA: {response.status_code}")
    except Exception as e:
        print(f"Error getting SHA for peak_elo.json: {e}")
        return

    try:
        contenido_json = json.dumps(peak_elo_dict, ensure_ascii=False, indent=2)
        contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')

        response = requests.put(
            url,
            headers={"Authorization": f"token {token}"},
            json={
                "message": "Update peak elo",
                "content": contenido_b64,
                "sha": sha,
                "branch": "Full-IA"
            }
        )
        if response.status_code in (200, 201):
            print("peak_elo.json file updated successfully on GitHub.")
        else:
            print(f"Error updating peak_elo.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error updating peak_elo.json file: {e}")

def leer_historial_jugador_github(puuid):
    """Reads a player's match history from GitHub."""
    url = f"https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/Full-IA/match_history/{puuid}.json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print(f"No history found for {puuid}. A new one will be created.")
            return {}
    except Exception as e:
        print(f"Error reading history for {puuid}: {e}")
    return {}

def guardar_historial_jugador_github(puuid, historial_data):
    """Saves or updates a player's match history on GitHub."""
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/match_history/{puuid}.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"GitHub token not found to save history for {puuid}.")
        return

    headers = {"Authorization": f"token {token}"}
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"Could not get SHA for {puuid}'s history: {e}")

    contenido_json = json.dumps(historial_data, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    data = {"message": f"Update match history for {puuid}", "content": contenido_b64, "branch": "Full-IA"}
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code in (200, 201):
            print(f"History for {puuid}.json updated successfully on GitHub.")
        else:
            print(f"Error updating history for {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error in PUT request to GitHub for {puuid}'s history: {e}")

def procesar_jugador(args_tuple):
    """
    Processes a single player's data.
    Implements smart update logic to reduce API calls.
    Only updates Elo if the player is currently in game or has recently finished a game.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list, check_in_game_this_update = args_tuple
    riot_id, jugador_nombre = cuenta

    if not puuid:
        print(f"WARNING: Skipping processing for {riot_id} because its PUUID could not be obtained. Check if the Riot ID is correct in accounts.txt.")
        return []

    champion_id = None
    is_currently_in_game = False
    if check_in_game_this_update: # Only calls esta_en_partida in specific cycles
        champion_id = esta_en_partida(api_key_spectator, puuid)
        is_currently_in_game = champion_id is not None

    # Check if the player was in game in the previous cycle.
    was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
    
    # Full update is only done if it's a new player, if they are currently in game,
    # or if they just finished a game (was in game before but not anymore).
    needs_full_update = not old_data_list or is_currently_in_game or was_in_game_before

    if not needs_full_update:
        # Inactive player, we reuse old data and only update their status.
        print(f"Player {riot_id} inactive. Skipping Elo update.")
        for data in old_data_list:
            data['en_partida'] = False # Ensure status is reflected correctly
        return old_data_list

    print(f"Updating full data for {riot_id} (status: {'in game' if is_currently_in_game else 'recently finished'}).")
    
    # Get summoner ID and profileIconId
    summoner_info = obtener_id_invocador(api_key_main, puuid)
    profile_icon_id = summoner_info.get('profileIconId') if summoner_info else None
    perfil_icon_url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/img/profileicon/{profile_icon_id}.png" if profile_icon_id else "https://placehold.co/120x120/4a90e2/ffffff?text=Icono"


    elo_info = obtener_elo(api_key_main, puuid)
    if not elo_info:
        print(f"Could not get Elo for {riot_id}. Reusing old data if available.")
        # If Elo retrieval fails, we return old data if it exists,
        # but update the 'en_partida' status if just checked.
        if old_data_list:
            for data in old_data_list:
                data['en_partida'] = is_currently_in_game
            return old_data_list
        return []

    riot_id_modified = riot_id.replace("#", "-")
    url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
    url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"
    
    datos_jugador_list = []
    for entry in elo_info:
        nombre_campeon = obtener_nombre_campeon(champion_id) if champion_id else "Desconocido"
        
        # Get the string queueType from the API response
        api_queue_type_string = entry.get('queueType', 'Desconocido')
        # Convert it to the numeric ID using the global map
        numeric_queue_id = QUEUE_TYPE_TO_ID_MAP.get(api_queue_type_string, api_queue_type_string) # Fallback to string if not found, though for ranked it should be found

        datos_jugador = {
            "game_name": riot_id,
            "queue_type": numeric_queue_id, # Store the numeric ID here
            "tier": entry.get('tier', 'Sin rango'),
            "rank": entry.get('rank', ''),
            "league_points": entry.get('leaguePoints', 0),
            "wins": entry.get('wins', 0),
            "losses": entry.get('losses', 0),
            "jugador": jugador_nombre,
            "url_perfil": url_perfil,
            "puuid": puuid,
            "url_ingame": url_ingame,
            "en_partida": is_currently_in_game,
            "valor_clasificacion": calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('leaguePoints', 0)
            ),
            "nombre_campeon": nombre_campeon,
            "champion_id": champion_id if champion_id else "Desconocido",
            "perfil_icon_url": perfil_icon_url # Add profile icon URL
        }
        datos_jugador_list.append(datos_jugador)
    return datos_jugador_list

def actualizar_cache():
    """
    This function performs the heavy lifting: it gets all data from the API
    and updates the global cache. It is designed to run in the background.
    """
    print("Starting cache update...")
    api_key_main = os.environ.get('RIOT_API_KEY')
    api_key_spectator = os.environ.get('RIOT_API_KEY_2', api_key_main) # Use secondary or primary

    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/Full-IA/cuentas.txt"
    
    if not api_key_main:
        print("CRITICAL ERROR: RIOT_API_KEY environment variable is not set. The application cannot function.")
        return
    
    with cache_lock:
        old_cache_data = cache.get('datos_jugadores', [])
        cache['update_count'] = cache.get('update_count', 0) + 1
        check_in_game_this_update = cache['update_count'] % 2 == 1 # Call esta_en_partida every two cycles
    
    old_data_map_by_puuid = {}
    for d in old_cache_data:
        puuid = d.get('puuid')
        if puuid:
            if puuid not in old_data_map_by_puuid:
                old_data_map_by_puuid[puuid] = []
            old_data_map_by_puuid[puuid].append(d)

    cuentas = leer_cuentas(url_cuentas)

    puuid_dict = leer_puuids()
    puuids_actualizados = False

    for riot_id, _ in cuentas:
        if riot_id not in puuid_dict:
            print(f"PUUID not found for {riot_id}. Getting it from the API...")
            game_name, tag_line = riot_id.split('#')[0], riot_id.split('#')[1]
            puuid_info = obtener_puuid(api_key_main, game_name, tag_line)
            if puuid_info and 'puuid' in puuid_info:
                puuid_dict[riot_id] = puuid_info['puuid']
                puuids_actualizados = True

    if puuids_actualizados:
        guardar_puuids_en_github(puuid_dict)

    todos_los_datos = []
    tareas = []
    for cuenta in cuentas:
        riot_id = cuenta[0]
        puuid = puuid_dict.get(riot_id)
        old_data_for_player = old_data_map_by_puuid.get(puuid, []) # Ensure it's an empty list if no old data
        tareas.append((cuenta, puuid, api_key_main, api_key_spectator, 
                      old_data_for_player, check_in_game_this_update))

    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    # Removed the redundant queue_map here as queue_type is now numeric from procesar_jugador
    for jugador in todos_los_datos:
        puuid = jugador.get('puuid')
        queue_id = jugador.get('queue_type') # queue_type is already the numeric ID
        
        jugador['top_champion_stats'] = []
        if not puuid or not queue_id:
            continue

        historial = leer_historial_jugador_github(puuid)
        partidas_jugador = [
            p for p in historial.get('matches', []) 
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
    print("Cache update completed.")

def obtener_datos_jugadores():
    """Gets cached player data."""
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    """Generates a key for peak ELO using the player's name and Riot ID."""
    return f"{jugador['queue_type']}|{jugador['jugador']}|{jugador['game_name']}"

def calcular_rachas(partidas):
    """
    Calculates the longest win and loss streaks from a list of matches.
    Matches must be sorted by date, from most recent to oldest.
    """
    if not partidas:
        return {'max_win_streak': 0, 'max_loss_streak': 0}

    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    for partida in reversed(partidas): # Invert to calculate chronologically
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
            
    return {'max_win_streak': max_win_streak, 'max_loss_streak': max_loss_streak}

# New function to calculate aggregated statistics per queue
def calcular_stats_agregadas(partidas):
    """Calculates aggregated statistics (per game/minute averages) for a list of matches."""
    stats = {
        'total_games': len(partidas),
        'total_wins': sum(1 for p in partidas if p.get('win')),
        'total_losses': sum(1 for p in partidas if not p.get('win')),
        'total_kills': sum(p.get('kills', 0) for p in partidas),
        'total_deaths': sum(p.get('deaths', 0) for p in partidas),
        'total_assists': sum(p.get('assists', 0) for p in partidas),
        'total_cs': sum(p.get('total_minions_killed', 0) + p.get('neutral_minions_killed', 0) for p in partidas),
        'total_gold_earned': sum(p.get('gold_earned', 0) for p in partidas),
        'total_damage_dealt_to_champions': sum(p.get('total_damage_dealt_to_champions', 0) for p in partidas),
        'total_vision_score': sum(p.get('vision_score', 0) for p in partidas),
        'total_game_duration_seconds': sum(p.get('game_duration', 0) for p in partidas),
        'total_kill_participation_sum': sum(p.get('kill_participation', 0) for p in partidas),
        'total_damage_share_sum': sum(p.get('damage_share', 0) for p in partidas), # New
        'total_gold_share_sum': sum(p.get('gold_share', 0) for p in partidas),     # New
        'total_cs_share_sum': sum(p.get('cs_share', 0) for p in partidas),         # New
        'total_damage_taken': sum(p.get('total_damage_taken', 0) for p in partidas), # New
        'total_heal': sum(p.get('total_heal', 0) for p in partidas),               # New
        'total_wards_placed': sum(p.get('wards_placed', 0) for p in partidas),     # New
        'total_wards_killed': sum(p.get('wards_killed', 0) for p in partidas),     # New
        'total_detector_wards_placed': sum(p.get('detector_wards_placed', 0) for p in partidas), # New
        'total_time_ccing_others': sum(p.get('time_ccing_others', 0) for p in partidas), # New
        'total_turret_kills': sum(p.get('turret_kills', 0) for p in partidas),     # New
        'total_inhibitor_kills': sum(p.get('inhibitor_kills', 0) for p in partidas), # New
        'total_baron_kills': sum(p.get('baron_kills', 0) for p in partidas),       # New
        'total_dragon_kills': sum(p.get('dragon_kills', 0) for p in partidas),     # New
        'total_penta_kills': sum(p.get('pentaKills', 0) for p in partidas),       # New
        'total_quadra_kills': sum(p.get('quadraKills', 0) for p in partidas),     # New
        'total_triple_kills': sum(p.get('tripleKills', 0) for p in partidas),     # New
        'total_double_kills': sum(p.get('doubleKills', 0) for p in partidas),     # New
        'total_first_blood_kills': sum(1 for p in partidas if p.get('first_blood_kill')), # New
        'total_first_blood_assists': sum(1 for p in partidas if p.get('first_blood_assist')), # New
        'total_objectives_stolen': sum(p.get('objectives_stolen', 0) for p in partidas), # New
        'total_largest_killing_spree_sum': sum(p.get('largest_killing_spree', 0) for p in partidas), # New
        'total_largest_multi_kill_sum': sum(p.get('largestMultiKill', 0) for p in partidas), # New
        'total_time_spent_dead': sum(p.get('total_time_spent_dead', 0) for p in partidas), # New
        'total_champion_level_sum': sum(p.get('champion_level', 0) for p in partidas) # New
    }

    if stats['total_games'] == 0:
        return {
            'avg_kda': 0.0, 'avg_cs_per_min': 0.0, 'avg_gold_per_min': 0.0,
            'avg_damage_per_min': 0.0, 'avg_vision_score_per_min': 0.0,
            'avg_kp': 0.0, 'win_rate': 0.0, 'total_games': 0,
            'avg_kills_per_min': 0.0, 'avg_deaths_per_min': 0.0, 'avg_assists_per_min': 0.0,
            'avg_damage_share': 0.0, 'avg_gold_share': 0.0, 'avg_cs_share': 0.0,
            'avg_damage_taken_per_min': 0.0, 'avg_heal_per_min': 0.0,
            'avg_wards_placed_per_min': 0.0, 'avg_wards_killed_per_min': 0.0, 'avg_detector_wards_placed_per_min': 0.0,
            'avg_time_ccing_others_per_min': 0.0, 'avg_turret_kills_per_game': 0.0,
            'avg_inhibitor_kills_per_game': 0.0, 'avg_baron_kills_per_game': 0.0, 'avg_dragon_kills_per_game': 0.0,
            'penta_kills': 0, 'quadra_kills': 0, 'triple_kills': 0, 'double_kills': 0,
            'first_blood_kill_rate': 0.0, 'first_blood_assist_rate': 0.0,
            'avg_objectives_stolen_per_game': 0.0, 'avg_largest_killing_spree': 0.0,
            'avg_largest_multi_kill': 0.0, 'avg_time_spent_dead_per_min': 0.0,
            'avg_champion_level': 0.0
        }

    avg_kda = (stats['total_kills'] + stats['total_assists']) / max(1, stats['total_deaths'])
    win_rate = (stats['total_wins'] / stats['total_games']) * 100

    total_duration_minutes = stats['total_game_duration_seconds'] / 60
    
    avg_cs_per_min = stats['total_cs'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_gold_per_min = stats['total_gold_earned'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_damage_per_min = stats['total_damage_dealt_to_champions'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_vision_score_per_min = stats['total_vision_score'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_kp = stats['total_kill_participation_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0

    # New per minute stats
    avg_kills_per_min = stats['total_kills'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_deaths_per_min = stats['total_deaths'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_assists_per_min = stats['total_assists'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_damage_taken_per_min = stats['total_damage_taken'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_heal_per_min = stats['total_heal'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_wards_placed_per_min = stats['total_wards_placed'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_wards_killed_per_min = stats['total_wards_killed'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_detector_wards_placed_per_min = stats['total_detector_wards_placed'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_time_ccing_others_per_min = stats['total_time_ccing_others'] / total_duration_minutes if total_duration_minutes > 0 else 0
    avg_time_spent_dead_per_min = stats['total_time_spent_dead'] / total_duration_minutes if total_duration_minutes > 0 else 0


    # New share stats
    avg_damage_share = stats['total_damage_share_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_gold_share = stats['total_gold_share_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_cs_share = stats['total_cs_share_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0

    # New per game stats
    avg_turret_kills_per_game = stats['total_turret_kills'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_inhibitor_kills_per_game = stats['total_inhibitor_kills'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_baron_kills_per_game = stats['total_baron_kills'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_dragon_kills_per_game = stats['total_dragon_kills'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_objectives_stolen_per_game = stats['total_objectives_stolen'] / stats['total_games'] if stats['total_games'] > 0 else 0
    
    first_blood_kill_rate = (stats['total_first_blood_kills'] / stats['total_games']) * 100 if stats['total_games'] > 0 else 0
    first_blood_assist_rate = (stats['total_first_blood_assists'] / stats['total_games']) * 100 if stats['total_games'] > 0 else 0

    avg_largest_killing_spree = stats['total_largest_killing_spree_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_largest_multi_kill = stats['total_largest_multi_kill_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0
    avg_champion_level = stats['total_champion_level_sum'] / stats['total_games'] if stats['total_games'] > 0 else 0


    return {
        'avg_kda': avg_kda,
        'avg_cs_per_min': avg_cs_per_min,
        'avg_gold_per_min': avg_gold_per_min,
        'avg_damage_per_min': avg_damage_per_min,
        'avg_vision_score_per_min': avg_vision_score_per_min,
        'avg_kp': avg_kp,
        'win_rate': win_rate,
        'total_games': stats['total_games'],
        'avg_kills_per_min': avg_kills_per_min,
        'avg_deaths_per_min': avg_deaths_per_min,
        'avg_assists_per_min': avg_assists_per_min,
        'avg_damage_share': avg_damage_share,
        'avg_gold_share': avg_gold_share,
        'avg_cs_share': avg_cs_share,
        'avg_damage_taken_per_min': avg_damage_taken_per_min,
        'avg_heal_per_min': avg_heal_per_min,
        'avg_wards_placed_per_min': avg_wards_placed_per_min,
        'avg_wards_killed_per_min': avg_wards_killed_per_min,
        'avg_detector_wards_placed_per_min': avg_detector_wards_placed_per_min,
        'avg_time_ccing_others_per_min': avg_time_ccing_others_per_min,
        'avg_turret_kills_per_game': avg_turret_kills_per_game,
        'avg_inhibitor_kills_per_game': avg_inhibitor_kills_per_game,
        'avg_baron_kills_per_game': avg_baron_kills_per_game,
        'avg_dragon_kills_per_game': avg_dragon_kills_per_game,
        'penta_kills': stats['total_penta_kills'],
        'quadra_kills': stats['total_quadra_kills'],
        'triple_kills': stats['total_triple_kills'],
        'double_kills': stats['total_double_kills'],
        'first_blood_kill_rate': first_blood_kill_rate,
        'first_blood_assist_rate': first_blood_assist_rate,
        'avg_objectives_stolen_per_game': avg_objectives_stolen_per_game,
        'avg_largest_killing_spree': avg_largest_killing_spree,
        'avg_largest_multi_kill': avg_largest_multi_kill,
        'avg_time_spent_dead_per_min': avg_time_spent_dead_per_min,
        'avg_champion_level': avg_champion_level
    }


@app.route('/')
def index():
    """Renders the main page with the player list."""

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
        print("WARNING: Could not read peak_elo.json file. Peak updates will be skipped.")
        for jugador in datos_jugadores:
            jugador["peak_elo"] = jugador["valor_clasificacion"]

    # Filtering logic for the main page
    queue_filter = request.args.get('queue_filter')
    if queue_filter == 'soloq':
        filtered_jugadores = [j for j in datos_jugadores if j.get('queue_type') == QUEUE_TYPE_TO_ID_MAP["RANKED_SOLO_5x5"]]
    elif queue_filter == 'flexq':
        filtered_jugadores = [j for j in datos_jugadores if j.get('queue_type') == QUEUE_TYPE_TO_ID_MAP["RANKED_FLEX_SR"]]
    else: # 'all' or no filter
        filtered_jugadores = datos_jugadores

    split_activo_nombre = SPLITS[ACTIVE_SPLIT_KEY]['name']
    ultima_actualizacion = (datetime.fromtimestamp(timestamp) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    
    return render_template('index.html', datos_jugadores=filtered_jugadores, # Pass filtered data
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION, 
                           split_activo_nombre=split_activo_nombre,
                           active_filter=queue_filter) # Pass active filter for UI highlighting

@app.route('/jugador/<path:game_name>')
def perfil_jugador(game_name):
    """
    Displays a profile page for a specific player, now using a single responsive template.
    """
    perfil = _get_player_profile_data(game_name)
    if not perfil:
        return render_template('404.html'), 404

    # Always render jugador.html, which is now responsive
    template_name = 'jugador.html'
    
    print(f"Rendering {template_name} for {game_name}.")

    return render_template(template_name,
                           perfil=perfil,
                           ddragon_version=DDRAGON_VERSION,
                           datetime=datetime,
                           now=datetime.now(),
                           soloq_elo_history_json=json.dumps(perfil.get('soloq_elo_history', [])),
                           flexq_elo_history_json=json.dumps(perfil.get('flexq_elo_history', [])))

@app.route('/buscar_invocador', methods=['GET'])
def buscar_invocador():
    """
    Handles summoner search requests and redirects to the player profile page.
    """
    game_name = request.args.get('game_name')
    if game_name:
        # You can add validation logic for game_name here if needed
        return redirect(url_for('perfil_jugador', game_name=game_name))
    return redirect(url_for('index')) # Redirect to the main page if no game_name provided


def _get_player_profile_data(game_name):
    """
    Auxiliary function that encapsulates the logic to get and process
    all data for a player profile.
    Returns the 'profile' dictionary or None if the player is not found.
    """
    todos_los_datos, _ = obtener_datos_jugadores()
    datos_del_jugador = [j for j in todos_los_datos if j.get('game_name') == game_name]
    
    if not datos_del_jugador:
        return None
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid')

    historial_partidas_completo = {}
    if puuid:
        historial_partidas_completo = leer_historial_jugador_github(puuid)

    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'),
        'game_name': game_name,
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', ''),
        'historial_partidas': historial_partidas_completo.get('matches', [])
    }
    
    for item in datos_del_jugador:
        if item.get('queue_type') == QUEUE_TYPE_TO_ID_MAP["RANKED_SOLO_5x5"]: # Use numeric ID
            perfil['soloq'] = item
        elif item.get('queue_type') == QUEUE_TYPE_TO_ID_MAP["RANKED_FLEX_SR"]: # Use numeric ID
            perfil['flexq'] = item

    historial_total = perfil.get('historial_partidas', [])
    
    # Sort history for LP calculation and graph data
    historial_total.sort(key=lambda x: x.get('game_end_timestamp', 0)) # Sort by timestamp ascending

    soloq_elo_history = []
    flexq_elo_history = []

    # Calculate LP changes and prepare graph data
    # Keep track of the last processed Elo for each queue type
    last_soloq_elo = None
    last_flexq_elo = None

    for i, match in enumerate(historial_total):
        lp_change = None # Default to None if not a ranked game or no previous data
        current_elo_data = match.get('player_elo_at_match_time', {})
        
        # Determine the queue type of the match
        match_queue_id = match.get('queue_id')

        # Process SoloQ matches
        if match_queue_id == QUEUE_TYPE_TO_ID_MAP["RANKED_SOLO_5x5"] and 'soloq' in current_elo_data:
            current_soloq_elo = current_elo_data['soloq'].get('valor_clasificacion')
            if current_soloq_elo is not None:
                if last_soloq_elo is not None:
                    lp_change = current_soloq_elo - last_soloq_elo
                last_soloq_elo = current_soloq_elo
                soloq_elo_history.append({
                    'timestamp': match['game_end_timestamp'],
                    'elo': current_soloq_elo,
                    'tier': current_elo_data['soloq'].get('tier', 'UNKNOWN'),
                    'rank': current_elo_data['soloq'].get('rank', '')
                })

        # Process FlexQ matches
        elif match_queue_id == QUEUE_TYPE_TO_ID_MAP["RANKED_FLEX_SR"] and 'flexq' in current_elo_data:
            current_flexq_elo = current_elo_data['flexq'].get('valor_clasificacion')
            if current_flexq_elo is not None:
                if last_flexq_elo is not None:
                    lp_change = current_flexq_elo - last_flexq_elo
                last_flexq_elo = current_flexq_elo
                flexq_elo_history.append({
                    'timestamp': match['game_end_timestamp'],
                    'elo': current_flexq_elo,
                    'tier': current_elo_data['flexq'].get('tier', 'UNKNOWN'),
                    'rank': current_elo_data['flexq'].get('rank', '')
                })
        
        match['lp_change'] = lp_change

    # Reverse sort for display in HTML table (most recent first)
    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    # Add Elo history to profile
    perfil['soloq_elo_history'] = soloq_elo_history
    perfil['flexq_elo_history'] = flexq_elo_history
    
    if 'soloq' in perfil:
        partidas_soloq = [p for p in historial_total if p.get('queue_id') == 420]
        rachas_soloq = calcular_rachas(partidas_soloq)
        perfil['soloq'].update(rachas_soloq)
        # Calculate and add analysis statistics for SoloQ
        perfil['soloq']['analysis_stats'] = calcular_stats_agregadas(partidas_soloq)

    if 'flexq' in perfil:
        partidas_flexq = [p for p in historial_total if p.get('queue_id') == 440]
        rachas_flexq = calcular_rachas(partidas_flexq)
        perfil['flexq'].update(rachas_flexq)
        # Calculate and add analysis statistics for FlexQ
        perfil['flexq']['analysis_stats'] = calcular_stats_agregadas(partidas_flexq)

    return perfil


def actualizar_historial_partidas_en_segundo_plano():
    """
    Function that runs in a separate thread to update the match history
    of all players periodically.
    """
    print("Starting match history update thread.")
    api_key = os.environ.get('RIOT_API_KEY')
    if not api_key:
        print("ERROR: RIOT_API_KEY not configured. Match history cannot be updated.")
        return

    # Use the global QUEUE_TYPE_TO_ID_MAP directly
    queue_map = QUEUE_TYPE_TO_ID_MAP 
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/Full-IA/cuentas.txt"

    while True:
        try:
            # Ensure DDragon data is loaded
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                print("DDragon data not fully loaded, attempting to re-update.")
                actualizar_ddragon_data()

            cuentas = leer_cuentas(url_cuentas)
            puuid_dict = leer_puuids()

            for riot_id, jugador_nombre in cuentas:
                puuid = puuid_dict.get(riot_id)
                if not puuid:
                    print(f"Skipping history update for {riot_id}: PUUID not found.")
                    continue

                historial_existente = leer_historial_jugador_github(puuid)
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}
                remakes_guardados = set(historial_existente.get('remakes', []))
                
                # Get current Elo for the player to embed in new matches
                current_elo_data_for_player = {}
                elo_info = obtener_elo(api_key, puuid)
                if elo_info:
                    for entry in elo_info:
                        if entry.get('queueType') == "RANKED_SOLO_5x5":
                            current_elo_data_for_player['soloq'] = {
                                "tier": entry.get('tier', 'Sin rango'),
                                "rank": entry.get('rank', ''),
                                "league_points": entry.get('leaguePoints', 0),
                                "valor_clasificacion": calcular_valor_clasificacion(
                                    entry.get('tier', 'Sin rango'),
                                    entry.get('rank', ''),
                                    entry.get('leaguePoints', 0)
                                )
                            }
                        elif entry.get('queueType') == "RANKED_FLEX_SR":
                            current_elo_data_for_player['flexq'] = {
                                "tier": entry.get('tier', 'Sin rango'),
                                "rank": entry.get('rank', ''),
                                "league_points": entry.get('leaguePoints', 0),
                                "valor_clasificacion": calcular_valor_clasificacion(
                                    entry.get('tier', 'Sin rango'),
                                    entry.get('rank', ''),
                                    entry.get('leaguePoints', 0)
                                )
                            }
                
                all_match_ids_season = []
                # Iterate over values of QUEUE_TYPE_TO_ID_MAP to get numeric queue IDs
                for queue_id in QUEUE_TYPE_TO_ID_MAP.values(): 
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
                
                nuevos_match_ids = [
                    mid for mid in all_match_ids_season 
                    if mid not in ids_partidas_guardadas and mid not in remakes_guardados
                ]

                if not nuevos_match_ids:
                    print(f"No new matches for {riot_id}. Skipping.")
                    continue

                print(f"Found {len(nuevos_match_ids)} new matches for {riot_id}. Processing...")

                tareas = []
                for match_id in nuevos_match_ids:
                    # Pass the entire current_elo_data_for_player dictionary
                    tareas.append((match_id, puuid, api_key, current_elo_data_for_player)) 

                with ThreadPoolExecutor(max_workers=10) as executor:
                    nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))
                
                nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]
                nuevos_remakes = [
                    match_id for i, match_id in enumerate(nuevos_match_ids)
                    if nuevas_partidas_info[i] is None
                ]
                
                if nuevas_partidas_validas:
                    historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                    # Sort after adding new matches to ensure correct LP calculation order later
                    historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'])

                if nuevos_remakes:
                    remakes_guardados.update(nuevos_remakes)
                    historial_existente['remakes'] = list(remakes_guardados)
                
                if nuevas_partidas_validas or nuevos_remakes:
                    guardar_historial_jugador_github(puuid, historial_existente)
                    print(f"History for {riot_id} updated with {len(nuevas_partidas_validas)} new matches and {len(nuevos_remakes)} remakes.")

            print("History update cycle completed. Next check in 5 minutes.")
            time.sleep(600)

        except Exception as e:
            print(f"Error in stats update thread: {e}. Retrying in 5 minutes.")
            time.sleep(600)

def keep_alive():
    """Sends a periodic request to the application itself to keep it active on services like Render."""
    while True:
        try:
            requests.get('https://soloq-cerditos-34kd.onrender.com/')
            print("Keeping the application active with a request.")
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
    """Updates the player data cache periodically."""
    while True:
        actualizar_cache()
        time.sleep(CACHE_TIMEOUT)

if __name__ == "__main__":
    # Thread to keep the app alive on Render
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()

    # Thread to update the cache in the background
    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()

    # Thread for match history update
    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)