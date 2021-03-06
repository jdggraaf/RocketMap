import inspect
from random import random
from threading import Thread, Event

from queue import Queue

import pokemonhandler
from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_webhooks, location
from behaviours import beh_handle_level_up, \
    determine_behaviour, is_pokestop, is_aggressive_pokestop, is_aggressive_pokemon, beh_random_bag_cleaning, \
    beh_spin_nearby_pokestops, L20_ITEM_LIMITS
from geofence import get_geofences
from geography import *
from getmapobjects import nearest_pokstop, \
    can_not_be_seen, cells_with_pokemon_data, inrange_gyms, raid_gyms, pokemons
from gymdb import gym_map
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.transform import jitter_location
from pogom.utils import gmaps_reverse_geolocate
from scannerutil import install_thread_excepthook, create_forced_update_check
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

print(inspect.getfile(inspect.currentframe()))  # script filename (usually with path)
print(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))  # script directory

location_fences = get_geofences("location_fences.txt", None)

cannot_be_seen_when_shadowbanned = can_not_be_seen()
runner_blinds = {}
# pprint(data)

parser = std_config("raidwatch")
parser.add_argument('-system-id', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-uad', '--using-account-db',
                    help='Indicates if the application wil enter accounts into account database',
                    default=True)
parser.add_argument('-s2g', '--s2-hook', default=None,
                    help='s2 discord hook')
parser.add_argument('-asi', '--account-search-interval', type=int,
                    default=86400*2,
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
parser.add_argument('-rac', '--lures-accountcsv',
                    help=('Load accounts from CSV file containing ' +
                          '"auth_service,username,passwd" lines.'))
parser.add_argument('-lo', '--lures-owner',
                    help=('Db owner of the lures accounts "auth_service,username,passwd" lines.'))
parser.add_argument('-stop', '--stop-at', default=None,
                    help='Time of day to stop in 24-hr clock: eg 18:02')

add_webhooks(parser)
add_geofence(parser)

args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)

pokemonhandler.set_args(args)
position = location(args)
args.player_locale = gmaps_reverse_geolocate(
    args.gmaps_key,
    args.locale,
    str(position[0]) + ', ' + str(position[1]))

install_thread_excepthook()

stop_at = None
if args.stop_at:
    dur_str = "100:00:00"
    h, m = list(map(int, args.stop_at.split(':')))
    stop_at = datetime.now().replace(hour=h, minute=m)
    msg = "Stopping at {}".format(str(stop_at))
    if stop_at < datetime.now():
        stop_at += timedelta(days=1)
        msg = "Stopping at {} (tomorrow)".format(str(stop_at))
    log.info(msg)

# update_missing_s2_ids()
# update_missing_altitudes(args.gmaps_key)

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

threads = []

account_manager = AccountManager(args.system_id, True, args, [], [], Queue(), {})
account_manager.initialize(args.accountcsv, ())

pokemon_queue = Queue()

if args.proxy:
    nthreads = len(args.proxy) * 2
else:
    nthreads = 4

log.info("Bot using {} threads".format(nthreads))

fences = get_geofences(args.geofence, args.fencename)

all_gyms = gym_map(fences)


def create_fnord(pos, worker_number, _seen_raid_defender, _forced_update):
    account = account_manager.get_account(False)
    worker = wrap_account(account, account_manager)
    with_alt = with_gmaps_altitude(pos, args.gmaps_key)
    the_thread = Thread(target=safe_do_work, name="fnord" + str(account_manager),
                        args=(worker, with_alt, worker_number, _seen_raid_defender, _forced_update))
    the_thread.start()
    threads.append(the_thread)
    time.sleep(15)
    return pos is not None


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


def safe_do_work(worker, suggested_pos, worker_number, _seen_raid_defender, _forced_update):
    map_objects = worker.do_get_map_objects(suggested_pos)
    distance, pokestop = nearest_pokstop(map_objects, suggested_pos)
    if distance < 34:
        pos = suggested_pos
    elif distance < 100:
        pos = jitter_location((pokestop.latitude, pokestop.longitude), 20)
    else:
        pos = suggested_pos

    if not worker.account_info().behaviour:
        worker.account_info().behaviour = determine_behaviour(pos, map_objects)
        db_set_behaviour(worker.account_info().username, worker.account_info().behaviour)

    while not _forced_update.isSet():
        # noinspection PyBroadException
        try:
            if do_work(worker, pos, worker_number, map_objects, _seen_raid_defender, _forced_update):
                return
        except:
            logging.exception("Outer worker catch block caught exception")
        time.sleep(60)


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random() * 2)
    time.sleep(random_)


def do_work(worker, pos, worker_number, initial_map_objects, seen_raid_defender, is_forced_update):
    next_ps = datetime.now() + timedelta(minutes=5, seconds=(100 * worker_number))  # always 5 minutes t be clear
    level = worker.account_info()["level"]

    numscans = 0
    while not is_forced_update.isSet():
        if stop_at and datetime.now() > stop_at:
            log.info("Reached stop-at time, exiting")
            return True

        map_objects = worker.do_get_map_objects(pos)
        reg_gyms = inrange_gyms(map_objects, pos)
        while len(reg_gyms) == 0:
            log.info("There are no visible gyms at this position {}, replacing account".format(pos))
            time.sleep(5)
            account = account_manager.get_account(False)
            worker = wrap_account(account, account_manager)
            map_objects = worker.do_get_map_objects(pos)
            reg_gyms = inrange_gyms(map_objects, pos)


        if len(reg_gyms) == 0 and len(cells_with_pokemon_data(map_objects)) > 0:
            log.info("There are no visible gyms at this position {}, exiting thread".format(pos))
            return True

        level = beh_handle_level_up(worker, level)

        behaviour = worker.account_info().behaviour
        if is_pokestop(behaviour) and datetime.now() > next_ps:
            beh_spin_nearby_pokestops(worker, map_objects, pos)
            next_ps = time_of_next_pokestop_spin(behaviour)
            beh_random_bag_cleaning(worker, L20_ITEM_LIMITS)

        numscans += 1


        gyms = raid_gyms(map_objects, pos)
        log.info("There are {} gyms, {} raid gyms in view at {}".format(str(len(reg_gyms)), str(len(gyms)), str(pos)))

        next_scan = []
        for gym in gyms:
            lat = gym.latitude
            lng = gym.longitude
            raid_info = gym.raid_info
            raid_seed = raid_info.raid_seed
            gym_id = gym.id
            gym_name = None
            if gym_id in all_gyms:
                gym_name = all_gyms[gym_id]
            if raid_info.HasField("raid_pokemon"):
                pokemon = raid_info.raid_pokemon.pokemon_id
                pokemon_name = pokemons[str(pokemon)]["name"]
            else:
                pokemon = 0
                pokemon_name = None
            when = datetime.utcfromtimestamp(raid_info.raid_battle_ms / 1000)
            cet_when = when + timedelta(hours=2)
            next_scan.append(cet_when + timedelta(seconds=20))
            short_time = cet_when.strftime("%H:%M")
            level = raid_info.raid_level
            fence_name = location_fences.fence_name(lat, lng)
            if not fence_name:
                fence_name = ""

            if pokemon and raid_seed not in seen_raid_defender:
                google = "https://maps.google.com/?q={},{}".format(lat, lng)
                try:
                    _msg = "{} raid{}@{}/{} {} {}".format(pokemon_name, str(level), gym_name, fence_name, short_time,
                                                          google)
                    log.info(_msg)
                except UnicodeEncodeError:
                    _msg = "{} raid{}@{} {} {}".format(pokemon_name, str(level), fence_name, short_time, google)
                    log.warn(_msg)
                if level > 0 or pokemon == 68:
                    pokemonhandler.s2msg(_msg)
                seen_raid_defender.add(raid_seed)

        real_sleep = 30 * 60
        if len(next_scan) > 0:
            actual_sleep_to = min(next_scan)
            actual_sleep_seconds = (actual_sleep_to - datetime.now()).seconds
            real_sleep = min(real_sleep, actual_sleep_seconds)
        log.info("Waiting {} seconds for next thing to happen".format(real_sleep))
        time.sleep(real_sleep)
        rnd_sleep(1)


# box = fences.box()
# moves = fnords_box_moves_generator(box[0], box[1], args.step_distance)
movesToUse = []
moves = [(59.895986,10.812309),(59.927919,10.72951)]
log.info("Filtering for fences")
for move in moves:
    if fences.within_fences(move[0], move[1]):
        movesToUse.append(move)

total_steps = len(movesToUse)
# log.info("Fence box is {}, total fnords is {}".format(str(box), total_steps))

i = 0

print("Guardposts")
for m in movesToUse:
    print(str(m[0]) + "," + str(m[1]))

forced_update = create_forced_update_check(args)
seen_raid_defender = set()

for move in movesToUse:
    create_fnord(move, i, seen_raid_defender, forced_update)
    i += 1
print("Created {} fnords".format(str(i)))

for thread in threads:
    thread.join()

print("Done scanning for all scanners")

pokemon_queue.join()
