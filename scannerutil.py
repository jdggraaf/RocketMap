import datetime
import logging
import math
import random
import sys
import time
from distutils.version import StrictVersion
from itertools import cycle
from itertools import tee, izip
from threading import Thread, Event

import requests
from geopy.distance import vincenty
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


def setup_logging(file_name=None):
    if not file_name:
        file_name ="log.out"
    else:
        file_name += ".log"
    print("Logging to {}".format( file_name))
    logging.basicConfig(
        format='%(asctime)s [%(threadName)12s][%(module)13s][%(levelname)8s][%(relativeCreated)d] ' +
               '%(message)s', filename=file_name, level=logging.INFO)
    logging.getLogger("pgoapi").setLevel(logging.WARN)
    logging.getLogger("connectionpool").setLevel(logging.WARN)
    logging.getLogger("Account").setLevel(logging.INFO)
    logging.getLogger("connectionpool").setLevel(logging.INFO)
    logging.getLogger("account").setLevel(logging.INFO)
    logging.getLogger("apiRequests").setLevel(logging.INFO)

log = logging.getLogger(__name__)


def adjust_for_spawnpoints(previous_stop, location, spawn_points):
    distance_from_stop = vincenty(previous_stop, location).m
    dist = sys.maxint
    best = None
    for point in spawn_points:
        from_stop = abs(point["distance"] - distance_from_stop)
        if from_stop < dist:
            dist = from_stop
            best = point
    if dist < 30 and best:
        return best
    else:
        log.info("No spawnpoints nearby, skipping")
        return None




def in_radius(loc1, loc2, distance):
    return equi_rect_distance(loc1, loc2) < distance


# Return equirectangular approximation distance in km.
def equi_rect_distance(loc1, loc2):
    R = 6371  # Radius of the earth in km.
    lat1 = math.radians(loc1[0])
    lat2 = math.radians(loc2[0])
    x = (math.radians(loc2[1]) - math.radians(loc1[1])
         ) * math.cos(0.5 * (lat2 + lat1))
    y = lat2 - lat1
    return R * math.sqrt(x * x + y * y)


def equi_rect_distance_m(loc1, loc2):
    return equi_rect_distance(loc1, loc2) * 1000


def distance_to_fort(player_location, fort):
    return equi_rect_distance(player_location, fort_as_coordinate(fort)) * 1000


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


pogo_api_version = '0.79.4'


def check_forced_version(api_check_time, pause_bit, proxy_cycler):
    if int(time.time()) > api_check_time:
        api_check_time = int(time.time()) + random.randint(60, 300)
        forced_api = get_api_version(proxy_cycler)

        if not forced_api:
            # Couldn't retrieve API version. Pause scanning. Nah do nothing
            log.warning('Forced API check got no or invalid response. ' +
                        'Possible bad proxy.')
            return api_check_time

        # Got a response let's compare version numbers.
        try:
            if StrictVersion(pogo_api_version) < StrictVersion(forced_api):
                # Installed API version is lower. Pause scanning.
                pause_bit.set()
                log.warning('Started with API: %s, ' +
                            'Niantic forced to API: %s',
                            pogo_api_version,
                            forced_api)
                log.warning('Scanner paused due to forced Niantic API update.')
            else:
                # API check was successful and
                # installed API version is newer or equal forced API.
                # Continue scanning.
                log.debug("API check was successful. Continue scanning.")
                pause_bit.clear()

        except ValueError:
            # Unknown version format. Pause scanning as well.
            pause_bit.set()
            log.warning('Niantic forced unknown API version format: %s.',
                        forced_api)
            log.warning('Scanner paused due to unknown API version format.')
        except Exception as e:
            # Something else happened. Pause scanning as well.
            pause_bit.set()
            log.warning('Unknown error on API version comparison: %s.',
                        repr(e))
            log.warning('Scanner paused due to unknown API check error.')

    return api_check_time


def is_forced_version(proxy):
    forced_api = get_api_version_with_proxy(proxy)
    return pogo_api_version != forced_api and forced_api != 0

def auth_service(account):
    return account.get("auth_service", account.get("provider",account.get("auth")))

def device_id(account):
    return account.get("device_id", account.get("id"))

def install_forced_update_check(args, force_update_bit):
    the_thread = Thread(target=run_forced_update_check,  args=(args, force_update_bit))
    the_thread.daemon = True
    the_thread.start()

def create_forced_update_check(args):
    forced_update_bit = Event()
    the_thread = Thread(target=run_forced_update_check,  args=(args, forced_update_bit))
    the_thread.daemon = True
    the_thread.start()
    return forced_update_bit


def run_forced_update_check(args, force_update_bit):
    current_cycler = None
    if "proxy" in args and args.proxy is not None:
        current_cycler = cycle(args.proxy)

    api_check_time = 0
    while not force_update_bit.isSet():
        api_check_time = check_forced_version(api_check_time, force_update_bit, current_cycler)
        time.sleep(10)


def fail_on_forced_update_with_external_bit(args,pause_bit):
    current_cycler = None
    if "proxy" in args and args.proxy is not None:
        current_cycler = cycle(args.proxy)

    pause_bit.clear()
    check_forced_version(0, pause_bit, current_cycler)
    if pause_bit.isSet():
        log.error("Forced update detected. Not starting")
        exit(1)

def fail_on_forced_update(args):
    return fail_on_forced_update_with_external_bit(args, Event())


def is_blank (my_string):
    if my_string and my_string.strip():
        #myString is not None AND myString is not empty or blank
        return False
    #myString is None OR myString is empty or blank
    return True


def get_api_version( proxy_cycler):
    if proxy_cycler is not None:
        return get_api_version_with_proxy(next(proxy_cycler))
    else:
        return get_api_version_with_proxy()


def get_api_version_with_proxy(proxy=None):
        proxies = {}

        if proxy is not None:
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


def nice_number_1(number):
    return str("%.1f" % round(number, 1))


def nice_number_2(number):
    return str("%.2f" % round(number, 2))

def precise_nice_number(number):
    return str("%.5f" % round(float(number), 5))


def filter_list(full_list, excludes):
    s = set(excludes)
    return (x for x in full_list if x not in s)


def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    next(b, None)
    return izip(a, b)

def nice_coordinate_string(pos):
    if len(pos) > 2 and pos[2]:
        return "({},{},{})".format(nice_number(pos[0]),
                                   nice_number(pos[1]),
                                   nice_number_1(pos[2]))
    else:
        return "({},{})".format(nice_number(pos[0]),
                                nice_number(pos[1]))


def precise_coordinate_string(pos):
    if len(pos) > 2 and pos[2] is not None:
        return "{},{},{}".format(precise_nice_number(pos[0]),
                                 precise_nice_number(pos[1]),
                                 nice_number_1(pos[2]))
    else:
        return "{},{}".format(precise_nice_number(pos[0]),
                              precise_nice_number(pos[1]))

def fort_as_coordinate(fort):
    return fort.latitude, fort.longitude

def full_precision_coordinate_string(pos):
    if len(pos) > 2 and pos[2] is not None:
        return "{},{},{}".format(float(pos[0]),
                                 float(pos[1]),
                                 float(pos[2]))
    else:
        return "{},{}".format(float(pos[0]),
                              float(pos[1]))


def action_delay(low, high):
    # Waits for random number of seconds between low & high numbers
    longNum = random.uniform(low, high)
    shortNum = float("{0:.2f}".format(longNum))
    time.sleep(shortNum)

def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]

def parse_hh_mm(stop_at_string):
    h, m = list(map(int, stop_at_string.split(':')))
    return h,m

def minute_of_day_parse_hh_mm(stop_at_string):
    h, m = list(map(int, stop_at_string.split(':')))
    return h*60+m

def start_at_datetime(stop_at_string):
    h, m = parse_hh_mm(stop_at_string)
    now = datetime.datetime.now()
    start_at = now.replace(hour=h, minute=m)
    return start_at


def stop_at_datetime(start_time_string, stop_time_string):
    stop_at = start_at_datetime(stop_time_string)

    stop_minute_of_day = minute_of_day_parse_hh_mm(stop_time_string)
    start_minute_of_day = minute_of_day_parse_hh_mm(start_time_string)
    if stop_minute_of_day < start_minute_of_day:
        stop_at += datetime.timedelta(days=1)
    return stop_at



'''
    now = datetime.now()
    epoch = datetime(1970, 1, 1)  # use POSIX epoch
    posix_timestamp_micros = (now - epoch) // timedelta(microseconds=1)
    posix_timestamp_millis = posix_timestamp_micros // 1000  # or `/ 1e3` for float
    return posix_timestamp_millis
'''

