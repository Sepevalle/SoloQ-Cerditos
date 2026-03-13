"""
Servicio de actualización de datos en segundo plano.
Contiene los hilos worker para mantener los datos actualizados.
"""

import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from config.settings import (
    RIOT_API_KEY, RIOT_API_KEY_2, GITHUB_TOKEN, CACHE_UPDATE_INTERVAL,
    BASE_URL_DDRAGON, DDRAGON_VERSION, FULL_HISTORY_UPDATE_INTERVAL
)
from services.cache_service import player_cache, global_stats_cache, personal_records_cache
from services.github_service import (
    read_accounts_file,
    read_puuids,
    read_player_match_history,
    read_lp_history,
    ensure_permission_files_for_players,
)
from services.riot_api import (
    obtener_puuid, obtener_id_invocador, obtener_elo, 
    obtener_info_partida, actualizar_ddragon_data,
    ALL_CHAMPIONS
)
from services.player_service import get_all_accounts, get_all_puuids, ensure_puuids_for_accounts
from services.match_service import save_player_matches
from services.stats_service import calculate_personal_records
from services.data_processing import process_player_match_history
from services.index_json_generator import generate_index_json
from services.player_update_tracker import (
    update_player_status, mark_player_updated, needs_update, 
    get_players_needing_update, load_tracker, save_tracker
)
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
            puuids, nuevos_puuids = ensure_puuids_for_accounts(cuentas, api_key=RIOT_API_KEY)
            if nuevos_puuids:
                print(f"[actualizar_cache_periodicamente] PUUIDs nuevos guardados: {nuevos_puuids}")

            # Asegurar archivos de permisos por jugador y por partida (por Riot ID legible)
            ensure_permission_files_for_players([riot_id for riot_id, _ in cuentas if riot_id])
            
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
            player_cache.set(datos_jugadores)
            print(f"[actualizar_cache_periodicamente] Caché actualizada con {len(datos_jugadores)} entradas")

            # Generar JSON del index con los nuevos datos
            print("[actualizar_cache_periodicamente] Generando JSON del index...")
            if generate_index_json():
                print("[actualizar_cache_periodicamente] ✓ JSON del index generado correctamente")
            else:
                print("[actualizar_cache_periodicamente] ⚠ Error generando JSON del index")
            
            # Actualizar datos de DDragon
            actualizar_ddragon_data()

            
        except Exception as e:
            print(f"[actualizar_cache_periodicamente] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(CACHE_UPDATE_INTERVAL)


def _es_jugador_activo(puuid, days_threshold=7):
    """
    Determina si un jugador ha sido activo recientemente.
    Considera activo si ha jugado al menos una partida en los últimos 'days_threshold' días.
    
    Args:
        puuid: PUUID del jugador
        days_threshold: Días de antigüedad para considerar activo
    
    Returns:
        bool: True si el jugador está activo, False si está inactivo
    """
    from datetime import timezone, timedelta
    
    try:
        # Leer historial de partidas
        historial = read_player_match_history(puuid)
        matches = historial.get('matches', [])
        
        if not matches:
            # No hay historial, considerar inactivo
            return False
        
        # Obtener timestamp de la última partida
        latest_match = max(matches, key=lambda m: m.get('game_end_timestamp', 0))
        last_game_ts = latest_match.get('game_end_timestamp', 0)
        
        if last_game_ts == 0:
            return False
        
        # Calcular diferencia de tiempo
        now = datetime.now(timezone.utc)
        last_game_date = datetime.fromtimestamp(last_game_ts / 1000, tz=timezone.utc)
        days_since_last_game = (now - last_game_date).days
        
        return days_since_last_game <= days_threshold
        
    except Exception as e:
        print(f"[_es_jugador_activo] Error verificando actividad para {puuid}: {e}")
        # En caso de error,假设默认为 activo para no perder actualizaciones
        return True


def _get_jugadores_a_actualizar(cuentas, puuids, force_all=False):
    """
    Filtra los jugadores que necesitan actualización.
    Solo retorna jugadores activos o aquellos que nunca han sido actualizados.
    
    Args:
        cuentas: Lista de tuplas (riot_id, jugador_nombre)
        puuids: Diccionario de puuids por riot_id
        force_all: Si True, actualiza todos los jugadores (forzar actualización)
    
    Returns:
        Lista de tuplas (riot_id, jugador_nombre) a actualizar
    """
    from services.player_update_tracker import get_player_status, needs_update
    import time
    
    jugadores_a_actualizar = []
    jugadores_inactivos = 0
    jugadores_sin_cambio = 0
    
    for riot_id, jugador_nombre in cuentas:
        puuid = puuids.get(riot_id)
        if not puuid:
            continue
        
        # Si se fuerza actualización, incluir todos
        if force_all:
            jugadores_a_actualizar.append((riot_id, jugador_nombre, puuid))
            continue
        
        # Verificar estado del jugador
        update_type, reason = needs_update(puuid)
        
        if update_type == 'none':
            # No necesita actualización - verificar si está inactivo
            if not _es_jugador_activo(puuid, days_threshold=7):
                # Jugador inactivo - no actualizar
                jugadores_inactivos += 1
                print(f"[_get_jugadores_a_actualizar] ⏭️ {jugador_nombre}: Inactivo (sin actualizaciones pendientes)")
                continue
        
        # Necesita actualización (full, incremental) o es activo
        jugadores_a_actualizar.append((riot_id, jugador_nombre, puuid))
        
        if update_type != 'none':
            print(f"[_get_jugadores_a_actualizar] ✓ {jugador_nombre}: {update_type} - {reason}")
        else:
            print(f"[_get_jugadores_a_actualizar] ✓ {jugador_nombre}: Actualización completa (jugador activo)")
    
    print(f"[_get_jugadores_a_actualizar] Resumen: {len(jugadores_a_actualizar)} a actualizar, {jugadores_inactivos} inactivos omitidos")
    return jugadores_a_actualizar


def actualizar_historial_partidas_en_segundo_plano():
    """Actualiza el historial de partidas de los jugadores activos."""
    print("[actualizar_historial_partidas_en_segundo_plano] Hilo iniciado.")
    
    # Importar SEASON_START_TIMESTAMP para filtrar partidas
    from config.settings import SEASON_START_TIMESTAMP
    
    # IDs de colas permitidas (SoloQ y Flex)
    ALLOWED_QUEUE_IDS = {420, 440}  # 420 = RANKED_SOLO_5x5, 440 = RANKED_FLEX_SR
    
    while True:
        try:
            print("[actualizar_historial_partidas_en_segundo_plano] Iniciando actualización de historiales...")
            
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            # Obtener lista de jugadores a actualizar (solo activos)
            jugadores_a_actualizar = _get_jugadores_a_actualizar(cuentas, puuids)
            
            total_actualizados = 0
            total_partidas_nuevas = 0
            
            for riot_id, jugador_nombre, puuid in jugadores_a_actualizar:
                try:
                    # Verificar si el jugador está activo antes de hacer llamadas API
                    if not _es_jugador_activo(puuid, days_threshold=7):
                        print(f"[actualizar_historial] ⏭️ {jugador_nombre}: Omitido (inactivo)")
                        continue
                    
                    # Leer historial existente
                    historial = read_player_match_history(puuid)
                    existing_matches = historial.get('matches', [])
                    existing_ids = {m.get('match_id') for m in existing_matches}
                    
                    # Obtener solo las últimas partidas para reducir carga en la API
                    from services.riot_api import make_api_request
                    
                    all_new_match_ids = []
                    start = 0
                    count = 100  # Aumentado para traer más partidas por iteración
                    max_iterations = 20  # Aumentado para permitir hasta 2000 partidas por actualización
                    iteration = 0
                    consecutive_existing = 0  # Contador de partidas existentes consecutivas
                    
                    print(f"[actualizar_historial] Cargando últimas partidas para {jugador_nombre}...")
                    
                    while iteration < max_iterations:
                        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"
                        response = make_api_request(url)
                        
                        if not response:
                            break
                        
                        match_ids = response.json()
                        
                        if not match_ids:
                            break
                        
                        # Procesar IDs de partidas
                        new_in_this_batch = 0
                        first_batch_has_existing = False
                        for match_id in match_ids:
                            # Verificar si ya tenemos esta partida
                            if match_id in existing_ids:
                                # Ya la tenemos, incrementar contador consecutivo
                                consecutive_existing += 1
                                if iteration == 0:
                                    first_batch_has_existing = True
                            else:
                                # Es una partida nueva, resetear contador y añadir
                                consecutive_existing = 0
                                all_new_match_ids.append(match_id)
                                new_in_this_batch += 1
                        
                        if iteration == 0 and first_batch_has_existing:
                            print("[actualizar_historial] Primera pagina con partidas ya existentes; se detiene paginacion")
                            break
                        
                        # Siizamos menos de 'count' partidas, hemos llegado al final
                        if len(match_ids) < count:
                            print(f"[actualizar_historial] Última página alcanzada ({len(match_ids)} partidas)")
                            break
                        
                        # Optimización: si encontramos muchas partidas existentes consecutivas, paramos
                        # Esto indica que hemos alcanzado el final de las partidas nuevas
                        if consecutive_existing >= 30:
                            print(f"[actualizar_historial] Encontradas {consecutive_existing} partidas existentes consecutivas, deteniendo paginación")
                            break
                        
                        # Si no hay partidas nuevas en este batch, incrementar contador de seguridad
                        if new_in_this_batch == 0:
                            # Si llevamos varias iteraciones sin nada nuevo, podemos parar
                            if iteration > 3:
                                print(f"[actualizar_historial] No hay partidas nuevas en iteración {iteration}, deteniendo")
                                break
                        
                        start += count
                        iteration += 1
                        time.sleep(0.3)  # Pausa reducida
                    
                    print(f"[actualizar_historial] IDs a procesar para {jugador_nombre}: {len(all_new_match_ids)} nuevas")


                    
                    matches_to_add = []
                    skipped_old = 0
                    skipped_queue = 0
                    skipped_none = 0
                    
                    # Procesar partidas nuevas (que no existen en el historial)
                    for match_id in all_new_match_ids:
                        if match_id not in existing_ids:
                            match_info = obtener_info_partida((match_id, puuid, RIOT_API_KEY))
                            # Filtrar explícitamente valores None (remakes u errores)
                            if match_info is not None:
                                # Verificar que la partida sea desde el inicio de la temporada
                                match_ts = match_info.get('game_end_timestamp', 0)
                                if match_ts >= SEASON_START_TIMESTAMP * 1000:
                                    # FILTRAR POR TIPO DE COLA: Solo permitir SoloQ (420) y Flex (440)
                                    queue_id = match_info.get('queue_id')
                                    if queue_id in ALLOWED_QUEUE_IDS:
                                        matches_to_add.append(match_info)
                                    else:
                                        skipped_queue += 1
                                else:
                                    skipped_old += 1
                            else:
                                skipped_none += 1
                    
                    # Log resumen de partidas descartadas
                    if skipped_old > 0 or skipped_queue > 0 or skipped_none > 0:
                        print(f"[actualizar_historial] Descartadas para {jugador_nombre}: {skipped_old} antiguas, {skipped_queue} cola no permitida, {skipped_none} remakes/error")

                    
                    if matches_to_add:
                        # Actualizar timestamp de última partida juganda
                        from services.player_update_tracker import update_player_status
                        latest_match_ts = max([m.get('game_end_timestamp', 0) for m in matches_to_add], default=0)
                        if latest_match_ts > 0:
                            update_player_status(puuid, game_timestamp=latest_match_ts)
                        
                        # Leer historial de LP para calcular cambios de LP
                        try:
                            _, lp_history_data = read_lp_history()
                            player_lp_history = lp_history_data.get(puuid, {}) if lp_history_data else {}
                            
                            if player_lp_history:
                                # Filtrar solo partidas nuevas que necesitan cálculo de LP (no tienen LP asignado)
                                matches_needing_lp = [
                                    m for m in matches_to_add 
                                    if m.get('lp_change_this_game') is None
                                ]
                                
                                if matches_needing_lp:
                                    print(f"[actualizar_historial] Calculando LP para {jugador_nombre}: {len(matches_needing_lp)} partidas nuevas necesitan LP (usando {len(player_lp_history.get('RANKED_SOLO_5x5', []))} SoloQ y {len(player_lp_history.get('RANKED_FLEX_SR', []))} Flex snapshots)...")
                                    
                                    # Combinar partidas existentes con las nuevas para cálculo correcto
                                    all_matches_for_calc = existing_matches + matches_to_add
                                    
                                    # Procesar SOLO si hay partidas que necesitan LP
                                    processed_matches = process_player_match_history(all_matches_for_calc, player_lp_history)
                                    
                                    # Extraer solo las partidas nuevas procesadas que necesitaban LP
                                    new_match_ids = {m.get('match_id') for m in matches_to_add}
                                    processed_new_matches = [
                                        m for m in processed_matches 
                                        if m.get('match_id') in new_match_ids
                                    ]
                                    
                                    # Reemplazar matches_to_add con las versiones procesadas (con LP calculado)
                                    matches_to_add = processed_new_matches
                                    calculated_count = len([m for m in matches_to_add if m.get('lp_change_this_game') is not None])
                                    print(f"[actualizar_historial] LP calculado para {calculated_count} de {len(matches_needing_lp)} partidas nuevas")
                                else:
                                    print(f"[actualizar_historial] Todas las partidas nuevas de {jugador_nombre} ya tienen LP asignado, omitiendo cálculo")
                            else:
                                print(f"[actualizar_historial] No hay historial de LP para {jugador_nombre}, las partidas se guardarán sin cálculo de LP")
                        except Exception as e:
                            print(f"[actualizar_historial] Error calculando LP para {jugador_nombre}: {e}")
                            import traceback
                            traceback.print_exc()
                            # Continuar sin cálculo de LP si hay error

                        
                        # Combinar y ordenar por timestamp descendente (más reciente primero)
                        all_matches = existing_matches + matches_to_add
                        all_matches.sort(key=lambda x: x.get('game_end_timestamp', 0) if x.get('game_end_timestamp') else 0, reverse=True)
                        
                        # Verificar ordenación
                        if all_matches:
                            newest_ts = all_matches[0].get('game_end_timestamp', 0)
                            oldest_ts = all_matches[-1].get('game_end_timestamp', 0)
                            newest_date = datetime.fromtimestamp(newest_ts/1000, tz=timezone.utc)
                            oldest_date = datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc)
                            print(f"[actualizar_historial] Ordenación verificada: {len(all_matches)} partidas, más reciente: {newest_date}, más antigua: {oldest_date}")
                        
                        # Guardar
                        save_player_matches(puuid, {'matches': all_matches}, riot_id=riot_id)
                        
                        # Marcar como actualizado
                        from services.player_update_tracker import mark_player_updated
                        mark_player_updated(puuid, is_full_update=True)
                        
                        print(f"[actualizar_historial] {len(matches_to_add)} nuevas partidas guardadas para {jugador_nombre}. Total: {len(all_matches)}")
                        total_actualizados += 1
                        total_partidas_nuevas += len(matches_to_add)
                    else:
                        # Marcar como actualizado aunque no haya partidas nuevas
                        from services.player_update_tracker import mark_player_updated
                        mark_player_updated(puuid, is_full_update=True)
                        
                        print(f"[actualizar_historial] No hay partidas nuevas para {jugador_nombre}")

                    
                except Exception as e:
                    print(f"[actualizar_historial] Error procesando {jugador_nombre}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
                
                # Pausa entre jugadores
                time.sleep(2)
            
            print(f"[actualizar_historial_partidas_en_segundo_plano] Actualización completada: {total_actualizados} jugadores actualizados, {total_partidas_nuevas} partidas nuevas")
            
        except Exception as e:
            print(f"[actualizar_historial_partidas_en_segundo_plano] Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Esperar 48 horas antes de la siguiente actualización completa
        # 48 horas = 172800 segundos
        time.sleep(172800)



def recalcular_lp_partidas_existentes():
    """
    Recalcula el LP para partidas existentes que no lo tienen calculado.
    Esto asegura que partidas antiguas también tengan LP asignado.
    """
    print("[recalcular_lp] Iniciando recálculo de LP para partidas existentes...")
    
    try:
        from services.match_service import get_player_match_history, save_player_matches
        
        cuentas = get_all_accounts()
        puuids = get_all_puuids()
        _, lp_history_data = read_lp_history()
        
        total_recalculadas = 0
        total_jugadores = 0
        
        for riot_id, jugador_nombre in cuentas:
            puuid = puuids.get(riot_id)
            if not puuid:
                continue
            
            try:
                # Leer historial del jugador
                historial = get_player_match_history(puuid, riot_id=riot_id, limit=-1)
                matches = historial.get('matches', [])
                
                if not matches:
                    continue
                
                # Contar partidas sin LP
                matches_sin_lp = [
                    m for m in matches 
                    if m.get('lp_change_this_game') is None and m.get('queue_id') in [420, 440]
                ]
                
                if not matches_sin_lp:
                    continue  # Todas las partidas tienen LP
                
                print(f"[recalcular_lp] {jugador_nombre}: {len(matches_sin_lp)} partidas sin LP")
                
                # Obtener historial de LP del jugador
                player_lp_history = lp_history_data.get(puuid, {}) if lp_history_data else {}
                
                if not player_lp_history:
                    print(f"[recalcular_lp] {jugador_nombre}: No hay historial de LP disponible")
                    continue
                
                # Recalcular LP para todas las partidas
                from services.data_processing import process_player_match_history
                matches_procesadas = process_player_match_history(matches, player_lp_history)
                
                # Contar cuántas se recalcularon
                recalculadas = len([
                    m for m in matches_procesadas 
                    if m.get('lp_change_this_game') is not None and m.get('match_id') in 
                       [x.get('match_id') for x in matches_sin_lp]
                ])
                
                if recalculadas > 0:
                    # Guardar historial actualizado
                    save_player_matches(puuid, {'matches': matches_procesadas}, riot_id=riot_id)
                    print(f"[recalcular_lp] {jugador_nombre}: ✓ {recalculadas} partidas actualizadas con LP")
                    total_recalculadas += recalculadas
                    total_jugadores += 1
                
            except Exception as e:
                print(f"[recalcular_lp] Error procesando {jugador_nombre}: {e}")
                continue
        
        print(f"[recalcular_lp] Total: {total_recalculadas} partidas recalculadas en {total_jugadores} jugadores")
        return total_recalculadas
        
    except Exception as e:
        print(f"[recalcular_lp] Error general: {e}")
        import traceback
        traceback.print_exc()
        return 0


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
            global_stats_cache.set(
                {'all_champions': all_champions, 'available_queue_ids': available_queue_ids},
                all_matches
            )

            
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
            
            # Guardar cada conjunto de récords con su puuid como clave
            for puuid, records in all_records.items():
                personal_records_cache.set(f"{puuid}_all_all", records)
            print(f"[_calculate_personal_records] Cacheados récords para {len(all_records)} jugadores")

            
        except Exception as e:
            print(f"[_calculate_personal_records] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(3600)  # 1 hora


def _check_all_players_live_games():
    """
    Verifica el estado 'en partida' de todos los jugadores y actualiza el caché.
    Este hilo se ejecuta independientemente de la generación del JSON.
    
    OPTIMIZACIÓN: Reduce llamadas API a jugadores inactivos verificando actividad primero.
    - Jugadores muy activos (últimas 24h): cada 2 min
    - Jugadores activos (últimos 3 días): cada 10 min
    - Jugadores inactivos (más de 3 días): cada 30 min
    """
    print("[_check_all_players_live_games] Hilo iniciado.")
    
    from services.riot_api import esta_en_partida, obtener_nombre_campeon
    
    # Intervalos ajustables para optimizar llamadas API
    ACTIVE_CHECK_INTERVAL = 120        # 2 min para jugadores muy activos
    INACTIVE_CHECK_INTERVAL = 600      # 10 min para jugadores activos
    VERY_INACTIVE_CHECK_INTERVAL = 1800  # 30 min para jugadores muy inactivos
    
    # Seguimiento del último check de cada jugador (en memoria)
    player_last_check = {}
    
    while True:
        try:
            print("[_check_all_players_live_games] Verificando estado de todos los jugadores...")
            
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            jugadores_en_partida = 0
            total_verificados = 0
            verificados_ahora = 0
            omitidos_por_inactividad = 0
            
            now = time.time()
            
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                total_verificados += 1
                
                # Determinar si debemos verificar ahora según actividad del jugador
                last_check = player_last_check.get(puuid, 0)
                time_since_last_check = now - last_check
                
                # Verificar si el jugador está activo recientemente
                es_activo = _es_jugador_activo(puuid, days_threshold=3)  # 3 días = activo
                es_muy_activo = _es_jugador_activo(puuid, days_threshold=1)  # 1 día = muy activo
                
                # Determinar intervalo de verificación según actividad
                if es_muy_activo:
                    check_interval = ACTIVE_CHECK_INTERVAL  # 2 min
                elif es_activo:
                    check_interval = INACTIVE_CHECK_INTERVAL  # 10 min
                else:
                    check_interval = VERY_INACTIVE_CHECK_INTERVAL  # 30 min
                
                # Saltar si no toca verificar todavía
                if time_since_last_check < check_interval:
                    omitidos_por_inactividad += 1
                    continue
                
                verificados_ahora += 1
                player_last_check[puuid] = now
                
                try:
                    # Verificar si está en partida (usa RIOT_API_KEY_2 para separar llamadas)
                    game_data = esta_en_partida(RIOT_API_KEY_2, puuid)
                    
                    if game_data:
                        # Buscar el campeón del jugador
                        champion_name = None
                        for participant in game_data.get("participants", []):
                            if participant.get("puuid") == puuid:
                                champion_id = participant.get("championId")
                                champion_name = obtener_nombre_campeon(champion_id)
                                break
                        
                        print(f"[_check_all_players_live_games] ✓ {jugador_nombre}: EN PARTIDA con {champion_name}")
                        # Actualizar estado: estaba en partida
                        update_player_status(puuid, was_in_game=True)
                        jugadores_en_partida += 1
                    else:
                        # El jugador NO está en partida ahora
                        # Verificar si estaba en partida antes - si es así, acabado de terminar
                        from services.player_update_tracker import get_player_status
                        status = get_player_status(puuid)
                        
                        if status.get('was_in_game', False):
                            # El jugador estaba en partida y ahora no está - acabó de terminar
                            print(f"[_check_all_players_live_games] ⚡ {jugador_nombre}: TERMINÓ PARTIDA - Actualizando historial...")
                            # Actualizar historial del jugador
                            actualizar_jugador_especifico(puuid, riot_id, jugador_nombre)
                            # Resetear estado
                            update_player_status(puuid, was_in_game=False)
                        else:
                            if es_muy_activo:
                                print(f"[_check_all_players_live_games] ✗ {jugador_nombre}: No en partida")
                            elif es_activo:
                                print(f"[_check_all_players_live_games] ~ {jugador_nombre}: Inactivo (verificado cada 10min)")
                            else:
                                print(f"[_check_all_players_live_games] - {jugador_nombre}: Muy inactivo (verificado cada 30min)")
                    
                    # Pausa entre jugadores para no saturar la API
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"[_check_all_players_live_games] Error verificando {jugador_nombre}: {e}")
                    continue
            
            print(f"[_check_all_players_live_games] ✓ Verificación: {jugadores_en_partida}/{verificados_ahora} en partida, {omitidos_por_inactividad} omitidos (inactivos)")
            
        except Exception as e:
            print(f"[_check_all_players_live_games] Error general: {e}")
            import traceback
            traceback.print_exc()
        
        # El worker se ejecuta cada 2 minutos, pero cada jugador tiene su propio intervalo
        time.sleep(120)


def actualizar_jugador_especifico(puuid, riot_id, jugador_nombre):
    """
    Actualiza el historial de partidas de un jugador específico.
    Esta función se llama cuando un jugador termina una partida o cuando el usuario solicita actualización manual.
    
    Args:
        puuid: PUUID del jugador
        riot_id: Riot ID del jugador (formato: gameName#tagLine)
        jugador_nombre: Nombre para mostrar del jugador
    """
    print(f"[actualizar_jugador_especifico] Actualizando historial para {jugador_nombre} ({riot_id})")
    
    # Importar SEASON_START_TIMESTAMP para filtrar partidas
    from config.settings import SEASON_START_TIMESTAMP
    
    # IDs de colas permitidas (SoloQ y Flex)
    ALLOWED_QUEUE_IDS = {420, 440}
    
    try:
        # Leer historial existente
        historial = read_player_match_history(puuid)
        existing_matches = historial.get('matches', [])
        existing_ids = {m.get('match_id') for m in existing_matches}
        
        # Obtener solo las últimas partidas
        from services.riot_api import make_api_request
        
        all_new_match_ids = []
        start = 0
        count = 20  # Solo últimas 20 partidas para actualización incremental
        max_iterations = 3  # Máximo 60 partidas
        iteration = 0
        consecutive_existing = 0
        
        print(f"[actualizar_jugador_especifico] Buscando nuevas partidas para {jugador_nombre}...")
        
        while iteration < max_iterations:
            url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"
            response = make_api_request(url)
            
            if not response:
                break
            
            match_ids = response.json()
            
            if not match_ids:
                break
            
            # Procesar IDs de partidas
            for match_id in match_ids:
                if match_id in existing_ids:
                    consecutive_existing += 1
                else:
                    consecutive_existing = 0
                    all_new_match_ids.append(match_id)
            
            # Si hay muchas consecutivas, parar
            if consecutive_existing >= 10:
                print(f"[actualizar_jugador_especifico] Encontradas {consecutive_existing} partidas existentes, deteniendo")
                break
            
            if len(match_ids) < count:
                break
            
            start += count
            iteration += 1
            time.sleep(0.2)
        
        print(f"[actualizar_jugador_especifico] {len(all_new_match_ids)} partidas nuevas encontradas para {jugador_nombre}")
        
        if not all_new_match_ids:
            # No hay partidas nuevas, solo marcar actualizado
            mark_player_updated(puuid, is_full_update=False)
            return {'status': 'no_new_matches', 'matches_added': 0}
        
        matches_to_add = []
        
        # Procesar partidas nuevas
        for match_id in all_new_match_ids:
            match_info = obtener_info_partida((match_id, puuid, RIOT_API_KEY))
            if match_info is not None:
                match_ts = match_info.get('game_end_timestamp', 0)
                if match_ts >= SEASON_START_TIMESTAMP * 1000:
                    queue_id = match_info.get('queue_id')
                    if queue_id in ALLOWED_QUEUE_IDS:
                        matches_to_add.append(match_info)
                        # Actualizar timestamp de última partida jugado
                        from services.player_update_tracker import update_player_status
                        update_player_status(puuid, game_timestamp=match_ts)
        
        if matches_to_add:
            # Calcular LP si hay historial
            try:
                _, lp_history_data = read_lp_history()
                player_lp_history = lp_history_data.get(puuid, {}) if lp_history_data else {}
                
                if player_lp_history and matches_to_add:
                    matches_needing_lp = [m for m in matches_to_add if m.get('lp_change_this_game') is None]
                    if matches_needing_lp:
                        all_matches_for_calc = existing_matches + matches_to_add
                        processed_matches = process_player_match_history(all_matches_for_calc, player_lp_history)
                        
                        new_match_ids = {m.get('match_id') for m in matches_to_add}
                        processed_new_matches = [m for m in processed_matches if m.get('match_id') in new_match_ids]
                        matches_to_add = processed_new_matches
            except Exception as e:
                print(f"[actualizar_jugador_especifico] Error calculando LP: {e}")
            
            # Combinar y ordenar
            all_matches = existing_matches + matches_to_add
            all_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
            
            # Guardar
            save_player_matches(puuid, {'matches': all_matches}, riot_id=riot_id)
            
            # Marcar como actualizado
            mark_player_updated(puuid, is_full_update=False)
            
            print(f"[actualizar_jugador_especifico] ✓ {len(matches_to_add)} partidas guardadas para {jugador_nombre}")
            return {'status': 'success', 'matches_added': len(matches_to_add)}
        else:
            mark_player_updated(puuid, is_full_update=False)
            return {'status': 'no_valid_matches', 'matches_added': 0}
            
    except Exception as e:
        print(f"[actualizar_jugador_especifico] Error procesando {jugador_nombre}: {e}")
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'error': str(e)}


def forzar_actualizacion_todos_jugadores():
    """
    Fuerza la actualización completa de todos los jugadores.
    Se usa para el ciclo de 48 horas.
    """
    print("[forzar_actualizacion_todos_jugadores] Iniciando actualización completa de todos los jugadores...")
    
    cuentas = get_all_accounts()
    puuids = get_all_puuids()
    
    total_actualizados = 0
    total_partidas = 0
    
    for riot_id, jugador_nombre in cuentas:
        puuid = puuids.get(riot_id)
        if not puuid:
            continue
        
        try:
            result = actualizar_jugador_especifico(puuid, riot_id, jugador_nombre)
            if result.get('status') == 'success':
                total_actualizados += 1
                total_partidas += result.get('matches_added', 0)
            
            # Pausa entre jugadores
            time.sleep(2)
            
        except Exception as e:
            print(f"[forzar_actualizacion_todos_jugadores] Error con {jugador_nombre}: {e}")
            continue
    
    print(f"[forzar_actualizacion_todos_jugadores] ✓ Completado: {total_actualizados} jugadores actualizados, {total_partidas} partidas añadidas")
    return {'players_updated': total_actualizados, 'matches_added': total_partidas}


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
    print("[data_updater] ✓ Worker de historial iniciado (cada 48 horas)")
    
    # Worker de recálculo de LP (cada 30 minutos)
    def _recalcular_lp_periodicamente():
        """Recalcula LP para partidas existentes periódicamente."""
        # Esperar 5 minutos antes de la primera ejecución
        time.sleep(300)
        while True:
            try:
                recalcular_lp_partidas_existentes()
            except Exception as e:
                print(f"[_recalcular_lp_periodicamente] Error: {e}")
            # Esperar 30 minutos entre ejecuciones
            time.sleep(1800)
    
    lp_recalc_thread = threading.Thread(target=_recalcular_lp_periodicamente, daemon=True)
    lp_recalc_thread.start()
    print("[data_updater] ✓ Worker de recálculo de LP iniciado")

    
    # Worker de estadísticas globales
    stats_thread = threading.Thread(target=_calculate_and_cache_global_stats_periodically, daemon=True)
    stats_thread.start()
    print("[data_updater] ✓ Worker de estadísticas globales iniciado")
    
    # Worker de récords personales
    records_thread = threading.Thread(target=_calculate_and_cache_personal_records_periodically, daemon=True)
    records_thread.start()
    print("[data_updater] ✓ Worker de récords personales iniciado")
    
    # Worker de verificación de estado "en partida" (INDEPENDIENTE del JSON)
    live_game_thread = threading.Thread(target=_check_all_players_live_games, daemon=True)
    live_game_thread.start()
    print("[data_updater] ✓ Worker de verificación de 'en partida' iniciado (cada 2 min)")
    
    # Worker de generación de JSON para el index
    from services.index_json_generator import start_json_generator_thread
    start_json_generator_thread(interval_seconds=130)  # Cada ~2 minutos
    print("[data_updater] ✓ Worker de generación de JSON iniciado")
    
    print("[data_updater] Todos los workers de actualización iniciados")
