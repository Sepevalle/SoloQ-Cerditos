"""
Funciones utilitarias y helpers del proyecto.
"""

import base64
import json
from datetime import datetime, timezone
from config.settings import TARGET_TIMEZONE, TIER_ORDER, RANK_ORDER


def calcular_valor_clasificacion(tier, rank, league_points):
    """
    Calcula un valor numérico para la clasificación de un jugador,
    permitiendo ordenar y comparar Elo de forma más sencilla.
    """
    tier_upper = tier.upper()
    
    if tier_upper in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        return 2800 + league_points

    valor_base_tier = TIER_ORDER.get(tier_upper, 0) * 400
    valor_division = RANK_ORDER.get(rank, 0) * 100

    return valor_base_tier + valor_division + league_points


def format_datetime_to_target_timezone(timestamp_ms):
    """Convierte un timestamp en milisegundos a datetime en la zona horaria objetivo."""
    if not timestamp_ms:
        return None
    timestamp_sec = timestamp_ms / 1000
    dt_utc = datetime.fromtimestamp(timestamp_sec, tz=timezone.utc)
    return dt_utc.astimezone(TARGET_TIMEZONE)


def decode_github_content(content_b64):
    """Decodifica contenido base64 de la API de GitHub."""
    try:
        return base64.b64decode(content_b64).decode('utf-8')
    except Exception:
        return None


def encode_github_content(content_str):
    """Codifica contenido para enviar a la API de GitHub."""
    try:
        return base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
    except Exception:
        return None


def get_github_file_url(file_path, raw=False):
    """Genera la URL para acceder a un archivo en GitHub."""
    from config.settings import GITHUB_REPO
    if raw:
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{file_path}"
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"


def resolve_champion_info(champion_id_raw, champion_name_from_api, all_champions, all_champion_names_to_ids):
    """
    Resuelve el nombre y ID del campeón de forma robusta.
    Intenta múltiples métodos para obtener información válida.
    """
    if isinstance(champion_id_raw, (int, float)):
        temp_id = int(champion_id_raw)
        if temp_id in all_champions:
            return all_champions[temp_id], temp_id
    elif isinstance(champion_id_raw, str) and champion_id_raw.isdigit():
        temp_id = int(champion_id_raw)
        if temp_id in all_champions:
            return all_champions[temp_id], temp_id
    
    if champion_name_from_api and champion_name_from_api != "Desconocido":
        champ_id = all_champion_names_to_ids.get(champion_name_from_api)
        if champ_id:
            return champion_name_from_api, champ_id
        return champion_name_from_api, "N/A"
    
    return "Desconocido", "N/A"


def chunk_list(lst, chunk_size):
    """Divide una lista en chunks de tamaño especificado."""
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


def safe_get(dictionary, key, default=None):
    """Obtiene un valor de un diccionario de forma segura."""
    try:
        return dictionary.get(key, default)
    except (AttributeError, TypeError):
        return default


def parse_json_safe(json_str, default=None):
    """Parsea un string JSON de forma segura."""
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def keep_alive():
    """Hilo que mantiene la aplicación activa con pings periódicos."""
    import time
    from datetime import datetime, timezone
    
    print("[keep_alive] Hilo keep_alive iniciado.")
    while True:
        try:
            time.sleep(600)  # 10 minutos
            print(f"[keep_alive] Ping: {datetime.now(timezone.utc)}")
        except Exception as e:
            print(f"[keep_alive] Error: {e}")
            time.sleep(60)
