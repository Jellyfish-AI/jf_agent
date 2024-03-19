import bisect
from collections import namedtuple, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
import threading
import time

import requests

from jf_ingest import logging_helper

logger = logging.getLogger(__name__)

RateLimitRealmConfig = namedtuple('RateLimitRealmConfig', ['max_calls', 'period_secs'])


class RateLimiter(object):
    def __init__(self, realm_config, timeout_secs=60 * 60):
        self.timeout_secs = timeout_secs

        # A dict, keyed by realm name, where the result is a RateLimitRealmConfig
        self.realm_config = realm_config

        # A dict, keyed by realm name, where the result is a sorted list of expiration
        # timestamps for the calls made to that realm
        self.realm_call_trackers = defaultdict(list)

        # Add thread safety
        self.lock = threading.RLock()

    @contextmanager
    def limit(self, realm):
        # if realm is None, don't rate limit, just execute the thing
        if realm is None:
            yield
            return

        max_calls, period_secs = self.realm_config[realm]
        start = datetime.utcnow()
        while True:
            # decide whether to sleep or call, inside the lock
            with self.lock:
                sleep_until, calls_made = self._call_available(realm, max_calls)
                if not sleep_until:
                    self._record_call(realm, period_secs)

            if not sleep_until:
                try:
                    # stuff within the context manager happens here
                    yield
                    return
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:
                        # Got rate limited anyway!
                        logging_helper.log_standard_error(
                            logging.ERROR, msg_args=[calls_made, max_calls, realm], error_code=3010,
                        )
                    raise

            logger.info(
                f'Rate limiter: exceeded {max_calls} calls in {period_secs} seconds for {realm}!',
            )
            if (sleep_until - start) >= timedelta(seconds=self.timeout_secs):
                logging_helper.log_standard_error(
                    logging.ERROR, msg_args=[self.timeout_secs], error_code=3020
                )
                raise Exception('Rate limit timeout')

            sleep_period_secs = (sleep_until - datetime.utcnow()).total_seconds()
            if sleep_period_secs > 0:  # it's possible that sleep_until was a couple ms ago
                logger.info(
                    f'Sleeping for {sleep_period_secs:.1f} secs ({sleep_period_secs / 60.0:.1f} mins)'
                )
                time.sleep(sleep_period_secs)

    def _call_available(self, realm, max_calls):
        '''
        Return a future time when there will be a call slot available, or None
        if one is available already.
        '''

        # First, clear out any expired calls
        now = datetime.utcnow().timestamp()
        existing_call_expirations = self.realm_call_trackers[realm]
        self.realm_call_trackers[realm] = existing_call_expirations[
            bisect.bisect_left(existing_call_expirations, now) :
        ]

        # See how many remain
        calls_made = len(self.realm_call_trackers[realm])
        if calls_made < max_calls:
            return None, calls_made

        next_tstamp = self.realm_call_trackers[realm][0]

        return datetime.fromtimestamp(next_tstamp), calls_made

    def _record_call(self, realm, period_secs):
        '''
        Record that we're making a call for this realm so that others know about
        it and don't exceed the rate limit later.
        '''
        expiration = (datetime.utcnow() + timedelta(seconds=period_secs)).timestamp()
        self.realm_call_trackers[realm].append(expiration)
