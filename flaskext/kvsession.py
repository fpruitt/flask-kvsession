# -*- coding: utf-8 -*-
"""
    flaskext.kvsession
    ~~~~~~~~~~~~~~~~~~

    Drop-in replacement module for Flask sessions that uses a
    :class:`simplekv.KeyValueStore` as a
    backend for server-side sessions.
"""

import hmac

import calendar
from flask import current_app
import flask
from random import SystemRandom
import re
import time


def generate_session_key(random_source, expires=None, bits=64):
    """Generates session ids.

    IDs generated are of the form of ``ID_EXPIRES``, where ``ID`` is a 64-bit
    integer generated by :data:`random_source` and ``EXPIRES`` is a UNIX
    timestamp denoting when this session should be considered invalid.

    Both values are encoded as hexadecimal integer strings.

    :param random_source: Where to get random bits from (must support the
                          python :mod:`random` interface.
    :param expires: An integer (UNIX timestamp) or a :class:`datetime.datetime`
                    object.
    :param bits: How many random bits should be used for the ID.
    """
    if None == expires:
        expires = 0
    elif not isinstance(expires, int) and not isinstance(expires, float):
        expires = calendar.timegm(expires.utctimetuple())

    idbits = random_source.getrandbits(bits)

    return '%x_%x' % (idbits, expires)


class Session(flask.Session):
    """This class actually derives from :class:`flask.Session` and overrides
    some behavior while trying to be as transparent as possible.

    The serialize and unserialize methods are overwritten and instead of
    returning a full serialization of the session data, will return a session
    ID generated by :func:`generate_sesion_key`, which will also be used as the
    key to store the data in the applications session key-value store (supplied
    to :meth:`KVSession.__init__`)."""

    def destroy(self):
        """Destroys a session completely, by removing it from the internal
        store.

        This allows removing a session for security reasons, e.g. a login
        stored in a session will cease to exist if the session is destroyed.

        It is, however, often also feasible to simply delete data from the
        session."""
        for key in self.keys():
            del self[key]

        current_app.session_kvstore.delete(self.__kvstore_key)

    def serialize(self, expires=None):
        # get session serialization
        sdata = super(Session, self).serialize(expires)

        # store sdata, receive key. the only exceptions expected are
        # ValueErrors, which should never happen with proper key generation
        # and IOErrors, which we do not want to catch here
        key = current_app.session_kvstore.put(
            key=generate_session_key(
                current_app.config['SESSION_RANDOM_SOURCE'],
                expires,
                current_app.config['SESSION_KEY_BITS']
            ),
            data=sdata
        )

        # sign key using HMAC to make guessing impossible
        mac = hmac.new(self.secret_key, key, self.hash_method)

        return '%s_%s' % (key, mac.hexdigest())

    @classmethod
    def unserialize(cls, string, secret_key):
        key, mac_hexdigest = string.rsplit('_', 1)

        sdata = ''

        mac = hmac.new(secret_key, key, cls.hash_method)

        if mac.hexdigest() == mac_hexdigest:
            # mac okay, load sdata from store
            try:
                sdata = current_app.session_kvstore.get(key)
            except KeyError:
                # someone deleted the session, leave sdata as ''
                pass

        # unserialize "normally"
        s = super(Session, cls).unserialize(sdata, secret_key)
        s.__kvstore_key = key

        return s


class KVSession(object):
    """Activates Flask-KVSession for an application.

    :param session_kvstore: An object supporting the
                            `simplekv.KeyValueStore` interface that session
                            data will be store in.
    :param app: The app to activate. If not `None`, this is essentially the
                same as calling :meth:`init_app` later."""
    key_regex = re.compile('^[0-9a-f]+_(?P<expires>[0-9a-f]+)$')

    def __init__(self, session_kvstore, app=None, random_source=None):
        app.session_kvstore = session_kvstore

        if app:
            self.init_app(app)

    def cleanup_sessions(self):
        """Removes all expired session from the store.

        Periodically, this function should be called to remove sessions from
        the backend store that have expired, as they are not removed
        automatically.

        This function retrieves all session keys, checks if their expiration
        time has passed and if so, removes them."""
        current_time = int(time.time())
        for key in self.app.session_kvstore.keys():
            m = self.key_regex.match(key)
            if m:
                # restore timestamp
                key_expiry_time = int(m.group('expires'), 16)

                # remove if expired
                if current_time >= key_expiry_time:
                    self.app.session_kvstore.delete(key)

    def init_app(self, app):
        """Initialize application and KVSession.

        This will replace the session management of the application with
        Flask-KVSession's."""
        self.app = app
        self.app.config.setdefault('SESSION_KEY_BITS', 64)
        self.app.config.setdefault('SESSION_RANDOM_SOURCE', SystemRandom())
        self.app.open_session = self.open_session

    def open_session(self, request):
        key = self.app.secret_key
        if key is not None:
            return Session.load_cookie(request, self.app.session_cookie_name,
                secret_key=key)
