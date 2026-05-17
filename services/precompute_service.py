import os
import time
import threading
from datetime import datetime, timezone
from config.settings import GITHUB_TOKEN
from services.github_service import (
    write_file_to_github,
    delete_file_from_github,
    get_file_sha_only,
    read_file_from_github,
)

BASE_DIR = os.path.join(os.getcwd(), 'static', 'precomputed')
GITHUB_FOLDER = 'precomputed'
MANIFEST_KEY = '_manifest'
MANIFEST_PATH = f'{GITHUB_FOLDER}/{MANIFEST_KEY}.json'
_manifest_lock = threading.Lock()


def _ensure_dir():
    os.makedirs(BASE_DIR, exist_ok=True)


def _safe_key(key: str) -> str:
    return str(key).replace('/', '_').replace('\\', '_').replace('..', '_')


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


def _now_ts() -> int:
    return int(time.time())


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_manifest() -> dict:
    content, _ = read_file_from_github(MANIFEST_PATH, use_raw=False)
    if isinstance(content, dict):
        return content
    return {"version": 1, "pages": {}}


def _write_manifest_entry(key: str, folder: str = GITHUB_FOLDER) -> bool:
    if not GITHUB_TOKEN:
        return False

    safe = _safe_key(key)
    with _manifest_lock:
        manifest = _read_manifest()
        pages = manifest.setdefault("pages", {})
        pages[key] = {
            "key": key,
            "safe_key": safe,
            "path": _github_path_for_key(key, folder=folder),
            "updated_at": _now_ts(),
            "updated_at_iso": _iso_now(),
        }
        manifest["updated_at"] = _now_ts()
        manifest["updated_at_iso"] = _iso_now()
        _, sha = read_file_from_github(MANIFEST_PATH, use_raw=False)
        return write_file_to_github(
            MANIFEST_PATH,
            manifest,
            message="Actualizar manifiesto de HTML pregenerado",
            sha=sha,
        )


def _github_manifest_entry_is_fresh(key: str, max_age_seconds: int) -> bool:
    try:
        manifest = _read_manifest()
        pages = manifest.get("pages", {})
        entry = pages.get(key) or pages.get(_safe_key(key))
        if not entry:
            return False
        updated_at = int(entry.get("updated_at") or 0)
        return updated_at > 0 and (_now_ts() - updated_at) < max_age_seconds
    except Exception as e:
        print(f"[precompute_service] Error leyendo manifest: {e}")
        return False


def read(key: str):
    path = file_path(key)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def read_github(key: str, folder: str = GITHUB_FOLDER):
    """Lee HTML pregenerado desde GitHub y lo hidrata en disco local."""
    gh_path = _github_path_for_key(key, folder=folder)
    content, _ = read_file_from_github(gh_path, use_raw=False)
    if not isinstance(content, str) or not content.strip():
        return None

    try:
        write(key, content)
    except Exception as e:
        print(f"[precompute_service.read_github] No se pudo cachear localmente {key}: {e}")
    return content


def read_fresh(key: str, max_age_seconds: int = 600, folder: str = GITHUB_FOLDER):
    """Devuelve HTML fresco, usando disco local primero y GitHub como fuente persistente."""
    try:
        if is_fresh(key, max_age_seconds=max_age_seconds):
            content = read(key)
            if content:
                return content
    except Exception as e:
        print(f"[precompute_service.read_fresh] Error leyendo local {key}: {e}")

    if _github_manifest_entry_is_fresh(key, max_age_seconds=max_age_seconds):
        return read_github(key, folder=folder)

    return None


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


def write_all(key: str, html: str) -> bool:
    """Escribe HTML localmente y lo persiste en GitHub antes de volver."""
    try:
        write(key, html)
    except Exception as e:
        print(f"[precompute_service.write_all] Error local: {e}")

    if GITHUB_TOKEN:
        return write_github(key, html)
    return True


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
        if ok:
            _write_manifest_entry(key, folder=folder)
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
        manifest = _read_manifest()
        pages = manifest.get("pages", {})
        if key in pages or _safe_key(key) in pages:
            pages.pop(key, None)
            pages.pop(_safe_key(key), None)
            _, manifest_sha = read_file_from_github(MANIFEST_PATH, use_raw=False)
            write_file_to_github(
                MANIFEST_PATH,
                manifest,
                message=f"Invalidar manifest precomputed: {key}",
                sha=manifest_sha,
            )

        sha = get_file_sha_only(gh_path)
        if not sha:
            return True
        return delete_file_from_github(gh_path, message=f"Invalidar precomputed: {gh_path}", sha=sha)
    except Exception as e:
        print(f"[precompute_service.invalidate] Error GitHub: {e}")
        return False
