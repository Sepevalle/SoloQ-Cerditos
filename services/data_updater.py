"""
Servicio de actualización de datos en segundo plano.
Contiene los hilos worker para mantener los datos actualizados.
"""

import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from config.settings import (
    RIOT_API_KEY, GITHUB_TOKEN, CACHE_UPDATE_INTERVAL,
    BASE_URL_DDRAGON, DDRAGON_VERSION
)
from services.cache_service import player_cache, global_stats_cache, personal_records_cache
from services.github_service import read_accounts_file, read_puuids, read_player_match_history, save_player_match_history
from services.riot_api import (
    obtener_puuid, obtener_id_invocador, obtener_elo, 
    obtener_info_partida, actualizar_ddragon_data,
    ALL_CHAMPIONS
)
from services.player_service import get_all_accounts, get_all_puuids
from services.stats_service import calculate_personal_records
from concurrent.futures import ThreadPoolExecutor


def keep_alive():
    """Hilo que mantiene la aplicación activa con pings periódicos."""
    print("[keep_alive] Hilo keep_alive iniciado.")
    while True:
        try:
            time.sleep(600)  # 10 minutos
            print(f"[keep_alive] Ping: {datetime.now(timezone.utc)}")
        except Exception as e:
            print(f"[keep_alive] Error: {e}")
            time.sleep(60)


def actualizar_cache_periodicamente():
    """Actualiza la caché de jugadores periódicamente."""
    print("[actualizar_cache_periodicamente] Hilo iniciado.")
    
    while True:
        try:
            print("[actualizar_cache_periodicamente] Actualizando caché de jugadores...")
            
            # Obtener cuentas y PUUIDs
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            datos_jugadores = []
            timestamp = time.time()
            
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                # Obtener datos del jugador
                summoner_data = obtener_id_invocador(RIOT_API_KEY, puuid)
                if not summoner_data:
                    continue
                
                profile_icon_id = summoner_data.get('profileIconId', 1)
                perfil_icon_url = f"{BASE_URL_DDRAGON}/cdn/{DDRAGON_VERSION}/img/profileicon/{profile_icon_id}.png"
                
                # Obtener ELO
                ranked_data = obtener_elo(RIOT_API_KEY, puuid) or []
                
                for entry in ranked_data:
                    queue_type = entry.get('queueType')
                    if queue_type in ['RANKED_SOLO_5x5', 'RANKED_FLEX_SR']:
                        tier = entry.get('tier', 'UNRANKED')
                        rank = entry.get('rank', '')
                        league_points = entry.get('leaguePoints', 0)
                        
                        # Calcular valor de clasificación
                        from utils.helpers import calcular_valor_clasificacion
                        valor_clasificacion = calcular_valor_clasificacion(tier, rank, league_points)
                        
                        datos_jugadores.append({
                            'jugador': jugador_nombre,
                            'game_name': riot_id,
                            'puuid': puuid,
                            'queue_type': queue_type,
                            'tier': tier,
                            'rank': rank,
                            'league_points': league_points,
                            'wins': entry.get('wins', 0),
                            'losses': entry.get('losses', 0),
                            'valor_clasificacion': valor_clasificacion,
                            'perfil_icon_url': perfil_icon_url
                        })
            
            # Actualizar caché
            player_cache.set(datos_jugadores, timestamp)
            print(f"[actualizar_cache_periodicamente] Caché actualizada con {len(datos_jugadores)} entradas")
            
            # Actualizar datos de DDragon
            actualizar_ddragon_data()
            
        except Exception as e:
            print(f"[actualizar_cache_periodicamente] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(CACHE_UPDATE_INTERVAL)


def actualizar_historial_partidas_en_segundo_plano():
    """Actualiza el historial de partidas de todos los jugadores."""
    print("[actualizar_historial_partidas_en_segundo_plano] Hilo iniciado.")
    
    while True:
        try:
            print("[actualizar_historial_partidas_en_segundo_plano] Iniciando actualización de historiales...")
            
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                try:
                    # Leer historial existente
                    historial = read_player_match_history(puuid)
                    existing_matches = historial.get('matches', [])
                    existing_ids = {m.get('match_id') for m in existing_matches}
                    
                    # Obtener nuevas partidas de la API
                    from services.riot_api import make_api_request
                    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=20&api_key={RIOT_API_KEY}"
                    response = make_api_request(url)
                    
                    if not response:
                        continue
                    
                    new_match_ids = response.json()
                    matches_to_add = []
                    
                    # Procesar solo partidas nuevas
                    for match_id in new_match_ids:
                        if match_id not in existing_ids:
                            match_info = obtener_info_partida((match_id, puuid, RIOT_API_KEY))
                            if match_info:
                                matches_to_add.append(match_info)
                    
                    if matches_to_add:
                        # Combinar y ordenar
                        all_matches = existing_matches + matches_to_add
                        all_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
                        
                        # Guardar
                        save_player_match_history(puuid, {'matches': all_matches})
                        print(f"[actualizar_historial] {len(matches_to_add)} nuevas partidas para {jugador_nombre}")
                    
                except Exception as e:
                    print(f"[actualizar_historial] Error procesando {jugador_nombre}: {e}")
                    continue
                
                # Pequeña pausa entre jugadores
                time.sleep(1)
            
            print("[actualizar_historial_partidas_en_segundo_plano] Actualización completada")
            
        except Exception as e:
            print(f"[actualizar_historial_partidas_en_segundo_plano] Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Esperar 5 minutos antes de la siguiente actualización
        time.sleep(300)


def _calculate_and_cache_global_stats_periodically():
    """Calcula y cachea estadísticas globales periódicamente."""
    print("[_calculate_and_cache_global_stats_periodically] Hilo iniciado.")
    
    while True:
        try:
            from services.stats_service import calculate_global_stats
            from services.player_service import get_all_accounts, get_all_puuids
            from services.match_service import get_player_match_history
            
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            all_matches = []
            all_champions = set()
            available_queue_ids = set()
            
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                historial = get_player_match_history(puuid, limit=-1)
                matches = historial.get('matches', [])
                
                for match in matches:
                    all_matches.append((jugador_nombre, match))
                    if match.get('champion_name'):
                        all_champions.add(match.get('champion_name'))
                    if match.get('queue_id'):
                        available_queue_ids.add(match.get('queue_id'))
            
            # Cachear datos brutos para estadísticas
            global_stats_cache.set({
                'all_matches': all_matches,
                'all_champions': all_champions,
                'available_queue_ids': available_queue_ids
            })
            
            print(f"[_calculate_global_stats] Cacheadas {len(all_matches)} partidas")
            
        except Exception as e:
            print(f"[_calculate_global_stats] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(300)  # 5 minutos


def _calculate_and_cache_personal_records_periodically():
    """Calcula y cachea récords personales periódicamente."""
    print("[_calculate_and_cache_personal_records_periodically] Hilo iniciado.")
    
    while True:
        try:
            from services.player_service import get_all_accounts, get_all_puuids, get_player_display_name, get_riot_id_for_puuid
            from services.match_service import get_player_match_history
            
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            all_records = {}
            
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                try:
                    historial = get_player_match_history(puuid, riot_id=riot_id, limit=-1)
                    matches = historial.get('matches', [])
                    
                    records = calculate_personal_records(
                        puuid, matches, jugador_nombre, riot_id
                    )
                    
                    all_records[puuid] = records
                    
                except Exception as e:
                    print(f"[_calculate_personal_records] Error con {jugador_nombre}: {e}")
                    continue
            
            personal_records_cache.set(all_records)
            print(f"[_calculate_personal_records] Cacheados récords para {len(all_records)} jugadores")
            
        except Exception as e:
            print(f"[_calculate_personal_records] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(3600)  # 1 hora


def start_data_updater(riot_api_key):
    """
    Función de inicio para el servicio de actualización de datos.
    Inicia todos los workers de actualización en segundo plano.
    
    Args:
        riot_api_key: API key de Riot Games
    """
    print("[data_updater] Iniciando servicio de actualización de datos...")
    
    if not riot_api_key:
        print("[data_updater] ⚠ Advertencia: RIOT_API_KEY no configurada")
        print("[data_updater] El servicio de actualización no funcionará correctamente")
        return
    
    # Iniciar workers en threads separados
    import threading
    
    # Worker de caché
    cache_thread = threading.Thread(target=actualizar_cache_periodicamente, daemon=True)
    cache_thread.start()
    print("[data_updater] ✓ Worker de caché iniciado")
    
    # Worker de historial de partidas
    history_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano, daemon=True)
    history_thread.start()
    print("[data_updater] ✓ Worker de historial iniciado")
    
    # Worker de estadísticas globales
    stats_thread = threading.Thread(target=_calculate_and_cache_global_stats_periodically, daemon=True)
    stats_thread.start()
    print("[data_updater] ✓ Worker de estadísticas globales iniciado")
    
    # Worker de récords personales
    records_thread = threading.Thread(target=_calculate_and_cache_personal_records_periodically, daemon=True)
    records_thread.start()
    print("[data_updater] ✓ Worker de récords personales iniciado")
    
    print("[data_updater] Todos los workers de actualización iniciados")
