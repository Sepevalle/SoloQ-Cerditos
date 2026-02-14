"""
Generador de JSON para la página principal (index.html).

Este módulo genera un archivo JSON pre-calculado con todas las estadísticas
necesarias para mostrar la tabla de jugadores, permitiendo una carga
instantánea de la página sin cálculos en tiempo real.
"""

import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from config.settings import (
    TARGET_TIMEZONE, 
    ACTIVE_SPLIT_KEY, 
    DDRAGON_VERSION,
    QUEUE_TYPE_MAP
)
from services.cache_service import player_cache
from services.github_service import read_peak_elo, read_lp_history
from services.match_service import get_player_match_history, calculate_streaks
from services.stats_service import get_top_champions_for_player
from services.riot_api import esta_en_partida, obtener_nombre_campeon, RIOT_API_KEY
from utils.helpers import calcular_valor_clasificacion

# Ruta del archivo JSON generado
INDEX_JSON_PATH = "stats_index.json"

# Lock para evitar escrituras concurrentes del JSON
_json_lock = threading.Lock()


def _get_peak_elo_key(jugador: Dict[str, Any]) -> str:
    """Genera la clave para peak elo basada en jugador."""
    return f"{ACTIVE_SPLIT_KEY}|{jugador['queue_type']}|{jugador['puuid']}"


def _calculate_player_stats(
    jugador: Dict[str, Any], 
    peak_elo_dict: Dict[str, int],
    lp_history: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calcula todas las estadísticas de un jugador para el index.
    
    Args:
        jugador: Datos básicos del jugador
        peak_elo_dict: Diccionario de peak ELO
        lp_history: Historial de LP
        
    Returns:
        Diccionario con todas las estadísticas calculadas
    """
    puuid = jugador.get('puuid')
    queue_type = jugador.get('queue_type')
    queue_id = 420 if queue_type == 'RANKED_SOLO_5x5' else 440 if queue_type == 'RANKED_FLEX_SR' else None
    
    # Calcular valor de clasificación
    valor = calcular_valor_clasificacion(
        jugador.get('tier', 'Unranked'),
        jugador.get('rank', 'IV'),
        jugador.get('league_points', 0)
    )
    jugador['valor_clasificacion'] = valor
    
    # Calcular peak ELO
    key = _get_peak_elo_key(jugador)
    peak = peak_elo_dict.get(key, 0)
    if valor > peak:
        peak = valor
        peak_elo_dict[key] = valor
    jugador['peak_elo'] = peak
    
    # Inicializar estadísticas por defecto
    stats = {
        'top_champion_stats': [],
        'current_win_streak': 0,
        'current_loss_streak': 0,
        'lp_change_24h': 0,
        'wins_24h': 0,
        'losses_24h': 0,
        'en_partida': False,
        'nombre_campeon': None
    }
    
    if not puuid:
        jugador.update(stats)
        return jugador
    
    try:
        # Obtener historial de partidas
        match_history = get_player_match_history(puuid, limit=20)
        matches = match_history.get('matches', [])
        
        # Calcular top campeones
        if queue_id:
            queue_matches_for_champs = [m for m in matches if m.get('queue_id') == queue_id]
            top_champions = get_top_champions_for_player(queue_matches_for_champs, limit=3)
        else:
            top_champions = get_top_champions_for_player(matches, limit=3)
        stats['top_champion_stats'] = top_champions
        
        # Calcular rachas
        if queue_id:
            queue_matches = [m for m in matches if m.get('queue_id') == queue_id]
            streaks = calculate_streaks(queue_matches)
            stats['current_win_streak'] = streaks.get('current_win_streak', 0)
            stats['current_loss_streak'] = streaks.get('current_loss_streak', 0)
        else:
            stats['current_win_streak'] = 0
            stats['current_loss_streak'] = 0
        
        # Calcular LP 24h
        if queue_id:
            now_utc = datetime.now(timezone.utc)
            one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
            lp_24h = wins_24h = losses_24h = 0
            recent_matches = [
                m for m in matches 
                if m.get('queue_id') == queue_id and m.get('game_end_timestamp', 0) > one_day_ago
            ]
            for m in recent_matches:
                lp_change = m.get('lp_change_this_game')
                if lp_change is not None:
                    lp_24h += lp_change
                if m.get('win'):
                    wins_24h += 1
                else:
                    losses_24h += 1
            stats['lp_change_24h'] = lp_24h
            stats['wins_24h'] = wins_24h
            stats['losses_24h'] = losses_24h
        
        # Verificar si está en partida (con timeout corto para no bloquear)
        try:
            if RIOT_API_KEY:
                game_data = esta_en_partida(RIOT_API_KEY, puuid)
                if game_data:
                    stats['en_partida'] = True
                    for participant in game_data.get("participants", []):
                        if participant.get("puuid") == puuid:
                            champion_id = participant.get("championId")
                            stats['nombre_campeon'] = obtener_nombre_campeon(champion_id)
                            break
        except Exception as e:
            # No bloquear si falla la verificación de partida
            pass
            
    except Exception as e:
        # Si falla el cálculo, usar valores por defecto
        pass
    
    jugador.update(stats)
    return jugador


def generate_index_json(force: bool = False) -> bool:
    """
    Genera el archivo JSON con todas las estadísticas para el index.
    
    Args:
        force: Si True, regenera aunque el JSON sea reciente
        
    Returns:
        True si se generó correctamente, False en caso contrario
    """
    start_time = time.time()
    print(f"[generate_index_json] Iniciando generación de JSON...")
    
    try:
        # Obtener datos de jugadores del caché
        datos_jugadores, timestamp = player_cache.get()
        
        if not datos_jugadores:
            print("[generate_index_json] ERROR: No hay datos de jugadores en caché")
            return False
        
        # Leer peak elo y LP history
        lectura_exitosa, peak_elo_dict = read_peak_elo()
        if not lectura_exitosa:
            peak_elo_dict = {}
            
        _, lp_history = read_lp_history()
        
        # Calcular estadísticas para cada jugador
        jugadores_procesados = []
        peak_elo_actualizado = False
        
        for jugador in datos_jugadores:
            try:
                jugador_procesado = _calculate_player_stats(jugador, peak_elo_dict, lp_history)
                jugadores_procesados.append(jugador_procesado)
                
                # Verificar si se actualizó el peak
                key = _get_peak_elo_key(jugador)
                if jugador['valor_clasificacion'] > peak_elo_dict.get(key, 0):
                    peak_elo_dict[key] = jugador['valor_clasificacion']
                    peak_elo_actualizado = True
            except Exception as e:
                print(f"[generate_index_json] Error procesando {jugador.get('jugador', 'unknown')}: {e}")
                # Incluir jugador con datos básicos aunque falle el cálculo
                jugadores_procesados.append(jugador)
        
        # Guardar peak elo actualizado si hubo cambios
        if peak_elo_actualizado:
            from services.github_service import save_peak_elo
            save_peak_elo(peak_elo_dict)
        
        # Preparar datos para el JSON
        dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
        ultima_actualizacion = dt_target.strftime("%d/%m/%Y %H:%M:%S")
        
        minutos_desde_actualizacion = int((time.time() - timestamp) / 60) if timestamp else 0
        
        json_data = {
            'datos_jugadores': jugadores_procesados,
            'ultima_actualizacion': ultima_actualizacion,
            'minutos_desde_actualizacion': minutos_desde_actualizacion,
            'timestamp_generacion': int(time.time()),
            'ddragon_version': DDRAGON_VERSION,
            'split_activo_nombre': "Temporada 2026 - Split 1",  # TODO: Obtener de settings
            'total_jugadores': len(jugadores_procesados),
            'cache_stale': player_cache.is_stale()
        }
        
        # Guardar JSON con lock para evitar escrituras concurrentes
        with _json_lock:
            with open(INDEX_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
        
        elapsed = time.time() - start_time
        print(f"[generate_index_json] ✓ JSON generado en {elapsed:.2f}s ({len(jugadores_procesados)} jugadores)")
        return True
        
    except Exception as e:
        print(f"[generate_index_json] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def load_index_json() -> Optional[Dict[str, Any]]:
    """
    Carga el JSON generado desde el disco.
    
    Returns:
        Diccionario con los datos del JSON, o None si no existe o está corrupto
    """
    try:
        with open(INDEX_JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"[load_index_json] ERROR: JSON corrupto: {e}")
        return None
    except Exception as e:
        print(f"[load_index_json] ERROR: {e}")
        return None


def get_json_age_seconds() -> int:
    """
    Obtiene la antigüedad del JSON en segundos.
    
    Returns:
        Segundos desde la última generación, o -1 si no existe
    """
    try:
        with open(INDEX_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            timestamp = data.get('timestamp_generacion', 0)
            return int(time.time() - timestamp)
    except:
        return -1


def is_json_fresh(max_age_seconds: int = 300) -> bool:
    """
    Verifica si el JSON es reciente.
    
    Args:
        max_age_seconds: Edad máxima considerada "fresca" (default: 5 min)
        
    Returns:
        True si el JSON existe y es más reciente que max_age_seconds
    """
    age = get_json_age_seconds()
    if age < 0:
        return False
    return age < max_age_seconds


def start_json_generator_thread(interval_seconds: int = 130):
    """
    Inicia un thread en background que regenera el JSON periódicamente.
    
    Args:
        interval_seconds: Intervalo entre generaciones (default: 130s = ~2min)
    """
    def _generator_loop():
        print(f"[json_generator_thread] Iniciado (intervalo: {interval_seconds}s)")
        while True:
            try:
                generate_index_json()
            except Exception as e:
                print(f"[json_generator_thread] Error: {e}")
            
            # Dormir hasta la próxima generación
            time.sleep(interval_seconds)
    
    thread = threading.Thread(target=_generator_loop, daemon=True)
    thread.start()
    print(f"[start_json_generator_thread] Thread iniciado")
