"""
Servicio de seguimiento de actualizaciones de jugadores.
Gestiona el estado de actualizaciones para optimizar llamadas a la API de Riot.
"""

import json
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ruta del archivo de seguimiento
TRACKER_FILE = "player_update_tracker.json"

# TTL de 48 horas para actualización completa
FULL_UPDATE_TTL = 48 * 60 * 60  # 48 horas en segundos

# TTL de 1 hora para actualización de jugador específico
SINGLE_UPDATE_TTL = 60 * 60  # 1 hora


def _get_tracker_path():
    """Obtiene la ruta del archivo de seguimiento."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", TRACKER_FILE)


def load_tracker():
    """Carga el tracker desde el archivo JSON."""
    try:
        path = _get_tracker_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[player_update_tracker] Error cargando tracker: {e}")
    return {}


def save_tracker(data):
    """Guarda el tracker en el archivo JSON."""
    try:
        path = _get_tracker_path()
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[player_update_tracker] Error guardando tracker: {e}")


def get_player_status(puuid):
    """
    Obtiene el estado de actualización de un jugador.
    Retorna: {
        'last_update': timestamp,
        'last_game_timestamp': timestamp del último juego,
        'was_in_game': bool,
        'last_full_update': timestamp última actualización completa
    }
    """
    tracker = load_tracker()
    return tracker.get(puuid, {
        'last_update': 0,
        'last_game_timestamp': 0,
        'was_in_game': False,
        'last_full_update': 0
    })


def update_player_status(puuid, was_in_game=False, game_timestamp=None):
    """
    Actualiza el estado de un jugador.
    
    Args:
        puuid: ID único del jugador
        was_in_game: Si el jugador estaba en partida
        game_timestamp: Timestamp de la última partida juganda (en milisegundos)
    """
    tracker = load_tracker()
    
    if puuid not in tracker:
        tracker[puuid] = {
            'last_update': 0,
            'last_game_timestamp': 0,
            'was_in_game': False,
            'last_full_update': 0
        }
    
    # Actualizar estado
    if was_in_game is not None:
        tracker[puuid]['was_in_game'] = was_in_game
    
    if game_timestamp and game_timestamp > tracker[puuid].get('last_game_timestamp', 0):
        tracker[puuid]['last_game_timestamp'] = game_timestamp
    
    tracker[puuid]['last_update'] = time.time()
    
    save_tracker(tracker)


def mark_player_updated(puuid, is_full_update=False):
    """
    Marca que un jugador ha sido actualizado.
    
    Args:
        puuid: ID único del jugador
        is_full_update: Si es una actualización completa (cada 48h)
    """
    tracker = load_tracker()
    
    if puuid not in tracker:
        tracker[puuid] = {
            'last_update': 0,
            'last_game_timestamp': 0,
            'was_in_game': False,
            'last_full_update': 0
        }
    
    if is_full_update:
        tracker[puuid]['last_full_update'] = time.time()
    
    tracker[puuid]['last_update'] = time.time()
    
    save_tracker(tracker)


def needs_update(puuid):
    """
    Determina si un jugador necesita actualización.
    
    Returns: ('full', 'incremental', 'none', reason)
    """
    status = get_player_status(puuid)
    now = time.time()
    
    # Verificar si necesita actualización completa (cada 48h)
    last_full = status.get('last_full_update', 0)
    if now - last_full >= FULL_UPDATE_TTL:
        return ('full', 'Actualización completa programada (48h)')
    
    # Verificar si estaba en partida y ahora no está -> actualización incremental
    if status.get('was_in_game', False):
        return ('incremental', 'El jugador terminó una partida')
    
    # Verificar si tiene partidas recientes (últimas 24h)
    last_game = status.get('last_game_timestamp', 0)
    if last_game > 0:
        # Convertir de milisegundos a segundos
        last_game_seconds = last_game / 1000
        if now - last_game_seconds < 24 * 60 * 60:  # 24 horas
            return ('incremental', 'El jugador tiene partidas recientes')
    
    # No necesita actualización
    return ('none', 'Sin actividad reciente')


def get_players_needing_update():
    """
    Obtiene lista de jugadores que necesitan actualización.
    
    Returns:
        dict: {puuid: ('full'|'incremental', reason)}
    """
    from services.player_service import get_all_puuids
    
    puuids = get_all_puuids()
    result = {}
    
    for riot_id, puuid in puuids.items():
        if puuid:
            update_type, reason = needs_update(puuid)
            if update_type != 'none':
                result[puuid] = (update_type, reason)
    
    return result


def reset_all_full_updates():
    """Reinicia el estado de actualizaciones completas (paraforzar actualización de todos)."""
    tracker = load_tracker()
    for puuid in tracker:
        tracker[puuid]['last_full_update'] = 0
    save_tracker(tracker)
    print("[player_update_tracker] ✓ Todas las actualizaciones completas han sido reiniciadas")


def cleanup_old_entries():
    """Limpia entradas antiguas del tracker (más de 7 días)."""
    tracker = load_tracker()
    now = time.time()
    cutoff = 7 * 24 * 60 * 60  # 7 días
    
    cleaned = 0
    to_remove = []
    
    for puuid, data in tracker.items():
        last_update = data.get('last_update', 0)
        if now - last_update > cutoff:
            to_remove.append(puuid)
    
    for puuid in to_remove:
        del tracker[puuid]
        cleaned += 1
    
    if cleaned > 0:
        save_tracker(tracker)
        print(f"[player_update_tracker] ✓ Limpiadas {cleaned} entradas antiguas")
    
    return cleaned
