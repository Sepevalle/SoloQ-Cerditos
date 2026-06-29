"""Generador de HTML precalculados.

Uso:
    python generate_precomputed.py [--players N]

Este script renderiza páginas importantes y las escribe localmente y a GitHub
usando `services/precompute_service` (si hay `GITHUB_TOKEN`).
"""
import sys
import argparse
from datetime import datetime
import os

# Evitar que la importación de `app` inicie servicios pesados cuando se
# ejecuta este script.
os.environ.setdefault('START_BACKGROUND_SERVICES', '0')

from app import create_app
from services.index_json_generator import generate_index_json, load_index_json
from services.precompute_service import write_all, write_github
from services.precompute_service import _safe_key

# Import helpers to build historial and player profiles
from blueprints import main as main_bp
from blueprints import player as player_bp
from services.cache_service import player_cache
from services.live_game_service import get_active_live_games
import config.settings as settings


def render_index(app):
    print("[generate] Generando index...")
    generate_index_json(force=True)
    json_data = load_index_json()
    datos_jugadores = json_data.get('datos_jugadores', []) if json_data else []
    ultima_actualizacion = json_data.get('ultima_actualizacion', 'N/A') if json_data else 'N/A'
    minutos_desde_actualizacion = json_data.get('minutos_desde_actualizacion', 0) if json_data else 0
    cache_stale = json_data.get('cache_stale', False) if json_data else False
    split_activo_nombre = json_data.get('split_activo_nombre', settings.SPLITS[settings.ACTIVE_SPLIT_KEY]['name']) if json_data else settings.SPLITS[settings.ACTIVE_SPLIT_KEY]['name']

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    template = app.jinja_env.get_template('index.html')
    rendered = template.render(
        datos_jugadores=datos_jugadores,
        active_live_games=get_active_live_games(),
        ultima_actualizacion=ultima_actualizacion,
        ddragon_version=settings.DDRAGON_VERSION,
        split_activo_nombre=split_activo_nombre,
        has_player_data=bool(datos_jugadores),
        cache_stale=cache_stale,
        minutos_desde_actualizacion=minutos_desde_actualizacion,
        generated_at=generated_at
    )

    write_all('index', rendered)
    print('[generate] index generado y encolado para escritura')


def render_historial(app, max_pages=5):
    print('[generate] Generando historial_global...')
    dataset = main_bp._build_historial_global_dataset()
    per_page = main_bp.HISTORIAL_GLOBAL_PER_PAGE
    max_allowed_pages = main_bp.HISTORIAL_GLOBAL_MAX_PAGES
    max_matches = main_bp.HISTORIAL_GLOBAL_MAX_MATCHES
    all_matches = dataset.get('matches', [])[:max_matches]
    total_matches = len(all_matches)
    total_pages = min(max_allowed_pages, max(1, (total_matches + per_page - 1) // per_page))

    pages_to_generate = min(total_pages, max_pages, max_allowed_pages)

    for page in range(1, pages_to_generate + 1):
        start = (page - 1) * per_page
        end = start + per_page
        page_matches = all_matches[start:end]
        page_numbers = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))
        generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        template = app.jinja_env.get_template('historial_global.html')
        rendered = template.render(
            matches=page_matches,
            page=page,
            per_page=per_page,
            total_matches=total_matches,
            total_pages=total_pages,
            page_numbers=page_numbers,
            players_total=dataset.get('players_total', 0),
            players_with_puuid=dataset.get('players_with_puuid', 0),
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True,
            generated_at=generated_at
        )
        key = f'historial_global_page_{page}'
        # Escribir sin hilos para controlar memoria/uso y asegurar que el
        # archivo se suba antes de continuar.
        write_all(key, rendered)
        print(f'[generate] historial page {page}/{pages_to_generate} encolado')


def render_players(app, max_players=50):
    print(f'[generate] Generando perfiles para hasta {max_players} jugadores...')
    all_players, _ = player_cache.get()
    # all_players is list of dicts with game_name
    seen = set()
    count = 0
    for entry in all_players:
        game_name = entry.get('game_name')
        if not game_name or game_name in seen:
            continue
        seen.add(game_name)
        try:
            perfil = player_bp._build_player_profile(game_name)
            if not perfil:
                continue
            # Render first page with defaults
            all_matches = perfil.get('historial_partidas', [])
            queue_options, champion_options = player_bp._get_match_filter_options(all_matches)
            selected_queue = 'all'
            selected_champion = 'all'
            current_page = 1
            # Filter/paginate as in view
            filtered_matches = player_bp._filter_matches(all_matches, selected_queue, selected_champion)
            total_filtered_matches = len(filtered_matches)
            total_pages = max(1, (total_filtered_matches + player_bp.MATCHES_PER_PAGE - 1) // player_bp.MATCHES_PER_PAGE)
            start_index = (current_page - 1) * player_bp.MATCHES_PER_PAGE
            end_index = start_index + player_bp.MATCHES_PER_PAGE
            page_matches = filtered_matches[start_index:end_index]

            perfil_view = dict(perfil)
            perfil_view['historial_partidas'] = page_matches

            generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            template = app.jinja_env.get_template('jugador.html')
            rendered = template.render(
                perfil=perfil_view,
                ddragon_version=settings.DDRAGON_VERSION,
                queue_options=queue_options,
                champion_options=champion_options,
                selected_queue=selected_queue,
                selected_champion=selected_champion,
                current_page=current_page,
                total_pages=total_pages,
                total_filtered_matches=total_filtered_matches,
                matches_per_page=player_bp.MATCHES_PER_PAGE,
                datetime=datetime,
                now=datetime.now(),
                generated_at=generated_at
            )

            key = f'player_{_safe_key(game_name)}_page_1_queue_all_champ_all'
            write_all(key, rendered)
            print(f'[generate] Perfil {game_name} encolado')
            count += 1
            if count >= max_players:
                break
        except Exception as e:
            print(f'[generate] Error generando perfil {game_name}: {e}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--players', type=int, default=50, help='Número máximo de perfiles a generar')
    parser.add_argument('--max-historial-pages', type=int, default=2, help='Número máximo de páginas de historial_global a generar')
    parser.add_argument('--include-historial', action='store_true', help='Generar tambien paginas de historial_global')
    args = parser.parse_args()

    app = create_app()
    with app.test_request_context("/"):
        render_index(app)
        if args.include_historial:
            render_historial(app, max_pages=args.max_historial_pages)
        else:
            print('[generate] historial_global omitido (usa --include-historial para generarlo)')
        render_players(app, max_players=args.players)

    print('[generate] Trabajo completado')


if __name__ == '__main__':
    main()
