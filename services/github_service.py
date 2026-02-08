"""
Servicio de interacción con la API de GitHub.
Centraliza todas las operaciones de lectura/escritura en el repositorio.
"""

import requests
import base64
import json
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
    try:
        response = requests.get(url, headers=get_github_headers(), timeout=timeout)
        if response.status_code == 200:
            content = response.json()
            file_content = decode_github_content(content['content'])
            if file_content:
                try:
                    return json.loads(file_content), content.get('sha')
                except:
                    return file_content, content.get('sha')
        elif response.status_code == 404:
            return None, None
        else:
            print(f"[read_file_from_github] Error API: {response.status_code}")
    except Exception as e:
        print(f"[read_file_from_github] Error: {e}")
    
    return None, None


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
    
    # Si no se proporcionó SHA, intentar obtenerlo leyendo el archivo primero
    if sha is None:
        _, sha = read_file_from_github(file_path, use_raw=False)
        if sha:
            print(f"[write_file_to_github] SHA obtenido para {file_path}: {sha[:8]}...")
    
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
    
    data = {
        "message": message,
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        data["sha"] = sha
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        if response.status_code in (200, 201):
            print(f"[write_file_to_github] Archivo {file_path} guardado correctamente")
            return True
        elif response.status_code == 422 and "sha" in response.text.lower():
            # Error 422 - intentar re-leer el SHA y reintentar una vez
            print(f"[write_file_to_github] Error 422, reintentando con SHA actualizado...")
            _, new_sha = read_file_from_github(file_path, use_raw=False)
            if new_sha and new_sha != sha:
                data["sha"] = new_sha
                response = requests.put(url, headers=headers, json=data, timeout=30)
                if response.status_code in (200, 201):
                    print(f"[write_file_to_github] Archivo {file_path} guardado correctamente en reintento")
                    return True
            print(f"[write_file_to_github] Error: {response.status_code} - {response.text}")
            return False
        else:
            print(f"[write_file_to_github] Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[write_file_to_github] Error en petición: {e}")
        return False



# ============================================================================
# FUNCIONES ESPECÍFICAS POR TIPO DE ARCHIVO
# ============================================================================

def read_accounts_file():
    """Lee el archivo de cuentas (cuentas.txt)."""
    content, _ = read_file_from_github("cuentas.txt", use_raw=False)
    if not content:
        return []
    
    try:
        # El archivo viene como string, parsear formato
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
        print(f"[read_accounts_file] Error parseando: {e}")
    
    return []


def read_puuids():
    """Lee el archivo de PUUIDs."""
    content, _ = read_file_from_github("puuids.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}



def save_puuids(puuid_dict):
    """Guarda el diccionario de PUUIDs."""
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github("puuids.json", use_raw=False)
    return write_file_to_github(
        "puuids.json",
        puuid_dict,
        message="Actualizar PUUIDs",
        sha=sha
    )



def read_peak_elo():
    """Lee el archivo de peak ELO."""
    content, _ = read_file_from_github("peak_elo.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}



def save_peak_elo(peak_elo_dict):
    """Guarda el diccionario de peak ELO."""
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github("peak_elo.json", use_raw=False)
    return write_file_to_github(
        "peak_elo.json",
        peak_elo_dict,
        message="Actualizar Peak ELO",
        sha=sha
    )



def read_lp_history():
    """Lee el archivo de historial de LP."""
    content, _ = read_file_from_github("lp_history.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}



def save_lp_history(lp_history):
    """Guarda el historial de LP."""
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github("lp_history.json", use_raw=False)
    return write_file_to_github(
        "lp_history.json",
        lp_history,
        message="Actualizar LP History",
        sha=sha
    )



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
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github(file_path, use_raw=False)
    return write_file_to_github(
        file_path,
        historial_data,
        message=f"Actualizar historial de partidas para {puuid}",
        sha=sha
    )



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
    return write_file_to_github(
        file_path,
        analysis_data,
        message=f"Actualizar análisis para {puuid}",
        sha=sha
    )


def read_player_permission(puuid):
    """
    Lee el permiso de un jugador para usar análisis de IA.
    Retorna: (tiene_permiso, sha, contenido_completo)
    """
    file_path = f"config/permisos/{puuid}.json"
    content, sha = read_file_from_github(file_path)
    
    if content and isinstance(content, dict):
        return content.get("permitir_llamada") == "SI", sha, content
    
    # Si no existe, crear por defecto con permiso SI
    default_content = {"permitir_llamada": "SI", "razon": "Inicializado"}
    save_player_permission(puuid, default_content)
    return True, None, default_content


def save_player_permission(puuid, content, sha=None):
    """Guarda el permiso de un jugador."""
    file_path = f"config/permisos/{puuid}.json"
    return write_file_to_github(
        file_path,
        content,
        message=f"Actualizar permiso para {puuid}",
        sha=sha
    )


def read_global_stats():
    """
    Lee las estadísticas globales calculadas desde GitHub.
    
    Returns:
        tuple: (exito, datos) donde datos incluye las estadísticas y timestamp
    """
    content, _ = read_file_from_github("global_stats.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_global_stats(stats_data):
    """
    Guarda las estadísticas globales en GitHub.
    
    Args:
        stats_data: Diccionario con estadísticas globales
    
    Returns:
        bool: True si se guardó correctamente
    """
    # Añadir timestamp de cuándo se calcularon
    from datetime import datetime, timezone
    stats_data['calculated_at'] = datetime.now(timezone.utc).isoformat()
    
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github("global_stats.json", use_raw=False)
    return write_file_to_github(
        "global_stats.json",
        stats_data,
        message="Actualizar estadísticas globales",
        sha=sha
    )


def start_github_service():

    """
    Función de inicio para el servicio de GitHub.
    Verifica la conectividad con GitHub al iniciar.
    """
    print("[github_service] Servicio de GitHub iniciado")
    
    if not GITHUB_TOKEN:
        print("[github_service] ⚠ Advertencia: GITHUB_TOKEN no configurado")
        print("[github_service] Las operaciones de escritura no funcionarán")
        return
    
    # Verificar conectividad leyendo un archivo simple
    try:
        content, _ = read_file_from_github("cuentas.txt", use_raw=True)
        if content:
            print("[github_service] ✓ Conexión con GitHub verificada")
        else:
            print("[github_service] ⚠ No se pudo verificar conexión con GitHub")
    except Exception as e:
        print(f"[github_service] ⚠ Error verificando conexión: {e}")
