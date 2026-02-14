from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for
from collections import Counter
from datetime import datetime, timezone, timedelta
import gc
import time

from config.settings import DDRAGON_VERSION, QUEUE_NAMES, TARGET_TIMEZONE

from services.cache_service import player_cache, global_stats_cache
from services.github_service import read_global_stats, save_global_stats, read_stats_reload_config, save_stats_reload_config
from services.match_service import get_player_match_history, filter_matches_by_queue, filter_matches_by_champion
from services.stats_service import extract_global_records, calculate_global_stats



stats_bp = Blueprint('stats', __name__)

# Tiempo mínimo entre cálculos de estadísticas (en segundos)
# 24 horas = 86400 segundos
MIN_TIME_BETWEEN_CALCULATIONS = 86400




def _compile_all_matches(batch_size=50):
    """
    Compila todas las partidas de todos los jugadores.
    Esta operación es costosa y solo debe ejecutarse bajo demanda.
    Versión optimizada para Render Free Tier con procesamiento por lotes.
    """
    print("[stats] Compilando todas las partidas de todos los jugadores...")
    datos_jugadores, _ = player_cache.get()
    
    # Log estructura del primer jugador para diagnosticar
    if datos_jugadores:
        first_player = datos_jugadores[0]
        print(f"[_compile_all_matches] Estructura del primer jugador: {list(first_player.keys())}")
        print(f"[_compile_all_matches] Primer jugador - jugador: {first_player.get('jugador')}, game_name: {first_player.get('game_name')}, riot_id: {first_player.get('riot_id')}")

    
    all_champions = set()
    all_matches = []
    available_queue_ids = set()
    total_players = len(datos_jugadores)
    
    # Procesar por lotes para evitar timeouts en Render
    for i, j in enumerate(datos_jugadores):
        puuid = j.get('puuid')
        if puuid:
            try:
                historial = get_player_match_history(puuid, limit=-1)
                matches = historial.get('matches', [])
                
                for idx, match in enumerate(matches):
                    # Include both player name and riot_id for record display
                    match['jugador_nombre'] = j.get('jugador', 'N/A')
                    match['riot_id'] = j.get('game_name', 'N/A')
                    # Log first match to verify riot_id is being set
                    if idx == 0 and i < 3:  # Log first 3 players' first match
                        print(f"[_compile_all_matches] Player {j.get('jugador')}: game_name={j.get('game_name', 'N/A')}, riot_id set to: {match.get('riot_id', 'N/A')}")
                    all_matches.append((j.get('jugador'), match))

                    if match.get('champion_name'):
                        all_champions.add(match.get('champion_name'))

                    if match.get('queue_id'):
                        available_queue_ids.add(match.get('queue_id'))
                
                # Liberar memoria cada batch_size jugadores
                if (i + 1) % batch_size == 0:
                    print(f"[stats] Procesados {i + 1}/{total_players} jugadores...")
                    gc.collect()
                    
            except Exception as e:
                print(f"[stats] Error procesando jugador {j.get('jugador')}: {e}")
                continue
    
    print(f"[stats] Compiladas {len(all_matches)} partidas de {total_players} jugadores")
    
    # Verify sample match has riot_id
    if all_matches:
        sample_player, sample_match = all_matches[0]
        print(f"[_compile_all_matches] Sample match: player={sample_player}, riot_id={sample_match.get('riot_id', 'N/A')}, keys={list(sample_match.keys())[:5]}")
    
    return {

        'all_matches': all_matches,
        'all_champions': list(all_champions),
        'available_queue_ids': list(available_queue_ids),
        'total_players': total_players
    }



def _calculate_and_save_global_stats():
    """
    Calcula las estadísticas globales y las guarda en GitHub.
    Versión optimizada para Render Free Tier con manejo de memoria eficiente.
    Retorna los datos calculados.
    """
    print("[stats] Iniciando cálculo completo de estadísticas globales...")
    start_time = time.time()
    
    # Verificar si hay cálculo en progreso
    if global_stats_cache.is_calculating():
        print("[stats] Cálculo ya en progreso, esperando...")
        # Esperar hasta 60 segundos
        for _ in range(60):
            if not global_stats_cache.is_calculating():
                break
            time.sleep(1)
        else:
            print("[stats] Timeout esperando cálculo previo")
            return None
    
    global_stats_cache.set_calculating(True)
    
    try:
        # Compilar todas las partidas con procesamiento por lotes
        compiled = _compile_all_matches(batch_size=50)
        all_matches = compiled['all_matches']
        
        # Calcular estadísticas por cola (procesar en lotes pequeños)
        stats_by_queue = {}
        for queue_id in compiled['available_queue_ids']:
            queue_matches = [m for m in all_matches if m[1].get('queue_id') == queue_id]
            if queue_matches:  # Solo procesar si hay partidas
                stats_by_queue[str(queue_id)] = {
                    'total_matches': len(queue_matches),
                    'wins': sum(1 for _, m in queue_matches if m.get('win')),
                    'losses': sum(1 for _, m in queue_matches if not m.get('win')),
                    'records': extract_global_records(queue_matches)
                }
                # Liberar memoria
                del queue_matches
                gc.collect()
        
        # Calcular estadísticas generales (todas las colas)
        all_records = extract_global_records(all_matches)
        
        # Calcular stats por jugador (usando generador para ahorrar memoria)
        player_stats = {}
        for player_name, match in all_matches:
            if player_name not in player_stats:
                player_stats[player_name] = {'wins': 0, 'losses': 0, 'matches': []}
            player_stats[player_name]['matches'].append(match)
            if match.get('win'):
                player_stats[player_name]['wins'] += 1
            else:
                player_stats[player_name]['losses'] += 1
        
        # Formatear stats por jugador
        formatted_player_stats = []
        for player_name, stats in player_stats.items():
            total = stats['wins'] + stats['losses']
            formatted_player_stats.append({
                'summonerName': player_name,
                'total_partidas': total,
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': (stats['wins'] / total * 100) if total > 0 else 0
            })
        formatted_player_stats.sort(key=lambda x: x['total_partidas'], reverse=True)
        
        # Campeones más jugados (usando generador)
        champion_counts = Counter(m[1].get('champion_name') for m in all_matches if m[1].get('champion_name'))
        most_played_champions = champion_counts.most_common(10)
        
        # Calcular estadísticas por campeón (procesar en lotes)
        stats_by_champion = {}
        for i, champion_name in enumerate(compiled['all_champions']):
            champion_matches = [m for m in all_matches if m[1].get('champion_name') == champion_name]
            if champion_matches:
                stats_by_champion[champion_name] = {
                    'total_matches': len(champion_matches),
                    'wins': sum(1 for _, m in champion_matches if m.get('win')),
                    'losses': sum(1 for _, m in champion_matches if not m.get('win')),
                    'records': extract_global_records(champion_matches)
                }
            # Liberar memoria cada 10 campeones
            if (i + 1) % 10 == 0:
                del champion_matches
                gc.collect()
        
        # Calcular estadísticas por jugador (optimizado)
        stats_by_player = {}
        for player_name, stats in player_stats.items():
            player_matches = [m for m in all_matches if m[0] == player_name]
            total = stats['wins'] + stats['losses']
            
            # Optimización: no guardar todas las partidas en stats_by_player
            # Solo guardar referencias necesarias para filtros
            stats_by_player[player_name] = {
                'total_matches': total,
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': (stats['wins'] / total * 100) if total > 0 else 0,
                'records': extract_global_records([(player_name, m) for m in player_matches]),
                'champions_played': list(set(m[1].get('champion_name') for m in player_matches if m[1].get('champion_name'))),
                # NO guardar todas las partidas aquí - ocupa demasiada memoria
                'match_count': len(player_matches)
            }
            
            # Liberar memoria
            del player_matches
            if len(stats_by_player) % 5 == 0:
                gc.collect()

        
        # Construir objeto final con todas las desagregaciones
        # Usar el mismo formato de hora que index_json_generator
        dt_utc = datetime.now(timezone.utc)
        dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
        calculated_at_formatted = dt_target.strftime("%d/%m/%Y %H:%M:%S")
        
        global_stats_data = {
            'all_matches_count': len(all_matches),
            'total_players': compiled['total_players'],
            'all_champions': compiled['all_champions'],
            'available_queue_ids': compiled['available_queue_ids'],
            'player_stats': formatted_player_stats,
            'most_played_champions': most_played_champions,
            'global_records': all_records,
            'stats_by_queue': stats_by_queue,
            'stats_by_champion': stats_by_champion,
            'stats_by_player': stats_by_player,
            'calculated_at': calculated_at_formatted,
            'calculated_at_iso': datetime.now(timezone.utc).isoformat()  # Mantener ISO para compatibilidad
        }


        
        # Guardar en caché para acceso rápido
        global_stats_cache.set(global_stats_data, all_matches)
        
        # Guardar en GitHub
        success = save_global_stats(global_stats_data)
        if success:
            elapsed = time.time() - start_time
            print(f"[stats] Estadísticas globales guardadas correctamente en GitHub (tiempo: {elapsed:.2f}s)")
        else:
            print("[stats] ⚠️ Error guardando estadísticas globales en GitHub")
        
        # Liberar memoria
        del all_matches
        gc.collect()
        
        return global_stats_data
        
    finally:
        global_stats_cache.set_calculating(False)



@stats_bp.route('/estadisticas')
def estadisticas_globales():
    """
    Renderiza la página de estadísticas globales.
    Lee desde GitHub siempre - no usa caché en memoria.
    """
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    current_queue = request.args.get('queue', 'all')
    selected_champion = request.args.get('champion', 'all')
    force_refresh = request.args.get('refresh', '0') == '1'
    
    # Si se solicita actualización forzada
    if force_refresh:
        print("[estadisticas_globales] Forzando recálculo de estadísticas...")
        stats_data = _calculate_and_save_global_stats()
        flash('Estadísticas globales actualizadas correctamente', 'success')
        return redirect(url_for('stats.estadisticas_globales', queue=current_queue, champion=selected_champion))
    
    # Leer desde GitHub
    success, stats_data = read_global_stats()
    print(f"[estadisticas_globales] read_global_stats: success={success}, has_data={bool(stats_data)}")
    if stats_data:
        print(f"[estadisticas_globales] stats_data keys: {list(stats_data.keys())}")
        global_records_check = stats_data.get('global_records', {})
        print(f"[estadisticas_globales] global_records count: {len(global_records_check)}")
        if global_records_check:
            first_key = list(global_records_check.keys())[0]
            first_record = global_records_check[first_key]
            print(f"[estadisticas_globales] Sample record ({first_key}): player={first_record.get('player')}, riot_id={first_record.get('riot_id')}, value={first_record.get('value')}")
    
    # Obtener tiempo restante para próximo cálculo (siempre necesario para el botón)
    can_calc, seconds_left, time_str = _get_time_until_next_calculation()

    
    if not success or not stats_data:
        print("[estadisticas_globales] No hay estadísticas guardadas. Mostrando mensaje de actualización.")
        return render_template(
            'estadisticas.html',
            stats=[],
            global_stats=None,
            ddragon_version=DDRAGON_VERSION,
            champion_list=[],
            selected_champion=selected_champion,
            current_queue=current_queue,
            available_queues=[],
            needs_update=True,
            last_updated=None,
            can_calculate=can_calc,
            seconds_remaining=seconds_left,
            time_remaining=time_str
        )

    
    # Extraer datos base
    all_matches_count = stats_data.get('all_matches_count', 0)
    player_stats = stats_data.get('player_stats', [])
    most_played_champions = stats_data.get('most_played_champions', [])
    global_records = stats_data.get('global_records', {})
    available_queue_ids = stats_data.get('available_queue_ids', [])
    all_champions = stats_data.get('all_champions', [])
    calculated_at = stats_data.get('calculated_at')
    
    # Si hay filtros aplicados, usar datos pre-calculados o recalcular
    if current_queue != 'all' or selected_champion != 'all':
        print(f"[estadisticas_globales] Aplicando filtros - Cola: {current_queue}, Campeón: {selected_champion}")
        
        # Si solo se filtra por cola (sin filtro de campeón), usar datos pre-calculados
        if current_queue != 'all' and selected_champion == 'all':
            try:
                queue_id_str = str(int(current_queue))
                stats_by_queue = stats_data.get('stats_by_queue', {})
                
                if queue_id_str in stats_by_queue:
                    queue_stats = stats_by_queue[queue_id_str]
                    print(f"[estadisticas_globales] Usando datos pre-calculados para cola {queue_id_str}")
                    
                    # Usar récords pre-calculados
                    global_records = queue_stats.get('records', {})
                    all_matches_count = queue_stats.get('total_matches', 0)
                    wins = queue_stats.get('wins', 0)
                    losses = queue_stats.get('losses', 0)
                    overall_win_rate = (wins / all_matches_count * 100) if all_matches_count > 0 else 0
                    
                    # Para campeones más jugados y stats por jugador, necesitamos recalcular o usar caché
                    # Por ahora, usamos los datos globales como aproximación
                    print(f"[estadisticas_globales] Réccords cargados: {len(global_records)} récords")
                else:
                    print(f"[estadisticas_globales] No hay datos pre-calculados para cola {queue_id_str}")
                    # No hay datos para esta cola - mostrar vacío
                    from services.stats_service import _default_record, PERSONAL_RECORD_KEYS
                    global_records = {key: _default_record() for key in PERSONAL_RECORD_KEYS}
                    for key in global_records:
                        global_records[key]['value'] = None
                    overall_win_rate = 0
                    all_matches_count = 0
                    
            except (ValueError, TypeError) as e:
                print(f"[estadisticas_globales] Error convirtiendo queue_id: {e}")
        else:
            # Filtro combinado o solo por campeón - requiere recálculo desde caché
            print(f"[estadisticas_globales] Filtro combinado/campeón - requiere recálculo")
            
            # OPTIMIZACIÓN: Usar caché si está disponible
            cache_data = global_stats_cache.get()
            if cache_data.get('all_matches'):
                all_matches = cache_data['all_matches']
                print(f"[estadisticas_globales] Usando {len(all_matches)} partidas desde caché")
                
                # Aplicar filtros
                if current_queue != 'all':
                    try:
                        queue_id = int(current_queue)
                        all_matches = [(p, m) for p, m in all_matches if m.get('queue_id') == queue_id]
                    except (ValueError, TypeError):
                        pass
                
                if selected_champion != 'all':
                    all_matches = [(p, m) for p, m in all_matches if m.get('champion_name') == selected_champion]
                
                # Recalcular estadísticas
                if all_matches:
                    global_records = extract_global_records(all_matches)
                    wins = sum(1 for _, m in all_matches if m.get('win'))
                    total_matches = len(all_matches)
                    overall_win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
                    all_matches_count = total_matches
                    
                    # Recalcular campeones más jugados
                    champion_counts = Counter(m.get('champion_name') for _, m in all_matches if m.get('champion_name'))
                    most_played_champions = champion_counts.most_common(10)
                    
                    # Recalcular stats por jugador
                    player_stats_filtered = {}
                    for player_name, match in all_matches:
                        if player_name not in player_stats_filtered:
                            player_stats_filtered[player_name] = {'wins': 0, 'losses': 0, 'total_partidas': 0}
                        player_stats_filtered[player_name]['total_partidas'] += 1
                        if match.get('win'):
                            player_stats_filtered[player_name]['wins'] += 1
                        else:
                            player_stats_filtered[player_name]['losses'] += 1
                    
                    player_stats = []
                    for player_name, stats in player_stats_filtered.items():
                        total = stats['total_partidas']
                        player_stats.append({
                            'summonerName': player_name,
                            'total_partidas': total,
                            'wins': stats['wins'],
                            'losses': stats['losses'],
                            'win_rate': (stats['wins'] / total * 100) if total > 0 else 0
                        })
                    player_stats.sort(key=lambda x: x['total_partidas'], reverse=True)
                    
                    del all_matches
                    gc.collect()
                else:
                    # No hay partidas con estos filtros
                    from services.stats_service import _default_record, PERSONAL_RECORD_KEYS
                    global_records = {key: _default_record() for key in PERSONAL_RECORD_KEYS}
                    for key in global_records:
                        global_records[key]['value'] = None
                    overall_win_rate = 0
                    most_played_champions = []
                    player_stats = []
                    all_matches_count = 0
            else:
                # No hay caché disponible
                print(f"[estadisticas_globales] No hay datos en caché para filtros dinámicos")
                flash('Los filtros dinámicos requieren recalcular estadísticas. Por favor, actualiza las estadísticas primero.', 'warning')
                return redirect(url_for('stats.estadisticas_globales'))



    else:
        # Sin filtros - usar datos pre-calculados
        total_wins = sum(p.get('wins', 0) for p in player_stats)
        total_losses = sum(p.get('losses', 0) for p in player_stats)
        total = total_wins + total_losses
        overall_win_rate = (total_wins / total * 100) if total > 0 else 0
    
    # Construir objeto global_stats para el template
    global_stats = {
        'overall_win_rate': overall_win_rate,
        'total_games': all_matches_count,
        'most_played_champions': most_played_champions,
        'player_with_most_games': player_stats[0] if player_stats else None,
        'global_records': global_records
    }

    
    # La fecha ya viene formateada desde el cálculo, pero manejamos ambos formatos
    # (ISO antiguo vs nuevo formato formateado)
    last_updated = None
    if calculated_at:
        # Si ya está en formato dd/mm/yyyy, usar directamente
        if isinstance(calculated_at, str) and len(calculated_at) == 19 and calculated_at[2] == '/' and calculated_at[5] == '/':
            last_updated = calculated_at
        else:
            # Intentar parsear formato ISO
            try:
                # Quitar 'Z' si existe y reemplazar con +00:00
                iso_str = calculated_at.replace('Z', '+00:00')
                dt = datetime.fromisoformat(iso_str)
                # Convertir a timezone target
                dt_target = dt.astimezone(TARGET_TIMEZONE)
                last_updated = dt_target.strftime("%d/%m/%Y %H:%M:%S")
            except:
                # Si falla el parseo, usar el valor original
                last_updated = str(calculated_at)


    
    available_queues = [{'id': q_id, 'name': QUEUE_NAMES.get(q_id, f"Unknown ({q_id})")} 
                       for q_id in sorted(available_queue_ids)]

    # Obtener tiempo restante para próximo cálculo
    can_calc, seconds_left, time_str = _get_time_until_next_calculation()

    return render_template(
        'estadisticas.html', 
        stats=player_stats, 
        global_stats=global_stats, 
        ddragon_version=DDRAGON_VERSION, 
        champion_list=sorted(all_champions), 
        selected_champion=selected_champion, 
        current_queue=current_queue,
        available_queues=available_queues,
        needs_update=False,
        last_updated=last_updated,
        can_calculate=can_calc,
        seconds_remaining=seconds_left,
        time_remaining=time_str
    )



def _get_time_until_next_calculation():
    """
    Calcula el tiempo restante hasta el próximo cálculo permitido.
    Verifica también si hay una recarga forzada configurada en GitHub.
    Retorna (puede_calcular, segundos_restantes, tiempo_formateado)
    """
    # Verificar si hay recarga forzada desde GitHub
    try:
        forzar_recarga, sha, config = read_stats_reload_config()
        if forzar_recarga:
            print("[_get_time_until_next_calculation] Recarga forzada detectada desde GitHub")
            return True, 0, "0s (forzado)"
    except Exception as e:
        print(f"[_get_time_until_next_calculation] Error leyendo config de recarga: {e}")
    
    # Leer estadísticas globales desde GitHub
    try:
        success, stats_data = read_global_stats()
        print(f"[_get_time_until_next_calculation] read_global_stats: success={success}, has_data={bool(stats_data)}")
    except Exception as e:
        print(f"[_get_time_until_next_calculation] Error leyendo global_stats: {e}")
        success, stats_data = False, {}
    
    # Si no hay estadísticas o falló la lectura, permitir cálculo inmediato
    if not success or not stats_data:
        print("[_get_time_until_next_calculation] No hay estadísticas previas, permitiendo cálculo inmediato")
        return True, 0, "0s"
    
    calculated_at = stats_data.get('calculated_at')
    if not calculated_at:
        print("[_get_time_until_next_calculation] No hay timestamp de cálculo, permitiendo cálculo inmediato")
        return True, 0, "0s"
    
    try:
        # Parsear timestamp con manejo de timezone
        last_calc = datetime.fromisoformat(calculated_at.replace('Z', '+00:00'))
        # Asegurar que last_calc tenga timezone info
        if last_calc.tzinfo is None:
            last_calc = last_calc.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        elapsed = (now - last_calc).total_seconds()
        
        print(f"[_get_time_until_next_calculation] Último cálculo: {last_calc}, Ahora: {now}, Transcurrido: {elapsed:.0f}s")
        
        if elapsed >= MIN_TIME_BETWEEN_CALCULATIONS:
            print(f"[_get_time_until_next_calculation] Tiempo mínimo cumplido ({MIN_TIME_BETWEEN_CALCULATIONS}s), permitiendo cálculo")
            return True, 0, "0s"
        
        remaining = MIN_TIME_BETWEEN_CALCULATIONS - elapsed
        
        # Formatear tiempo restante (horas, minutos, segundos)
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        seconds = int(remaining % 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}s")
        
        time_str = " ".join(parts)
        
        print(f"[_get_time_until_next_calculation] Debe esperar {time_str} ({remaining:.0f}s)")
        return False, int(remaining), time_str
        
    except Exception as e:
        print(f"[_get_time_until_next_calculation] Error parseando timestamp: {e}")
        # En caso de error, permitir cálculo para no bloquear al usuario
        return True, 0, "0s"




@stats_bp.route('/estadisticas/actualizar', methods=['POST'])
def actualizar_estadisticas():
    """
    Endpoint para solicitar actualización de estadísticas globales.
    Verifica que no se haya calculado recientemente, a menos que haya recarga forzada.
    """
    print("[actualizar_estadisticas] Solicitud de actualización recibida")
    
    # Verificar si hay recarga forzada
    forzar_recarga = False
    config_sha = None
    config = None
    try:
        forzar_recarga, config_sha, config = read_stats_reload_config()
        print(f"[actualizar_estadisticas] Config recarga: forzar={forzar_recarga}, sha={config_sha[:8] if config_sha else 'None'}")
    except Exception as e:
        print(f"[actualizar_estadisticas] Error leyendo config de recarga: {e}")
        import traceback
        traceback.print_exc()
    
    # Verificar tiempo desde último cálculo (solo si no hay recarga forzada)
    if not forzar_recarga:
        print("[actualizar_estadisticas] Verificando tiempo desde último cálculo...")
        can_calculate, seconds_remaining, time_str = _get_time_until_next_calculation()
        
        print(f"[actualizar_estadisticas] Resultado: can_calculate={can_calculate}, time_str={time_str}")
        
        if not can_calculate:
            message = f'Las estadísticas se calcularon recientemente. Espera {time_str} antes de solicitar un nuevo cálculo.'
            print(f"[actualizar_estadisticas] BLOQUEADO: {message}")
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    'success': False,
                    'error': message,
                    'seconds_remaining': seconds_remaining,
                    'time_remaining': time_str
                }), 429  # Too Many Requests
            
            flash(message, 'warning')
            return redirect(url_for('stats.estadisticas_globales'))
    else:
        print("[actualizar_estadisticas] Recarga forzada activada - ignorando límite de tiempo")
    
    try:
        print("[actualizar_estadisticas] Iniciando cálculo de estadísticas...")
        stats_data = _calculate_and_save_global_stats()
        
        if stats_data is None:
            raise Exception("El cálculo de estadísticas retornó None")
        
        # Si había recarga forzada, desactivarla después de usarla
        if forzar_recarga and config_sha:
            try:
                new_config = {"forzar_recarga": "NO", "razon": "Recarga completada automáticamente"}
                save_success = save_stats_reload_config(new_config, sha=config_sha)
                print(f"[actualizar_estadisticas] Configuración de recarga reseteada: {save_success}")
            except Exception as e:
                print(f"[actualizar_estadisticas] Error reseteando config: {e}")
        
        # Calcular tiempo de procesamiento
        calculated_at = stats_data.get('calculated_at', 'Desconocido')
        
        success_message = 'Estadísticas globales actualizadas correctamente'
        if forzar_recarga:
            success_message += ' (recarga forzada desde GitHub)'
        
        print(f"[actualizar_estadisticas] ÉXITO: {success_message}")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'message': success_message,
                'calculated_at': calculated_at,
                'total_matches': stats_data.get('all_matches_count', 0),
                'total_players': stats_data.get('total_players', 0),
                'forced_reload': forzar_recarga
            })
        
        flash(success_message, 'success')
        return redirect(url_for('stats.estadisticas_globales'))
        
    except Exception as e:
        error_msg = str(e)
        print(f"[actualizar_estadisticas] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': False,
                'error': error_msg
            }), 500
        
        flash(f'Error al actualizar estadísticas: {error_msg}', 'error')
        return redirect(url_for('stats.estadisticas_globales'))
