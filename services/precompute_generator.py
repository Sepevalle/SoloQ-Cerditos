"""Generacion periodica de HTML pregenerado para servir trafico barato."""

import os
import threading
import time
from datetime import datetime

import config.settings as settings
from services.index_json_generator import generate_index_json, load_index_json
from services.live_game_service import get_active_live_games
from services.precompute_service import write_all

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


def generate_precomputed_html(app, max_players: int | None = None) -> bool:
    """Regenera solo el index pregenerado y lo persiste en GitHub."""
    started_at = time.time()
    try:
        with app.test_request_context("/"):
            _render_index(app)
        print(f"[precompute_generator] Index pregenerado en {time.time() - started_at:.1f}s")
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
