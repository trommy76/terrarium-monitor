"""
updater.py
Check GitHub for the latest release and apply it in place.

Only the app's own code (*.py, sample_config.ini, terrarium.service) is
replaced; config.ini, data/ and venv/ are never touched. After applying,
the process exits and systemd (Restart=always) brings the new code up.
"""

import io
import logging
import re
import shutil
import tarfile
import time
from pathlib import Path

import requests

from version import __version__

logger = logging.getLogger(__name__)

REPO       = "trommy76/terrarium-monitor"
API_URL    = f"https://api.github.com/repos/{REPO}/releases/latest"
APP_DIR    = Path(__file__).resolve().parent
CACHE_TTL  = 3600
_UPDATE_GLOBS = ("*.py", "sample_config.ini", "terrarium.service")

_cache = {"at": 0.0, "data": None}


def _parse(tag: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", tag)[:4])


def check(force: bool = False) -> dict:
    """Return {current, latest, available, url, notes} or {error}."""
    now = time.monotonic()
    if not force and _cache["data"] and now - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    try:
        r = requests.get(API_URL, timeout=10,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        rel = r.json()
        latest = rel["tag_name"]
        data = {
            "current":   __version__,
            "latest":    latest.lstrip("v"),
            "available": _parse(latest) > _parse(__version__),
            "url":       rel.get("html_url", ""),
            "tarball":   rel["tarball_url"],
            "notes":     (rel.get("body") or "")[:1000],
        }
    except Exception as e:
        logger.warning("Update check failed: %s", e)
        return {"current": __version__, "available": False, "error": str(e)}
    _cache.update(at=now, data=data)
    return data


def apply() -> dict:
    """Download the latest release and copy its code over APP_DIR."""
    info = check(force=True)
    if info.get("error"):
        return {"ok": False, "error": info["error"]}
    if not info["available"]:
        return {"ok": False, "error": "Already up to date."}
    try:
        r = requests.get(info["tarball"], timeout=60)
        r.raise_for_status()
        copied = []
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            # GitHub tarballs have a single top-level "<repo>-<sha>/" folder.
            for m in tar.getmembers():
                parts = Path(m.name).parts
                if not m.isfile() or len(parts) != 2:
                    continue
                name = parts[1]
                if not any(Path(name).match(g) for g in _UPDATE_GLOBS):
                    continue
                dest = APP_DIR / name
                tmp  = dest.with_name(dest.name + ".new")
                with tar.extractfile(m) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                tmp.replace(dest)
                copied.append(name)
        logger.info("Updated to %s (%d files).", info["latest"], len(copied))
        return {"ok": True, "version": info["latest"], "files": copied}
    except Exception as e:
        logger.exception("Update failed")
        return {"ok": False, "error": str(e)}
