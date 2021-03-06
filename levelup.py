from threading import Thread

from queue import Queue

import pokemonhandler
from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_webhooks, add_search_rest, parse_unicode, \
    add_threads_per_proxy, add_use_account_db_true, setup_proxies
from argutils import thread_count
from behaviours import beh_aggressive_bag_cleaning
from catchmanager import CatchManager, CatchFeed, OneOfEachCatchFeed, Candy12Feed, NoOpFeed, CatchConditions
from geography import *
from getmapobjects import is_discardable, is_starter_pokemon, catchable_pokemon
from gymdbsql import set_args
from hamburg import xp_route_1
from hamburg import xp_route_2
from levelup_tools import get_pos_to_use, is_plain_coordinate, is_encounter_to, exclusion_pokestops, CountDownLatch, \
    is_array_pokestops
from pogom.apiRequests import set_goman_hash_endpoint
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.proxy import check_proxies
from pogoservice import TravelTime, ApplicationBehaviour
from pokestoproutesv2 import routes_p1, initial_grind, initial_130_stops, routes_p2, xp_p1, xp_p2
from scannerutil import install_thread_excepthook, setup_logging, \
    create_forced_update_check
from stopmanager import StopManager
from workermanager import WorkerManager, PositionFeeder
from workers import wrap_account_no_replace

parser = std_config("levelup_default")
add_search_rest(parser)
add_use_account_db_true(parser)
parser.add_argument('-system-id', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-fsi', '--final-system-id',
                    help='Define the name of the node where accounts are transferred upon successful botting',
                    default=None)
parser.add_argument('-fasi', '--failed-system-id',
                    help='Define the name of the node where accounts are transferred upon unsuccessful botting',
                    default=None)
parser.add_argument('-fl', '--fail-last', default=0,
                    help='When this number of accounts remain, fail any accounts that are less than 95% done')
parser.add_argument('-locs', '--locations', type=parse_unicode,
                    help='Location, can be an address or coordinates.')
parser.add_argument('-r', '--route', type=parse_unicode,
                    help='Predefined route (locations). Known routes are oslo, copenhagen')
parser.add_argument('-lvl', '--target-level', default=5,
                    help='Target level of the bot')
add_threads_per_proxy(parser)
parser.add_argument('-st', '--max-stops', default=3490,
                    help='Max pokestops for a single session')
parser.add_argument('-tc', '--thread-count', default=5,
                    help='Number of threads to use')
parser.add_argument('-pokemon', '--catch-pokemon', default=3490,
                    help='If the levelup should catch pokemon (not recommended)')
parser.add_argument('-egg', '--use-eggs', default=True,
                    help='True to use lucky eggs')
parser.add_argument('-fs', '--fast-speed', default=25,
                    help='Fast speed in m/s')
parser.add_argument('-fast-levlup', '--fast-levelup', default=False, action='store_true',
                    help='True to use stop-only double XP mode')
parser.add_argument('-iegg', '--use-initial-egg', default=True, action='store_true',
                    help='True to use lucky eggs')
parser.add_argument('-ca', '--catch-all', default=False, action='store_true',
                    help='Catch all eligible')
parser.add_argument('-am', '--alt-mode', default=False, action='store_true',
                    help='Alt mode')
parser.add_argument('-ns', '--non-stop', default=False, action='store_true',
                    help='Run without stop')

add_webhooks(parser)
add_geofence(parser)

args = parser.parse_args()
setup_logging(args.system_id)

logging.getLogger("pogoservice").setLevel(logging.DEBUG)
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)



args.player_locale = {'country': 'DE', 'language': 'de', 'timezone': 'Europe/Berlin'}
args.status_name = args.system_id

setup_proxies(args)
set_args(args)
set_account_db_args(args)
if args.overflow_hash_key:
    set_goman_hash_endpoint(args.overflow_hash_key)

pokemonhandler.set_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
threads = []
num_started = 0
lock = Lock()
num_completed = 0


account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {})
account_manager.reallocate = False
account_manager.initialize(args.accountcsv, ())

global_catch_feed = CatchFeed()
one_of_each_catch_feed = OneOfEachCatchFeed()
candy_12_feed = Candy12Feed()


def safe_do_work(thread_num, global_catch_feed, latch , forced_update_):
    global num_completed
    # while not forced_update_.isSet():
    # noinspection PyBroadException
    while True:
        try:
            worker = next_worker()
            if worker:
                if args.fast_levelup:
                    do_fast25(thread_num, worker, forced_update_)
                else:
                    do_work(thread_num, worker, global_catch_feed, latch, forced_update_)
        except OutOfAccounts:
            logging.info("No more accounts, exiting worker thread")
            return
        except GaveUp:
            logging.info("Gave UP, exiting")
            return
        except:
            logging.exception("Outer worker catch block caught exception")
        finally:
            latch.count_down()
        if not args.non_stop:  # latch does not work in non-stop mode
            break
    with lock:
        num_completed += 1


def next_worker():
    account = account_manager.get_account(False)
    worker = wrap_account_no_replace(account, account_manager, int(args.fast_speed))
    return worker


def do_just_stops(locations, location_feeder, sm, wm, travel_time, phase, num_eggs):
    first_loc = locations[0][0]
    map_objects = wm.move_to_with_gmo(first_loc)
    travel_time.set_fast_speed(True)
    for route_element, next_route_element in pairwise(locations):
        if sm.reached_limits():
            return

        has_egg = wm.has_lucky_egg()
        egg_active = wm.has_active_lucky_egg()
        if has_egg and not egg_active and num_eggs > 0:
            num_eggs -= 1
            wm.use_egg()

        player_location = route_element[0]
        next_pos = next_route_element[0]

        num_spun = sm.spin_all_stops(map_objects, player_location, range_m=50, exclusion={})
        expected_spins = len(route_element[1])
        if num_spun != expected_spins:
            log.info("{} pokestops spun, expected {}".format(str(num_spun),str(expected_spins)))

        sm.log_status(egg_active, wm.has_egg, wm.egg_number, location_feeder.index(), phase)

        map_objects = wm.move_to_with_gmo(next_pos)
        log.info("Complieted one route element")


def do_iterable_point_list(locations, xp_feeder, xp_boost_phase, catch_feed, cm, sm, wm, thread_num, travel_time,
                           worker, phase, catch_condition, first_time=None, outer=True, pos_index=0):
    first_loc = get_pos_to_use(locations[0], None, thread_num)
    fallback_altitude = first_loc[2]
    log.info("First lof {}".format(str(first_loc)))
    map_objects = wm.move_to_with_gmo(first_loc)
    did_map_objects = True

    excluded_stops = exclusion_pokestops(xp_route_1 + xp_route_2)
    if first_time:
        first_time()
    catch_condition.log_description(phase)
    do_extra_gmo_after_pokestops = False

    for route_element, next_route_element in pairwise(locations):
        if cm.is_caught_already(route_element):
            continue
        if sm.reached_limits():
            return
        # wm.use_incense()   # disabled until I can find out how to identify
        if cm.can_start_evolving() and xp_feeder:
            beh_aggressive_bag_cleaning(worker)
            do_iterable_point_list(xp_feeder, None, True, NoOpFeed(), cm, sm, wm, None, travel_time, worker, phase,
                                   catch_condition, outer=False)

        egg_active = wm.use_egg(cm)
        player_location = get_pos_to_use(route_element, fallback_altitude, thread_num if outer else None)
        fallback_altitude = player_location[2]
        next_pos = get_pos_to_use(next_route_element, fallback_altitude, thread_num if outer else None)

        if is_encounter_to(route_element) or is_plain_coordinate(route_element) or xp_boost_phase or is_array_pokestops(route_element):
            sm.spin_all_stops(map_objects, player_location, range_m=50 if xp_boost_phase else 39.8, exclusion={} if xp_boost_phase else excluded_stops )
        else:
            pokestop = route_element[1]
            pokestop_id = pokestop[3]
            sm.spin_stops(map_objects, pokestop_id, player_location, pos_index, excluded_stops)
        if pos_index % 10 == 0:
            sm.log_inventory()

        if do_extra_gmo_after_pokestops:
            log.info("Wating an extra cycle after fast moves")
            map_objects = wm.get_map_objects(player_location)

        sm.log_status(egg_active, wm.has_egg, wm.egg_number, pos_index, phase)
        cm.do_catch_moving(map_objects, player_location, next_pos, pos_index, catch_condition)
        cm.do_bulk_transfers()

        time_to_location = travel_time.time_to_location(next_pos)
        out_of_eggs = wm.is_out_of_eggs_before_l30()
        if egg_active or out_of_eggs:
            candy_ = worker.account_info()["candy"]
            for evo in range(0, int(math.ceil(time_to_location / 15))):
                cm.evolve_one(candy_, fast=True)

        if outer:
            while True:
                encs = catch_feed.items[pos_index]
                enc_pos = None
                enc_id = None
                for encounter_id in encs:
                    if encounter_id not in cm.processed_encounters:
                        enc_id = encounter_id
                        enc_pos = encs[enc_id][0]
                if not enc_id:
                    break
                log.info("Dealing with nested location {}".format(str(enc_pos)))
                do_iterable_point_list([encs[enc_id][0], encs[enc_id][0]], xp_feeder, xp_boost_phase, NoOpFeed(), cm,
                                       sm, wm, None, travel_time, worker, phase, catch_condition, outer=False,
                                       pos_index=pos_index)
                # i dont like these heuristics one damn bit
                cm.processed_encounters.add(enc_id)  # this must be done in case there is nothing at the location
                for encounter_id in encs:  # dump all other stuff reported from this location too, we'v been here.
                    if encs[encounter_id][0] == enc_pos:
                        cm.processed_encounters.add(encounter_id)

        slow_time_to_location = travel_time.slow_time_to_location(next_pos)
        use_fast = slow_time_to_location > 20
        travel_time.set_fast_speed( use_fast)

        if use_fast:
            map_objects = wm.move_to_with_gmo(next_pos,is_fast_speed=use_fast)
            do_extra_gmo_after_pokestops = len(catchable_pokemon(map_objects)) == 0
        else:
            map_objects = wm.move_to_with_gmo(next_pos,is_fast_speed=use_fast, at_location=lambda po, mo:cm.do_catch_moving(mo, po, next_pos, pos_index, catch_condition, broadcast=False) )
            do_extra_gmo_after_pokestops = False
        cm.do_bulk_transfers()
        if time_to_location > 20:
            cm.clear_state()
        pos_index += 1


def initial_stuff(feeder, wm, cm, worker):
    wm.move_to_with_gmo(get_pos_to_use(feeder.peek(), None, None))
    wm.explain()
    inv_pokemon = worker.account_info().pokemons
    buddy_id=worker.account_info()["buddy"]
    log.info("Byddy id is {}".format(str(buddy_id)))
    nonfavs = [(id_,pokemon) for id_,pokemon in inv_pokemon.items() if is_discardable(id_,pokemon, buddy_id) and not is_starter_pokemon(pokemon)]
    log.info("Transferring all pokemon that cant be evolved, considering {} pokemons".format(str(len(nonfavs))))
    for p_id,pokemon in nonfavs:
        pokemon_id = pokemon["pokemon_id"]
        cm.process_evolve_transfer_item(p_id, pokemon_id)
    log.info("Evolve-map {}".format(str(cm.evolve_map)))
    cm.do_transfers()


def do_fast25(thread_num, worker, is_forced_update):
    travel_time = worker.getlayer(TravelTime)

    wm = WorkerManager(worker, True, args.target_level)
    wm.fast_egg = True
    cm = CatchManager(worker, args.catch_pokemon, NoOpFeed())
    sm = StopManager(worker, cm, wm, args.max_stops)

    app_behaviour = worker.getlayer(ApplicationBehaviour)
    app_behaviour.behave_properly = False

    feeder = PositionFeeder(xp_p1[args.route], is_forced_update)
    do_iterable_point_list(feeder, None, True, candy_12_feed, cm, sm, wm, thread_num, travel_time, worker, 1,
                           CatchConditions.everything_condition())

    if not sm.reached_limits():
        xp_feeder2 = PositionFeeder(xp_p2[args.route], is_forced_update)
        do_iterable_point_list(xp_feeder2, None, True, global_catch_feed, cm, sm, wm, thread_num, travel_time, worker,
                               2, CatchConditions.everything_condition())

    if not sm.reached_limits():
        last_feeder = PositionFeeder(routes_p2[args.route], is_forced_update)
        do_iterable_point_list(last_feeder, None, False, global_catch_feed, cm, sm, wm, thread_num, travel_time, worker,
                               3, CatchConditions.grind_condition())

    if args.final_system_id:
        db_set_system_id(worker.name(), args.final_system_id)
        log.info("Transferred account {} to system-id {}".format(worker.name(), args.final_system_id))

    log.info("Reached end of route with {} spins, going to rest".format(str(len(sm.spun_stops))))


def do_work(thread_num, worker, global_catch_feed, latch, is_forced_update, use_eggs=True):
    travel_time = worker.getlayer(TravelTime)

    wm = WorkerManager(worker, use_eggs, args.target_level)
    cm = CatchManager(worker, args.catch_pokemon, global_catch_feed)
    sm = StopManager(worker, cm, wm, args.max_stops)

    cm.catch_feed = candy_12_feed
    initial_pokestops = initial_130_stops.get(args.route)
    num_items = max(136, len(initial_pokestops) - thread_num)
    feeder = PositionFeeder(list(reversed(initial_pokestops))[:num_items], is_forced_update)
    started_at_0 = wm.player_level() < 1
    if wm.player_level() < 8:
        log.info("Doing initial pokestops PHASE")

        do_iterable_point_list(feeder, None, False, candy_12_feed, cm, sm, wm, thread_num, travel_time, worker, 1,
                               CatchConditions.initial_condition())

    sm.clear_state()
    if False and (started_at_0 or wm.player_level() < 22):
        log.info("Doing initial catches PHASE, player level is {}".format(str(wm.player_level())))
        grind_points = initial_grind.get(args.route)
        grind_locs = [with_gmaps_altitude(x, args.gmaps_key) for x in grind_points]
        grind_route = create_route(grind_locs, 3*35, (thread_num % 3) * 35, int(thread_num / 3) * 35)  # cover 3x3

        cm.catch_feed = one_of_each_catch_feed
        feeder = PositionFeeder(grind_route, is_forced_update)
        initial_stuff(feeder, wm, cm, worker)

        latch.count_down()
        log.info("Waiting for other workers to join here")
        latch.await()

        #if args.use_initial_egg:  # ensure this is done after GMO so we are in position
        #    if not has_lucky_egg(worker):
        #        log.error("Has no egg for initial catches. Initial phase did not produce egg or bot was restarted")
        #    wm.use_incense()
        #    wm.use_egg(force=True)
        do_iterable_point_list(feeder, None, False, one_of_each_catch_feed, cm, sm, wm, thread_num, travel_time, worker,
                               2, CatchConditions.initial_condition())

    log.info("Main grind PHASE 1")
    wm.explain()
    cm.catch_feed = global_catch_feed
    feeder = PositionFeeder(routes_p1[args.route], is_forced_update)
    xp_feeder = PositionFeeder(xp_p1[args.route], is_forced_update)
    initial_stuff(feeder, wm, cm, worker)

    latch.count_down()
    log.info("Waiting for other workers to join here")
    latch.await()

    do_iterable_point_list(feeder, xp_feeder, False, global_catch_feed, cm, sm, wm, thread_num, travel_time, worker, 3,
                           CatchConditions.grind_condition())

    sm.clear_state()
    cm.evolve_requirement = 90
    log.info("Main grind PHASE 2")
    wm.explain()
    cm.catch_feed = global_catch_feed
    feeder = PositionFeeder(routes_p2[args.route], is_forced_update)
    xp_feeder2 = PositionFeeder(xp_p2[args.route], is_forced_update)
    initial_stuff(feeder, wm, cm, worker)
    do_iterable_point_list(feeder, xp_feeder2, False, global_catch_feed, cm, sm, wm, thread_num, travel_time, worker, 3,
                           CatchConditions.grind_condition())

    if args.final_system_id:
        db_set_system_id(worker.name(), args.final_system_id)
        log.info("Transferred account {} to system-id {}".format(worker.name(), args.final_system_id))

    log.info("Reached end of route with {} spins, going to rest".format(str(len(sm.spun_stops))))


forced_update = create_forced_update_check(args)
nthreads = int(args.thread_count)
log.info("Bot using {} threads".format(str(nthreads)))
latch = CountDownLatch(nthreads)
for i in range(nthreads):
    the_thread = Thread(target=safe_do_work, name="bot-"+str(i),args=(i, global_catch_feed, latch, forced_update))
    num_started += 1
    the_thread.start()
    threads.append(the_thread)
    if args.proxy and i % len(args.proxy) == 0:
        time.sleep(10)

for thread in threads:
    thread.join()


