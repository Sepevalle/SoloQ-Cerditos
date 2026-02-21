"""
Servicio de interacción con la API de GitHub.
Centraliza todas las operaciones de lectura/escritura en el repositorio.
"""

import requests
import base64
import json
import time
import os
from datetime import datetime
from collections import defaultdict
from config.settings import GITHUB_REPO, GITHUB_TOKEN
from utils.helpers import get_github_file_url, decode_github_content, encode_github_content

# Constantes para manejo de tamaño y chunking
MAX_B64_BYTES = 950_000  # Umbral conservador para payload Base64 (~1MB con overhead)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # Segundos base para backoff exponencial

def log_config():
    """Log de configuración del servicio GitHub."""
    print(f"[github_service] Configuración:")
    print(f"  - MAX_B64_BYTES: {MAX_B64_BYTES:,} bytes ({MAX_B64_BYTES/1024/1024:.2f} MB)")
    print(f"  - MAX_RETRIES: {MAX_RETRIES}")
    print(f"  - RETRY_BACKOFF_BASE: {RETRY_BACKOFF_BASE}s")
    print(f"  - GITHUB_TOKEN configurado: {bool(GITHUB_TOKEN)}")




def get_github_headers():
    """Retorna los headers necesarios para las peticiones a GitHub."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def read_file_from_github(file_path, use_raw=True, timeout=30):
    """
    Lee un archivo desde GitHub.
    
    Args:
        file_path: Ruta del archivo en el repositorio
        use_raw: Si True, usa raw.githubusercontent (más rápido para archivos públicos)
        timeout: Timeout de la petición en segundos
    
    Returns:
        tuple: (contenido_decodificado, sha) o (None, None) si hay error
    """
    # Intentar primero con raw (más rápido para archivos públicos)
    if use_raw:
        raw_url = get_github_file_url(file_path, raw=True)
        try:
            response = requests.get(raw_url, timeout=timeout)
            if response.status_code == 200:
                try:
                    return response.json(), None
                except:
                    return response.text, None
        except Exception as e:
            print(f"[read_file_from_github] Error leyendo raw: {e}")
    
    # Fallback a API de contenidos
    url = get_github_file_url(file_path, raw=False)
    print(f"[read_file_from_github] Leyendo desde API: {url}")
    try:
        response = requests.get(url, headers=get_github_headers(), timeout=timeout)
        print(f"[read_file_from_github] Respuesta API: {response.status_code}")
        
        if response.status_code == 200:
            content = response.json()
            file_content = decode_github_content(content['content'])
            sha = content.get('sha')
            print(f"[read_file_from_github] SHA obtenido: {sha[:8] if sha else 'None'}")
            if file_content:
                try:
                    return json.loads(file_content), sha
                except:
                    return file_content, sha
        elif response.status_code == 404:
            print(f"[read_file_from_github] Archivo no encontrado (404)")
            return None, None
        else:
            print(f"[read_file_from_github] Error API: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        print(f"[read_file_from_github] Error: {e}")
        import traceback
        traceback.print_exc()
    
    return None, None


def delete_file_from_github(file_path, message="Eliminar archivo", sha=None):
    """
    Elimina un archivo de GitHub.
    
    Args:
        file_path: Ruta del archivo en el repositorio
        message: Mensaje del commit
        sha: SHA del archivo (requerido)
    
    Returns:
        bool: True si se eliminó correctamente
    """
    print(f"[delete_file_from_github] Iniciando eliminación de {file_path}")
    
    if not GITHUB_TOKEN:
        print(f"[delete_file_from_github] Error: Token de GitHub no configurado")
        return False
    
    # Si no se proporcionó SHA, intentar obtenerlo
    if sha is None:
        print(f"[delete_file_from_github] Obteniendo SHA...")
        _, sha = read_file_from_github(file_path, use_raw=False)
    
    if not sha:
        print(f"[delete_file_from_github] No se puede eliminar: no se obtuvo SHA")
        return False
    
    url = get_github_file_url(file_path, raw=False)
    headers = get_github_headers()
    
    data = {
        "message": message,
        "sha": sha,
        "branch": "main"
    }
    
    print(f"[delete_file_from_github] URL: {url}")
    print(f"[delete_file_from_github] SHA: {sha[:8]}...")
    
    try:
        response = requests.delete(url, headers=headers, json=data, timeout=30)
        print(f"[delete_file_from_github] Respuesta: {response.status_code}")
        
        if response.status_code in (200, 204):
            print(f"[delete_file_from_github] ✓ Archivo eliminado correctamente")
            return True
        else:
            print(f"[delete_file_from_github] Error: {response.status_code} - {response.text[:500]}")
            return False
    except Exception as e:
        print(f"[delete_file_from_github] Error en petición: {e}")
        import traceback
        traceback.print_exc()
        return False


def estimate_payload_size(content):
    """
    Estima el tamaño del payload JSON y su versión Base64.
    
    Args:
        content: Contenido (dict o str)
    
    Returns:
        tuple: (bytes_json, bytes_b64)
    """
    if isinstance(content, dict):
        json_str = json.dumps(content, indent=2, ensure_ascii=False)
    else:
        json_str = str(content)
    
    bytes_json = len(json_str.encode('utf-8'))
    bytes_b64 = len(base64.b64encode(json_str.encode('utf-8')))
    
    return bytes_json, bytes_b64


def write_file_to_github(file_path, content, message="Actualización automática", sha=None, max_retries=MAX_RETRIES):
    """
    Escribe o actualiza un archivo en GitHub con logging defensivo y reintentos.
    
    Args:
        file_path: Ruta del archivo en el repositorio
        content: Contenido a escribir (dict para JSON, str para texto)
        message: Mensaje del commit
        sha: SHA del archivo existente (requerido para actualizaciones)
        max_retries: Número máximo de reintentos en caso de conflicto/error temporal
    
    Returns:
        bool: True si se guardó correctamente
    """
    if not GITHUB_TOKEN:
        print(f"[write_file_to_github] Error: Token de GitHub no configurado")
        return False
    
    url = get_github_file_url(file_path, raw=False)
    headers = get_github_headers()
    
    # Preparar contenido
    if isinstance(content, dict):
        content_json = json.dumps(content, indent=2, ensure_ascii=False)
    else:
        content_json = str(content)
    
    content_b64 = encode_github_content(content_json)
    if not content_b64:
        return False
    
    # Estimar tamaños para logging defensivo
    bytes_json, bytes_b64 = estimate_payload_size(content)
    content_size_mb = bytes_json / (1024 * 1024)
    b64_size_mb = bytes_b64 / (1024 * 1024)
    
    print(f"[write_file_to_github] Tamaño JSON: {bytes_json} bytes ({content_size_mb:.2f} MB)")
    print(f"[write_file_to_github] Tamaño Base64: {bytes_b64} bytes ({b64_size_mb:.2f} MB)")
    print(f"[write_file_to_github] Incluye SHA: {bool(sha)}")
    
    # Verificar si excede el límite práctico
    if bytes_b64 > MAX_B64_BYTES:
        print(f"[write_file_to_github] ⚠️ ADVERTENCIA: Payload Base64 ({bytes_b64}) excede umbral ({MAX_B64_BYTES})")
    
    # Intentar escribir con reintentos
    for attempt in range(max_retries):
        # Construir data (refrescar SHA si es reintento)
        data = {
            "message": message,
            "content": content_b64,
            "branch": "main"
        }
        if sha:
            data["sha"] = sha
        
        try:
            response = requests.put(url, headers=headers, json=data, timeout=60)
            print(f"[write_file_to_github] Intento {attempt + 1}/{max_retries} - Respuesta: {response.status_code}")
            
            if response.status_code in (200, 201):
                print(f"[write_file_to_github] ✓ Archivo guardado correctamente")
                return True
            
            # Manejar errores específicos para reintentos
            if response.status_code == 409:  # Conflicto SHA
                print(f"[write_file_to_github] ⚠️ Conflicto SHA detectado, releyendo...")
                _, new_sha = read_file_from_github(file_path, use_raw=False)
                if new_sha:
                    sha = new_sha
                    print(f"[write_file_to_github] Nuevo SHA obtenido: {sha[:8] if sha else 'None'}")
                else:
                    print(f"[write_file_to_github] ⚠️ No se pudo obtener nuevo SHA")
            
            elif response.status_code == 403:  # Rate limit o forbidden
                wait_time = RETRY_BACKOFF_BASE ** attempt
                print(f"[write_file_to_github] ⚠️ Rate limit (403), esperando {wait_time}s...")
                time.sleep(wait_time)
            
            elif response.status_code >= 500:  # Errores de servidor
                wait_time = RETRY_BACKOFF_BASE ** attempt
                print(f"[write_file_to_github] ⚠️ Error servidor ({response.status_code}), esperando {wait_time}s...")
                time.sleep(wait_time)
            
            else:  # Otros errores (422, etc.) - no reintentar
                print(f"[write_file_to_github] Error: {response.status_code} - {response.text[:500]}")
                return False
                
        except requests.exceptions.Timeout:
            wait_time = RETRY_BACKOFF_BASE ** attempt
            print(f"[write_file_to_github] ⚠️ Timeout, esperando {wait_time}s...")
            time.sleep(wait_time)
        except Exception as e:
            print(f"[write_file_to_github] Error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    print(f"[write_file_to_github] ✗ Falló después de {max_retries} intentos")
    return False



def get_file_sha_only(file_path):
    """
    Obtiene solo el SHA de un archivo sin intentar leer su contenido.
    Útil para archivos grandes donde el contenido no es necesario.
    
    Returns:
        str: SHA del archivo o None si no existe/error
    """
    url = get_github_file_url(file_path, raw=False)
    headers = get_github_headers()
    
    # Usar un timeout corto y no seguir redirects para obtener solo metadata
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        if response.status_code == 200:
            data = response.json()
            sha = data.get('sha')
            print(f"[get_file_sha_only] SHA obtenido para {file_path}: {sha[:8] if sha else 'None'}")
            return sha
        elif response.status_code == 404:
            print(f"[get_file_sha_only] Archivo no existe: {file_path}")
            return None
        else:
            print(f"[get_file_sha_only] Error {response.status_code} para {file_path}")
            return None
    except Exception as e:
        print(f"[get_file_sha_only] Error: {e}")
        return None


def save_global_stats(stats_data):
    """
    Guarda las estadísticas globales en GitHub.
    Estrategia: Eliminar archivo existente y crear uno nuevo.
    """
    print(f"[save_global_stats] Iniciando guardado de estadísticas globales...")
    print(f"[save_global_stats] Timestamp: {stats_data.get('calculated_at', 'N/A')}")
    print(f"[save_global_stats] Total partidas: {stats_data.get('all_matches_count', 0)}")
    
    file_path = "global_stats.json"
    
    # PASO 1: Obtener solo el SHA (más eficiente para archivos grandes)
    print(f"[save_global_stats] Paso 1: Obteniendo SHA del archivo existente...")
    existing_sha = get_file_sha_only(file_path)
    
    if existing_sha:
        print(f"[save_global_stats] Archivo existente encontrado (SHA: {existing_sha[:8]}), eliminando...")
        deleted = delete_file_from_github(file_path, message="Eliminar para regenerar estadísticas", sha=existing_sha)
        if deleted:
            print(f"[save_global_stats] Archivo eliminado, esperando 3 segundos...")
            time.sleep(3)
        else:
            print(f"[save_global_stats] ⚠ No se pudo eliminar archivo, intentando sobrescribir...")
    else:
        print(f"[save_global_stats] No existe archivo anterior o no se pudo obtener SHA")
    
    # PASO 2: Crear archivo nuevo (sin SHA)
    print(f"[save_global_stats] Paso 2: Creando archivo nuevo...")
    success = write_file_to_github(
        file_path,
        stats_data,
        message="Actualizar estadísticas globales",
        sha=None  # Sin SHA = crear nuevo
    )
    
    if success:
        print(f"[save_global_stats] ✓ Estadísticas guardadas correctamente")
    else:
        print(f"[save_global_stats] ✗ ERROR al guardar estadísticas")
    
    return success



# ============================================================================
# FUNCIONES ESPECÍFICAS POR TIPO DE ARCHIVO
# ============================================================================

def read_accounts_file():
    """Lee el archivo de cuentas (cuentas.txt)."""
    content, _ = read_file_from_github("cuentas.txt", use_raw=False)
    if not content:
        return []
    
    try:
        if isinstance(content, str):
            contenido = content.strip().split(';')
            cuentas = []
            for linea in contenido:
                partes = linea.split(',')
                if len(partes) == 2:
                    riot_id = partes[0].strip()
                    jugador = partes[1].strip()
                    cuentas.append((riot_id, jugador))
            return cuentas
    except Exception as e:
        print(f"[read_accounts_file] Error: {e}")
    
    return []


def read_puuids():
    """Lee el archivo de PUUIDs."""
    content, _ = read_file_from_github("puuids.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_puuids(puuid_dict):
    """Guarda el diccionario de PUUIDs."""
    _, sha = read_file_from_github("puuids.json", use_raw=False)
    return write_file_to_github("puuids.json", puuid_dict, message="Actualizar PUUIDs", sha=sha)


def read_peak_elo():
    """Lee el archivo de peak ELO."""
    content, _ = read_file_from_github("peak_elo.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_peak_elo(peak_elo_dict):
    """Guarda el diccionario de peak ELO."""
    _, sha = read_file_from_github("peak_elo.json", use_raw=False)
    return write_file_to_github("peak_elo.json", peak_elo_dict, message="Actualizar Peak ELO", sha=sha)


def read_lp_history():
    """Lee el archivo de historial de LP."""
    content, _ = read_file_from_github("lp_history.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_lp_history(lp_history):
    """Guarda el historial de LP."""
    _, sha = read_file_from_github("lp_history.json", use_raw=False)
    return write_file_to_github("lp_history.json", lp_history, message="Actualizar LP History", sha=sha)


def get_iso_week(timestamp_ms):
    """
    Obtiene el año y semana ISO para un timestamp en milisegundos.
    Retorna string en formato 'YYYY-WWW'
    """
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def read_player_match_history(puuid):
    """
    Lee el historial de partidas de un jugador.
    Soporta formatos:
    - v3 (chunked): match_history/{puuid}/index.json + weeks/*.json
    - v2 (weekly): match_history/{puuid}/index.json + weeks/*.json
    - legacy: match_history/{puuid}.json
    
    Returns:
        dict: {'matches': [...], 'remakes': [...], 'last_updated': timestamp}
    """
    # Intentar formato v2/v3 (con index)
    index_path = f"match_history/{puuid}/index.json"
    index_content, _ = read_file_from_github(index_path)
    
    if index_content and isinstance(index_content, dict):
        # Formato v2/v3 detectado
        files = index_content.get('files', [])
        all_matches = []
        all_remakes = []
        
        print(f"[read_player_match_history] Formato v2/v3 detectado para {puuid[:16]}...")
        print(f"[read_player_match_history] Cargando {len(files)} archivos...")
        
        for file_path in files:
            full_path = f"match_history/{puuid}/{file_path}"
            chunk_content, _ = read_file_from_github(full_path)
            
            if chunk_content and isinstance(chunk_content, dict):
                matches = chunk_content.get('matches', [])
                remakes = chunk_content.get('remakes', [])
                all_matches.extend(matches)
                all_remakes.extend(remakes)
                print(f"[read_player_match_history]   ✓ {file_path}: {len(matches)} matches, {len(remakes)} remakes")
            else:
                print(f"[read_player_match_history]   ⚠️ No se pudo cargar: {file_path}")
        
        # Eliminar duplicados por match_id y ordenar
        seen_ids = set()
        unique_matches = []
        for match in sorted(all_matches, key=lambda x: x.get('game_end_timestamp', 0), reverse=True):
            match_id = match.get('match_id')
            if match_id and match_id not in seen_ids:
                seen_ids.add(match_id)
                unique_matches.append(match)
        
        result = {
            'matches': unique_matches,
            'remakes': all_remakes,
            'last_updated': index_content.get('last_updated', time.time())
        }
        
        print(f"[read_player_match_history] Total: {len(unique_matches)} matches únicos, {len(all_remakes)} remakes")
        return result
    
    # Fallback a formato legacy
    legacy_path = f"match_history/{puuid}.json"
    content, _ = read_file_from_github(legacy_path)
    
    if content and isinstance(content, dict):
        print(f"[read_player_match_history] Formato legacy detectado para {puuid[:16]}...")
        return content
    
    return {}


def save_player_match_history(puuid, historial_data):
    """
    Guarda el historial de partidas de un jugador usando formato v3 (chunked).
    
    Estrategia:
    1. Agrupar matches por semana ISO
    2. Para cada semana, si excede umbral, partir en chunks numerados
    3. Guardar todos los chunks primero
    4. Actualizar index.json al final con lista de archivos reales
    
    Args:
        puuid: ID del jugador
        historial_data: dict con 'matches', 'remakes', etc.
    
    Returns:
        bool: True si se guardó correctamente
    """
    if not GITHUB_TOKEN:
        print(f"[save_player_match_history] ⚠️ GITHUB_TOKEN no configurado, no se puede guardar")
        return False
    
    matches = historial_data.get('matches', [])
    remakes = historial_data.get('remakes', [])
    last_updated = historial_data.get('last_updated', time.time())
    
    if not matches and not remakes:
        print(f"[save_player_match_history] ⚠️ No hay datos para guardar")
        return False
    
    print(f"[save_player_match_history] Guardando historial para {puuid[:16]}...")
    print(f"[save_player_match_history] Total matches: {len(matches)}, remakes: {len(remakes)}")
    
    # Agrupar matches por semana ISO
    weeks_data = defaultdict(list)
    for match in matches:
        ts = match.get('game_end_timestamp', 0)
        if ts > 0:
            week_key = get_iso_week(ts)
            weeks_data[week_key].append(match)
    
    # Ordenar matches dentro de cada semana (más reciente primero)
    for week in weeks_data:
        weeks_data[week].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    
    print(f"[save_player_match_history] Distribuido en {len(weeks_data)} semanas")
    
    # Preparar chunks para cada semana
    base_path = f"match_history/{puuid}"
    all_files = []  # Lista de archivos que se guardarán exitosamente
    chunks_to_save = []  # (file_path, content_dict)
    
    for week_key in sorted(weeks_data.keys(), reverse=True):  # Semanas más recientes primero
        week_matches = weeks_data[week_key]
        
        # Preparar JSON de la semana
        week_data = {
            'matches': week_matches,
            'remakes': [],  # Los remakes se manejan por separado si es necesario
            'week': week_key
        }
        
        json_str = json.dumps(week_data, indent=2, ensure_ascii=False)
        bytes_json, bytes_b64 = estimate_payload_size(week_data)
        
        print(f"[save_player_match_history] Semana {week_key}: {len(week_matches)} matches, {bytes_json} bytes JSON, {bytes_b64} bytes Base64")
        
        if bytes_b64 <= MAX_B64_BYTES:
            # Cabe en un solo archivo
            file_name = f"weeks/{week_key}.json"
            chunks_to_save.append((file_name, week_data))
        else:
            # Necesita partición en chunks
            print(f"[save_player_match_history]   ⚠️ Semana {week_key} excede umbral, partiendo...")
            
            # Calcular tamaño aproximado por match para estimar chunk size
            avg_bytes_per_match = bytes_json / len(week_matches) if week_matches else 1000
            # Estimar matches por chunk (con margen de seguridad del 20%)
            matches_per_chunk = max(1, int((MAX_B64_BYTES * 0.75) / avg_bytes_per_match))
            
            chunk_num = 1
            for i in range(0, len(week_matches), matches_per_chunk):
                chunk_matches = week_matches[i:i + matches_per_chunk]
                chunk_data = {
                    'matches': chunk_matches,
                    'remakes': [],
                    'week': week_key,
                    'chunk': chunk_num
                }
                
                # Verificar que el chunk cabe
                _, chunk_b64 = estimate_payload_size(chunk_data)
                if chunk_b64 > MAX_B64_BYTES and len(chunk_matches) > 1:
                    # Reducir y reintentar
                    reduced_size = max(1, len(chunk_matches) // 2)
                    chunk_matches = week_matches[i:i + reduced_size]
                    chunk_data['matches'] = chunk_matches
                    _, chunk_b64 = estimate_payload_size(chunk_data)
                
                file_name = f"weeks/{week_key}-{chunk_num:02d}.json"
                chunks_to_save.append((file_name, chunk_data))
                print(f"[save_player_match_history]   → Chunk {chunk_num}: {len(chunk_matches)} matches, ~{chunk_b64} bytes B64")
                chunk_num += 1
    
    # Guardar remakes en archivo separado si existen
    if remakes:
        remakes_data = {'matches': [], 'remakes': remakes, 'type': 'remakes'}
        chunks_to_save.append(("remakes.json", remakes_data))
        print(f"[save_player_match_history] Remakes: {len(remakes)} en archivo separado")
    
    # FASE 1: Guardar todos los chunks
    print(f"[save_player_match_history] Fase 1: Guardando {len(chunks_to_save)} archivos...")
    successfully_saved = []
    
    for file_name, content in chunks_to_save:
        full_path = f"{base_path}/{file_name}"
        
        # Intentar obtener SHA existente
        _, sha = read_file_from_github(full_path, use_raw=False)
        
        success = write_file_to_github(
            full_path,
            content,
            message=f"Actualizar {file_name} para {puuid[:16]}...",
            sha=sha
        )
        
        if success:
            successfully_saved.append(file_name)
            print(f"[save_player_match_history]   ✓ Guardado: {file_name}")
        else:
            print(f"[save_player_match_history]   ✗ Falló: {file_name}")
            # Continuar con los demás, el index solo incluirá los exitosos
    
    if not successfully_saved:
        print(f"[save_player_match_history] ✗ ERROR: No se pudo guardar ningún chunk")
        return False
    
    print(f"[save_player_match_history] {len(successfully_saved)}/{len(chunks_to_save)} archivos guardados exitosamente")
    
    # FASE 2: Actualizar index.json (solo con archivos que realmente se guardaron)
    index_content = {
        'puuid': puuid,
        'last_updated': last_updated,
        'format_version': 'v3',
        'files': successfully_saved,
        'total_matches': len(matches),
        'total_remakes': len(remakes)
    }
    
    index_path = f"{base_path}/index.json"
    _, index_sha = read_file_from_github(index_path, use_raw=False)
    
    print(f"[save_player_match_history] Fase 2: Actualizando index.json...")
    index_success = write_file_to_github(
        index_path,
        index_content,
        message=f"Actualizar index para {puuid[:16]}... ({len(successfully_saved)} archivos)",
        sha=index_sha
    )
    
    if index_success:
        print(f"[save_player_match_history] ✓ Historial guardado completamente")
        return True
    else:
        print(f"[save_player_match_history] ⚠️ Index no actualizado, pero chunks guardados")
        # Los chunks están guardados pero el index no los referencia
        # En la próxima ejecución se detectarán y reconstruirán
        return False



def read_analysis(puuid):
    """Lee el análisis de IA de un jugador."""
    file_path = f"analisisIA/{puuid}.json"
    content, sha = read_file_from_github(file_path)
    if content and isinstance(content, dict):
        return content, sha
    return None, None


def save_analysis(puuid, analysis_data, sha=None):
    """Guarda el análisis de IA de un jugador."""
    file_path = f"analisisIA/{puuid}.json"
    return write_file_to_github(file_path, analysis_data, message=f"Actualizar análisis para {puuid}", sha=sha)


def read_match_timeline(match_id):
    """Lee el timeline de una partida guardado en GitHub."""
    file_path = f"timelines/{match_id}.json"
    content, sha = read_file_from_github(file_path)
    if content and isinstance(content, dict):
        return content, sha
    return None, None


def save_match_timeline(match_id, timeline_data, sha=None):
    """Guarda el timeline completo de una partida en GitHub."""
    file_path = f"timelines/{match_id}.json"
    return write_file_to_github(
        file_path,
        timeline_data,
        message=f"Guardar timeline de {match_id}",
        sha=sha
    )


def _sanitize_file_key(value):
    """Normaliza una clave para usarla de forma segura en nombre de archivo."""
    if not value:
        return "unknown"
    safe = str(value).replace("/", "_").replace("\\", "_").replace("..", "_")
    return safe


def read_match_detail_analysis(match_id, player_key=None):
    """Lee el análisis detallado de una partida para un jugador específico."""
    player_key_safe = _sanitize_file_key(player_key)
    file_path = f"analisisPartida/{match_id}_{player_key_safe}.json"
    content, sha = read_file_from_github(file_path)
    if content and isinstance(content, dict):
        return content, sha
    return None, None


def save_match_detail_analysis(match_id, player_key, analysis_data, sha=None):
    """Guarda el análisis detallado de una partida para un jugador específico."""
    player_key_safe = _sanitize_file_key(player_key)
    file_path = f"analisisPartida/{match_id}_{player_key_safe}.json"
    return write_file_to_github(
        file_path,
        analysis_data,
        message=f"Guardar análisis de partida {match_id} ({player_key_safe})",
        sha=sha
    )


def read_player_permission(puuid):
    """
    Lee el permiso de un jugador para usar análisis de IA.
    Verifica automáticamente si han pasado 24h desde la última llamada
    y rehabilita el permiso si es necesario.
    
    Returns:
        tuple: (tiene_permiso, sha, contenido_completo, segundos_restantes)
    """
    file_path = f"config/permisos/{puuid}.json"
    content, sha = read_file_from_github(file_path)
    
    # Si no existe, crear con valores por defecto (deshabilitado por defecto)
    if not content or not isinstance(content, dict):
        default_content = {
            "permitir_llamada": "NO",
            "razon": "Deshabilitado por defecto. Habilitar manualmente en GitHub.",
            "ultima_llamada": 0,
            "proxima_llamada_disponible": 0,
            "modo_forzado": False,
            "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_player_permission(puuid, default_content)
        return False, None, default_content, 0

    # Modo de permiso manual: no auto-rehabilitar por tiempo.
    ultima_llamada = content.get("ultima_llamada", 0)
    proxima_disponible = content.get("proxima_llamada_disponible", 0)
    modo_forzado = content.get("modo_forzado", False)
    permitir = content.get("permitir_llamada") == "SI"
    
    ahora = time.time()
    segundos_restantes = max(0, proxima_disponible - ahora)
    
    # Si está en modo forzado, permitir una llamada manual.
    if modo_forzado and permitir:
        permitir = True

    return permitir, sha, content, segundos_restantes



def save_player_permission(puuid, content, sha=None):
    """
    Guarda el permiso de un jugador.
    Asegura que todos los campos necesarios estén presentes.
    """
    file_path = f"config/permisos/{puuid}.json"
    
    # Asegurar campos por defecto
    if "ultima_llamada" not in content:
        content["ultima_llamada"] = 0
    if "proxima_llamada_disponible" not in content:
        content["proxima_llamada_disponible"] = 0
    if "modo_forzado" not in content:
        content["modo_forzado"] = False
    if "ultima_modificacion" not in content:
        content["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return write_file_to_github(file_path, content, message=f"Actualizar permiso para {puuid}", sha=sha)


def format_time_remaining(seconds):
    """
    Formatea los segundos restantes en formato legible.
    
    Args:
        seconds: Segundos restantes
    
    Returns:
        str: Tiempo formateado (ej: "5h 30m" o "45m 20s")
    """
    if seconds <= 0:
        return "Disponible ahora"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"



def read_global_stats():
    """Lee las estadísticas globales calculadas desde GitHub."""
    content, _ = read_file_from_github("global_stats.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def read_stats_reload_config():
    """Lee la configuración de recarga forzada de estadísticas."""
    file_path = "config/stats_reload.json"
    content, sha = read_file_from_github(file_path, use_raw=False)
    
    if content and isinstance(content, dict):
        return content.get("forzar_recarga") == "SI", sha, content
    
    default_content = {"forzar_recarga": "NO", "razon": "Inicializado"}
    save_stats_reload_config(default_content)
    return False, None, default_content


def save_stats_reload_config(content, sha=None):
    """Guarda la configuración de recarga forzada de estadísticas."""
    file_path = "config/stats_reload.json"
    return write_file_to_github(file_path, content, message="Actualizar config de recarga", sha=sha)


def read_stats_index():
    """Lee el archivo de estadísticas del index."""
    content, _ = read_file_from_github("stats_index.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_stats_index(stats_data):
    """Guarda las estadísticas del index en GitHub."""
    _, sha = read_file_from_github("stats_index.json", use_raw=False)
    return write_file_to_github("stats_index.json", stats_data, message="Actualizar estadísticas del index", sha=sha)


def start_github_service():
    """Función de inicio para el servicio de GitHub."""
    print("[github_service] Servicio de GitHub iniciado")
    
    # Log de configuración
    log_config()
    
    if not GITHUB_TOKEN:
        print("[github_service] ⚠ GITHUB_TOKEN no configurado")
        return
    
    try:
        content, _ = read_file_from_github("cuentas.txt", use_raw=True)
        if content:
            print("[github_service] ✓ Conexión con GitHub verificada")
        else:
            print("[github_service] ⚠ No se pudo verificar conexión")
    except Exception as e:
        print(f"[github_service] ⚠ Error: {e}")
