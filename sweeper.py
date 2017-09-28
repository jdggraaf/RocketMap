from threading import Thread

from queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_geofence, add_search_rest
from behaviours import beh_process_single_gmo_gym_no_dups
from geofence import get_geofences
from geography import *
from getmapobjects import parse_gyms
from gymdbsql import set_args
from management_errors import GaveUp
from scannerutil import install_thread_excepthook
from workers import wrap_account

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)
logging.getLogger("gymsweeper").setLevel(logging.INFO)

'''
Schema changes:
alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
alter table gym add column gymscanner smallint null;
'''
parser = std_config("gymscanner")
add_search_rest(parser)
add_geofence(parser)
parser.set_defaults(DEBUG=False)
args = parser.parse_args()

load_proxies(args)
set_args(args)
set_account_db_args(args)


install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

CONST_NUMSCANNERS = args.sweep_workers

account_manager = AccountManager("gymsweeper", False, args, [], [], Queue(), {})
account_manager.initialize(args.accountcsv, ())

threads = []


def create_scanner_acct(allsteps, count):
    steps = []
    step = next(allsteps, None)
    while step is not None and len(steps) < count:
        steps.append(step)
        step = next(allsteps, None)

    account = account_manager.get_account()
    worker = wrap_account(account, account_manager)
    the_thread = Thread(target=safe_do_work, args=(worker, iter(steps)))
    the_thread.start()
    threads.append(the_thread)
    time.sleep(2)
    return step is not None


def safe_do_work(worker, moves_gen):
    # noinspection PyBroadException
    try:
        do_work(worker, moves_gen)
    except:
        logging.exception("Outer worker catch block caught exception")
    logging.info("Worker complete")


def do_work(worker, moves_gen):
    seen_gyms = set()

    for position in moves_gen:
        try:
            map_objects = worker.do_get_map_objects(position)
        except GaveUp:
            log.warn("Gave up getting map objects at " + str(position))
            continue

        gyms = []
        try:
            if map_objects is None:  # can this ever happen ??
                log.warning(
                    "Did not get any map objects at {}, moving on".format(str(map_objects)))
            else:
                gyms = parse_gyms(map_objects)
        except StopIteration:
            log.warn("Iteration over forts failed " + str(map_objects))  # can this ever happen ?
            pass
        for gym in gyms:
            beh_process_single_gmo_gym_no_dups(worker, seen_gyms, gym, position)


fences = get_geofences(args.geofence, args.fencename)
box = fences.box()
moves = box_moves_generator(box[0], box[1])
movesToUse = []
log.info("Filtering for fences")
for move in moves:
    if fences.within_fences(move[0], move[1]):
        movesToUse.append(move)

total_steps = len(movesToUse)
steps_per_scanner = total_steps / CONST_NUMSCANNERS  # todo maybe use time-based target metric instead
log.info("Fence box is {}".format(str(box)))
log.info("Steps per scanner account is {}".format(steps_per_scanner))

i = 0
moveGen = iter(movesToUse)
while create_scanner_acct(moveGen, steps_per_scanner):
    log.info("Created scanner {}".format(str(i)))
    i += 1

for thread in threads:
    thread.join()
print("Done scanning for all scanners")
