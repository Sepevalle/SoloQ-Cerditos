from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for
from collections import Counter
from datetime import datetime, timezone, timedelta
from config.settings import DDRAGON_VERSION, QUEUE_NAMES
from services.cache_service import player_cache
from services.github_service import read_global_stats, save_global_stats
from services.match_service import get_player_match_history, filter_matches_by_queue, filter_matches_by_champion
from services.stats_service import extract_global_records, calculate_global_stats

stats_bp = Blueprint('stats', __name__)

# Tiempo mínimo entre cálculos de estadísticas (en segundos)
# 24 horas = 86400 segundos
MIN_TIME_BETWEEN_CALCULATIONS = 86400




def _compile_all_matches():
    """
    Compila todas las partidas de todos los jugadores.
    Esta operación es costosa y solo debe ejecutarse bajo demanda.
    """
    print("[stats] Compilando todas las partidas de todos los jugadores...")
    datos_jugadores, _ = player_cache.get()
    
    all_champions = set()
    all_matches = []
    available_queue_ids = set()

    for j in datos_jugadores:
        puuid = j.get('puuid')
        if puuid:
            historial = get_player_match_history(puuid, limit=-1)
            matches = historial.get('matches', [])
            
            for match in matches:
                all_matches.append((j.get('jugador'), match))
                if match.get('champion_name'):
                    all_champions.add(match.get('champion_name'))
                if match.get('queue_id'):
                    available_queue_ids.add(match.get('queue_id'))
    
    print(f"[stats] Compiladas {len(all_matches)} partidas de {len(datos_jugadores)} jugadores")
    
    return {
        'all_matches': all_matches,
        'all_champions': list(all_champions),
        'available_queue_ids': list(available_queue_ids),
        'total_players': len(datos_jugadores)
    }


def _calculate_and_save_global_stats():
    """
    Calcula las estadísticas globales y las guarda en GitHub.
    Retorna los datos calculados.
    """
    print("[stats] Iniciando cálculo completo de estadísticas globales...")
    
    # Compilar todas las partidas
    compiled = _compile_all_matches()
    all_matches = compiled['all_matches']
    
    # Calcular estadísticas por cola
    stats_by_queue = {}
    for queue_id in compiled['available_queue_ids']:
        queue_matches = [m for m in all_matches if m[1].get('queue_id') == queue_id]
        stats_by_queue[str(queue_id)] = {
            'total_matches': len(queue_matches),
            'wins': sum(1 for _, m in queue_matches if m.get('win')),
            'losses': sum(1 for _, m in queue_matches if not m.get('win')),
            'records': extract_global_records(queue_matches)
        }
    
    # Calcular estadísticas generales (todas las colas)
    all_records = extract_global_records(all_matches)
    
    # Calcular stats por jugador
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
    
    # Campeones más jugados
    champion_counts = Counter(m[1].get('champion_name') for m in all_matches if m[1].get('champion_name'))
    most_played_champions = champion_counts.most_common(10)
    
    # Calcular estadísticas por campeón
    stats_by_champion = {}
    for champion_name in compiled['all_champions']:
        champion_matches = [m for m in all_matches if m[1].get('champion_name') == champion_name]
        if champion_matches:
            stats_by_champion[champion_name] = {
                'total_matches': len(champion_matches),
                'wins': sum(1 for _, m in champion_matches if m.get('win')),
                'losses': sum(1 for _, m in champion_matches if not m.get('win')),
                'records': extract_global_records(champion_matches)
            }
    
    # Calcular estadísticas por jugador
    stats_by_player = {}
    for player_name, stats in player_stats.items():
        player_matches = [m for m in all_matches if m[0] == player_name]
        total = stats['wins'] + stats['losses']
        stats_by_player[player_name] = {
            'total_matches': total,
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': (stats['wins'] / total * 100) if total > 0 else 0,
            'records': extract_global_records([(player_name, m) for m in player_matches]),
            'champions_played': list(set(m[1].get('champion_name') for m in player_matches if m[1].get('champion_name')))
        }
    
    # Construir objeto final con todas las desagregaciones
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
        'calculated_at': datetime.now(timezone.utc).isoformat()
    }

    
    # Guardar en GitHub
    success = save_global_stats(global_stats_data)
    if success:
        print("[stats] Estadísticas globales guardadas correctamente en GitHub")
    else:
        print("[stats] ⚠️ Error guardando estadísticas globales en GitHub")
    
    return global_stats_data


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
            last_updated=None
        )
    
    # Extraer datos
    all_matches_count = stats_data.get('all_matches_count', 0)
    player_stats = stats_data.get('player_stats', [])
    most_played_champions = stats_data.get('most_played_champions', [])
    global_records = stats_data.get('global_records', {})
    available_queue_ids = stats_data.get('available_queue_ids', [])
    all_champions = stats_data.get('all_champions', [])
    calculated_at = stats_data.get('calculated_at')
    
    # Filtrar por cola si se especifica
    if current_queue != 'all':
        try:
            queue_id = int(current_queue)
            queue_stats = stats_data.get('stats_by_queue', {}).get(str(queue_id), {})
            if queue_stats:
                # Usar records específicos de la cola
                global_records = queue_stats.get('records', {})
                # Recalcular win rate para esta cola
                total_matches = queue_stats.get('total_matches', 0)
                wins = queue_stats.get('wins', 0)
                overall_win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
            else:
                overall_win_rate = 0
        except (ValueError, TypeError):
            overall_win_rate = 0
    else:
        # Calcular win rate general
        total_wins = sum(1 for p in player_stats for _ in range(p.get('wins', 0)))
        total_losses = sum(1 for p in player_stats for _ in range(p.get('losses', 0)))
        total = total_wins + total_losses
        overall_win_rate = (total_wins / total * 100) if total > 0 else 0
    
    # Filtrar por campeón usando datos pre-calculados
    if selected_champion != 'all':
        champion_stats = stats_data.get('stats_by_champion', {}).get(selected_champion, {})
        if champion_stats:
            global_records = champion_stats.get('records', {})
            total_matches = champion_stats.get('total_matches', 0)
            wins = champion_stats.get('wins', 0)
            overall_win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
            # Actualizar campeones más jugados para mostrar solo este campeón
            most_played_champions = [(selected_champion, total_matches)]
        else:
            flash(f'No hay datos para el campeón "{selected_champion}"', 'warning')
            overall_win_rate = 0

    
    # Construir objeto global_stats para el template
    global_stats = {
        'overall_win_rate': overall_win_rate,
        'total_games': all_matches_count,
        'most_played_champions': most_played_champions,
        'player_with_most_games': player_stats[0] if player_stats else None,
        'global_records': global_records
    }
    
    # Formatear fecha de última actualización
    last_updated = None
    if calculated_at:
        try:
            dt = datetime.fromisoformat(calculated_at)
            last_updated = dt.strftime("%d/%m/%Y %H:%M:%S")
        except:
            last_updated = calculated_at
    
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
    Retorna (puede_calcular, segundos_restantes, tiempo_formateado)
    """
    success, stats_data = read_global_stats()
    
    if not success or not stats_data:
        return True, 0, "0s"
    
    calculated_at = stats_data.get('calculated_at')
    if not calculated_at:
        return True, 0, "0s"
    
    try:
        last_calc = datetime.fromisoformat(calculated_at)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_calc).total_seconds()
        
        if elapsed >= MIN_TIME_BETWEEN_CALCULATIONS:
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

        
        return False, int(remaining), time_str
        
    except Exception as e:
        print(f"[_get_time_until_next_calculation] Error: {e}")
        return True, 0, "0s"


@stats_bp.route('/estadisticas/actualizar', methods=['POST'])
def actualizar_estadisticas():
    """
    Endpoint para solicitar actualización de estadísticas globales.
    Verifica que no se haya calculado recientemente.
    """
    print("[actualizar_estadisticas] Solicitud de actualización recibida")
    
    # Verificar tiempo desde último cálculo
    can_calculate, seconds_remaining, time_str = _get_time_until_next_calculation()
    
    if not can_calculate:
        message = f'Las estadísticas se calcularon recientemente. Espera {time_str} antes de solicitar un nuevo cálculo.'
        print(f"[actualizar_estadisticas] {message}")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': False,
                'error': message,
                'seconds_remaining': seconds_remaining,
                'time_remaining': time_str
            }), 429  # Too Many Requests
        
        flash(message, 'warning')
        return redirect(url_for('stats.estadisticas_globales'))
    
    try:
        stats_data = _calculate_and_save_global_stats()
        
        # Calcular tiempo de procesamiento
        calculated_at = stats_data.get('calculated_at', 'Desconocido')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'message': 'Estadísticas actualizadas correctamente',
                'calculated_at': calculated_at,
                'total_matches': stats_data.get('all_matches_count', 0),
                'total_players': stats_data.get('total_players', 0)
            })
        
        flash('Estadísticas globales actualizadas correctamente', 'success')
        return redirect(url_for('stats.estadisticas_globales'))
        
    except Exception as e:
        print(f"[actualizar_estadisticas] Error: {e}")
        import traceback
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
        
        flash(f'Error al actualizar estadísticas: {str(e)}', 'error')
        return redirect(url_for('stats.estadisticas_globales'))
