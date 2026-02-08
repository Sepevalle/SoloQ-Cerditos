# services/lp_tracker.py

"""
Este módulo se encarga de realizar un seguimiento periódico del ELO de los jugadores.
"""
import os
import time
import json
import base64
import requests
from datetime import datetime, timezone

# --- CONFIGURACIÓN ---
LP_HISTORY_FILE_PATH = "lp_history.json"
ACCOUNTS_FILE_PATH = "cuentas.txt"
PUUIDS_FILE_PATH = "puuids.json"
REPO_OWNER = "Sepevalle"
REPO_NAME = "SoloQ-Cerditos"
GITHUB_API_BASE_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/"

# --- FUNCIONES DE UTILIDAD DE GITHUB ---

def _read_cuentas_from_github(token):
    """Lee y parsea el archivo cuentas.txt desde GitHub."""
    url = f"{GITHUB_API_BASE_URL}{ACCOUNTS_FILE_PATH}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            content = response.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            contenido = file_content.strip().split(';')
            cuentas = []
            for linea in contenido:
                partes = linea.split(',')
                if len(partes) == 2:
                    riot_id = partes[0].strip()
                    jugador = partes[1].strip()
                    cuentas.append((riot_id, jugador))
            return cuentas
        else:
            print(f"[LP_TRACKER] Error al leer {ACCOUNTS_FILE_PATH} desde GitHub: {response.status_code}")
            response.raise_for_status()
    except Exception as e:
        print(f"[LP_TRACKER] Excepción al leer {ACCOUNTS_FILE_PATH} de GitHub: {e}")
    return []

def _read_json_from_github(file_path, token):
    """Lee un archivo JSON desde el repositorio de GitHub."""
    url = f"{GITHUB_API_BASE_URL}{file_path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            content = resp.json()
            file_content = base64.b64decode(content['content']).decode('utf-8')
            # Manejar archivo vacío
            if not file_content or not file_content.strip():
                print(f"[LP_TRACKER] Archivo {file_path} está vacío. Inicializando...")
                return {}, content.get('sha')
            try:
                return json.loads(file_content), content.get('sha')
            except json.JSONDecodeError:
                print(f"[LP_TRACKER] Archivo {file_path} tiene JSON inválido. Inicializando...")
                return {}, content.get('sha')
        elif resp.status_code == 404:
            print(f"[LP_TRACKER] Archivo no encontrado en GitHub: {file_path}. Se creará uno nuevo.")
            return {}, None
        else:
            print(f"[LP_TRACKER] Error al leer {file_path} desde GitHub: {resp.status_code}")
            return {}, None
    except Exception as e:
        print(f"[LP_TRACKER] Excepción al leer {file_path} de GitHub: {e}")
    return {}, None


def _write_to_github(file_path, data, sha, token):
    """Escribe o actualiza un archivo en el repositorio de GitHub."""
    url = f"{GITHUB_API_BASE_URL}{file_path}"
    headers = {"Authorization": f"token {token}"}
    
    content_json = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content_json.encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": f"Actualizar {file_path}",
        "content": content_b64,
        "branch": "main"
    }
    
    # Solo incluir SHA si existe (para actualizar archivo existente)
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            print(f"[LP_TRACKER] Archivo {file_path} actualizado correctamente en GitHub.")
            return True
        elif response.status_code == 422:
            # Error 422: intentar sin SHA (crear nuevo archivo)
            print(f"[LP_TRACKER] Error 422, intentando crear archivo nuevo...")
            if "sha" in payload:
                del payload["sha"]
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            if response.status_code in (200, 201):
                print(f"[LP_TRACKER] Archivo {file_path} creado correctamente en GitHub.")
                return True
            
            # Si sigue fallando, intentar obtener SHA fresco
            print(f"[LP_TRACKER] Reintentando con SHA actualizado...")
            _, fresh_sha = _read_json_from_github(file_path, token)
            if fresh_sha:
                payload["sha"] = fresh_sha
                response = requests.put(url, headers=headers, json=payload, timeout=30)
                if response.status_code in (200, 201):
                    print(f"[LP_TRACKER] Archivo {file_path} actualizado con SHA fresco.")
                    return True
            
            print(f"[LP_TRACKER] Error al actualizar {file_path}: {response.status_code} - {response.text}")
            return False
        else:
            print(f"[LP_TRACKER] Error al actualizar {file_path}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[LP_TRACKER] Excepción al escribir en GitHub para {file_path}: {e}")
        return False




# --- LÓGICA DE LA API DE RIOT (SIMPLIFICADA) ---

class RateLimiter:
    def __init__(self, rate_per_second, burst_limit):
        self.rate_per_second = rate_per_second
        self.burst_limit = burst_limit
        self.tokens = burst_limit
        self.last_refill_time = time.time()

    def consume_token(self):
        now = time.time()
        time_elapsed = now - self.last_refill_time
        self.tokens = min(self.burst_limit, self.tokens + time_elapsed * self.rate_per_second)
        self.last_refill_time = now
        
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

# Límites modestos para no interferir con la app principal
riot_api_limiter = RateLimiter(rate_per_second=1, burst_limit=10)

def make_api_request(url, api_key):
    """Realiza una petición a la API de Riot respetando el rate limit."""
    while not riot_api_limiter.consume_token():
        time.sleep(0.1)
    
    headers = {"X-Riot-Token": api_key}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 2))
            print(f"[LP_TRACKER] Rate limit excedido. Esperando {retry_after} segundos...")
            time.sleep(retry_after)
            return make_api_request(url, api_key) # Reintentar
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        print(f"[LP_TRACKER] Error en la petición a {url}: {e}")
        return None

def obtener_elo(api_key, puuid):
    """Obtiene la información de Elo de un jugador."""
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={api_key}"
    response = make_api_request(url, api_key)
    if response:
        return response.json()
    return None

def calcular_valor_clasificacion(tier, rank, league_points):
    """Calcula un valor numérico para la clasificación."""
    if tier.upper() in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        return 2800 + league_points
    tierOrden = {"DIAMOND": 6, "EMERALD": 5, "PLATINUM": 4, "GOLD": 3, "SILVER": 2, "BRONZE": 1, "IRON": 0}
    rankOrden = {"I": 3, "II": 2, "III": 1, "IV": 0}
    valor_base_tier = tierOrden.get(tier.upper(), 0) * 400
    valor_division = rankOrden.get(rank, 0) * 100
    return valor_base_tier + valor_division + league_points

# --- WORKER PRINCIPAL ---

def elo_tracker_worker(riot_api_key, github_token):
    """
    Worker que se ejecuta periódicamente para tomar 'snapshots' del ELO de los jugadores.
    
    MEJORADO PARA RENDER FREE:
    - Ejecuta cada 30 minutos (en lugar de 5) para reducir consumo de API
    - Evita snapshots duplicados - solo guarda si hay cambio de ELO
    - Detecta cambios reales en ELO antes de guardar
    - No guarda en GitHub si no hay cambios (ahorra writes)
    """
    print("[LP_TRACKER] Iniciando el worker de seguimiento de ELO...")
    print("[LP_TRACKER] OPTIMIZACIÓN RENDER: Se ejecutará cada 30 minutos para reducir consumo de API")
    while True:
        try:
            print(f"[{datetime.now()}] [LP_TRACKER] Iniciando snapshot de ELO...")
            
            # 1. Leer cuentas y PUUIDs
            cuentas = _read_cuentas_from_github(github_token)
            puuids_data, _ = _read_json_from_github(PUUIDS_FILE_PATH, github_token)

            if not cuentas or not puuids_data:
                print("[LP_TRACKER] No se pudieron leer las cuentas o los PUUIDs. Saltando este ciclo.")
                time.sleep(600)
                continue

            # 2. Leer historial de LP existente
            lp_history, lp_history_sha = _read_json_from_github(LP_HISTORY_FILE_PATH, github_token)

            # 3. Iterar sobre los jugadores y actualizar su historial de LP
            snapshots_added = 0
            snapshots_skipped = 0
            
            for riot_id, player_name in cuentas:
                puuid = puuids_data.get(riot_id)
                if not puuid:
                    print(f"[LP_TRACKER] PUUID no encontrado para {riot_id}. Saltando.")
                    continue

                print(f"[LP_TRACKER] Procesando ELO para {riot_id} ({player_name})...")
                elo_info = obtener_elo(riot_api_key, puuid)
                if not elo_info:
                    print(f"[LP_TRACKER] No se pudo obtener ELO para {riot_id}.")
                    continue

                if puuid not in lp_history:
                    lp_history[puuid] = {"RANKED_SOLO_5x5": [], "RANKED_FLEX_SR": []}

                timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

                for entry in elo_info:
                    queue_type = entry.get('queueType')
                    if queue_type in ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]:
                        valor = calcular_valor_clasificacion(
                            entry.get('tier', 'Sin rango'),
                            entry.get('rank', ''),
                            entry.get('leaguePoints', 0)
                        )
                        
                        # Obtener el último snapshot de esta cola
                        queue_history = lp_history[puuid][queue_type]
                        last_snapshot = queue_history[-1] if queue_history else None
                        
                        # Evitar duplicados: no guardar si el valor ELO es idéntico al último snapshot
                        # dentro de los últimos 10 minutos (600000 ms)
                        if last_snapshot:
                            time_diff = timestamp - last_snapshot['timestamp']
                            elo_diff = abs(valor - last_snapshot['elo'])
                            
                            if time_diff < 600000 and elo_diff == 0:
                                # Es un duplicado - mismo ELO dentro de 10 minutos
                                print(f"[LP_TRACKER] Snapshot duplicado detectado para {riot_id} en {queue_type}. Saltando.")
                                snapshots_skipped += 1
                                continue
                        
                        # Añadir el nuevo snapshot
                        lp_history[puuid][queue_type].append({
                            "timestamp": timestamp,
                            "elo": valor,
                            "league_points_raw": entry.get('leaguePoints', 0)
                        })
                        print(f"[LP_TRACKER] Snapshot añadido para {riot_id} en {queue_type}: {valor} ELO (Raw LP: {entry.get('leaguePoints', 0)})")
                        snapshots_added += 1

            # 4. Guardar el historial actualizado en GitHub
            if snapshots_added > 0:
                # RELEER PARA OBTENER SHA ACTUAL (puede haber cambiado)
                _, current_sha = _read_json_from_github(LP_HISTORY_FILE_PATH, github_token)
                _write_to_github(LP_HISTORY_FILE_PATH, lp_history, current_sha, github_token)
                print(f"[{datetime.now()}] [LP_TRACKER] Snapshot de ELO completado. "
                      f"Añadidos: {snapshots_added}, Saltados: {snapshots_skipped}. "
                      f"Próxima ejecución en 5 minutos.")
            else:
                print(f"[{datetime.now()}] [LP_TRACKER] No se añadieron snapshots nuevos (todos duplicados o sin cambios). "
                      f"Próxima ejecución en 5 minutos.")
            
        except Exception as e:
            print(f"[LP_TRACKER] Error inesperado en el worker de ELO: {e}")
            
        time.sleep(1800) # OPTIMIZACIÓN RENDER: 30 minutos (1800 seg) en lugar de 5 minutos


def start_lp_tracker(riot_api_key, github_token):
    """
    Función de inicio para el servicio de seguimiento de LP.
    Wrapper que inicia el worker principal.
    
    Args:
        riot_api_key: API key de Riot Games
        github_token: Token de GitHub para persistencia
    """
    print("[lp_tracker] Iniciando servicio de seguimiento de LP...")
    
    if not riot_api_key:
        print("[lp_tracker] ⚠ Error: RIOT_API_KEY no configurada")
        return
    
    if not github_token:
        print("[lp_tracker] ⚠ Error: GITHUB_TOKEN no configurado")
        return
    
    # Iniciar el worker principal
    elo_tracker_worker(riot_api_key, github_token)
