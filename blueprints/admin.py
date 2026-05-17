from flask import Blueprint, request, jsonify
import os
from services import precompute_service
from config.settings import SECRET_KEY

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
        return jsonify({'ok': True, 'files': files})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
