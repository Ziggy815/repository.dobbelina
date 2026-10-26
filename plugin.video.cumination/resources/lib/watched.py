# -*- coding: utf-8 -*-
"""
    Cumination - watched state tracking

    This module intentionally does not import resources.lib.basics, so it can
    be used from the plugin process as well as from service.py (where the
    plugin's sys.argv is not available).

    Storage lives in its own SQLite database (watched.db) in the addon
    profile. Two tables are used:

    watched(url, title, mode, added)
        One row per video that has been marked as watched.

    pending(id, url, title, mode, created)
        A single row describing the video the plugin is about to play. The
        service consumes this row when playback actually starts.

    This file is GPL 2.0, like the rest of Cumination.
"""

import os
import sqlite3
import threading
import time

from kodi_six import xbmc, xbmcaddon, xbmcvfs

ADDON_ID = 'plugin.video.cumination'
LOG_PREFIX = 'Cumination watched: '

# A pending entry is only valid for this long. It is written when the plugin
# resolves a stream and consumed when Kodi starts playing it.
PENDING_MAX_AGE = 10 * 60

# Number of consecutive "not playing" polls before a session is finalized.
# Avoids losing a session during short buffering gaps.
STOP_GRACE_POLLS = 3

_CONTEXT = {'url': None, 'name': None, 'mode': None}
_WATCHED_CACHE = None
_INITIALIZED_PATH = None
_DB_PATH = None
_ADDON = None


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def _log(message, level=xbmc.LOGDEBUG):
    try:
        xbmc.log(LOG_PREFIX + str(message), level)
    except Exception:
        pass


def _addon():
    global _ADDON
    if _ADDON is None:
        _ADDON = xbmcaddon.Addon(ADDON_ID)
    return _ADDON


def _translate_path(path):
    if hasattr(xbmcvfs, 'translatePath'):
        return xbmcvfs.translatePath(path)
    return xbmc.translatePath(path)


def _profile_dir():
    addon = _addon()
    return _translate_path(addon.getAddonInfo('profile'))


def _db_path():
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    path = _profile_dir()
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            pass
    _DB_PATH = os.path.join(path, 'watched.db')
    return _DB_PATH


def _setting(key, default=''):
    try:
        return _addon().getSetting(key) or default
    except Exception:
        return default


def _connect(timeout=15):
    conn = sqlite3.connect(_db_path(), timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA busy_timeout={0}'.format(int(timeout * 1000)))
        # synchronous is a per-connection setting. NORMAL keeps WAL commits
        # fast without risking corruption (only the last transaction can be
        # lost on a power failure).
        conn.execute('PRAGMA synchronous=NORMAL')
    except sqlite3.Error:
        pass
    return conn


def _invalidate_cache():
    global _WATCHED_CACHE
    _WATCHED_CACHE = None


def init():
    """Create the database/schema once per process."""
    global _INITIALIZED_PATH
    try:
        path = _db_path()
    except Exception as exc:
        _log('cannot resolve profile path: {0}'.format(exc), xbmc.LOGERROR)
        return False

    if _INITIALIZED_PATH == path:
        return True

    conn = None
    try:
        conn = _connect()
        # Fast path: the schema already exists, so no write lock and no
        # journal-mode switch is needed. This keeps playback startup quick
        # even while the service is polling the same database.
        existing = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watched'"
        ).fetchone()
        if existing is None:
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
            except sqlite3.Error:
                # WAL is not available on every filesystem (e.g. some network
                # shares). The default journal still works, just with less
                # concurrency.
                pass
            with conn:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS watched ('
                    'url TEXT PRIMARY KEY, '
                    'title TEXT, '
                    'mode TEXT, '
                    'added INTEGER NOT NULL)'
                )
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS pending ('
                    'id INTEGER PRIMARY KEY CHECK (id = 1), '
                    'url TEXT NOT NULL, '
                    'title TEXT, '
                    'mode TEXT, '
                    'created INTEGER NOT NULL)'
                )
        _INITIALIZED_PATH = path
        return True
    except Exception as exc:
        _log('database init failed: {0}'.format(exc), xbmc.LOGERROR)
        return False
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Watched state
# ---------------------------------------------------------------------------

def watched_urls():
    """Return the set of watched URLs (cached for the current process)."""
    global _WATCHED_CACHE
    if _WATCHED_CACHE is not None:
        return _WATCHED_CACHE

    urls = set()
    if init():
        conn = None
        try:
            conn = _connect()
            for row in conn.execute('SELECT url FROM watched'):
                urls.add(row['url'])
        except Exception as exc:
            _log('could not read watched list: {0}'.format(exc), xbmc.LOGERROR)
        finally:
            if conn is not None:
                conn.close()
    _WATCHED_CACHE = urls
    return urls


def is_watched(url):
    if not url:
        return False
    return url in watched_urls()


def mark(url, title=None, mode=None):
    if not url or not init():
        return False
    conn = None
    try:
        conn = _connect()
        with conn:
            conn.execute(
                'INSERT OR REPLACE INTO watched (url, title, mode, added) '
                'VALUES (?, ?, ?, ?)',
                (url, title, mode, int(time.time()))
            )
        _invalidate_cache()
        return True
    except Exception as exc:
        _log('mark failed: {0}'.format(exc), xbmc.LOGERROR)
        return False
    finally:
        if conn is not None:
            conn.close()


def unmark(url):
    if not url or not init():
        return False
    conn = None
    try:
        conn = _connect()
        with conn:
            conn.execute('DELETE FROM watched WHERE url = ?', (url,))
        _invalidate_cache()
        return True
    except Exception as exc:
        _log('unmark failed: {0}'.format(exc), xbmc.LOGERROR)
        return False
    finally:
        if conn is not None:
            conn.close()


def toggle(url, title=None, mode=None):
    """Toggle the watched flag and return the new state."""
    if is_watched(url):
        unmark(url)
        return False
    mark(url, title=title, mode=mode)
    return True


def clear():
    if not init():
        return False
    conn = None
    try:
        conn = _connect()
        with conn:
            conn.execute('DELETE FROM watched')
            conn.execute('DELETE FROM pending')
        _invalidate_cache()
        return True
    except Exception as exc:
        _log('clear failed: {0}'.format(exc), xbmc.LOGERROR)
        return False
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Playback context / pending entry
# ---------------------------------------------------------------------------

def set_context(url=None, name=None, mode=None):
    """Remember the current plugin request so playvid() can report it."""
    _CONTEXT['url'] = url or None
    _CONTEXT['name'] = name or None
    _CONTEXT['mode'] = mode or None


def auto_mark_enabled():
    return str(_setting('mark_watched', 'true')).lower() == 'true'


def threshold():
    try:
        value = int(float(_setting('mark_watched_pct', '90')))
    except (TypeError, ValueError):
        value = 90
    return max(1, min(100, value))


def begin_playback(url=None, title=None, mode=None):
    """
    Record the video that is about to be played.

    Called from utils.playvid() after the stream has been resolved. The
    service picks this up when Kodi reports that playback started.
    """
    url = url or _CONTEXT.get('url')
    title = title or _CONTEXT.get('name')
    mode = mode or _CONTEXT.get('mode')
    if not url:
        return False

    if not auto_mark_enabled() or not init():
        return False

    conn = None
    try:
        conn = _connect(timeout=5)
        with conn:
            conn.execute(
                'INSERT OR REPLACE INTO pending (id, url, title, mode, created) '
                'VALUES (1, ?, ?, ?, ?)',
                (url, title, mode, int(time.time()))
            )
        _log('pending playback: {0} ({1})'.format(url, mode))
        return True
    except Exception as exc:
        _log('could not store pending playback: {0}'.format(exc), xbmc.LOGERROR)
        return False
    finally:
        if conn is not None:
            conn.close()


def begin_playback_async(url=None, title=None, mode=None):
    """
    Non-blocking variant used on the playback path.

    The write itself only takes a few milliseconds, but it must not delay
    setResolvedUrl() on slow storage. The plugin process waits for the
    non-daemon thread at shutdown, so the pending row is still written.
    """
    def worker():
        try:
            begin_playback(url=url, title=title, mode=mode)
        except Exception as exc:
            _log('async begin_playback failed: {0}'.format(exc), xbmc.LOGERROR)

    try:
        thread = threading.Thread(target=worker, name='cumination-watched')
        thread.daemon = False
        thread.start()
        return True
    except Exception:
        return begin_playback(url=url, title=title, mode=mode)


def get_pending():
    if not init():
        return None
    conn = None
    try:
        conn = _connect()
        row = conn.execute(
            'SELECT url, title, mode, created FROM pending WHERE id = 1'
        ).fetchone()
        if row is None:
            return None
        if time.time() - row['created'] > PENDING_MAX_AGE:
            with conn:
                conn.execute('DELETE FROM pending WHERE id = 1')
            _log('discarded stale pending playback')
            return None
        return {
            'url': row['url'],
            'title': row['title'],
            'mode': row['mode'],
            'created': row['created'],
        }
    except Exception as exc:
        _log('could not read pending playback: {0}'.format(exc), xbmc.LOGERROR)
        return None
    finally:
        if conn is not None:
            conn.close()


def clear_pending():
    conn = None
    try:
        conn = _connect()
        with conn:
            conn.execute('DELETE FROM pending WHERE id = 1')
    except Exception as exc:
        _log('could not clear pending playback: {0}'.format(exc))
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def should_mark(percent, ended, threshold_value):
    if ended:
        return True
    return percent >= threshold_value


def finalize_session(session, ended=False):
    """Mark a finished playback session if it passed the threshold."""
    percent = 100.0 if ended else float(session.get('max_percent', 0.0))
    limit = threshold()
    if should_mark(percent, ended, limit):
        mark(session['url'], title=session.get('title'), mode=session.get('mode'))
        _log(
            'marked watched at {0:.1f}% (threshold {1}%): {2}'.format(
                percent, limit, session['url']
            ),
            xbmc.LOGINFO
        )
        # The list the user returns to was created before playback started,
        # so refresh it to show the watched flag immediately.
        try:
            xbmc.sleep(1000)
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass
        return True
    _log(
        'not marked, stopped at {0:.1f}% (threshold {1}%): {2}'.format(
            percent, limit, session['url']
        )
    )
    return False


class _PlaybackMonitor(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        self.ended = False

    def onPlayBackEnded(self):
        self.ended = True

    def onPlayBackStarted(self):
        self.ended = False


def service_loop():
    """Entry point used by service.py. Runs until Kodi shuts down."""
    monitor = _PlaybackMonitor()
    player = xbmc.Player()
    session = None
    stopped_polls = 0

    _log('service started', xbmc.LOGINFO)

    while not monitor.abortRequested():
        try:
            playing = player.isPlayingVideo()
        except Exception:
            playing = False

        if playing:
            stopped_polls = 0
        else:
            stopped_polls += 1

        if playing and session is None:
            pending = get_pending()
            if pending:
                session = {
                    'url': pending['url'],
                    'title': pending['title'],
                    'mode': pending['mode'],
                    'max_percent': 0.0,
                    'total': 0.0,
                }
                clear_pending()
                _log('playback started: {0}'.format(session['url']))

        if playing and session is not None:
            try:
                total = float(player.getTotalTime() or 0)
                position = float(player.getTime() or 0)
            except Exception:
                total, position = 0.0, 0.0
            if total > 0:
                session['total'] = total
                percent = max(0.0, min(100.0, position * 100.0 / total))
                if percent > session['max_percent']:
                    session['max_percent'] = percent
        elif not playing and session is not None and stopped_polls >= STOP_GRACE_POLLS:
            finalize_session(session, ended=monitor.ended)
            monitor.ended = False
            session = None
            stopped_polls = 0

        if monitor.waitForAbort(1):
            break

    if session is not None:
        finalize_session(session, ended=monitor.ended)
