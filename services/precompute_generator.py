"""Generacion periodica de HTML pregenerado para servir trafico barato."""

import os
import threading
import time
from datetime import datetime

import config.settings as settings
from services.cache_service import player_cache
from services.index_json_generator import generate_index_json, load_index_json
from services.live_game_service import get_active_live_games
from services.precompute_service import _safe_key, write_all

_refresh_event = threading.Event()
_started = False
_start_lock = threading.Lock()


def request_precompute_refresh(reason: str = "datos actualizados") -> None:
    """Pide al worker que regenere HTML cuanto antes."""
    print(f"[precompute_generator] Refresco solicitado: {reason}")
    _refresh_event.set()


def _render_index(app) -> None:
    print("[precompute_generator] Generando index.html pregenerado...")
    generate_index_json(force=True)
    json_data = load_index_json() or {}
    datos_jugadores = json_data.get("datos_jugadores", [])
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rendered = app.jinja_env.get_template("index.html").render(
        datos_jugadores=datos_jugadores,
        active_live_games=get_active_live_games(),
        ultima_actualizacion=json_data.get("ultima_actualizacion", "N/A"),
        ddragon_version=settings.DDRAGON_VERSION,
        split_activo_nombre=json_data.get(
            "split_activo_nombre",
            settings.SPLITS[settings.ACTIVE_SPLIT_KEY]["name"],
        ),
        has_player_data=bool(datos_jugadores),
        cache_stale=json_data.get("cache_stale", False),
        minutos_desde_actualizacion=json_data.get("minutos_desde_actualizacion", 0),
        generated_at=generated_at,
    )
    write_all("index", rendered)


def _render_historial(app) -> None:
    from blueprints import main as main_bp

    print("[precompute_generator] Generando historial_global pregenerado...")
    dataset = main_bp._build_historial_global_dataset()
    per_page = main_bp.HISTORIAL_GLOBAL_PER_PAGE
    max_pages = main_bp.HISTORIAL_GLOBAL_MAX_PAGES
    max_matches = main_bp.HISTORIAL_GLOBAL_MAX_MATCHES
    all_matches = dataset.get("matches", [])[:max_matches]
    total_matches = len(all_matches)
    total_pages = min(max_pages, max(1, (total_matches + per_page - 1) // per_page))

    for page in range(1, total_pages + 1):
        start = (page - 1) * per_page
        end = start + per_page
        page_numbers = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))
        rendered = app.jinja_env.get_template("historial_global.html").render(
            matches=all_matches[start:end],
            page=page,
            per_page=per_page,
            total_matches=total_matches,
            total_pages=total_pages,
            page_numbers=page_numbers,
            players_total=dataset.get("players_total", 0),
            players_with_puuid=dataset.get("players_with_puuid", 0),
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        write_all(f"historial_global_page_{page}", rendered)


def _precompute_historial_enabled() -> bool:
    return os.environ.get("PRECOMPUTE_HISTORIAL_GLOBAL", "1").lower() in ("1", "true", "yes", "si")


def _render_players(app, max_players: int) -> None:
    from blueprints import player as player_bp

    print(f"[precompute_generator] Generando perfiles pregenerados: max {max_players}")
    all_players, _ = player_cache.get()
    seen = set()
    count = 0

    for entry in all_players:
        game_name = entry.get("game_name")
        if not game_name or game_name in seen:
            continue
        seen.add(game_name)

        try:
            perfil = player_bp._build_player_profile(game_name)
            if not perfil:
                continue

            all_matches = perfil.get("historial_partidas", [])
            queue_options, champion_options = player_bp._get_match_filter_options(all_matches)
            filtered_matches = player_bp._filter_matches(all_matches, "all", "all")
            total_filtered_matches = len(filtered_matches)
            total_pages = max(
                1,
                (total_filtered_matches + player_bp.MATCHES_PER_PAGE - 1)
                // player_bp.MATCHES_PER_PAGE,
            )

            perfil_view = dict(perfil)
            perfil_view["historial_partidas"] = filtered_matches[: player_bp.MATCHES_PER_PAGE]

            rendered = app.jinja_env.get_template("jugador.html").render(
                perfil=perfil_view,
                ddragon_version=settings.DDRAGON_VERSION,
                queue_options=queue_options,
                champion_options=champion_options,
                selected_queue="all",
                selected_champion="all",
                current_page=1,
                total_pages=total_pages,
                total_filtered_matches=total_filtered_matches,
                matches_per_page=player_bp.MATCHES_PER_PAGE,
                datetime=datetime,
                now=datetime.now(),
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            key = f"player_{_safe_key(game_name)}_page_1_queue_all_champ_all"
            write_all(key, rendered)
            count += 1
            if count >= max_players:
                break
        except Exception as e:
            print(f"[precompute_generator] Error generando perfil {game_name}: {e}")


def generate_precomputed_html(app, max_players: int | None = None) -> bool:
    """Regenera las paginas HTML principales y las persiste en GitHub."""
    max_players = max_players or int(os.environ.get("PRECOMPUTE_MAX_PLAYERS", "50"))
    started_at = time.time()
    try:
        with app.test_request_context("/"):
            _render_index(app)
            if _precompute_historial_enabled():
                _render_historial(app)
            else:
                print("[precompute_generator] Historial global omitido (PRECOMPUTE_HISTORIAL_GLOBAL=0)")
            _render_players(app, max_players=max_players)
        print(f"[precompute_generator] HTML pregenerado en {time.time() - started_at:.1f}s")
        return True
    except Exception as e:
        print(f"[precompute_generator] Error general: {e}")
        import traceback

        traceback.print_exc()
        return False


def start_precompute_generator_thread(app, interval_seconds: int | None = None) -> None:
    """Inicia un worker unico que mantiene HTML pregenerado en GitHub."""
    global _started
    interval_seconds = interval_seconds or int(os.environ.get("PRECOMPUTE_INTERVAL_SECONDS", "600"))

    with _start_lock:
        if _started:
            return
        _started = True

    def _loop():
        print(f"[precompute_generator] Worker iniciado (intervalo: {interval_seconds}s)")
        time.sleep(int(os.environ.get("PRECOMPUTE_INITIAL_DELAY_SECONDS", "30")))
        while True:
            generate_precomputed_html(app)
            _refresh_event.clear()
            _refresh_event.wait(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
