# -*- coding: utf-8 -*-
"""
    Cumination - watched state service

    Runs for the lifetime of Kodi and marks videos as watched once playback
    passed the configured percentage. Kept separate from the plugin process
    because Kodi tears that process down as soon as playback starts.
"""

from kodi_six import xbmc

try:
    from resources.lib import watched
    watched.service_loop()
except Exception as exc:
    xbmc.log(
        'Cumination watched: service failed: {0}'.format(exc),
        xbmc.LOGERROR
    )
