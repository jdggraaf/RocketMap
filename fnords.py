import inspect
from random import random
from threading import Thread, Event

from queue import Queue

import pokemonhandler
from accountdbsql import db_set_behaviour
from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_webhooks
from behaviours import beh_handle_level_up, \
    determine_behaviour, is_pokestop, is_aggressive_pokestop, is_aggressive_pokemon, beh_random_bag_cleaning, \
    discard_random_pokemon, \
    beh_spin_nearby_pokestops
from catchbot import CatchBot
from geofence import get_geofences
from geography import *
from getmapobjects import catchable_pokemon_from_cell, nearby_pokemon_from_cell, catchable_pokemon, \
    nearest_pokstop, can_not_be_seen, cells_with_pokemon_data, raid_gyms
from gymdb import update_missing_s2_ids, cell_spawnpoints, update_missing_altitudes
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.transform import jitter_location
from scannerutil import install_thread_excepthook, install_forced_update_check
from spawnpoint import SpawnPoints
from workers import wrap_account

'''
Shortlist:

create table spawnpoints2 (
    id varchar(50) not null primary key,
    s2cell BIGINT not null
    altitude float null
)

alter table spawnpoint add column s2cell BIGINT null;
alter table spawnpoints2 add column altitude float null;

altitude
spawn point determination
check timings everywhere
'''
logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)
'''
Shortlist todo:
kontorotasjon !!
Identify spawnpoints
next-list:
gyms

'''

print inspect.getfile(inspect.currentframe())  # script filename (usually with path)
print os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # script directory


cannot_be_seen_when_shadowbanned = can_not_be_seen()
runner_blinds = {}
# pprint(data)

parser = std_config("fnord")
parser.add_argument('-system-id', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-uad', '--using-account-db',
                    help='Indicates if the application wil enter accounts into account database',
                    default=True)
parser.add_argument('-s2g', '--s2-hook', default=None,
                    help='s2 discord hook')
parser.add_argument('-asi', '--account-search-interval', type=int,
                    default=3600,
                    help=('Seconds for accounts to search before ' +
                          'switching to a new account. 0 to disable.'))
parser.add_argument('-ari', '--account-rest-interval', type=int,
                    default=550,
                    help=('Seconds for accounts to rest when they fail ' +
                          'or are switched out.'))
parser.add_argument('-sd', '--step-distance', type=float,
                    default=550.0,
                    help=('Seconds for accounts to rest when they fail ' +
                          'or are switched out.'))
parser.add_argument('-rac', '--runner-accountcsv',
                    help=('Load accounts from CSV file containing ' +
                          '"auth_service,username,passwd" lines.'))

add_webhooks(parser)
add_geofence(parser)

args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)

pokemonhandler.set_args(args)

install_thread_excepthook()

update_missing_s2_ids()
update_missing_altitudes(args.gmaps_key)

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

threads = []

account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {})
account_manager.initialize(args.accountcsv, ())

pokemon_queue = Queue()

if args.proxy:
    nthreads = len(args.proxy) * 2
else:
    nthreads = 4

log.info("Bot using {} threads".format(nthreads))


def create_fnord(pos, worker_number, forced_update):
    account = account_manager.get_account(False)
    worker = wrap_account(account, account_manager)
    with_alt = with_gmaps_altitude(pos, args.gmaps_key)
    the_thread = Thread(target=safe_do_work, args=(worker, with_alt, worker_number, args.account_search_interval,
                                                   args.account_rest_interval, forced_update))
    the_thread.start()
    threads.append(the_thread)
    time.sleep(2)
    return pos is not None


candy_rares = {131, 147, 148, 246, 247}
real_rares = {113, 114, 143, 149, 201, 242, 248}


def is_candy_rare(pkmn):
    id_ = pkmn['pokemon_id']
    return id_ in candy_rares


def is_rare(pkmn):
    id_ = pkmn['pokemon_id']
    return id_ in real_rares


lock = Lock()
runner_lock = Lock()
being_run_for = set()

def time_of_next_pokestop_spin(behaviour):
    if is_aggressive_pokestop(behaviour):
        return datetime.now() + timedelta(minutes=(10 + random() * 20))
    else:
        return datetime.now() + timedelta(minutes=(20 + random() * 20))


def time_of_next_pokemon(behaviour):
    if is_aggressive_pokemon(behaviour):
        return datetime.now() + timedelta(minutes=(30 + random() * 10))
    else:
        return datetime.now() + timedelta(minutes=(60 + random() * 10))


def safe_do_work(worker, suggested_pos, worker_number, account_search_interval, account_rest_interval, forced_update):
    map_objects = worker.do_get_map_objects(suggested_pos)
    distance, pokestop = nearest_pokstop(map_objects, suggested_pos)
    if distance < 34:
        pos = suggested_pos
    elif distance < 100:
        pos = jitter_location((pokestop["latitude"], pokestop["longitude"]), 20)
    else:
        pos = suggested_pos

    if not worker.account_info().behaviour:
        worker.account_info().behaviour = determine_behaviour(pos, map_objects, worker_number)
        db_set_behaviour(worker.account_info().username, worker.account_info().behaviour)

    cells = worker.process_map_objects(map_objects)
    spawn_points = {}
    for cell in cells:
        spawn_points[cell["s2_cell_id"]] = cell_spawnpoints(cell["s2_cell_id"])

    discard_random_pokemon(worker)

    while not forced_update.isSet():
        # noinspection PyBroadException
        try:
            do_work(worker, pos, worker_number, map_objects, spawn_points, forced_update)
        except:
            logging.exception("Outer worker catch block caught exception")
        time.sleep(60)


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random() * 2)
    time.sleep(random_)

from datetime import datetime, timedelta

def do_work(worker, pos, worker_number, initial_map_objects, spawn_points, is_forced_update):
    next_ps = datetime.now() + timedelta(minutes=5, seconds=(10 * worker_number))  # always 5 minutes t be clear
    next_mon = datetime.now() + timedelta(seconds=(10 * worker_number))
    level = worker.account_info()["level"]

    prev_map_objects = datetime.now()
    encountered = set()
    rares = set()
    seen_encounters = set()
    numscans = 0
    seen_raids = set()
    gym_last_modifieds = {}
    inrange_pokestops = {}
    while not is_forced_update.isSet():
        map_objects = worker.do_get_map_objects(pos)
        this_map_objects = datetime.now()

        if not worker.account_info().behaviour:
            worker.account_info().behaviour = determine_behaviour(pos, map_objects, worker_number)
            db_set_behaviour(worker.account_info().username, worker.account_info().behaviour)

        level = beh_handle_level_up(worker, level, map_objects)

        cells = worker.process_map_objects(map_objects)
        for cell in cells:
            cell_id = cell["s2_cell_id"]
            cell_spawn_points = spawn_points.get(cell_id, SpawnPoints([]))
            process_collection(nearby_pokemon_from_cell(cell), cell_spawn_points, prev_map_objects, rares,
                               this_map_objects, cell_id, seen_encounters, numscans == 0)
            process_collection(catchable_pokemon_from_cell(cell), cell_spawn_points, prev_map_objects, rares,
                               this_map_objects, cell_id, seen_encounters, numscans == 0)


        behaviour = worker.account_info().behaviour
        if is_pokestop(behaviour) and datetime.now() > next_ps:
            beh_spin_nearby_pokestops(worker, map_objects, pos)
            next_ps = time_of_next_pokestop_spin(behaviour)
            beh_random_bag_cleaning(worker, ITEM_LIMITS)

        '''
        if is_pokemon(behaviour) and datetime.now() > next_mon:
            beh_catch_all_nearby_pokemon(worker, pos, map_objects, encountered)
            # discard_random_pokemon(worker, map_objects)
            next_mon = time_of_next_pokemon(behaviour)
        '''

        prev_map_objects = this_map_objects + timedelta(seconds=-20)
        numscans += 1

        gyms = raid_gyms(map_objects,pos)

        for gym in gyms:
            lat = gym["latitude"]
            lng = gym["longitude"]
            raid_info = gym["raid_info"]
            raid_seed = raid_info["raid_seed"]
            if "raid_pokemon" in raid_info:
                pokemon = raid_info["raid_pokemon"]["pokemon_id"]
            else:
                pokemon = 0
            when = datetime.utcfromtimestamp(raid_info["raid_battle_ms"]/1000)
            cet_when = when + timedelta(hours=2)
            level = raid_info["raid_level"]

            if raid_seed not in seen_raids and level > 2:
                google = "https://maps.google.com/?q={},{}".format(lat, lng)
                msg = "Level {} raid at {} {}".format(str(level), str(cet_when), google)
                print (msg)
                pokemonhandler.s2msg(msg)
                seen_raids.add(raid_seed)



        '''
        for gym in inrange_gyms(map_objects, pos):
            gym_id = gym["id"]
            last_modified = gym["last_modified_timestamp_ms"]
            if gym_last_modifieds.get(gym_id, None) != last_modified:
                gym_last_modifieds[gym_id] = last_modified
                beh_do_process_single_gmo_gym(worker, gym, pos)
        '''
        rnd_sleep(60*5)


def is_seeing_blinds(map_objects):
    for cell in cells_with_pokemon_data(map_objects):
        for pkmn in nearby_pokemon_from_cell(cell):
            if pkmn["pokemon_id"] in cannot_be_seen_when_shadowbanned:
                return True
        for pkmn in catchable_pokemon_from_cell(cell):
            if pkmn["pokemon_id"] in cannot_be_seen_when_shadowbanned:
                return True
    return False


def run_for_spawnpoints(runner, encounter_id, runner_spawnpoints):
    for idx, spawnpoint in enumerate(runner_spawnpoints):
        objects = runner.do_get_map_objects(spawnpoint.location())
        if not is_seeing_blinds( objects):
            if not runner.name() in runner_blinds:
                runner_blinds[runner.name()] = 1
            else:
                runner_blinds[runner.name()] += 1
        else:
            runner_blinds[runner.name()] = 0

        if runner_blinds[runner.name()] > 20:
            log.warning("Runner {} has not seen any blinding pokemons for last 20 scans, probably blind".format(runner.name()))

        for pkmn in catchable_pokemon(objects):  # todo: Consider other useful info the runner is picking up too
            log.info("{}={} and {}={}".format(type(encounter_id),encounter_id,type(pkmn["encounter_id"]),pkmn["encounter_id"]))
            if encounter_id == pkmn["encounter_id"]:
                log.info("Runner found after {} attempts (of {}): {}".format(idx, len(runner_spawnpoints), pkmn))
                return pkmn


def process_collection(pokemons, cell_spawn_points, prev_map_objects, rares, this_map_objects, cell_id,
                       seen_encounters, first_scan):
    seen_blinds = False
    for pkmn in pokemons:
        encounter_id = pkmn["encounter_id"]
        pokemon_id = pkmn["pokemon_id"]
        if pokemon_id in cannot_be_seen_when_shadowbanned:
            seen_blinds = True
        if encounter_id not in seen_encounters:
            running = False
            pkmn["s2_cell_id"] = cell_id
            if "spawn_point_id" in pkmn:
                id_ = pkmn["spawn_point_id"]
                point = cell_spawn_points.spawn_point(id_)
                if point:
                    pkmn["disappear_time"] = point.expires_at().timetuple()
                else:
                    log.debug("Spawn point {} not in db".format(pkmn["spawn_point_id"]))  # todo warning
            elif not first_scan:
                possible_spawn_points = cell_spawn_points.points_that_can_spawn(prev_map_objects, this_map_objects)
                if len(possible_spawn_points) == 1:
                    point = possible_spawn_points[0]
                    pkmn["longitude"] = point.longitude
                    pkmn["disappear_time"] = point.expires_at().timetuple()
                elif False and is_worth_running_for(pkmn):
                    if len(possible_spawn_points) > 1:
                        runner_pos = possible_spawn_points
                    else:
                        runner_pos = cell_spawn_points.search_points_for_runner(prev_map_objects, this_map_objects)
                        explain = cell_spawn_points.explain(pokemon_id, prev_map_objects, this_map_objects)
                        log.info(
                            "Spawnpoint search for {} gave {} possible spawn points, {}".format(encounter_id, len(runner_pos), explain))
                        if len(runner_pos) == 0:
                            log.info(explain)

            if not first_scan and not running and pokemon_id not in args.webhook_blacklist:
                pokemon_queue.put(pkmn)

            seen_encounters.add(encounter_id)
    return seen_blinds


def try_acquire_run_permission(encounter_id):
    with runner_lock:
        if not encounter_id in being_run_for:
            being_run_for.add(encounter_id)
            return True
    return False


def is_worth_running_for(pkmn):
    return is_snipe_target(pkmn) or is_good_shit(pkmn)

def is_snipe_target(pkmn):
    return pkmn["pokemon_id"] == 64 or pkmn["pokemon_id"] == 63

def is_good_shit(pkmn):
    return pkmn["pokemon_id"] == 64


# noinspection PyBroadException
def queue_worker():
    rares = set()
    catchbot = CatchBot(args, args.system_id + "_lvlr", "levelers.txt")
    catchbot.start_threads(nthreads)

    while True:
        pkmn = pokemon_queue.get()
        try:
            encounter_id = pkmn["encounter_id"]
            cell_id = pkmn["s2_cell_id"]
            if "disappear_time" not in pkmn:
                now = datetime.utcnow()
                hardcoded_disappear = now + timedelta(hours=4, minutes=20)
                pkmn["disappear_time"] = hardcoded_disappear.timetuple()

            is_s2 = "latitude" not in pkmn
            if is_rare(pkmn) and encounter_id not in rares:
                if is_s2:
                    log.info("Rare pokemon found with s2 location {}".format(str(pkmn)))
                    pokemonhandler.pms2(pkmn["pokemon_id"], cell_id)
                else:
                    log.info("Rare: {}".format(str(pkmn)))
                rares.add(encounter_id)
            pokemonhandler.send_to_webhook(pkmn)
            spawnpoint_id = pkmn.get("spawn_point_id", None)
            if spawnpoint_id and is_snipe_target(pkmn):
                pos_with_alt = with_gmaps_altitude((pkmn["latitude"], pkmn["longitude"]), args.gmaps_key)
                log.info("Snipe target {} found at {}".format(str(pkmn["pokemon_id"]), str(pos_with_alt)))
                catchbot.give_spawn(encounter_id, spawnpoint_id, pos_with_alt)
        except Exception:
            log.exception("Something broke in queue worker for {}".format(str(pkmn)))
        finally:
            pokemon_queue.task_done()

fences = get_geofences(args.geofence, args.fencename)
box = fences.box()
moves = fnords_box_moves_generator(box[0], box[1], args.step_distance)
movesToUse = []
log.info("Filtering for fences")
for move in moves:
    if fences.within_fences(move[0], move[1]):
        movesToUse.append(move)

#movesToUse.append((59.909323, 10.723079))  # ab fineart
#movesToUse.append((59.908709, 10.721656))  # ab BAR
#movesToUse.append((59.908298, 10.722281))  # ab olav selvaags plass
#movesToUse.append((59.907183, 10.722128))  # ab olav selvaags plass
#movesToUse.append((59.906428, 10.721344))  # tjuvholmen skulpturpark
#movesToUse.append((59.907542, 10.721157))  # the thief
#movesToUse.append((59.909974, 10.727908))  # lighthouse

#movesToUse.append((59.9150325314212,10.6599511879855))  # apswn
#movesToUse.append((59.8984676019913,10.7305756381957))  # spawn
#movesToUse.append((59.9148840802383,10.6695963519783))  # spaw
#movesToUse.append((59.87281427624,10.8463705329275))  # spaw
#movesToUse.append((59.9488288755032,10.6756637150508))  # spaw

total_steps = len(movesToUse)
log.info("Fence box is {}, total fnords is {}".format(str(box), total_steps))

i = 0

print "Guardposts"
for m in movesToUse:
    print str(m[0]) + "," + str(m[1])


forced_update = Event()
install_forced_update_check(args, forced_update)
for move in movesToUse:
    create_fnord(move, i, forced_update)
    i += 1
print "Created {} fnords".format(str(i))

for i in range(1):
    t = Thread(target=queue_worker)
    t.daemon = True
    t.start()

for thread in threads:
    thread.join()

print("Done scanning for all scanners")

pokemon_queue.join()
