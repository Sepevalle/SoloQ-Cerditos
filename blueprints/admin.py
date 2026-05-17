from flask import Blueprint, request, jsonify
import os
from services import precompute_service
from config.settings import SECRET_KEY
from services.cache_service import live_game_cache

admin_bp = Blueprint('admin', __name__)


def _check_token(req):
    token = req.headers.get('X-ADMIN-TOKEN') or req.args.get('token')
    expected = os.environ.get('ADMIN_TOKEN') or SECRET_KEY
    return token and token == expected


@admin_bp.route('/admin/invalidate', methods=['POST'])
def invalidate():
    if not _check_token(request):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    key = request.form.get('key') or request.json.get('key') if request.is_json else None
    if not key:
        return jsonify({'ok': False, 'error': 'missing key'}), 400
    ok = precompute_service.invalidate(key)
    return jsonify({'ok': bool(ok), 'key': key})


@admin_bp.route('/admin/list', methods=['GET'])
def list_keys():
    if not _check_token(request):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    # List local precomputed files
    base = os.path.join(os.getcwd(), 'static', 'precomputed')
    try:
        files = []
        if os.path.exists(base):
            for fn in os.listdir(base):
                if fn.endswith('.html'):
                    files.append(fn)
        manifest = precompute_service._read_manifest()
        github_pages = sorted(manifest.get("pages", {}).keys())
        return jsonify({'ok': True, 'files': files, 'github_pages': github_pages})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/admin/live_games', methods=['GET'])
def live_games():
    if not _check_token(request):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    try:
        snapshot = live_game_cache.snapshot()
        # Simplify output: include counts and a sample of entries
        total = len(snapshot)
        entries = []
        for puuid, info in list(snapshot.items())[:200]:
            entries.append({
                'puuid': puuid,
                'age_seconds': info.get('age_seconds'),
                'game_id': info.get('data', {}).get('gameId') if info.get('data') else None,
                'queue_id': info.get('data', {}).get('gameQueueConfigId') if info.get('data') else None,
            })
        return jsonify({'ok': True, 'total_cached': total, 'entries_sample': entries})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
