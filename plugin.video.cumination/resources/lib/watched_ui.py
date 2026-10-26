# -*- coding: utf-8 -*-
"""
    Cumination - watched state UI

    Registered plugin modes for manually marking videos. Imported from
    default.py so URL_Dispatcher knows about them.
"""

from kodi_six import xbmc

from resources.lib import utils
from resources.lib import watched
from resources.lib.url_dispatcher import URL_Dispatcher

url_dispatcher = URL_Dispatcher('watched')


def _refresh():
    xbmc.executebuiltin('Container.Refresh')


@url_dispatcher.register()
def toggle(url, name=None):
    try:
        state = watched.toggle(url, title=name)
    except Exception as exc:
        utils.kodilog('watched toggle failed: {0}'.format(exc), xbmc.LOGERROR)
        return
    utils.notify(msg=utils.i18n('marked_watched') if state else utils.i18n('marked_unwatched'))
    _refresh()


@url_dispatcher.register()
def mark(url, name=None):
    watched.mark(url, title=name)
    utils.notify(msg=utils.i18n('marked_watched'))
    _refresh()


@url_dispatcher.register()
def unmark(url, name=None):
    watched.unmark(url)
    utils.notify(msg=utils.i18n('marked_unwatched'))
    _refresh()


@url_dispatcher.register()
def clear():
    if not utils.dialog.yesno(
        'Cumination',
        utils.i18n('clear_watched_confirm')
    ):
        return
    watched.clear()
    utils.notify(msg=utils.i18n('watched_cleared'))
    _refresh()
