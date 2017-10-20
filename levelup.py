import random
from threading import Thread, Event

from queue import Queue

import pokemonhandler
from accountdbsql import set_account_db_args, db_set_egg_count
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_webhooks, add_search_rest, parse_unicode, \
    location_parse, add_threads_per_proxy, add_use_account_db_true
from argutils import thread_count
from behaviours import beh_handle_level_up, \
    beh_random_bag_cleaning, beh_spin_nearby_pokestops, PHASE_0_ITEM_LIMITS, beh_catch_pokemon, L20_ITEM_LIMITS, \
    beh_aggressive_bag_cleaning, L12_ITEM_LIMITS
from geography import *
from getmapobjects import catchable_pokemon
from gymdbsql import set_args
from inventory import has_lucky_egg, poke_balls, egg_count
from management_errors import NoMoreWorkers
from pogom.fnord_altitude import with_gmaps_altitude
from pokestop_routes import all_routes
from scannerutil import install_thread_excepthook, install_forced_update_check, setup_logging, nice_number_1
from workers import wrap_account_no_replace

setup_logging()
logging.getLogger("pogoservice").setLevel(logging.DEBUG)

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

runner_blinds = {}

parser = std_config("levelup")
add_search_rest(parser)
add_use_account_db_true(parser)
parser.add_argument('-system-id', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-locs', '--locations', type=parse_unicode,
                    help='Location, can be an address or coordinates.')
parser.add_argument('-r', '--route', type=parse_unicode,
                    help='Predefined route (locations). Known routes are oslo, copenhagen')
parser.add_argument('-lvl', '--target-level', default=5,
                    help='Target level of the bot')
add_threads_per_proxy(parser)
parser.add_argument('-st', '--max-stops', default=999,
                    help='Max pokestops for a single session')
parser.add_argument('-pokemon', '--catch-pokemon', default=0,
                    help='If the levelup should catch pokemon (not recommended)')
parser.add_argument('-egg', '--use-eggs', default=True,
                    help='True to use lucky eggs')

add_webhooks(parser)
add_geofence(parser)

args = parser.parse_args()

args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

load_proxies(args)
set_args(args)
set_account_db_args(args)

pokemonhandler.set_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

threads = []

account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {})
account_manager.reallocate = False
account_manager.initialize(args.accountcsv, ())

pokemon_queue = Queue()

nthreads = thread_count(args)

log.info("Bot using {} threads".format(str(nthreads)))


def create_leveler_thread(pos, thread_num, forced_update_check):
    the_thread = Thread(target=safe_do_work, args=(pos, thread_num, forced_update_check))
    the_thread.start()
    threads.append(the_thread)
    time.sleep(6)


def safe_do_work(locations, thread_num, forced_update_):

    #while not forced_update_.isSet():
    # noinspection PyBroadException
    try:
        worker = next_worker()
        if worker:
            do_work(worker, locations, thread_num, forced_update_)
    except OutOfAccounts:
        logging.info("No more accounts, exiting worker thread")
        return
    except GaveUp:
        logging.info("Gave UP, exiting")
        return
    except:
        logging.exception("Outer worker catch block caught exception")
    time.sleep(60)


def next_worker():
    account = account_manager.get_account(False)
    worker = wrap_account_no_replace(account, account_manager)
    return worker


def random_sleep(lower, upper):
    ms = int(random.uniform(lower, upper))
    log.info("Sleeping(2) for {}ms".format(str(ms)))
    time.sleep(float(ms)/1000)


def do_work(worker, locations, thread_num, is_forced_update, use_eggs=True):
    level = None
    caught_pokemon_ids = set()
    caught_encounters = set()
    pokemon_caught = 0
    started_at = datetime.now()
    next_phase = datetime.now() + timedelta(minutes=30)
    phase = 0
    egg_seen = False
    next_egg = datetime.now() if use_eggs else datetime.now() + timedelta(days=365)

    spun = 0
    for index, pos in enumerate(locations):
        next_pos = locations[index+1] if index < len(locations) else None
        if is_forced_update.isSet():
            log.info("Forced update, qutting")
            return
        if phase == 0 and datetime.now() > next_phase:
            phase += 1
            log.info("Advancing to phase {}".format(str(phase)))
        if spun > args.max_stops:
            log.info("Reached target spins {}".format(str(spun)))
            break
        map_objects = worker.do_get_map_objects(pos)
        level = beh_handle_level_up(worker, level, map_objects)
        if level == args.target_level:
            log.info("Reached target level {}, exiting thread".format(level))
            return
        if has_lucky_egg(worker) and not egg_seen:
            egg_seen = True
            balls = poke_balls(worker)
            log.info("Worker has lucky egg at {} spins, {} pokeballs".format(str(spun), str(balls)))
        if has_lucky_egg(worker) and datetime.now() > next_egg:
            worker.do_use_lucky_egg()
            # incense=8
            next_egg = datetime.now() + timedelta(minutes=30)
            db_set_egg_count(worker.account_info().username, egg_count(worker))
            phase = 1

        spun += beh_spin_nearby_pokestops(worker, map_objects, pos)
        if spun % 10 == 0:
            log.info("{} spun {} pokestops".format(worker.name(), str(spun)))
        seconds_between_locations = time_between_locations(pos, next_pos, 8)
        limits = PHASE_0_ITEM_LIMITS if phase == 0 else L20_ITEM_LIMITS
        if seconds_between_locations > 20:
            beh_aggressive_bag_cleaning(worker, limits)
        else:
            beh_random_bag_cleaning(worker, limits)

        if phase >= 1 and args.catch_pokemon > 0 and pokemon_caught < args.catch_pokemon and seconds_between_locations > 15:
            log.info("Entering catch block with {} seconds until next location".format(str(nice_number_1(seconds_between_locations))))
            scan_catchable = catchable_pokemon(map_objects)
            to_catch = prioritize_catchable(caught_pokemon_ids, scan_catchable)
            if to_catch and to_catch.encounter_id not in caught_encounters:
                caught_encounters.add(to_catch.encounter_id)
                caught = beh_catch_pokemon(worker, map_objects, pos, to_catch.encounter_id, to_catch.spawn_point_id)
                if caught:
                    pokemon_caught += 1
                    rval = worker.do_transfer_pokemon([caught])
                    if rval > 1:
                        log.error("Transfering pokemon {} gave status {}".format(caught, rval))

    log.info("Reached end of route with {} spins, going to rest".format(str(spun)))

preferred = {10, 13, 16, 19, 29, 32, 41, 69, 74, 92, 183}


def prioritize_catchable(caught, catchable):
    for pokemon in catchable:
        if pokemon.pokemon_id not in caught:
            caught.add(pokemon.pokemon_id)
            return pokemon
    for pokemon in catchable:
        if pokemon.pokemon_id in preferred:
            return pokemon
    return None


def get_limits(level):
    # one level above actual level to ensure supplies are accumulated
    return PHASE_0_ITEM_LIMITS if level < 13 else L12_ITEM_LIMITS if level < 21 else L20_ITEM_LIMITS

def get_level(worker):
    level = worker.account_info()["level"]
    if not level:
        level = 1
    return level


forced_update = Event()

if args.route:
    locs = all_routes.get(args.route)
    if not locs:
        raise "The route {} is not found".format(args.route)
else:
    locs = [with_gmaps_altitude(location_parse(x), args.gmaps_key) for x in args.locations.split(' ')]

install_forced_update_check(args, forced_update)
for i in range(nthreads):
    create_leveler_thread(locs, i, forced_update)

for thread in threads:
    thread.join()
