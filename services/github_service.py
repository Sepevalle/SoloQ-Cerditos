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
        print(f"[write_file_to_github] Obteniendo SHA para {file_path}...")
        _, sha = read_file_from_github(file_path, use_raw=False)
        print(f"[write_file_to_github] Resultado de lectura: SHA={'Obtenido' if sha else 'None'}")
        if sha:
            print(f"[write_file_to_github] SHA obtenido: {sha[:8]}...")
    
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
    
    # Construir data - solo incluir SHA si existe
    data = {
        "message": message,
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        data["sha"] = sha
    
    # Verificar tamaño del archivo (GitHub tiene límite de 100MB, pero la API puede fallar antes)
    content_size_mb = len(content_json) / (1024 * 1024)
    print(f"[write_file_to_github] Tamaño del contenido: {len(content_json)} bytes ({content_size_mb:.2f} MB)")
    
    if content_size_mb > 50:
        print(f"[write_file_to_github] ⚠️ ADVERTENCIA: Archivo muy grande ({content_size_mb:.2f} MB), puede fallar en GitHub")
    
    print(f"[write_file_to_github] Enviando petición a GitHub...")
    print(f"[write_file_to_github] URL: {url}")
    print(f"[write_file_to_github] Incluye SHA: {bool(sha)}")
    print(f"[write_file_to_github] Tamaño base64: {len(content_b64)} bytes")
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=60)  # Aumentar timeout para archivos grandes
        print(f"[write_file_to_github] Respuesta: {response.status_code}")
        if response.status_code != 200 and response.status_code != 201:
            print(f"[write_file_to_github] Respuesta completa: {response.text[:1000]}")
        
        if response.status_code in (200, 201):
            print(f"[write_file_to_github] ✓ Archivo {file_path} guardado correctamente")
            return True
        elif response.status_code == 422:
            error_text = response.text
            print(f"[write_file_to_github] Error 422 detallado: {error_text}")
            
            # Si el error es sobre SHA, intentar estrategia alternativa
            if "sha" in error_text.lower():
                print(f"[write_file_to_github] Error relacionado con SHA, intentando estrategia alternativa...")
                
                # Estrategia 1: Intentar obtener SHA fresco y reintentar (sin usar raw)
                print(f"[write_file_to_github] Releyendo archivo desde API para obtener SHA...")
                _, fresh_sha = read_file_from_github(file_path, use_raw=False)
                print(f"[write_file_to_github] SHA fresco obtenido: {fresh_sha[:8] if fresh_sha else 'None'}")
                
                if fresh_sha:
                    # El archivo existe, actualizar con SHA fresco
                    data_fresh = {
                        "message": message,
                        "content": content_b64,
                        "branch": "main",
                        "sha": fresh_sha
                    }
                    print(f"[write_file_to_github] Reintentando con SHA fresco...")
                    response2 = requests.put(url, headers=headers, json=data_fresh, timeout=60)
                    print(f"[write_file_to_github] Respuesta del reintento: {response2.status_code}")
                    
                    if response2.status_code in (200, 201):
                        print(f"[write_file_to_github] ✓ Archivo {file_path} guardado correctamente en reintento")
                        return True
                    else:
                        print(f"[write_file_to_github] Reintento falló: {response2.status_code} - {response2.text[:500]}")
                else:
                    # El archivo no existe, intentar crear sin SHA
                    print(f"[write_file_to_github] El archivo no existe o no se puede leer")
                    print(f"[write_file_to_github] Intentando crear archivo nuevo...")
                    
                    # Para crear un archivo nuevo, NO debe incluir SHA
                    data_clean = {
                        "message": message,
                        "content": content_b64,
                        "branch": "main"
                    }
                    
                    # Verificar que no hay SHA en los datos
                    if "sha" in data_clean:
                        del data_clean["sha"]
                    
                    print(f"[write_file_to_github] Datos para creación: {list(data_clean.keys())}")
                    print(f"[write_file_to_github] Intentando crear archivo nuevo sin SHA...")
                    response3 = requests.put(url, headers=headers, json=data_clean, timeout=60)
                    print(f"[write_file_to_github] Respuesta de creación: {response3.status_code}")
                    
                    if response3.status_code in (200, 201):
                        print(f"[write_file_to_github] ✓ Archivo {file_path} creado correctamente")
                        return True
                    else:
                        print(f"[write_file_to_github] Creación falló: {response3.status_code} - {response3.text[:500]}")
                        # Si sigue fallando, podría ser un problema de permisos o rama
                        print(f"[write_file_to_github] Posibles causas: archivo existe pero no accesible, o problema con la rama 'main'")
            
            return False
        else:
            print(f"[write_file_to_github] Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[write_file_to_github] Error en petición: {e}")
        import traceback
        traceback.print_exc()
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
    if not GITHUB_TOKEN:
        print(f"[delete_file_from_github] Error: Token de GitHub no configurado")
        return False
    
    # Si no se proporcionó SHA, intentar obtenerlo
    if sha is None:
        _, sha = read_file_from_github(file_path, use_raw=False)
    
    if not sha:
        print(f"[delete_file_from_github] No se puede eliminar: archivo no existe o no se puede leer")
        return False
    
    url = get_github_file_url(file_path, raw=False)
    headers = get_github_headers()
    
    data = {
        "message": message,
        "sha": sha,
        "branch": "main"
    }
    
    print(f"[delete_file_from_github] Eliminando {file_path}...")
    
    try:
        response = requests.delete(url, headers=headers, json=data, timeout=30)
        print(f"[delete_file_from_github] Respuesta: {response.status_code}")
        
        if response.status_code in (200, 204):
            print(f"[delete_file_from_github] ✓ Archivo {file_path} eliminado correctamente")
            return True
        else:
            print(f"[delete_file_from_github] Error: {response.status_code} - {response.text[:500]}")
            return False
    except Exception as e:
        print(f"[delete_file_from_github] Error en petición: {e}")
        return False


def save_global_stats(stats_data):
    """
    Guarda las estadísticas globales en GitHub.
    Estrategia: Eliminar archivo existente y crear uno nuevo para evitar problemas con SHA.
    
    Args:
        stats_data: Diccionario con estadísticas globales
    
    Returns:
        bool: True si se guardó correctamente
    """
    # El timestamp ya viene formateado desde _calculate_and_save_global_stats
    print(f"[save_global_stats] Intentando guardar estadísticas globales...")

    print(f"[save_global_stats] Timestamp: {stats_data.get('calculated_at', 'N/A')}")
    print(f"[save_global_stats] Total de partidas: {stats_data.get('all_matches_count', 0)}")
    print(f"[save_global_stats] GITHUB_TOKEN configurado: {bool(GITHUB_TOKEN)}")
    
    # Verificar tamaño estimado antes de intentar guardar
    import json
    temp_json = json.dumps(stats_data, indent=2, ensure_ascii=False)
    size_mb = len(temp_json) / (1024 * 1024)
    print(f"[save_global_stats] Tamaño estimado del JSON: {len(temp_json)} bytes ({size_mb:.2f} MB)")
    
    # ESTRATEGIA: Eliminar archivo existente primero, luego crear nuevo
    file_path = "global_stats.json"
    
    # Paso 1: Intentar obtener SHA y eliminar archivo existente
    _, existing_sha = read_file_from_github(file_path, use_raw=False)
    if existing_sha:
        print(f"[save_global_stats] Archivo existente detectado, eliminando primero...")
        deleted = delete_file_from_github(file_path, message="Eliminar para regenerar estadísticas", sha=existing_sha)
        if deleted:
            print(f"[save_global_stats] Archivo anterior eliminado, esperando 2 segundos...")
            import time
            time.sleep(2)  # Esperar a que GitHub procese la eliminación
        else:
            print(f"[save_global_stats] No se pudo eliminar archivo anterior, intentando sobrescribir...")
    
    # Paso 2: Crear archivo nuevo (sin SHA, ya que lo eliminamos o no existe)
    print(f"[save_global_stats] Creando archivo nuevo...")
    success = write_file_to_github(
        file_path,
        stats_data,
        message="Actualizar estadísticas globales",
        sha=None  # Sin SHA = crear nuevo archivo
    )
    
    if success:
        print(f"[save_global_stats] ✓ Estadísticas globales guardadas correctamente")
    else:
        print(f"[save_global_stats] ✗ ERROR: No se pudieron guardar las estadísticas globales")
    
    return success





def read_stats_reload_config():
    """
    Lee la configuración de recarga forzada de estadísticas.
    Similar al sistema de permisos de IA.
    SIEMPRE usa la API (no raw) para evitar caché de GitHub.
    
    Returns:
        tuple: (forzar_recarga, sha, contenido_completo)
    """
    file_path = "config/stats_reload.json"
    # Forzar uso de API para obtener siempre la versión más reciente
    content, sha = read_file_from_github(file_path, use_raw=False)
    
    if content and isinstance(content, dict):
        return content.get("forzar_recarga") == "SI", sha, content
    
    # Si no existe, crear por defecto con recarga desactivada
    default_content = {"forzar_recarga": "NO", "razon": "Inicializado"}
    save_stats_reload_config(default_content)
    return False, None, default_content



def save_stats_reload_config(content, sha=None):
    """
    Guarda la configuración de recarga forzada de estadísticas.
    
    Args:
        content: Diccionario con la configuración
        sha: SHA del archivo existente (opcional)
    
    Returns:
        bool: True si se guardó correctamente
    """
    file_path = "config/stats_reload.json"
    return write_file_to_github(
        file_path,
        content,
        message="Actualizar configuración de recarga de estadísticas",
        sha=sha
    )


def read_stats_index():
    """
    Lee el archivo de estadísticas del index (stats_index.json).
    
    Returns:
        tuple: (exito, datos) donde datos es el diccionario con las estadísticas
    """
    content, _ = read_file_from_github("stats_index.json")
    if content and isinstance(content, dict):
        return True, content
    return False, {}


def save_stats_index(stats_data):
    """
    Guarda las estadísticas del index en GitHub.
    
    Args:
        stats_data: Diccionario con las estadísticas del index
    
    Returns:
        bool: True si se guardó correctamente
    """
    # Leer primero para obtener el SHA si existe
    _, sha = read_file_from_github("stats_index.json", use_raw=False)
    return write_file_to_github(
        "stats_index.json",
        stats_data,
        message="Actualizar estadísticas del index",
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
