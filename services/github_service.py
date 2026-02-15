"""
Servicio de interacción con la API de GitHub.
Centraliza todas las operaciones de lectura/escritura en el repositorio.
"""

import requests
import base64
import json
import time
from config.settings import GITHUB_REPO, GITHUB_TOKEN
from utils.helpers import get_github_file_url, decode_github_content, encode_github_content


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


def write_file_to_github(file_path, content, message="Actualización automática", sha=None):
    """
    Escribe o actualiza un archivo en GitHub.
    
    Args:
        file_path: Ruta del archivo en el repositorio
        content: Contenido a escribir (dict para JSON, str para texto)
        message: Mensaje del commit
        sha: SHA del archivo existente (requerido para actualizaciones)
    
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
    
    # Construir data
    data = {
        "message": message,
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        data["sha"] = sha
    
    # Verificar tamaño
    content_size_mb = len(content_json) / (1024 * 1024)
    print(f"[write_file_to_github] Tamaño: {len(content_json)} bytes ({content_size_mb:.2f} MB)")
    print(f"[write_file_to_github] Incluye SHA: {bool(sha)}")
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=60)
        print(f"[write_file_to_github] Respuesta: {response.status_code}")
        
        if response.status_code in (200, 201):
            print(f"[write_file_to_github] ✓ Archivo guardado correctamente")
            return True
        else:
            print(f"[write_file_to_github] Error: {response.status_code} - {response.text[:500]}")
            return False
    except Exception as e:
        print(f"[write_file_to_github] Error: {e}")
        import traceback
        traceback.print_exc()
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


def read_player_match_history(puuid):
    """Lee el historial de partidas de un jugador."""
    file_path = f"match_history/{puuid}.json"
    content, _ = read_file_from_github(file_path)
    if content and isinstance(content, dict):
        return content
    return {}


def save_player_match_history(puuid, historial_data):
    """Guarda el historial de partidas de un jugador."""
    file_path = f"match_history/{puuid}.json"
    _, sha = read_file_from_github(file_path, use_raw=False)
    return write_file_to_github(file_path, historial_data, message=f"Actualizar historial para {puuid}", sha=sha)


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


def read_player_permission(puuid):
    """Lee el permiso de un jugador para usar análisis de IA."""
    file_path = f"config/permisos/{puuid}.json"
    content, sha = read_file_from_github(file_path)
    
    if content and isinstance(content, dict):
        return content.get("permitir_llamada") == "SI", sha, content
    
    default_content = {"permitir_llamada": "SI", "razon": "Inicializado"}
    save_player_permission(puuid, default_content)
    return True, None, default_content


def save_player_permission(puuid, content, sha=None):
    """Guarda el permiso de un jugador."""
    file_path = f"config/permisos/{puuid}.json"
    return write_file_to_github(file_path, content, message=f"Actualizar permiso para {puuid}", sha=sha)


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
