import inspect
from random import random
from threading import Thread, Event

from queue import Queue

import pokemonhandler
from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_webhooks, add_search_rest, parse_unicode, \
    location_parse
from behaviours import beh_handle_level_up, \
    beh_random_bag_cleaning, beh_spin_nearby_pokestops_with_log_map, beh_catch_all_nearby_pokemon
from geography import *
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude
from scannerutil import install_thread_excepthook, install_forced_update_check, rnd_sleep, setup_logging
from workers import WorkerManager

setup_logging()
log = logging.getLogger(__name__)

print inspect.getfile(inspect.currentframe())  # script filename (usually with path)
print os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # script directory


runner_blinds = {}

parser = std_config("fnord")
add_search_rest(parser)
parser.add_argument('-node-name', '--node-name',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-uad', '--using-account-db',
                    help='Indicates if the application wil enter accounts into account database',
                    default=True)
parser.add_argument('-locs', '--locations', type=parse_unicode,
                    help='Location, can be an address or coordinates.')

add_webhooks(parser)
add_geofence(parser)

args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)

pokemonhandler.set_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

threads = []

account_manager = AccountManager(args.node_name, args.using_account_db, args.accountcsv, (), args, [], [],
                                 Queue(), {})
worker_manager = WorkerManager(account_manager, 25, 9)

pokemon_queue = Queue()

if args.proxy:
    nthreads = len(args.proxy) * 5
else:
    nthreads = 5

log.info("Bot using {} threads".format(nthreads))


def create_leveler_thread(pos, worker_number, forced_update):
    the_thread = Thread(target=safe_do_work, args=( pos, forced_update))
    the_thread.start()
    threads.append(the_thread)
    time.sleep(2)


def time_of_next_pokestop_spin():
    return datetime.now() + timedelta(minutes=(6 + random() * 20))


def time_of_next_pokemon():
    return datetime.now() + timedelta(minutes=(10 + random() * 10))


def safe_do_work(locations, forced_update):

    while not forced_update.isSet():
        # noinspection PyBroadException
        try:
            do_work(locations, forced_update)
        except:
            logging.exception("Outer worker catch block caught exception")
        time.sleep(60)


def next_worker():
    log.info("Getting next worker")
    while True:
        worker = worker_manager.get_worker()
        if not worker.account_info().level or worker.account_info().level < 5:
            log.info("Starting with {}".format(worker.name()) )
            return worker
        else:
            worker.account_info().set_resting()


def do_work(locations, is_forced_update):
    encountered = set()
    worker = next_worker()
    level = get_level(worker)
    inrange_pokestops = {}
    while not is_forced_update.isSet():
        for pos in locations:
            map_objects = worker.do_get_map_objects(pos)
            level = beh_handle_level_up(worker, level, map_objects)
            if level == 5:
                worker = next_worker()
                map_objects = worker.do_get_map_objects(pos)

            beh_spin_nearby_pokestops_with_log_map(worker, map_objects, pos, inrange_pokestops)
            beh_random_bag_cleaning(map_objects, worker)
            beh_catch_all_nearby_pokemon(worker, pos, map_objects, encountered)
            did_work = True

        worker.account_info().set_resting()
        worker_manager.free_worker(worker)
        worker = next_worker()
        level = get_level(worker)
        encountered.clear()


def get_level(worker):
    level = worker.account_info().level
    if not level:
        level = 1
    return level


forced_update = Event()

locs = [with_gmaps_altitude(location_parse(x), args.gmaps_key) for x in args.locations.split(' ')]

install_forced_update_check(args, forced_update)
for i in range(nthreads):
    create_leveler_thread(locs, i, forced_update)

for thread in threads:
    thread.join()
