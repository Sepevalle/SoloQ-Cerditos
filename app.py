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

# Caché para estadísticas de campeones
CHAMPION_STATS_CACHE_TIMEOUT = 86400 # 24 horas

top_champion_stats_cache = {
    "data": {},
    "lock": threading.Lock()
}

# Para proteger la caché en un entorno multihilo
cache_lock = threading.Lock()

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
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener PUUID: {response.status_code} - {response.text}")
        return None

def obtener_id_invocador(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener ID del invocador: {response.status_code} - {response.text}")
        return None

def obtener_elo(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener Elo: {response.status_code} - {response.text}")
        return None

def esta_en_partida(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        for participant in data.get("participants", []):
            if participant['puuid'] == puuid:
                return participant.get('championId', None)
    return None

def obtener_estadisticas_campeon_mas_jugado(api_key, puuid, queue_id, count=20):
    """
    Analiza las últimas 'count' partidas de una cola para encontrar el campeón más jugado
    y calcular su winrate y número de partidas.

    Args:
        api_key (str): Clave de la API de Riot.
        puuid (str): El PUUID del jugador.
        queue_id (int): ID de la cola (420 para SoloQ, 440 para Flex).
        count (int): Número de partidas a analizar.

    Returns:
        dict: Estadísticas del campeón más jugado, o un diccionario vacío si no hay datos.
    """
    url_matches = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue={queue_id}&start=0&count={count}&api_key={api_key}"
    try:
        response_matches = requests.get(url_matches, timeout=10)
        if response_matches.status_code != 200:
            if response_matches.status_code != 404:
                print(f"Error al obtener historial para {puuid} (cola {queue_id}): {response_matches.status_code}")
            return {}
        
        match_ids = response_matches.json()
        if not match_ids:
            return {}

        partidas_jugador = []
        for match_id in match_ids:
            time.sleep(0.07) # Pausa para no exceder rate limit
            url_match = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={api_key}"
            try:
                response_match = requests.get(url_match, timeout=5)
                if response_match.status_code == 200:
                    match_data = response_match.json()
                    for p in match_data['info']['participants']:
                        if p['puuid'] == puuid:
                            partidas_jugador.append({'champion': p['championName'], 'win': p['win']})
                            break
                elif response_match.status_code == 429:
                    print("Rate limit excedido, esperando...")
                    time.sleep(5)
            except requests.exceptions.RequestException:
                continue

        if not partidas_jugador:
            return {}

        # Encontrar el campeón más jugado
        contador_campeones = Counter(p['champion'] for p in partidas_jugador)
        campeon_mas_jugado, _ = contador_campeones.most_common(1)[0]

        # Filtrar partidas solo de ese campeón y calcular stats
        partidas_con_campeon = [p for p in partidas_jugador if p['champion'] == campeon_mas_jugado]
        wins = sum(1 for p in partidas_con_campeon if p['win'])
        total_partidas = len(partidas_con_campeon)
        winrate = (wins / total_partidas * 100) if total_partidas > 0 else 0

        return {"champion_name": campeon_mas_jugado, "win_rate": winrate, "games_played": total_partidas}

    except requests.exceptions.RequestException as e:
        print(f"Error de red obteniendo stats de campeones: {e}")
        return {}


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
    tierOrden = {
        "CHALLENGER": 7,
        "GRANDMASTER": 7,
        "MASTER": 7,
        "DIAMOND": 6,
        "EMERALD": 5,
        "PLATINUM": 4,
        "GOLD": 3,
        "SILVER": 2,
        "BRONZE": 1,
        "IRON": 0
    }

    rankOrden = {
        "I": 4,
        "II": 3,
        "III": 2,
        "IV": 1
    }

    tierValue = tierOrden.get(tier.upper(), 0)
    rankValue = rankOrden.get(rank, 0)

    return (tierValue * 400 + rankValue * 100 + league_points - 100)

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

def leer_top_champion_stats():
    """Lee el archivo de estadísticas de campeones desde GitHub."""
    url = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/top_champion_stats.json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            print("El archivo top_champion_stats.json no existe, se creará uno nuevo.")
            return {}
    except Exception as e:
        print(f"Error leyendo top_champion_stats.json: {e}")
    return {}

def guardar_top_champion_stats_en_github(stats_dict):
    """Guarda o actualiza el archivo top_champion_stats.json en GitHub."""
    url = "https://api.github.com/repos/Sepevalle/SoloQ-Cerditos/contents/top_champion_stats.json"
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Token de GitHub no encontrado para guardar top_champion_stats.json.")
        return

    headers = {"Authorization": f"token {token}"}
    
    sha = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json().get('sha')
    except Exception as e:
        print(f"No se pudo obtener el SHA de top_champion_stats.json: {e}")

    contenido_json = json.dumps(stats_dict, indent=2)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": "Actualizar estadísticas de campeones", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code in (200, 201):
            print("Archivo top_champion_stats.json actualizado correctamente en GitHub.")
        else:
            print(f"Error al actualizar top_champion_stats.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error en la petición PUT a GitHub para top_champion_stats.json: {e}")

# FUNCIÓN MODIFICADA
def procesar_jugador(args):
    """Procesa los datos de un solo jugador usando su PUUID."""
    cuenta, puuid, api_key = args
    riot_id, jugador_nombre = cuenta

    if not puuid:
        print(f"ADVERTENCIA: Omitiendo procesamiento para {riot_id} porque no se pudo obtener su PUUID. Revisa que el Riot ID sea correcto en cuentas.txt.")
        return []

    # Llamadas a la API para datos en tiempo real
    elo_info = obtener_elo(api_key, puuid)
    champion_id = esta_en_partida(api_key, puuid)

    if not elo_info:
        return []

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
            "en_partida": champion_id is not None,
            "valor_clasificacion": calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('league_points', 0)
            ),
            "nombre_campeon": nombre_campeon,
            "champion_id": champion_id if champion_id else "Desconocido",
            "top_champion_stats": {} # Placeholder, se rellenará desde el caché
        }
        datos_jugador_list.append(datos_jugador)
    return datos_jugador_list

def actualizar_cache():
    """
    Esta función realiza el trabajo pesado: obtiene todos los datos de la API
    y actualiza la caché global. Está diseñada para ser ejecutada en segundo plano.
    """
    print("Iniciando actualización de la caché...")
    api_key = os.environ.get('RIOT_API_KEY', 'RIOT_API_KEY')
    url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
    cuentas = leer_cuentas(url_cuentas)
    puuid_dict = leer_puuids()
    puuids_actualizados = False

    # Paso 1: Asegurarse de que todos los jugadores tienen un PUUID en el diccionario
    for riot_id, _ in cuentas:
        if riot_id not in puuid_dict:
            print(f"No se encontró PUUID para {riot_id}. Obteniéndolo de la API...")
            puuid_info = obtener_puuid(api_key, riot_id.split('#')[0], riot_id.split('#')[-1])
            if puuid_info and 'puuid' in puuid_info:
                puuid_dict[riot_id] = puuid_info['puuid']
                puuids_actualizados = True

    if puuids_actualizados:
        guardar_puuids_en_github(puuid_dict)

    # Paso 2: Procesar todos los jugadores en paralelo con sus PUUIDs ya conocidos
    todos_los_datos = []
    tareas = [(cuenta, puuid_dict.get(cuenta[0]), api_key) for cuenta in cuentas]

    with ThreadPoolExecutor(max_workers=10) as executor:
        resultados = executor.map(procesar_jugador, tareas)

    for datos_jugador_list in resultados:
        if datos_jugador_list:
            todos_los_datos.extend(datos_jugador_list)

    # Paso 3: Obtener y cachear estadísticas de campeones más jugados
    with top_champion_stats_cache["lock"]:
        stats_data = leer_top_champion_stats()
        stats_actualizadas = False
        queue_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}

        jugadores_a_revisar = {(jugador['puuid'], jugador['queue_type']) for jugador in todos_los_datos if jugador['queue_type'] in queue_map}

        for puuid, queue_type in jugadores_a_revisar:
            queue_id = str(queue_map[queue_type])
            ahora = time.time()
            
            entrada_cache = stats_data.get(puuid, {}).get(queue_id, {})
            if not entrada_cache or (ahora - entrada_cache.get("timestamp", 0)) > CHAMPION_STATS_CACHE_TIMEOUT:
                print(f"Actualizando stats de campeón para PUUID {puuid[:8]} en cola {queue_type}...")
                nuevas_stats = obtener_estadisticas_campeon_mas_jugado(api_key, puuid, int(queue_id))
                
                if nuevas_stats:
                    if puuid not in stats_data:
                        stats_data[puuid] = {}
                    
                    stats_data[puuid][queue_id] = {
                        "stats": nuevas_stats,
                        "timestamp": ahora
                    }
                    stats_actualizadas = True

        if stats_actualizadas:
            guardar_top_champion_stats_en_github(stats_data)

        # Paso 4: Inyectar los datos de campeones en la lista de jugadores
        for jugador in todos_los_datos:
            queue_id_str = str(queue_map.get(jugador['queue_type']))
            if queue_id_str:
                stats_campeon = stats_data.get(jugador['puuid'], {}).get(queue_id_str, {}).get('stats', {})
                jugador['top_champion_stats'] = stats_campeon

    with cache_lock:
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()
    print("Actualización de la caché completada.")

def obtener_datos_jugadores():
    with cache_lock:
        return cache.get('datos_jugadores', []), cache.get('timestamp', 0)

def get_peak_elo_key(jugador):
    # Usar PUUID como clave única para evitar problemas con cambios de nombre
    return f"{jugador['queue_type']}|{jugador['puuid']}"

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

    ultima_actualizacion = (datetime.fromtimestamp(timestamp) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    
    
    return render_template('index.html', datos_jugadores=datos_jugadores, ultima_actualizacion=ultima_actualizacion, ddragon_version=DDRAGON_VERSION)

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

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
