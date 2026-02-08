"""
Servicio de gestión de jugadores.
Maneja PUUIDs, cuentas, y datos básicos de jugadores.
"""

from services.github_service import read_accounts_file, read_puuids, save_puuids
from services.riot_api import obtener_puuid
from config.settings import RIOT_API_KEY


def get_all_accounts():
    """Obtiene todas las cuentas registradas."""
    return read_accounts_file()


def get_all_puuids():
    """Obtiene el diccionario completo de PUUIDs."""
    success, puuids = read_puuids()
    return puuids if success else {}



def get_puuid_for_riot_id(riot_id):
    """Obtiene el PUUID para un Riot ID específico."""
    puuids = get_all_puuids()
    return puuids.get(riot_id)


def ensure_puuid_for_account(riot_id, api_key=None):
    """
    Asegura que un Riot ID tenga un PUUID asignado.
    Si no existe, lo obtiene de la API y lo guarda.
    
    Returns:
        tuple: (puuid, was_updated)
    """
    puuid = get_puuid_for_riot_id(riot_id)
    if puuid:
        return puuid, False
    
    # Obtener de la API
    api_key = api_key or RIOT_API_KEY
    if not api_key:
        return None, False
    
    try:
        game_name, tag_line = riot_id.split('#')
        puuid_info = obtener_puuid(api_key, game_name, tag_line)
        if puuid_info and 'puuid' in puuid_info:
            puuid = puuid_info['puuid']
            # Guardar
            puuids = get_all_puuids()
            puuids[riot_id] = puuid
            save_puuids(puuids)
            return puuid, True
    except Exception as e:
        print(f"[ensure_puuid_for_account] Error: {e}")
    
    return None, False


def get_player_display_name(riot_id):
    """Obtiene el nombre de display para un Riot ID."""
    accounts = get_all_accounts()
    for rid, name in accounts:
        if rid == riot_id:
            return name
    return riot_id


def get_riot_id_for_puuid(puuid):
    """Obtiene el Riot ID asociado a un PUUID."""
    puuids = get_all_puuids()
    for riot_id, pid in puuids.items():
        if pid == puuid:
            return riot_id
    return None


def get_all_players_with_puuids():
    """
    Obtiene todos los jugadores con sus PUUIDs.
    Retorna lista de tuplas: (riot_id, display_name, puuid)
    """
    accounts = get_all_accounts()
    puuids = get_all_puuids()
    
    result = []
    for riot_id, display_name in accounts:
        puuid = puuids.get(riot_id)
        result.append((riot_id, display_name, puuid))
    
    return result
