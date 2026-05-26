import json
from collections import defaultdict

from flask import Blueprint, abort, flash, redirect, render_template, request, Response, url_for
from datetime import datetime, timezone, timedelta
import time
import threading
import config.settings as settings

from config.settings import TARGET_TIMEZONE, ACTIVE_SPLIT_KEY, SPLITS, QUEUE_NAMES
from services.cache_service import (
    player_cache,
    player_stats_cache,
    page_data_cache,
    match_lookup_cache,
    achievements_cache,
    historial_global_cache,
)

from services.github_service import read_peak_elo, save_peak_elo, read_lp_history
from services.github_service import (
    read_player_permission,
    save_player_permission,
    get_permission_file_path,
    ensure_permission_files_for_players,
    read_stats_reload_config,
    save_stats_reload_config,
    read_stats_index,
    read_hours_report,
    save_hours_report,
    read_achievements_report,
    save_achievements_report,
)
from services.stats_service import get_top_champions_for_player
from services.match_service import get_player_match_history, calculate_streaks
from services.riot_api import esta_en_partida, obtener_nombre_campeon, RIOT_API_KEY
from services.live_game_service import get_active_live_games, get_live_game_by_id
from services.player_service import get_all_accounts, get_all_players_with_puuids, get_all_puuids
from services.achievements_service import (
    calculate_global_achievements,
    get_achievements_config_document,
    get_achievement_editor_options,
    save_achievements_config_document,
)
from utils.helpers import calcular_valor_clasificacion

# Importar el generador de JSON para el index
from services.index_json_generator import (
    load_index_json, 
    generate_index_json, 
    is_json_fresh,
    INDEX_JSON_PATH
)
from services.precompute_service import read_fresh as pre_read_fresh, write_async as pre_write_async, write_all_async as pre_write_all_async, write_github as pre_write_github






main_bp = Blueprint('main', __name__)


def _refresh_achievements_in_background():
    """Recalcula logros en background si no hay otro calculo en marcha."""
    if achievements_cache.is_calculating():
        return

    achievements_cache.set_calculating(True)
    try:
        print("[logros-background] Iniciando refresco de logros...")
        data = calculate_global_achievements()
        achievements_cache.set(data)
        print("[logros-background] Refresco de logros completado.")
    except Exception as e:
        print(f"[logros-background] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        achievements_cache.set_calculating(False)


def _get_peak_elo_key(jugador):
    """Genera la clave para peak elo basada en jugador."""
    return f"{ACTIVE_SPLIT_KEY}|{jugador['queue_type']}|{jugador['puuid']}"


def _actualizar_stats_en_background(datos_jugadores, lp_history):
    """Actualiza estadísticas en background sin bloquear la página."""
    try:
        print("[background] Iniciando actualización de estadísticas...")
        for jugador in datos_jugadores:
            try:
                puuid = jugador.get('puuid')
                queue_type = jugador.get('queue_type')
                queue_id = 420 if queue_type == 'RANKED_SOLO_5x5' else 440 if queue_type == 'RANKED_FLEX_SR' else None
                
                if not puuid or not queue_type:
                    continue
                
                # Verificar si el jugador está en partida (solo en background)
                if RIOT_API_KEY:
                    live_game_key = f"live_{puuid}"
                    now = time.time()
                    live_cached = getattr(player_stats_cache, '_live_game_cache', {}).get(live_game_key, {})
                    
                    if not live_cached or (now - live_cached.get('timestamp', 0)) >= 60:
                        # Caché expirado o no existe, actualizar
                        from services.riot_api import esta_en_partida, obtener_nombre_campeon
                        game_data = esta_en_partida(RIOT_API_KEY, puuid)
                        if game_data:
                            champion_name = None
                            for participant in game_data.get("participants", []):
                                if participant.get("puuid") == puuid:
                                    champion_id = participant.get("championId")
                                    champion_name = obtener_nombre_campeon(champion_id)
                                    break
                            if not hasattr(player_stats_cache, '_live_game_cache'):
                                player_stats_cache._live_game_cache = {}
                            player_stats_cache._live_game_cache[live_game_key] = {
                                'en_partida': True,
                                'nombre_campeon': champion_name,
                                'timestamp': now
                            }
                        else:
                            if not hasattr(player_stats_cache, '_live_game_cache'):
                                player_stats_cache._live_game_cache = {}
                            player_stats_cache._live_game_cache[live_game_key] = {
                                'en_partida': False,
                                'nombre_campeon': None,
                                'timestamp': now
                            }
                
                # Calcular estadísticas si no están en caché o están antiguas
                cached_stats = player_stats_cache.get(puuid, queue_type)
                if not cached_stats:
                    match_history = get_player_match_history(puuid, limit=20)
                    matches = match_history.get('matches', [])
                    
                    if queue_id:
                        queue_matches_for_champs = [m for m in matches if m.get('queue_id') == queue_id]
                        top_champions = get_top_champions_for_player(queue_matches_for_champs, limit=3)
                    else:
                        top_champions = get_top_champions_for_player(matches, limit=3)
                    
                    if queue_id:
                        queue_matches = [m for m in matches if m.get('queue_id') == queue_id]
                        streaks = calculate_streaks(queue_matches)
                    else:
                        streaks = {'current_win_streak': 0, 'current_loss_streak': 0}
                    
                    # Calcular LP 24h
                    lp_24h = wins_24h = losses_24h = 0
                    if queue_id:
                        now_utc = datetime.now(timezone.utc)
                        one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
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
                    
                    # Guardar en caché
                    stats_to_cache = {
                        'top_champion_stats': top_champions,
                        'current_win_streak': streaks.get('current_win_streak', 0),
                        'current_loss_streak': streaks.get('current_loss_streak', 0),
                        'lp_change_24h': lp_24h,
                        'wins_24h': wins_24h,
                        'losses_24h': losses_24h,
                        'en_partida': False,
                        'nombre_campeon': None
                    }
                    player_stats_cache.set(puuid, queue_type, stats_to_cache)
                    
            except Exception as e:
                print(f"[background] Error actualizando {jugador.get('jugador', 'unknown')}: {e}")
                continue
        
        print("[background] Actualización de estadísticas completada")
    except Exception as e:
        print(f"[background] Error general: {e}")


@main_bp.route('/')
def index():
    """
    Renderiza la página principal con la lista de jugadores.
    Usa un JSON pre-generado para carga instantánea.
    """
    print("[index] Petición recibida para la página principal.")
    
    # Intentar servir HTML precomputado si existe
    pre_key = 'index'
    try:
        # index: 5 minutes
        content = pre_read_fresh(pre_key, max_age_seconds=300)
        if content:
            return Response(content, mimetype='text/html')
    except Exception:
        pass

    # Intentar cargar desde JSON pre-generado
    json_data = load_index_json()
    
    # Si no existe JSON o está muy antiguo (>10 min), generarlo sincrónicamente
    if json_data is None:
        print("[index] JSON no encontrado, generando sincrónicamente...")
        if generate_index_json(force=True):
            json_data = load_index_json()
        else:
            # Fallback: usar datos del caché básico sin estadísticas
            print("[index] ERROR: No se pudo generar JSON, usando caché básico")
            datos_jugadores, timestamp = player_cache.get()
            return render_template('index.html',
                                   datos_jugadores=datos_jugadores,
                                   active_live_games=get_active_live_games(),
                                   ultima_actualizacion="N/A",
                                   ddragon_version=settings.DDRAGON_VERSION,
                                   split_activo_nombre=SPLITS[ACTIVE_SPLIT_KEY]['name'],
                                   has_player_data=bool(datos_jugadores),
                                   cache_stale=True,
                                   minutos_desde_actualizacion=999)
    
    # Si el JSON existe pero está antiguo (>5 min), iniciar regeneración en background
    elif not is_json_fresh(max_age_seconds=300):
        print("[index] JSON antiguo detectado, iniciando regeneración en background...")
        # Iniciar thread para regenerar sin bloquear
        thread = threading.Thread(target=generate_index_json, daemon=True)
        thread.start()
    
    # Extraer datos del JSON
    datos_jugadores = json_data.get('datos_jugadores', [])
    ultima_actualizacion = json_data.get('ultima_actualizacion', 'N/A')
    minutos_desde_actualizacion = json_data.get('minutos_desde_actualizacion', 0)
    cache_stale = json_data.get('cache_stale', False)
    split_activo_nombre = json_data.get('split_activo_nombre', SPLITS[ACTIVE_SPLIT_KEY]['name'])
    
    print(f"[index] Renderizando index.html con JSON ({len(datos_jugadores)} jugadores, "
          f"actualizado hace {minutos_desde_actualizacion} min)")
    
    # Añadir timestamp de generación y renderizar a string para posible precomputado
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rendered = render_template('index.html', 
                           datos_jugadores=datos_jugadores,
                           active_live_games=get_active_live_games(),
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=settings.DDRAGON_VERSION,
                           split_activo_nombre=split_activo_nombre,
                           has_player_data=bool(datos_jugadores),
                           cache_stale=cache_stale,
                           minutos_desde_actualizacion=minutos_desde_actualizacion,
                           generated_at=generated_at)

    # Guardar el HTML generado en background para próximas peticiones (local + GitHub si está configurado)
    try:
        pre_write_all_async(pre_key, rendered)
    except Exception:
        try:
            pre_write_async(pre_key, rendered)
        except Exception:
            pass

    return Response(rendered, mimetype='text/html')


@main_bp.route('/partida-en-vivo/<game_id>')
def detalle_partida_en_vivo(game_id):
    live_game = get_live_game_by_id(game_id)
    if not live_game:
        abort(404)

    return render_template(
        'partida_en_vivo.html',
        live_game=live_game,
        ddragon_version=settings.DDRAGON_VERSION,
    )


def _build_historial_global_dataset():
    """Construye un snapshot ligero del historial global."""

    from services.player_service import get_all_accounts, get_all_puuids

    cuentas = get_all_accounts()
    puuids = get_all_puuids()
    players_total = len(cuentas)
    players_with_puuid = 0
    all_matches = []

    for riot_id, jugador_nombre in cuentas:
        puuid = puuids.get(riot_id)
        if not puuid:
            continue

        players_with_puuid += 1
        historial = get_player_match_history(puuid, limit=-1)
        matches = historial.get('matches', [])

        for match in matches:
            match_summary = {
                'match_id': match.get('match_id'),
                'jugador_nombre': jugador_nombre,
                'riot_id': riot_id,
                'champion_name': match.get('champion_name'),
                'win': match.get('win'),
                'kills': match.get('kills', 0),
                'deaths': match.get('deaths', 0),
                'assists': match.get('assists', 0),
                'queue_id': match.get('queue_id'),
                'lp_change_this_game': match.get('lp_change_this_game'),
                'game_end_timestamp': match.get('game_end_timestamp', 0),
            }
            all_matches.append(match_summary)

            match_id = match_summary.get('match_id')
            if match_id:
                match_lookup_cache.set(match_id, {
                    'game_name': riot_id,
                    'puuid': puuid
                })

    all_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    dataset = {
        'matches': all_matches,
        'players_total': players_total,
        'players_with_puuid': players_with_puuid,
    }
    return dataset


def _refresh_historial_global_in_background():
    """Recalcula el historial global en background si no hay otro calculo en marcha."""
    if historial_global_cache.is_calculating():
        return

    historial_global_cache.set_calculating(True)
    try:
        print("[historial-global-background] Iniciando refresco...")
        dataset = _build_historial_global_dataset()
        historial_global_cache.set(dataset)
        print("[historial-global-background] Refresco completado.")
    except Exception as e:
        print(f"[historial-global-background] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        historial_global_cache.set_calculating(False)


@main_bp.route('/historial_global')
def historial_global():
    """Renderiza la página de historial global de partidas."""
    print("[historial_global] Petición recibida.")
    try:
        page = request.args.get('page', 1, type=int)
        if page is None or page < 1:
            page = 1
        per_page = 15

        cache_data = historial_global_cache.get()
        dataset = cache_data.get('data')
        if not dataset:
            dataset = _build_historial_global_dataset()
            historial_global_cache.set(dataset)
            cache_data = historial_global_cache.get()
        elif historial_global_cache.is_stale() and not historial_global_cache.is_calculating():
            threading.Thread(target=_refresh_historial_global_in_background, daemon=True).start()
        all_matches = dataset.get('matches', [])
        players_total = dataset.get('players_total', 0)
        players_with_puuid = dataset.get('players_with_puuid', 0)

        total_matches = len(all_matches)
        total_pages = max(1, (total_matches + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        end = start + per_page
        page_matches = all_matches[start:end]
        page_start = max(1, page - 2)
        page_end = min(total_pages, page + 2)
        page_numbers = list(range(page_start, page_end + 1))
        
        # Intentar servir HTML precomputado por página (solo para primeras 5 páginas)
        pre_key = f"historial_global_page_{page}"
        try:
            if page <= 5:
                # historial paginado: 120 minutes
                content = pre_read_fresh(pre_key, max_age_seconds=7200)
                if content:
                    return Response(content, mimetype='text/html')
        except Exception:
            pass

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rendered = render_template('historial_global.html',
                             matches=page_matches,
                             page=page,
                             per_page=per_page,
                             total_matches=total_matches,
                             total_pages=total_pages,
                             page_numbers=page_numbers,
                             players_total=players_total,
                             players_with_puuid=players_with_puuid,
                             ddragon_version=settings.DDRAGON_VERSION,
                             has_player_data=True,
                             generated_at=generated_at)

        try:
            pre_write_all_async(pre_key, rendered)
        except Exception:
            try:
                pre_write_async(pre_key, rendered)
            except Exception:
                pass

        return Response(rendered, mimetype='text/html')
    except Exception as e:
        print(f"[historial_global] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500


@main_bp.route('/logros')
def logros():
    """Renderiza la página de logros globales por jugador."""
    print("[logros] Petición recibida.")
    try:
        success, data = read_achievements_report()
        if not success:
            data = {}
        can_generate, seconds_remaining, time_remaining = _get_time_until_next_achievements_generation(data)
        return render_template(
            'logros.html',
            players=data.get('players', []),
            achievements_catalog=data.get('achievements_catalog', []),
            achievements_view=data.get('achievements_view', []),
            negative_achievements_view=data.get('negative_achievements_view', []),
            secret_achievements_view=data.get('secret_achievements_view', []),
            achievements_config_source=data.get('config_source', 'unknown'),
            achievements_config_errors=data.get('config_errors', []),
            global_stats=data.get('global_stats', {}),
            needs_update=not bool(data),
            can_generate=can_generate,
            seconds_remaining=seconds_remaining,
            time_remaining=time_remaining,
            generated_at=data.get("generated_at", "N/A"),
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True
        )
    except Exception as e:
        print(f"[logros] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500


def _get_time_until_next_achievements_generation(snapshot=None):
    if not snapshot:
        success, snapshot = read_achievements_report()
        if not success:
            snapshot = {}

    calculated_at = snapshot.get("calculated_at_iso")
    if not calculated_at:
        return True, 0, "0s"

    try:
        last_calc = datetime.fromisoformat(str(calculated_at).replace("Z", "+00:00"))
        if last_calc.tzinfo is None:
            last_calc = last_calc.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_calc).total_seconds()
        interval = settings.GLOBAL_STATS_UPDATE_INTERVAL
        if elapsed >= interval:
            return True, 0, "0s"
        remaining = int(interval - elapsed)
        return False, remaining, _format_hours_wait(remaining)
    except Exception as e:
        print(f"[_get_time_until_next_achievements_generation] Error parseando fecha: {e}")
        return True, 0, "0s"


@main_bp.route('/logros/actualizar', methods=['POST'])
def actualizar_logros():
    """Genera y guarda el snapshot de logros, como maximo cada 24h."""
    success, snapshot = read_achievements_report()
    can_generate, _seconds_remaining, time_remaining = _get_time_until_next_achievements_generation(snapshot if success else {})
    if not can_generate:
        flash(f"El informe de logros se genero recientemente. Espera {time_remaining}.", "warning")
        return redirect(url_for("main.logros"))

    try:
        data = calculate_global_achievements()
        data["generated_at"] = datetime.now(TARGET_TIMEZONE).strftime("%d/%m/%Y %H:%M")
        data["calculated_at_iso"] = datetime.now(timezone.utc).isoformat()
        achievements_cache.set(data)
        if save_achievements_report(data):
            flash("Informe de logros actualizado correctamente.", "success")
        else:
            flash("No se pudo guardar el informe de logros. Revisa GITHUB_TOKEN o permisos.", "danger")
    except Exception as e:
        print(f"[actualizar_logros] Error: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error al generar el informe de logros: {e}", "danger")

    return redirect(url_for("main.logros"))


def _build_hours_report_data():
    """Construye el snapshot de horas jugadas por jugador."""
    def format_seconds(seconds):
        seconds = int(seconds or 0)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes:02d}m"

    accounts = dict(get_all_accounts())
    puuids = get_all_puuids()
    index_ok, index_data = read_stats_index()
    index_rows = index_data.get("datos_jugadores", []) if index_ok and isinstance(index_data, dict) else []

    players_by_riot_id = {
        riot_id: {
            "riot_id": riot_id,
            "display_name": accounts.get(riot_id) or riot_id,
            "puuid": puuid,
        }
        for riot_id, puuid in puuids.items()
        if puuid
    }
    ranked_visible_by_player = defaultdict(int)

    for jugador in index_rows:
        riot_id = jugador.get("game_name") or jugador.get("riot_id")
        puuid = jugador.get("puuid")
        display_name = jugador.get("jugador") or accounts.get(riot_id) or riot_id
        if riot_id and puuid:
            players_by_riot_id[riot_id] = {
                "riot_id": riot_id,
                "display_name": display_name,
                "puuid": puuid,
            }
        if display_name:
            ranked_visible_by_player[display_name] += int(jugador.get("wins") or 0) + int(jugador.get("losses") or 0)

    players = [
        (entry["riot_id"], entry["display_name"], entry["puuid"])
        for entry in players_by_riot_id.values()
    ]
    players.sort(key=lambda item: (item[1] or item[0]).lower())
    rows_by_player = {}
    unique_matches = {}
    queue_totals = defaultdict(lambda: {"seconds": 0, "matches": 0})
    latest_timestamp = 0
    processed_puuids = set()

    for riot_id, display_name, puuid in players:
        if not puuid:
            continue
        if puuid in processed_puuids:
            continue
        processed_puuids.add(puuid)

        player_key = display_name or riot_id
        if player_key not in rows_by_player:
            rows_by_player[player_key] = {
                "player_name": player_key,
                "accounts": set(),
                "seconds": 0,
                "recent_seconds": 0,
                "matches": 0,
                "wins": 0,
                "losses": 0,
                "riot_ids": set(),
                "queues": defaultdict(lambda: {"seconds": 0, "matches": 0}),
                "last_played_ts": 0,
                "match_times": [],
            }

        row = rows_by_player[player_key]
        row["accounts"].add(riot_id)
        row["riot_ids"].add(riot_id)
        historial = get_player_match_history(puuid, riot_id=riot_id, limit=-1)
        raw_matches = historial.get("matches", [])
        matches = []
        seen_match_ids = set()
        for match in raw_matches:
            match_id = match.get("match_id")
            if match_id and match_id in seen_match_ids:
                continue
            if match_id:
                seen_match_ids.add(match_id)
            matches.append(match)

        for match in matches:
            duration = int(match.get("game_duration") or 0)
            if duration <= 0:
                continue

            queue_id = match.get("queue_id") or 0
            timestamp = int(match.get("game_end_timestamp") or 0)
            latest_timestamp = max(latest_timestamp, timestamp)

            row["seconds"] += duration
            row["matches"] += 1
            row["last_played_ts"] = max(row["last_played_ts"], timestamp)
            row["match_times"].append((timestamp, duration))
            if match.get("win"):
                row["wins"] += 1
            else:
                row["losses"] += 1

            row["queues"][queue_id]["seconds"] += duration
            row["queues"][queue_id]["matches"] += 1
            queue_totals[queue_id]["seconds"] += duration
            queue_totals[queue_id]["matches"] += 1

            match_id = match.get("match_id")
            if match_id and match_id not in unique_matches:
                unique_matches[match_id] = duration

    recent_cutoff = latest_timestamp - (30 * 24 * 60 * 60 * 1000) if latest_timestamp else 0
    if recent_cutoff:
        for row in rows_by_player.values():
            row["recent_seconds"] = sum(
                duration
                for timestamp, duration in row["match_times"]
                if timestamp >= recent_cutoff
            )

    player_rows = []
    total_player_seconds = sum(row["seconds"] for row in rows_by_player.values())
    for row in rows_by_player.values():
        top_queue_id, top_queue = max(
            row["queues"].items(),
            key=lambda item: item[1]["seconds"],
            default=(None, {"seconds": 0, "matches": 0})
        )
        total_games = row["wins"] + row["losses"]
        ranked_visible_matches = ranked_visible_by_player.get(row["player_name"], 0)
        history_gap = max(0, ranked_visible_matches - row["matches"])
        player_rows.append({
            "player_name": row["player_name"],
            "accounts_count": len(row["accounts"]),
            "accounts_list": sorted(row["riot_ids"]),
            "hours": row["seconds"] / 3600,
            "hours_label": format_seconds(row["seconds"]),
            "recent_label": format_seconds(row["recent_seconds"]),
            "matches": row["matches"],
            "ranked_visible_matches": ranked_visible_matches,
            "history_gap": history_gap,
            "avg_label": format_seconds(row["seconds"] / row["matches"]) if row["matches"] else "0h 00m",
            "win_rate": round((row["wins"] / total_games) * 100, 1) if total_games else 0,
            "share": round((row["seconds"] / total_player_seconds) * 100, 1) if total_player_seconds else 0,
            "top_queue": QUEUE_NAMES.get(top_queue_id, f"Cola {top_queue_id}") if top_queue_id else "N/A",
            "top_queue_label": format_seconds(top_queue["seconds"]),
            "last_played": datetime.fromtimestamp(row["last_played_ts"] / 1000, TARGET_TIMEZONE).strftime("%d/%m/%Y") if row["last_played_ts"] else "N/A",
        })

    player_rows.sort(key=lambda row: row["hours"], reverse=True)

    queue_rows = []
    for queue_id, data in queue_totals.items():
        queue_rows.append({
            "name": QUEUE_NAMES.get(queue_id, f"Cola {queue_id}"),
            "hours": data["seconds"] / 3600,
            "hours_label": format_seconds(data["seconds"]),
            "matches": data["matches"],
            "share": round((data["seconds"] / total_player_seconds) * 100, 1) if total_player_seconds else 0,
        })
    queue_rows.sort(key=lambda row: row["hours"], reverse=True)

    leader = player_rows[0] if player_rows else None
    latest_date = (
        datetime.fromtimestamp(latest_timestamp / 1000, TARGET_TIMEZONE).strftime("%d/%m/%Y")
        if latest_timestamp else "N/A"
    )
    report = {
        "total_player_hours": round(total_player_seconds / 3600, 1),
        "total_player_label": format_seconds(total_player_seconds),
        "unique_match_label": format_seconds(sum(unique_matches.values())),
        "matches_count": sum(row["matches"] for row in rows_by_player.values()),
        "unique_matches_count": len(unique_matches),
        "players_count": len(player_rows),
        "avg_per_player_label": format_seconds(total_player_seconds / len(player_rows)) if player_rows else "0h 00m",
        "leader": leader,
        "latest_date": latest_date,
        "generated_at": datetime.now(TARGET_TIMEZONE).strftime("%d/%m/%Y %H:%M"),
        "calculated_at_iso": datetime.now(timezone.utc).isoformat(),
        "schema_version": 3,
        "accounts_source_count": len(accounts),
        "puuids_source_count": len(puuids),
        "accounts_processed_count": len(processed_puuids),
        "stats_index_rows_count": len(index_rows),
    }

    return {
        "report": report,
        "player_rows": player_rows,
        "queue_rows": queue_rows,
    }


def _format_hours_wait(seconds):
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _get_time_until_next_hours_generation(snapshot=None):
    if not snapshot:
        success, snapshot = read_hours_report()
        if not success:
            snapshot = {}

    report = snapshot.get("report") or {}
    if report.get("schema_version") != 3:
        return True, 0, "0s"

    calculated_at = report.get("calculated_at_iso")
    if not calculated_at:
        return True, 0, "0s"

    try:
        last_calc = datetime.fromisoformat(str(calculated_at).replace("Z", "+00:00"))
        if last_calc.tzinfo is None:
            last_calc = last_calc.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_calc).total_seconds()
        interval = settings.GLOBAL_STATS_UPDATE_INTERVAL
        if elapsed >= interval:
            return True, 0, "0s"
        remaining = int(interval - elapsed)
        return False, remaining, _format_hours_wait(remaining)
    except Exception as e:
        print(f"[_get_time_until_next_hours_generation] Error parseando fecha: {e}")
        return True, 0, "0s"


@main_bp.route('/horas')
def horas():
    """Informe secreto de horas jugadas, leido desde snapshot precalculado."""
    success, snapshot = read_hours_report()
    if not success:
        snapshot = {}

    can_generate, seconds_remaining, time_remaining = _get_time_until_next_hours_generation(snapshot)
    report_data = snapshot if snapshot else {
        "report": {
            "total_player_label": "0h 00m",
            "players_count": 0,
            "matches_count": 0,
            "latest_date": "N/A",
            "unique_matches_count": 0,
            "unique_match_label": "0h 00m",
            "avg_per_player_label": "0h 00m",
            "leader": None,
            "generated_at": "N/A",
        },
        "player_rows": [],
        "queue_rows": [],
    }

    return render_template(
        'horas.html',
        report=report_data.get("report", {}),
        player_rows=report_data.get("player_rows", []),
        queue_rows=report_data.get("queue_rows", []),
        needs_update=not bool(snapshot),
        can_generate=can_generate,
        seconds_remaining=seconds_remaining,
        time_remaining=time_remaining,
        has_player_data=True,
    )


@main_bp.route('/horas/actualizar', methods=['POST'])
def actualizar_horas():
    """Genera y guarda el snapshot de horas jugadas, como maximo cada 24h."""
    success, snapshot = read_hours_report()
    can_generate, seconds_remaining, time_remaining = _get_time_until_next_hours_generation(snapshot if success else {})
    if not can_generate:
        flash(f"El informe de horas se genero recientemente. Espera {time_remaining}.", "warning")
        return redirect(url_for("main.horas"))

    try:
        report_data = _build_hours_report_data()
        if save_hours_report(report_data):
            flash("Informe de horas jugadas actualizado correctamente.", "success")
        else:
            flash("No se pudo guardar el informe de horas. Revisa GITHUB_TOKEN o permisos.", "danger")
    except Exception as e:
        print(f"[actualizar_horas] Error: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error al generar el informe de horas: {e}", "danger")

    return redirect(url_for("main.horas"))


@main_bp.route('/configsv', methods=['GET', 'POST'])
def configsv():
    """
    Editor visual de desafios (/configsv).
    No se enlaza desde la navegacion normal.
    """
    status_message = None
    status_kind = "info"

    if request.method == 'POST':
        raw_json = request.form.get('config_json', '')
        try:
            payload = json.loads(raw_json)
            ok, msg = save_achievements_config_document(payload)
            status_message = msg
            status_kind = "success" if ok else "danger"
        except Exception as e:
            status_message = f"Error guardando configuracion: {e}"
            status_kind = "danger"

    try:
        config_doc, source, errors = get_achievements_config_document(force_refresh=True)
        return render_template(
            'configsv.html',
            config_doc=config_doc,
            config_source=source,
            config_errors=errors,
            editor_options=get_achievement_editor_options(),
            status_message=status_message,
            status_kind=status_kind,
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True,
        )
    except Exception as e:
        print(f"[configsv] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500


@main_bp.route('/configops', methods=['GET', 'POST'])
def configops():
    """
    Panel privado de operaciones:
    - Permisos de analisis IA por jugador (scope jugador / partida)
    - Config de recarga forzada de estadisticas
    """
    status_message = None
    status_kind = "info"

    try:
        players = get_all_players_with_puuids()
        player_keys = [riot_id for riot_id, _display, _puuid in players if riot_id]
        ensure_permission_files_for_players(player_keys)

        if request.method == 'POST':
            action = (request.form.get('action') or '').strip()

            if action == 'save_permission':
                player_key = (request.form.get('player_key') or '').strip()
                scope = (request.form.get('scope') or 'jugador').strip().lower()
                if scope not in ('jugador', 'partida'):
                    scope = 'jugador'
                permitir = (request.form.get('permitir_llamada') or 'NO').strip().upper()
                permitir = "SI" if permitir == "SI" else "NO"
                razon = (request.form.get('razon') or '').strip()

                if not player_key:
                    status_message = "player_key vacio."
                    status_kind = "danger"
                else:
                    _, permiso_sha, content, _ = read_player_permission(player_key, scope=scope)
                    if not isinstance(content, dict):
                        content = {}
                    content["permitir_llamada"] = permitir
                    content["razon"] = razon or ("Habilitado manualmente desde /configops." if permitir == "SI" else "Deshabilitado manualmente desde /configops.")
                    content["modo_forzado"] = False
                    content["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ok = save_player_permission(player_key, content, sha=permiso_sha, scope=scope)
                    if ok:
                        status_message = f"Permiso actualizado: {player_key} ({scope}) -> {permitir}."
                        status_kind = "success"
                    else:
                        status_message = f"No se pudo guardar permiso para {player_key} ({scope})."
                        status_kind = "danger"

            elif action == 'save_stats_reload':
                forzar = (request.form.get('forzar_recarga') or 'NO').strip().upper()
                forzar = "SI" if forzar == "SI" else "NO"
                razon = (request.form.get('razon_reload') or '').strip()
                _, reload_sha, reload_content = read_stats_reload_config()
                if not isinstance(reload_content, dict):
                    reload_content = {}
                reload_content["forzar_recarga"] = forzar
                reload_content["razon"] = razon or ("Habilitado manualmente desde /configops." if forzar == "SI" else "Deshabilitado manualmente desde /configops.")
                ok = save_stats_reload_config(reload_content, sha=reload_sha)
                if ok:
                    status_message = f"Config stats_reload guardada: {forzar}."
                    status_kind = "success"
                else:
                    status_message = "No se pudo guardar config stats_reload."
                    status_kind = "danger"

        rows = []
        for riot_id, display_name, puuid in players:
            player_key = riot_id
            p_ok, _p_sha, p_content, _ = read_player_permission(player_key, scope='jugador')
            m_ok, _m_sha, m_content, _ = read_player_permission(player_key, scope='partida')
            rows.append({
                "riot_id": riot_id,
                "display_name": display_name,
                "puuid": puuid,
                "player_key": player_key,
                "perm_jugador": "SI" if p_ok else "NO",
                "perm_partida": "SI" if m_ok else "NO",
                "reason_jugador": (p_content or {}).get("razon", ""),
                "reason_partida": (m_content or {}).get("razon", ""),
                "path_jugador": get_permission_file_path(player_key, scope='jugador'),
                "path_partida": get_permission_file_path(player_key, scope='partida'),
            })

        forzar_recarga, _reload_sha, reload_content = read_stats_reload_config()

        return render_template(
            'configops.html',
            players=rows,
            forzar_recarga="SI" if forzar_recarga else "NO",
            razon_reload=(reload_content or {}).get("razon", ""),
            status_message=status_message,
            status_kind=status_kind,
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True,
        )
    except Exception as e:
        print(f"[configops] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500
