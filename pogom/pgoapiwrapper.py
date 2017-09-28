#!/usr/bin/python
# -*- coding: utf-8 -*-

import logging

from .pgorequestwrapper import PGoRequestWrapper
import collections

log = logging.getLogger(__name__)


class PGoApiWrapper:
    def __init__(self, api):
        log.debug('Wrapped PGoApi.')
        self.api = api

    def __getattr__(self, attr):
        orig_attr = getattr(self.api, attr)

        if isinstance(orig_attr, collections.Callable):
            def hooked(*args, **kwargs):
                result = orig_attr(*args, **kwargs)
                # Prevent wrapped class from becoming unwrapped.
                if result == self.api:
                    return self
                return result
            return hooked
        else:
            return orig_attr

    def create_request(self, *args, **kwargs):
        request = self.api.create_request(*args, **kwargs)
        return PGoRequestWrapper(request)
