import os
import time
import threading
from config.settings import GITHUB_TOKEN
from services.github_service import write_file_to_github, delete_file_from_github, get_file_sha_only

BASE_DIR = os.path.join(os.getcwd(), 'static', 'precomputed')


def _ensure_dir():
    os.makedirs(BASE_DIR, exist_ok=True)


def _safe_key(key: str) -> str:
    return key.replace('/', '_').replace('..', '_')


def file_path(key: str) -> str:
    _ensure_dir()
    return os.path.join(BASE_DIR, f"{_safe_key(key)}.html")


def is_fresh(key: str, max_age_seconds: int = 600) -> bool:
    path = file_path(key)
    if not os.path.exists(path):
        return False
    try:
        age = time.time() - os.path.getmtime(path)
        return age < max_age_seconds
    except Exception:
        return False


def read(key: str):
    path = file_path(key)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def write(key: str, html: str) -> None:
    path = file_path(key)
    _ensure_dir()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(html)
    os.replace(tmp, path)


def write_async(key: str, html: str) -> None:
    try:
        thread = threading.Thread(target=write, args=(key, html), daemon=True)
        thread.start()
    except Exception:
        # best-effort, never raise from background write
        try:
            write(key, html)
        except Exception:
            pass


def _github_path_for_key(key: str, folder: str = 'precomputed') -> str:
    safe = _safe_key(key)
    return f"{folder}/{safe}.html"


def write_github(key: str, html: str, folder: str = 'precomputed', message: str | None = None) -> bool:
    """Guarda el HTML en GitHub (branch main) usando `services.github_service`.

    Devuelve True si se guardó correctamente.
    """
    if not GITHUB_TOKEN:
        return False
    path = _github_path_for_key(key, folder=folder)
    msg = message or f"Actualizar precomputed: {path}"
    try:
        ok = write_file_to_github(path, html, message=msg)
        return bool(ok)
    except Exception as e:
        print(f"[precompute_service.write_github] Error: {e}")
        return False


def write_all_async(key: str, html: str) -> None:
    """Escribe en disco local y, si hay token, sube a GitHub en background."""
    # Escribir localmente
    try:
        t_local = threading.Thread(target=write, args=(key, html), daemon=True)
        t_local.start()
    except Exception:
        try:
            write(key, html)
        except Exception:
            pass

    # Intentar subir a GitHub en background
    if GITHUB_TOKEN:
        try:
            t_git = threading.Thread(target=write_github, args=(key, html), daemon=True)
            t_git.start()
        except Exception:
            try:
                write_github(key, html)
            except Exception:
                pass


def invalidate(key: str, folder: str = 'precomputed') -> bool:
    """Elimina el precomputed local y en GitHub (si aplica)."""
    # Borrar local
    path = file_path(key)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[precompute_service.invalidate] Error borrando local: {e}")

    # Borrar en GitHub
    if not GITHUB_TOKEN:
        return True
    gh_path = _github_path_for_key(key, folder=folder)
    try:
        sha = get_file_sha_only(gh_path)
        if not sha:
            return True
        return delete_file_from_github(gh_path, message=f"Invalidar precomputed: {gh_path}", sha=sha)
    except Exception as e:
        print(f"[precompute_service.invalidate] Error GitHub: {e}")
        return False
