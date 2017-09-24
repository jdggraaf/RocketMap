import json
import logging
import os
import random

import datetime
from geopy.distance import vincenty
from threading import Thread, Event
import time
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from pogom.proxy import get_new_proxy
from itertools import cycle


def setup_logging():
    logging.basicConfig(
        format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
               '%(message)s', level=logging.INFO)
    logging.getLogger("pgoapi").setLevel(logging.WARN)
    logging.getLogger("connectionpool").setLevel(logging.WARN)
    logging.getLogger("Account").setLevel(logging.INFO)

log = logging.getLogger(__name__)


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random.random() * 2)
    time.sleep(random_)


def distancMatrix(gyms):
    for gym1 in gyms:
        gym1["coords"] = (gym1["latitude"], gym1["longitude"])
    for gym in gyms:
        nearby = {}
        gym["nearby"] = nearby
        for othergym in gyms:
            if gym["gym_id"] != othergym["gym_id"]:
                distance = vincenty(gym["coords"], othergym["coords"]).m
                if (distance < 499):
                    nearby[othergym["name"] + str(distance)] = othergym
    return gyms


def distancMatrix2(gyms):
    for gym1 in gyms:
        gym1["coords"] = (gym1["latitude"], gym1["longitude"])
    for gym in gyms:
        nearby = []
        gym["nearby"] = nearby
        for othergym in gyms:
            if gym["gym_id"] != othergym["gym_id"]:
                distance = vincenty(gym["coords"], othergym["coords"]).m
                if (distance < 499):
                    nearby.append(othergym["gym_id"])
    return gyms



def distancMatrix3(gyms):
    for gym1 in gyms:
        gym1["coords"] = (gym1["latitude"], gym1["longitude"])
    for gym in gyms:
        nearby = []
        gym["nearby"] = nearby
        for othergym in gyms:
            if gym["gym_id"] != othergym["gym_id"]:
                distance = vincenty(gym["coords"], othergym["coords"]).m
                if (distance < 499):
                    nearby.append((distance,othergym["gym_id"]))
    return gyms


def timestamp_ms():
    return time.time() * 1000


def check_forced_version(api_version, api_check_time, pause_bit, proxy_cycler):
    if int(time.time()) > api_check_time:
        api_check_time = int(time.time()) + random.randint(60, 300)
        forced_api = get_api_version(proxy_cycler)

        if (api_version != forced_api and forced_api != 0):
            pause_bit.set()
            log.info(('Started with API: {}, ' +
                      'Niantic forced to API: {}').format(
                api_version,
                forced_api))
            log.info('Scanner paused due to forced Niantic API update.')
            log.info('Stop the scanner process until RocketMap ' +
                     'has updated.')

    return api_check_time

pogo_api_version = '0.73.1'


def install_forced_update_check(args, force_update_bit):
    the_thread = Thread(target=run_forced_update_check, args=(args, force_update_bit))
    the_thread.start()


def run_forced_update_check(args, force_update_bit):
    current_cycler = None
    if "proxy" in args and args.proxy is not None:
        current_cycler = cycle(args.proxy)

    api_check_time = 0
    while not force_update_bit.isSet():
        api_check_time = check_forced_version(pogo_api_version, api_check_time, force_update_bit, current_cycler)
        time.sleep(10)


def fail_on_forced_update_with_external_bit(args,pause_bit):
    current_cycler = None
    if "proxy" in args and args.proxy is not None:
        current_cycler = cycle(args.proxy)

    pause_bit.clear()
    check_forced_version(pogo_api_version, 0, pause_bit, current_cycler)
    if pause_bit.isSet():
        log.error("Forced update detected. Not starting")
        exit(1)

def fail_on_forced_update(args):
    return fail_on_forced_update_with_external_bit(args, Event())


def get_api_version( proxy_cycler):
    proxies = {}

    if proxy_cycler is not None:
        proxy = next(proxy_cycler)
        proxies = {
            'http': proxy,
            'https': proxy
        }

    try:
        s = requests.Session()
        s.mount('https://',
                HTTPAdapter(max_retries=Retry(total=3,
                                              backoff_factor=0.1,
                                              status_forcelist=[500, 502,
                                                                503, 504])))
        r = s.get(
            'https://pgorelease.nianticlabs.com/plfe/version',
            proxies=proxies,
            verify=False)
        return r.text[2:] if (r.status_code == requests.codes.ok and
                              r.text[2:].count('.') == 2) else 0
    except Exception as e:
        log.warning('error on API check: %s', repr(e))
        return 0




# Patch to make exceptions in threads cause an exception.
def install_thread_excepthook():
    """
    Workaround for sys.excepthook thread bug
    (https://sourceforge.net/tracker/?func=detail&atid=105470&aid=1230540&group_id=5470).
    Call once from __main__ before creating any threads.
    If using psyco, call psycho.cannotcompile(threading.Thread.run)
    since this replaces a new-style class method.
    """
    import sys
    run_old = Thread.run

    def run(*args, **kwargs):
        try:
            run_old(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            sys.excepthook(*sys.exc_info())
    Thread.run = run


def nice_number(number):
    return str("%.3f" % round(number, 3))


def nice_coordinate_string(pos):
    if len(pos) > 2 and pos[2]:
        return "({},{},{})".format(nice_number(pos[0]),
                                   nice_number(pos[1]),
                                   nice_number(pos[2]))
    else:
        return "({},{})".format(nice_number(pos[0]),
                                nice_number(pos[1]))

def action_delay(low, high):
    # Waits for random number of seconds between low & high numbers
    longNum = random.uniform(low, high)
    shortNum = float("{0:.2f}".format(longNum))
    time.sleep(shortNum)

def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in xrange(0, len(l), n):
        yield l[i:i + n]

def parse_hh_mm(stop_at_string):
    h, m = map(int, stop_at_string.split(':'))
    return h,m


def start_at_datetime(stop_at_string):
    h, m = parse_hh_mm(stop_at_string)
    now = datetime.datetime.now()
    start_at = now.replace(hour=h, minute=m)
    return start_at


def stop_at_datetime(time_string):
    now = datetime.datetime.now()
    stop_at = start_at_datetime(time_string)
    if stop_at < now:
        stop_at += datetime.timedelta(days=1)
    return stop_at



'''
    now = datetime.now()
    epoch = datetime(1970, 1, 1)  # use POSIX epoch
    posix_timestamp_micros = (now - epoch) // timedelta(microseconds=1)
    posix_timestamp_millis = posix_timestamp_micros // 1000  # or `/ 1e3` for float
    return posix_timestamp_millis
'''

