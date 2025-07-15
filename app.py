from flask import Flask, render_template
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
        url = "https://ddragon.leagueoflegends.com/api/versions.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            DDRAGON_VERSION = response.json()[0]
            print(f"Versión de Data Dragon establecida a: {DDRAGON_VERSION}")
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener la versión de Data Dragon: {e}. Usando versión de respaldo: {DDRAGON_VERSION}")

actualizar_version_ddragon()

def cargar_campeones():
    url_campeones = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
    response = requests.get(url_campeones)
    if response.status_code == 200:
        campeones = response.json()["data"]
        return {int(campeon["key"]): campeon["id"] for campeon in campeones.values()}
    else:
        print(f"Error al cargar campeones: {response.status_code}")
        return {}

campeones = cargar_campeones()

def obtener_nombre_campeon(champion_id):
    return campeones.get(champion_id, "Desconocido")

def obtener_puuid(api_key, riot_id, region):
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el PUUID para {riot_id} después de varios intentos.")
        return None

def obtener_id_invocador(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el ID de invocador para {puuid}.")
        return None

def obtener_elo(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url)
    if response:
        return response.json()
    else:
        print(f"No se pudo obtener el Elo para {puuid}.")
        return None

def esta_en_partida(api_key, puuid):
    """Comprueba si un jugador está en una partida activa. Realiza un único intento."""
    url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
    try:
        # Hacemos una única petición directa, sin reintentos.
        # Un 404 es el resultado esperado si el jugador no está en partida.
        response = API_SESSION.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for participant in data.get("participants", []):
                if participant['puuid'] == puuid:
                    return participant.get('championId', None)
    except requests.exceptions.RequestException as e:
        # Si hay un error de red, asumimos que no está en partida para no bloquear la actualización.
        print(f"Error de red al comprobar si el jugador {puuid} está en partida: {e}")
    return None

def obtener_info_partida(args):
    """
    Función auxiliar para ThreadPoolExecutor. Obtiene el campeón jugado y el resultado de una partida.
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
        # Ignoramos estas partidas para que no cuenten como derrotas y afecten al winrate.
        if any(p.get('gameEndedInEarlySurrender', False) for p in participants):
            print(f"Partida {match_id} ignorada por ser un remake.")
            return None

        game_end_timestamp = info.get('gameEndTimestamp', 0)
        
        for p in participants:
            if p.get('puuid') == puuid:
                return {
                    "match_id": match_id,
                    "champion_name": p.get('championName', 'Desconocido'),
                    "win": p.get('win', False),
                    "kills": p.get('kills', 0),
                    "deaths": p.get('deaths', 0),
                    "assists": p.get('assists', 0),
                    "items": [p.get(f'item{i}', 0) for i in range(7)],  # Obtener items
                    "game_end_timestamp": game_end_timestamp,
                    "queue_id": info.get('queueId')
                }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error procesando los detalles de la partida {match_id}: {e}")
    return None

def leer_cuentas(url):
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
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/peak_elo.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado")
        return

    # Obtener el contenido actual del archivo para el SHA
    try:
        response = requests.get(url, headers={"Authorization": f"token {token}"})
        if response.status_code == 200:
            contenido_actual = response.json()
            sha = contenido_actual['sha']
        else:
            print(f"Error al obtener el archivo: {response.status_code}")
            return
    except Exception as e:
        print(f"Error al obtener el archivo: {e}")
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
            print("Archivo actualizado correctamente en GitHub")
        else:
            print(f"Error al actualizar el archivo: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error al actualizar el archivo: {e}")

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
            print("Archivo top_champion_stats.json actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar top_champion_stats.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error en la petición PUT a GitHub para el historial de {puuid}: {e}")

# FUNCIÓN MODIFICADA
def procesar_jugador(args_tuple):
    """
    Procesa los datos de un solo jugador.
    Implementa una lógica de actualización inteligente para reducir llamadas a la API.
    Solo actualiza el Elo si el jugador está o ha estado en partida recientemente.
    """
    cuenta, puuid, api_key_main, api_key_spectator, old_data_list = args_tuple
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

def obtener_historial_partidas(puuid, queue_map, api_key):
    """Obtiene el historial de partidas completo para un jugador y colas específicas."""
    historial_completo = []
    for queue_id in queue_map.values():
        start_index = 0
        while True:
            url_matches = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime={SEASON_START_TIMESTAMP}&queue={queue_id}&start={start_index}&count=100&api_key={api_key}"
            response_matches = make_api_request(url_matches)
            if not response_matches:
                break
            match_ids = response_matches.json()
            if not match_ids:
                break
            historial_completo.extend(match_ids)
            if len(match_ids) < 100:
                break
            start_index += 100
    return historial_completo
                historial_existente = leer_historial_jugador_github(puuid)
                ids_partidas_guardadas = {p['match_id'] for p in historial_existente.get('matches', [])}

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
                
                # 3. Filtrar para obtener solo los IDs de partidas nuevas
                nuevos_match_ids = [mid for mid in all_match_ids_season if mid not in ids_partidas_guardadas]

                if not nuevos_match_ids:
                    print(f"No hay partidas nuevas para {riot_id}. Omitiendo.")
                    continue

                print(f"Se encontraron {len(nuevos_match_ids)} partidas nuevas para {riot_id}. Procesando...")

                # 4. Procesar solo las partidas nuevas en paralelo
                tareas = [(match_id, puuid, api_key) for match_id in nuevos_match_ids]
                with ThreadPoolExecutor(max_workers=10) as executor:
                    nuevas_partidas_info = list(executor.map(obtener_info_partida, tareas))

                # 5. Añadir las nuevas partidas al historial y guardar
                nuevas_partidas_validas = [p for p in nuevas_partidas_info if p is not None]
                if nuevas_partidas_validas:
                    historial_existente.setdefault('matches', []).extend(nuevas_partidas_validas)
                    # Opcional: ordenar por fecha
                    historial_existente['matches'].sort(key=lambda x: x['game_end_timestamp'], reverse=True)
                    guardar_historial_jugador_github(puuid, historial_existente)
                    print(f"Historial de {riot_id} actualizado con {len(nuevas_partidas_validas)} partidas.")

            print("Ciclo de actualización de historial completado. Próxima revisión en 5 minutos.")
            time.sleep(300) # Esperar 5 minutos para el siguiente ciclo

        except Exception as e:
            print(f"Error en el hilo de actualización de estadísticas: {e}. Reintentando en 5 minutos.")
            time.sleep(300)

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
    puuid_dict = leer_puuids()
    puuids_actualizados = False

    # Paso 1: Asegurarse de que todos los jugadores tienen un PUUID en el diccionario
    for riot_id, _ in cuentas:
        if riot_id not in puuid_dict:
            print(f"No se encontró PUUID para {riot_id}. Obteniéndolo de la API...")
            puuid_info = obtener_puuid(api_key_main, riot_id.split('#')[0], riot_id.split('#')[-1])
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
        tareas.append((cuenta, puuid, api_key_main, api_key_spectator, old_data_for_player))

    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    # Paso 3: Calcular y añadir estadísticas del campeón más jugado desde el historial
    queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    for jugador in todos_los_datos:
        puuid = jugador.get('puuid')
        queue_type = jugador.get('queue_type')
        queue_id = queue_map.get(queue_type)

        jugador['top_champion_stats'] = {} # Inicializar por si no hay datos

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

        # Contar campeones para encontrar el más jugado
        contador_campeones = Counter(p['champion_name'] for p in partidas_jugador)
        if not contador_campeones:
            continue
        
        campeon_mas_jugado, _ = contador_campeones.most_common(1)[0]

        # Calcular stats para ese campeón
        partidas_del_campeon = [p for p in partidas_jugador if p['champion_name'] == campeon_mas_jugado]
        
        total_partidas = len(partidas_del_campeon)
        wins = sum(1 for p in partidas_del_campeon if p.get('win'))
        win_rate = (wins / total_partidas * 100) if total_partidas > 0 else 0

        total_kills = sum(p.get('kills', 0) for p in partidas_del_campeon)
        total_deaths = sum(p.get('deaths', 0) for p in partidas_del_campeon)
        total_assists = sum(p.get('assists', 0) for p in partidas_del_campeon)
        
        # Evitar división por cero para el KDA
        kda = (total_kills + total_assists) / total_deaths if total_deaths > 0 else float(total_kills + total_assists)

        jugador['top_champion_stats'] = {
            "champion_name": campeon_mas_jugado,
            "win_rate": win_rate,
            "games_played": total_partidas,
            "kda": kda
        }

    with cache_lock:
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()
    print("Actualización de la caché completada.")

def obtener_datos_jugadores():
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    # Clave para el peak ELO usando el nombre del jugador y su Riot ID
    return f"{jugador['queue_type']}|{jugador['jugador']}|{jugador['game_name']}"

@app.route('/')
def index():
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

def obtener_datos_jugadores():
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    # Clave para el peak ELO usando el nombre del jugador y su Riot ID
    return f"{jugador['queue_type']}|{jugador['jugador']}|{jugador['game_name']}"

@app.route('/jugador/<game_name>')
def perfil_jugador(game_name):
    """Muestra una página de perfil para un jugador específico."""
    todos_los_datos, _ = obtener_datos_jugadores()
    
    # Filtrar los datos para el jugador específico
    datos_del_jugador = [j for j in todos_los_datos if j['game_name'] == game_name]
    
    if not datos_del_jugador:
        return render_template('404.html'), 404

    # Suponiendo que el game_name es único, tomamos el primer elemento.
    # Si no es único, podrías querer refinar la lógica.
    primer_perfil = datos_del_jugador[0]  # Acceder al primer elemento directamente
    
    perfil = {
        'nombre': primer_perfil['jugador'],  # 'nombre' ahora se refiere al nombre real del jugador
        'game_name': game_name,  # Asignar game_name al perfil
        'soloq': next((item for item in datos_del_jugador if item['queue_type'] == 'RANKED_SOLO_5x5'), None),
        'flex': next((item for item in datos_del_jugador if item['queue_type'] == 'RANKED_FLEX_SR'), None)
    }
    
    return render_template('jugador.html', perfil=perfil, ddragon_version=DDRAGON_VERSION)


def keep_alive():
    while True:
        try:
            requests.get('https://soloq-cerditos.onrender.com/')
            print("Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
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