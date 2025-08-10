import requests
import os
import time
import threading
import json
import base64
from datetime import datetime, timedelta, timezone
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from services.riot_api import *

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": [],
    "timestamp": 0
}
CACHE_TIMEOUT = 130  # 2 minutos
cache_lock = threading.Lock()

# Global storage for LP tracking
player_in_game_lp = {}
player_in_game_lp_lock = threading.Lock()

pending_lp_updates = {}
pending_lp_updates_lock = threading.Lock()


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
        resp = requests.get(url, timeout=30)
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
        resp = requests.get(url, timeout=30)
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
        response = requests.get(url, headers=headers, timeout=30)
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
        response = requests.put(url, headers=headers, json=data, timeout=30)
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
        response = requests.get(url, headers=headers, timeout=30)
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
            timeout=30
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
        resp = requests.get(url, timeout=30)
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
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            sha = response.json().get('sha')
            print(f"[guardar_historial_jugador_github] SHA del historial de {puuid} obtenido: {sha}.")
        elif response.status_code == 404:
            print(f"[guardar_historial_jugador_github] Archivo {puuid}.json no existe en GitHub, se creará uno nuevo.")
        else:
            print(f"[guardar_historial_jugador_github] Error al obtener SHA del historial de {puuid}: {response.status_code} - {response.text}")
            return
    except Exception as e:
        print(f"[guardar_historial_jugador_github] Excepción al obtener SHA del historial de {puuid}: {e}")
        return

    contenido_json = json.dumps(historial_data, indent=2, ensure_ascii=False)
    contenido_b64 = base64.b64encode(contenido_json.encode('utf-8')).decode('utf-8')
    
    data = {"message": f"Actualizar historial de partidas para {puuid}", "content": contenido_b64, "branch": "main"}
    if sha:
        data["sha"] = sha

    try:
        print(f"[guardar_historial_jugador_github] Intentando guardar historial para {puuid} en GitHub. SHA: {sha}")
        response = requests.put(url, headers=headers, json=data, timeout=30)
        if response.status_code in (200, 201):
            print(f"[guardar_historial_jugador_github] Historial de {puuid}.json actualizado correctamente en GitHub. Status: {response.status_code}")
        else:
            print(f"[guardar_historial_jugador_github] ERROR: Fallo al actualizar historial de {puuid}.json: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[guardar_historial_jugador_github] ERROR: Excepción en la petición PUT a GitHub para el historial de {puuid}: {e}")

def _calculate_lp_change_for_player(puuid, queue_type_api_name, all_matches_for_player):
    """
    Calcula el cambio total de LP para un jugador en una cola específica en las últimas 24 horas.
    """
    now_utc = datetime.now(timezone.utc)
    one_day_ago_utc = now_utc - timedelta(days=1)
    one_day_ago_timestamp_ms = int(one_day_ago_utc.timestamp() * 1000)
    
    lp_change_24h = 0
    
    queue_id_map = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
    target_queue_id = queue_id_map.get(queue_type_api_name)

    if not target_queue_id:
        print(f"[_calculate_lp_change_for_player] Tipo de cola '{queue_type_api_name}' no reconocido. Retornando 0 LP.")
        return 0

    for match in all_matches_for_player:
        match_timestamp_utc = match.get('game_end_timestamp', 0)
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

    elo_info = obtener_elo(api_key_main, puuid)
    if not elo_info:
        print(f"[procesar_jugador] No se pudo obtener el Elo para {riot_id}. No se puede rastrear LP ni actualizar datos.")
        return old_data_list if old_data_list else []

    game_data = esta_en_partida(api_key_spectator, puuid)
    is_currently_in_game = game_data is not None

    with player_in_game_lp_lock:
        if is_currently_in_game:
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

        keys_to_remove_from_in_game = []
        for lp_tracking_key, pre_game_data in player_in_game_lp.items():
            tracked_puuid, tracked_queue_type = lp_tracking_key
            if tracked_puuid == puuid and not is_currently_in_game:
                print(f"[{riot_id}] [LP Tracker] Jugador {riot_id} (cola {tracked_queue_type}) terminó una partida. Moviendo a actualizaciones pendientes.")
                with pending_lp_updates_lock:
                    pending_lp_updates[lp_tracking_key] = {
                        'pre_game_valor_clasificacion': pre_game_data['pre_game_valor_clasificacion'],
                        'detection_timestamp': time.time(),
                        'riot_id': riot_id,
                        'queue_type': tracked_queue_type
                    }
                keys_to_remove_from_in_game.append(lp_tracking_key)
        
        for key in keys_to_remove_from_in_game:
            del player_in_game_lp[key]

    was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
    
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
            "puuid": puuid,
            "url_ingame": url_ingame,
            "en_partida": is_currently_in_game,
            "valor_clasificacion": calcular_valor_clasificacion(
                entry.get('tier', 'Sin rango'),
                entry.get('rank', ''),
                entry.get('leaguePoints', 0)
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

        if queue_type == "RANKED_SOLO_5x5":
            jugador['lp_change_24h'] = historial.get('soloq_lp_change_24h', 0)
        elif queue_type == "RANKED_FLEX_SR":
            jugador['lp_change_24h'] = historial.get('flexq_lp_change_24h', 0)
        else:
            jugador['lp_change_24h'] = 0

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

    current_streak_type = 'win' if partidas[0].get('win') else 'loss'
    current_streak_count = 0
    for partida in partidas:
        is_win = partida.get('win')
        if (is_win and current_streak_type == 'win') or (not is_win and current_streak_type == 'loss'):
            current_streak_count += 1
        else:
            break

    final_current_win_streak = current_streak_count if current_streak_type == 'win' else 0
    final_current_loss_streak = current_streak_count if current_streak_type == 'loss' else 0

    print(f"[calcular_rachas] Rachas calculadas: Max V: {max_win_streak}, Max D: {max_loss_streak}, Actual: {final_current_win_streak}V/{final_current_loss_streak}D.")
    return {'max_win_streak': max_win_streak, 'max_loss_streak': max_loss_streak, 'current_win_streak': final_current_win_streak, 'current_loss_streak': final_current_loss_streak}
