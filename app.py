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
    """
    Filtro Jinja2 para obtener el nombre legible de una cola de partida
    dado su ID numérico.
    """
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
    """
    Filtro Jinja2 para formatear un timestamp de Riot (milisegundos)
    a una fecha y hora legibles.
    """
    # El timestamp de Riot está en milisegundos, Python datetime espera segundos
    # Divide por 1000 para convertir milisegundos a segundos
    return datetime.fromtimestamp(timestamp / 1000).strftime("%d/%m/%Y %H:%M")

# Configuración de las API Keys de Riot Games
# Prioriza las variables de entorno, si no existen, usa un valor de respaldo
# Es CRÍTICO que RIOT_API_KEY y RIOT_API_KEY_2 (si se usa) estén configuradas en tu entorno de despliegue.
RIOT_API_KEY = os.environ.get("RIOT_API_KEY", "TU_API_KEY_PRINCIPAL_AQUI")
RIOT_API_KEY_2 = os.environ.get("RIOT_API_KEY_2", RIOT_API_KEY) # Usa la principal como fallback

if RIOT_API_KEY == "TU_API_KEY_PRINCIPAL_AQUI":
    print("ADVERTENCIA: RIOT_API_KEY no está configurada en las variables de entorno. La aplicación podría no funcionar correctamente.")

# URLs base de la API de Riot
# Todas las llamadas a la API de Riot (excepto DDragon) usarán esta URL base.
BASE_URL_RIOT = "https://euw1.api.riotgames.com"
BASE_URL_DDRAGON = "https://ddragon.leagueoflegends.com"

# Caché para almacenar los datos de los jugadores en la página principal
cache = {
    "datos_jugadores": [],
    "timestamp": 0
}
CACHE_TIMEOUT = 130  # Tiempo de vida de la caché en segundos (ej. 130 segundos = 2 minutos y 10 segundos)
cache_lock = threading.Lock() # Para proteger la caché en un entorno multihilo

# --- CONFIGURACIÓN DE SPLITS ---
# Define aquí los splits de la temporada.
# Las fechas deben ser objetos datetime.
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

# Clave del split activo para la aplicación
ACTIVE_SPLIT_KEY = "s15_split1" # Cambia esta variable para seleccionar el split activo.

# El timestamp de inicio se calcula automáticamente a partir del split activo
# Se convierte a milisegundos para la API de Riot
SEASON_START_TIMESTAMP = int(SPLITS[ACTIVE_SPLIT_KEY]["start_date"].timestamp()) * 1000

# Usar una sesión para reutilizar conexiones HTTP y mejorar el rendimiento
API_SESSION = requests.Session() 

# Variable global para la versión de Data Dragon
DDRAGON_VERSION = "14.10.1"  # Versión de respaldo por si falla la API

def make_api_request(url, retries=3, backoff_factor=0.5):
    """
    Realiza una petición a la API de Riot con reintentos y backoff exponencial.
    Utiliza una sesión de requests para reutilizar conexiones.
    """
    for i in range(retries):
        try:
            response = API_SESSION.get(url, timeout=10)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                print(f"Rate limit excedido. Esperando {retry_after} segundos...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status() # Lanza una excepción para errores HTTP (4xx o 5xx)
            return response
        except requests.exceptions.RequestException as e:
            print(f"Error en la petición a {url}: {e}. Intento {i + 1}/{retries}")
            if i < retries - 1:
                time.sleep(backoff_factor * (2 ** i))
    return None

def actualizar_version_ddragon():
    """Obtiene la última versión de Data Dragon y la guarda en una variable global."""
    global DDRAGON_VERSION
    try:
        url = f"{BASE_URL_DDRAGON}/api/versions.json"
        response = requests.get(url, timeout=5) # Usamos requests.get directamente para DDragon versions
        response.raise_for_status()
        DDRAGON_VERSION = response.json()[0]
        print(f"Versión de Data Dragon establecida a: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener la versión de Data Dragon: {e}. Usando versión de respaldo: {DDRAGON_VERSION}")
    except (IndexError, json.JSONDecodeError) as e:
        print(f"Error al procesar la respuesta de la versión de Data Dragon: {e}. Usando versión de respaldo: {DDRAGON_VERSION}")

# Se llama una vez al inicio para establecer la versión de DDragon
actualizar_version_ddragon()

# Cachés para datos de DDragon (campeones, runas, hechizos de invocador)
ALL_CHAMPIONS = {}
ALL_RUNES = {}
ALL_SUMMONER_SPELLS = {}

def obtener_todos_los_campeones():
    """Carga los datos de los campeones desde Data Dragon."""
    url_campeones = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    response = make_api_request(url_campeones)
    if response:
        try:
            # DDragon champion data has "key" as numerical ID (string) and "id" as champion name (string)
            # Mapeamos ID numérico (de la API de Riot) a nombre de campeón (para la URL de la imagen de DDragon)
            return {int(v['key']): v['id'] for k, v in response.json()['data'].items()}
        except (KeyError, ValueError) as e:
            print(f"Error al procesar datos de campeones de DDragon: {e}")
    return {}

def obtener_todas_las_runas():
    """Carga los datos de las runas desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/runesReforged.json"
    response = make_api_request(url)
    runes = {}
    if response:
        try:
            for tree in response.json():
                # Almacena la ruta del icono para el estilo de runa principal (ej. Precision, Domination)
                runes[tree['id']] = tree['icon']
                for slot in tree['slots']:
                    for perk in slot['runes']:
                        # Almacena la ruta del icono para las runas individuales (perks)
                        runes[perk['id']] = perk['icon']
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Error al procesar datos de runas de DDragon: {e}")
    return runes

def obtener_todos_los_hechizos():
    """Carga los datos de los hechizos de invocador desde Data Dragon."""
    url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
    response = make_api_request(url)
    spells = {}
    if response and 'data' in response.json():
        try:
            for k, v in response.json()['data'].items():
                # La API de Riot proporciona IDs de hechizos de invocador como enteros (ej. 4 para Flash).
                # DDragon usa un ID de cadena (ej. "SummonerFlash") para el archivo de imagen.
                # v['key'] es el ID numérico como cadena (ej. "4")
                # v['id'] es el nombre de la imagen de DDragon (ej. "SummonerFlash")
                spells[int(v['key'])] = v['id'] # Mapear ID numérico (int) a ID de imagen de DDragon (str)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"Error al procesar datos de hechizos de invocador de DDragon: {e}")
    return spells

def actualizar_ddragon_data():
    """Actualiza todos los datos de DDragon (campeones, runas, hechizos) en las variables globales."""
    global ALL_CHAMPIONS, ALL_RUNES, ALL_SUMMONER_SPELLS
    print("Actualizando datos de campeones, runas y hechizos de invocador de DDragon...")
    ALL_CHAMPIONS = obtener_todos_los_campeones()
    ALL_RUNES = obtener_todas_las_runas()
    ALL_SUMMONER_SPELLS = obtener_todos_los_hechizos()
    print("Datos de DDragon actualizados.")

# Cargar los datos de DDragon al inicio
actualizar_ddragon_data()

def obtener_nombre_campeon(champion_id):
    """Obtiene el nombre de un campeón dado su ID numérico."""
    # Usamos ALL_CHAMPIONS que ya está cargado con el mapeo correcto
    return ALL_CHAMPIONS.get(champion_id, "Desconocido")

def obtener_puuid(api_key, game_name, tag_line):
    """Obtiene el PUUID de un jugador dado su Riot ID (gameName y tagLine)."""
    # La API de cuentas (account-v1) es global, no regional.
    # El endpoint es https://<region>.api.riotgames.com/riot/account/v1/accounts/by-riot-id/<gameName>/<tagLine>
    # La región para esta API debe ser una de las regiones "maestras" (AMERICAS, ASIA, EUROPE).
    # Como estamos en EUW, usamos "europe" para la API de cuentas.
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el PUUID para {game_name}#{tag_line}.")
        return None

def obtener_id_invocador(api_key, puuid):
    """Obtiene el ID de invocador de un jugador dado su PUUID."""
    url = f"{BASE_URL_RIOT}/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el ID de invocador para {puuid}.")
        return None

def obtener_elo(api_key, puuid):
    """Obtiene la información de Elo de un jugador dado su PUUID."""
    url = f"{BASE_URL_RIOT}/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el Elo para {puuid}.")
        return None

def esta_en_partida(api_key, puuid):
    """
    Comprueba si un jugador está en una partida activa.
    Realiza un único intento sin reintentos adicionales para no bloquear.
    Devuelve el championId si está en partida, None en caso contrario.
    """
    url = f"{BASE_URL_RIOT}/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
    try:
        # Usamos API_SESSION.get directamente para esta API específica
        # ya que un 404 es una respuesta esperada (jugador no en partida)
        # y no queremos que make_api_request haga reintentos en ese caso.
        response = API_SESSION.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for participant in data.get("participants", []):
                if participant['puuid'] == puuid:
                    return participant.get('championId', None)
        elif response.status_code == 404:
            # Jugador no en partida, es un comportamiento esperado.
            return None
        else:
            print(f"Error al comprobar estado de partida para {puuid}: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        # Si hay un error de red, asumimos que no está en partida para no bloquear la actualización.
        print(f"Error de red al comprobar si el jugador {puuid} está en partida: {e}")
    return None

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida,
    además del nivel, hechizos y runas.
    """
    match_id, puuid, api_key = args
    # La API de Match V5 es global (europe, americas, asia), no regional (euw1).
    # Por lo tanto, usamos la URL base de Match API que es diferente.
    # Para EUW, la API de Match V5 usa el routing value 'europe'.
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
        # Ignoramos estas partidas para que no cuenten como derrotas y afecten al winrate.
        if any(p.get('gameEndedInEarlySurrender', False) for p in participants):
            return None

        game_end_timestamp = info.get('gameEndTimestamp', 0)
        
        for p in participants:
            if p.get('puuid') == puuid:
                # Extraer IDs de ítems, reemplazando None con 0
                items = [p.get(f'item{i}', 0) for i in range(0, 7)]

                # Obtener IDs de hechizos y runas
                spell1_id = p.get('summoner1Id')
                spell2_id = p.get('summoner2Id')
                
                # Obtener runas (perks)
                perks = p.get('perks', {})
                perk_main_id = None
                perk_sub_id = None

                if 'styles' in perks and len(perks['styles']) > 0:
                    # La primera entrada en 'styles' es la rama principal
                    if len(perks['styles'][0]['selections']) > 0:
                        # La primera selección en la rama principal es la runa clave (keystone)
                        perk_main_id = perks['styles'][0]['selections'][0]['perk']
                    # La segunda entrada en 'styles' es la rama secundaria
                    if len(perks['styles']) > 1:
                        perk_sub_id = perks['styles'][1]['style']

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
                    "perk_sub_id": ALL_RUNES.get(perk_sub_id)
                }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error procesando los detalles de la partida {match_id}: {e}")
    return None

def leer_cuentas(url):
    """Lee las cuentas de jugadores desde un archivo de texto alojado en GitHub."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        contenido = response.text.strip().split(';')
        cuentas = []
        for linea in contenido:
            partes = linea.split(',')
            if len(partes) == 2:
                riot_id = partes[0].strip()
                jugador = partes[1].strip()
                cuentas.append((riot_id, jugador))
        return cuentas
    except Exception as e:
        print(f"Error al leer las cuentas desde {url}: {e}")
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
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/peak_elo.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return True, resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Error leyendo peak elo desde {url}: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decodificando JSON de peak elo: {e}")
    return False, {}

def leer_puuids():
    """Lee el archivo de PUUIDs desde GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/puuids.json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print("El archivo puuids.json no existe en GitHub, se creará uno nuevo si es necesario.")
            return {}
        else:
            print(f"Error al leer puuids.json desde {url}: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error leyendo puuids.json: {e}")
    return {}

def guardar_archivo_github(file_path, content_dict, commit_message):
    """
    Función genérica para guardar o actualizar un archivo JSON en GitHub.
    `file_path` debe ser la ruta completa dentro del repositorio (ej. "puuids.json").
    `content_dict` es el diccionario Python a guardar.
    """
    url = f"https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/{file_path}"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(f"Token de GitHub no encontrado para guardar {file_path}. No se guardará el archivo.")
        return

    headers = {"Authorization": f"token {token}"}
    
    # Intentar obtener el SHA del archivo si existe
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"No se pudo obtener el SHA de {file_path}: {e}")

    contenido_json = json.dumps(content_dict, indent=2, ensure_ascii=False)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": commit_message, "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code in (200, 201):
            print(f"Archivo {file_path} actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar {file_path}: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error en la petición PUT a GitHub para {file_path}: {e}")

def guardar_puuids_en_github(puuid_dict):
    """Guarda o actualiza el archivo puuids.json en GitHub."""
    guardar_archivo_github("puuids.json", puuid_dict, "Actualizar PUUIDs")

def guardar_peak_elo_en_github(peak_elo_dict):
    """Guarda o actualiza el archivo peak_elo.json en GitHub."""
    guardar_archivo_github("peak_elo.json", peak_elo_dict, "Actualizar peak elo")

def leer_historial_jugador_github(puuid):
    """Lee el historial de partidas de un jugador desde GitHub."""
    url = f"https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/match_history/{puuid}.json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            return {"matches": []} # Devolver un diccionario con una lista vacía para 'matches'
        else:
            print(f"Error al leer el historial para {puuid}: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error leyendo el historial para {puuid}: {e}")
    return {"matches": []}

def guardar_historial_jugador_github(puuid, historial_data):
    """Guarda o actualiza el historial de partidas de un jugador en GitHub."""
    guardar_archivo_github(f"match_history/{puuid}.json", historial_data, f"Actualizar historial de partidas para {puuid}")

# Caché para estadísticas de campeones
CHAMPION_STATS_CACHE_TIMEOUT = 86400 # 24 horas (en segundos)

top_champion_stats_cache = {
    "data": {}, # {puuid: {queue_id: {stats: {}, timestamp: ..., total_games_snapshot: ...}}}
    "lock": threading.Lock()
}

def leer_top_champion_stats():
    """Lee el archivo de estadísticas de campeones desde GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/top_champion_stats.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Error leyendo top_champion_stats.json desde {url}: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decodificando JSON de top_champion_stats.json: {e}")
    return {}

def guardar_top_champion_stats_en_github(stats_dict):
    """Guarda o actualiza el archivo top_champion_stats.json en GitHub."""
    guardar_archivo_github("top_champion_stats.json", stats_dict, "Actualizar estadísticas de campeones")


def procesar_jugador(args_tuple):
    """
    Procesa los datos de un solo jugador.
    Implementa una lógica de actualización inteligente para reducir llamadas a la API.
    Solo actualiza el Elo si el jugador está o ha estado en partida recientemente.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list = args_tuple
    riot_id_full, jugador_nombre = cuenta
    game_name, tag_line = riot_id_full.split('#') # Ya validado el formato antes de llamar a procesar_jugador

    if not puuid:
        print(f"ADVERTENCIA: Omitiendo procesamiento para {riot_id_full} porque no se pudo obtener su PUUID.")
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
        for data in old_data_list:
            data['en_partida'] = False
            data['nombre_campeon'] = "N/A"
            data['champion_id'] = None
        return old_data_list

    elo_info = obtener_elo(api_key_main, puuid)
    if not elo_info: # Si falla la obtención de Elo, devolvemos los datos antiguos si existen
        return old_data_list if old_data_list else []

    riot_id_modified = riot_id_full.replace("#", "-")
    url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
    url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"
    
    datos_jugador_list = []
    for entry in elo_info:
        nombre_campeon_en_partida = obtener_nombre_campeon(champion_id) if champion_id else "N/A"
        datos_jugador = {
            "game_name": riot_id_full, # Mantener el formato original para mostrar
            "queue_type": entry.get('queueType', 'Desconocido'),
            "tier": entry.get('tier', 'Sin rango'),
            "rank": entry.get('rank', ''),
            "league_points": entry.get('leaguePoints', 0),
            "wins": entry.get('wins', 0),
            "losses": entry.get('losses', 0),
            "jugador": jugador_nombre, # Nombre corto para display
            "url_perfil": url_perfil,
            "puuid": puuid, # Añadir PUUID para futuras referencias
            "url_ingame": url_ingame,
            "en_partida": is_currently_in_game,
            "valor_clasificacion": calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('leaguePoints', 0)
            ),
            "nombre_campeon": nombre_campeon_en_partida,
            "champion_id": champion_id if champion_id else None, # Guardar el ID numérico
            "top_champion_stats": [] # Se rellenará después de la obtención de datos principales
        }
        datos_jugador_list.append(datos_jugador)
    return datos_jugador_list

def actualizar_cache():
    """
    Esta función realiza el trabajo pesado: obtiene todos los datos de la API
    y actualiza la caché global. Está diseñada para ser ejecutada en segundo plano.
    """
    print("Iniciando actualización de la caché principal de jugadores...")
    
    if RIOT_API_KEY == "TU_API_KEY_PRINCIPAL_AQUI":
        print("ERROR CRÍTICO: RIOT_API_KEY no está configurada. No se puede actualizar la caché principal.")
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

    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
    cuentas = leer_cuentas(url_cuentas)
    puuid_dict = leer_puuids()
    puuids_actualizados = False

    # Paso 1: Asegurarse de que todos los jugadores tienen un PUUID en el diccionario
    for riot_id_full, _ in cuentas:
        try:
            game_name, tag_line = riot_id_full.split('#')
        except ValueError:
            print(f"Formato de Riot ID incorrecto para '{riot_id_full}'. Debe ser 'GameName#TagLine'. Saltando la obtención de PUUID para este jugador.")
            continue

        if riot_id_full not in puuid_dict:
            print(f"No se encontró PUUID para {riot_id_full}. Obteniéndolo de la API...")
            puuid_info = obtener_puuid(RIOT_API_KEY, game_name, tag_line)
            if puuid_info and 'puuid' in puuid_info:
                puuid_dict[riot_id_full] = puuid_info['puuid']
                puuids_actualizados = True
            else:
                print(f"No se pudo obtener PUUID para {riot_id_full}. Se omitirá su procesamiento.")

    if puuids_actualizados:
        guardar_puuids_en_github(puuid_dict)

    # Paso 2: Procesar todos los jugadores en paralelo, pasando sus datos antiguos
    todos_los_datos = []
    tareas = []
    for cuenta in cuentas:
        riot_id_full = cuenta[0]
        puuid = puuid_dict.get(riot_id_full)
        # Solo añadir a tareas si tenemos un PUUID válido
        if puuid:
            old_data_for_player = old_data_map_by_puuid.get(puuid, []) # Asegurarse de que sea una lista
            tareas.append((cuenta, puuid, RIOT_API_KEY, RIOT_API_KEY_2, old_data_for_player))
        else:
            print(f"Omitiendo procesamiento de {riot_id_full} en caché principal debido a PUUID faltante.")

    # Asegurarse de que los campeones estén cargados antes de procesar jugadores
    global ALL_CHAMPIONS
    if not ALL_CHAMPIONS:
        actualizar_ddragon_data() # Reintentar cargar datos de DDragon
        if not ALL_CHAMPIONS:
            print("Error: No se pudieron cargar los datos de campeones para procesar jugadores.")
            # Si no se cargan, las funciones de nombre de campeón devolverán "Desconocido"

    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    # Paso 3: Inyectar los datos de campeones desde el caché (ya no se calculan aquí)
    with top_champion_stats_cache["lock"]:
        stats_data = top_champion_stats_cache["data"] # Leer directamente del caché en memoria
        queue_map_reverse = {420: "RANKED_SOLO_5x5", 440: "RANKED_FLEX_SR"} # Mapeo inverso
        
        for jugador in todos_los_datos:
            puuid = jugador.get('puuid')
            queue_type_str = jugador.get('queue_type')
            
            # Obtener el queue_id numérico para acceder al caché
            queue_id_num = next((qid for qid, qtype in queue_map_reverse.items() if qtype == queue_type_str), None)

            if puuid and queue_id_num:
                # Leemos el diccionario completo de estadísticas de campeones para este jugador y cola
                all_champ_stats = stats_data.get(puuid, {}).get(str(queue_id_num), {}).get('stats', {})
                
                # Calculamos el campeón top al vuelo para mostrarlo
                if all_champ_stats:
                    # Convertir claves a int para el max() y luego de vuelta a str si es necesario
                    # max() con key para encontrar el campeón con más partidas
                    top_champ_id_str = max(all_champ_stats, key=lambda k: all_champ_stats[k]['games'])
                    top_champ_data = all_champ_stats[top_champ_id_str]
                    
                    games = top_champ_data.get('games', 0)
                    wins = top_champ_data.get('wins', 0)
                    win_rate = (wins / games * 100) if games > 0 else 0
                    
                    # Calcular KDA para el top campeón
                    total_kills = top_champ_data.get('kills', 0)
                    total_deaths = top_champ_data.get('deaths', 0)
                    total_assists = top_champ_data.get('assists', 0)
                    kda = (total_kills + total_assists) / total_deaths if total_deaths > 0 else (total_kills + total_assists) # Si deaths es 0, KDA es Kills+Assists

                    jugador['top_champion_stats'] = {
                        "champion_name": obtener_nombre_campeon(int(top_champ_id_str)),
                        "win_rate": win_rate,
                        "games_played": games,
                        "kda": kda
                    }
                else:
                    jugador['top_champion_stats'] = {} # No hay estadísticas de campeón disponibles
            else:
                jugador['top_champion_stats'] = {} # No hay PUUID o Queue ID válido

    with cache_lock:
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()
    print("Actualización de la caché principal de jugadores completada.")

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
    
    # Si la caché está vacía (primera ejecución), indicamos que se está cargando.
    cargando = not datos_jugadores and timestamp == 0
    
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
    
    # Formatear la última actualización, sumando 2 horas para CEST (horario de verano de Europa Central)
    # Riot API timestamps son UTC. Si tu servidor está en UTC, esto lo ajusta a CEST.
    if timestamp > 0:
        ultima_actualizacion = (datetime.fromtimestamp(timestamp) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    else:
        ultima_actualizacion = "Calculando..." # Mensaje si aún no hay datos en caché

    return render_template('index.html', 
                           datos_jugadores=datos_jugadores, 
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION,
                           split_activo_nombre=split_activo_nombre,
                           cargando=cargando)

@app.route('/jugador/<path:game_name>') # Use <path:game_name> to handle '/' in Riot IDs
def perfil_jugador(game_name):
    """Muestra una página de perfil para un jugador específico."""
    todos_los_datos, _ = obtener_datos_jugadores()
    
    # Filtrar datos para el jugador específico
    datos_del_jugador = [j for j in todos_los_datos if j['game_name'] == game_name]
    
    if not datos_del_jugador:
        return render_template('404.html'), 404
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid') # Obtener el PUUID para el historial de partidas

    # Obtener historial de partidas
    historial_partidas = {}
    if puuid:
        historial_partidas = leer_historial_jugador_github(puuid)

    # Preparar partidas recientes para SoloQ y Flex
    # Filtrar partidas para el split activo actual
    soloq_matches = [
        m for m in historial_partidas.get('matches', [])
        if m.get('queue_id') == 420 and m.get('game_end_timestamp', 0) >= SEASON_START_TIMESTAMP
    ]
    flex_matches = [
        m for m in historial_partidas.get('matches', [])
        if m.get('queue_id') == 440 and m.get('game_end_timestamp', 0) >= SEASON_START_TIMESTAMP
    ]

    # Ordenar partidas por timestamp (las más recientes primero)
    soloq_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    flex_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    perfil = {
        'nombre': primer_perfil['jugador'],
        'game_name': game_name,
        'soloq': next((item for item in datos_del_jugador if item['queue_type'] == 'RANKED_SOLO_5x5'), None),
        'flex': next((item for item in datos_del_jugador if item['queue_type'] == 'RANKED_FLEX_SR'), None)
    }

    # Añadir partidas recientes a los datos del perfil si existen
    if perfil['soloq']:
        perfil['soloq']['recent_matches'] = soloq_matches[:5] # Pasar las 5 partidas más recientes
    if perfil['flex']:
        perfil['flex']['recent_matches'] = flex_matches[:5] # Pasar las 5 partidas más recientes
  
    return render_template('jugador.html', 
                           perfil=perfil, 
                           ddragon_version=DDRAGON_VERSION,
                           ALL_SUMMONER_SPELLS=ALL_SUMMONER_SPELLS, # Pasar hechizos para imágenes
                           ALL_RUNES=ALL_RUNES # Pasar runas para imágenes
                          )


def actualizar_historial_partidas_en_segundo_plano():
    """
    Función que se ejecuta en un hilo separado para actualizar el historial de partidas
    de todos los jugadores de forma periódica.
    También calcula y actualiza las estadísticas de campeones por cola.
    """
    print("Iniciando hilo de actualización de historial de partidas y estadísticas de campeones.")
    
    if RIOT_API_KEY == "TU_API_KEY_PRINCIPAL_AQUI":
        print("ERROR: RIOT_API_KEY no configurada. No se puede actualizar el historial de partidas.")
        return

    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"

    while True:
        try:
            # Asegurarse de que los datos de DDragon estén cargados
            if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                print("DDragon data no cargada en el hilo de historial. Intentando actualizar...")
                actualizar_ddragon_data()
                if not ALL_CHAMPIONS or not ALL_RUNES or not ALL_SUMMONER_SPELLS:
                    print("Error crítico: No se pudo cargar DDragon data. Saltando este ciclo de historial.")
                    time.sleep(300)
                    continue

            cuentas = leer_cuentas(url_cuentas)
            puuid_dict = leer_puuids()
            
            # Leer las estadísticas de campeones existentes para actualización incremental
            with top_champion_stats_cache["lock"]:
                current_top_champion_stats = top_champion_stats_cache["data"].copy()

            stats_actualizadas_en_ciclo = False

            for riot_id_full, jugador_nombre in cuentas:
                puuid = puuid_dict.get(riot_id_full)
                if not puuid:
                    print(f"Saltando actualización de historial para {riot_id_full}: PUUID no encontrado.")
                    continue

                historial_existente = leer_historial_jugador_github(puuid)
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}

                # Para cada jugador, obtener todos los IDs de partidas del split actual para ambas colas
                all_match_ids_for_player_in_split = []
                for queue_id in queue_map.values():
                    start_index = 0
                    while True:
                        url_matches = f"{BASE_URL_RIOT}/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime={SEASON_START_TIMESTAMP}&queue={queue_id}&start={start_index}&count=100&api_key={RIOT_API_KEY}"
                        response_matches = make_api_request(url_matches)
                        if not response_matches: break
                        match_ids_page = response_matches.json()
                        if not match_ids_page: break
                        all_match_ids_for_player_in_split.extend(match_ids_page)
                        if len(match_ids_page) < 100: break
                        start_index += 100
                
                # Filtrar para obtener solo los IDs de partidas nuevas
                nuevos_match_ids = [mid for mid in all_match_ids_for_player_in_split if mid not in ids_partidas_guardadas]

                if nuevos_match_ids:
                    print(f"Se encontraron {len(nuevos_match_ids)} partidas nuevas para {riot_id_full}. Procesando...")
                    tareas = [(match_id, puuid, RIOT_API_KEY) for match_id in nuevos_match_ids]
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))

                    nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]
                    if nuevas_partidas_validas:
                        historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                        historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'], reverse=True)
                        guardar_historial_jugador_github(puuid, historial_existente)
                        print(f"Historial de {riot_id_full} actualizado con {len(nuevas_partidas_validas)} partidas.")
                        stats_actualizadas_en_ciclo = True # Hubo cambios en el historial, potencialmente en stats

                # Ahora, recalcular las estadísticas de campeones para este jugador desde el historial completo del split
                # Esto se hace cada ciclo para asegurar precisión, o si el caché de stats ha expirado
                ahora = time.time()
                for q_type_str, q_id_num in queue_map.items():
                    # Comprobar si el caché de stats para esta cola ha expirado o si el número de partidas ha cambiado
                    entrada_cache_stats = current_top_champion_stats.get(puuid, {}).get(str(q_id_num), {})
                    total_games_cached = entrada_cache_stats.get("total_games_snapshot", 0)
                    
                    # Contar partidas actuales para esta cola y split
                    current_games_in_queue_split = sum(1 for p in historial_existente.get('matches', []) 
                                                       if p.get('queue_id') == q_id_num and 
                                                          p.get('game_end_timestamp', 0) >= SEASON_START_TIMESTAMP)

                    needs_stats_update = (
                        not entrada_cache_stats or
                        (ahora - entrada_cache_stats.get("timestamp", 0)) > CHAMPION_STATS_CACHE_TIMEOUT or
                        (current_games_in_queue_split > total_games_cached)
                    )

                    if needs_stats_update and current_games_in_queue_split > 0:
                        print(f"Recalculando estadísticas de campeón para {riot_id_full} en cola {q_type_str} (partidas: {current_games_in_queue_split}).")
                        
                        partidas_para_stats = [
                            p for p in historial_existente.get('matches', []) 
                            if p.get('queue_id') == q_id_num and
                               p.get('game_end_timestamp', 0) >= SEASON_START_TIMESTAMP
                        ]

                        champion_stats_for_queue = {}
                        for p in partidas_para_stats:
                            champ_name = p['champion_name']
                            champ_id_str = str(next((k for k, v in ALL_CHAMPIONS.items() if v == champ_name), None)) # Obtener ID numérico del campeón
                            
                            if champ_id_str not in champion_stats_for_queue:
                                champion_stats_for_queue[champ_id_str] = {"games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0}
                            
                            champion_stats_for_queue[champ_id_str]["games"] += 1
                            if p['win']:
                                champion_stats_for_queue[champ_id_str]["wins"] += 1
                            champion_stats_for_queue[champ_id_str]["kills"] += p.get('kills', 0)
                            champion_stats_for_queue[champ_id_str]["deaths"] += p.get('deaths', 0)
                            champion_stats_for_queue[champ_id_str]["assists"] += p.get('assists', 0)
                        
                        if puuid not in current_top_champion_stats:
                            current_top_champion_stats[puuid] = {}
                        
                        current_top_champion_stats[puuid][str(q_id_num)] = {
                            "stats": champion_stats_for_queue,
                            "timestamp": ahora,
                            "total_games_snapshot": current_games_in_queue_split
                        }
                        stats_actualizadas_en_ciclo = True
            
            if stats_actualizadas_en_ciclo:
                with top_champion_stats_cache["lock"]:
                    top_champion_stats_cache["data"] = current_top_champion_stats
                guardar_top_champion_stats_en_github(current_top_champion_stats)
                print("Estadísticas de campeones actualizadas y guardadas en GitHub.")

            print("Ciclo de actualización de historial de partidas y estadísticas completado. Próxima revisión en 5 minutos.")
            time.sleep(300) # Esperar 5 minutos para el siguiente ciclo

        except Exception as e:
            print(f"Error en el hilo de actualización de historial de partidas y estadísticas: {e}. Reintentando en 5 minutos.")
            time.sleep(300)

def keep_alive():
    """Envía una solicitud periódica a la propia aplicación para mantenerla activa en servicios como Render."""
    while True:
        try:
            # Asegúrate de que esta URL sea la de tu aplicación desplegada
            # Si no tienes una URL fija, puedes omitir esta función o configurarla dinámicamente
            requests.get('https://soloq-cerditos.onrender.com/', timeout=10)
            # print("Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"Error en keep_alive: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
    """Actualiza la caché de datos de los jugadores de forma periódica."""
    while True:
        actualizar_cache()
        time.sleep(CACHE_TIMEOUT)

if __name__ == "__main__":
    # Hilo para mantener la app activa en Render (opcional, si usas Render u otro servicio similar)
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()

    # Hilo para actualizar la caché principal de datos de jugadores
    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()

    # Hilo para la actualización del historial de partidas y estadísticas de campeones (puede ser intensivo en API calls)
    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()

    # Iniciar la aplicación Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)