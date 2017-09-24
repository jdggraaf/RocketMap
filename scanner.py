import os
import sys

from queue import Queue, PriorityQueue

from accountdbsql import set_account_db_args
from accounts import AccountManager
from argparser import std_config, load_proxies, location, add_geofence, add_search_rest
from behaviours import beh_safe_do_gym_scan
from geofence import filter_for_geofence
from geography import gym_moves_generator, step_position
from gymdbsql import gymscannercoordinates, set_args
from scannerutil import *
from workers import WorkerManager

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)

'''
Schema changes:
alter table pokemon add column cp_multiplier float null;

alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
'''

parser = std_config("gymwatcher")
add_geofence(parser)
add_search_rest(parser)
parser.add_argument('-r', '--radius',
                    help='Radius in meters from location',
                    type=int, default=None)
parser.add_argument('-len', '--length',
                    help='length',
                    type=int, default=40000)
parser.set_defaults(DEBUG=False)
args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)


install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

fail_on_forced_update(args)

queue = PriorityQueue()
dbqueue = Queue()

account_manager = AccountManager("gymwatcher", False, args.accountcsv, (), args, [], [],
                                 Queue(), {})
worker_manager = WorkerManager(account_manager, 25, 9)

seen_gyms = {}

running = True

MAX_LEN = args.length


def find_closest(current_list, first):
    shortest_distance = 10000000
    shortest_idx = -1
    coordinates_ = first["coordinates"]
    max_longitude = 1000
    for idx, gym in enumerate(current_list):
        if gym["longitude"] > max_longitude:
            break
        current_distance = vincenty(coordinates_, gym["coordinates"]).m
        if current_distance < shortest_distance:
            shortest_distance = current_distance
            shortest_idx = idx
            max_longitude = step_position(
                gym["coordinates"], 0, current_distance)[1]
    closes = gym_map[shortest_idx]
    del gym_map[shortest_idx]
    return closes


def length_of_route(current_route):
    length = 0
    prev_gym = None
    for gym in current_route:
        if prev_gym is not None:
            length += vincenty(prev_gym, gym["coordinates"]).m
        prev_gym = gym["coordinates"]
    return length


gym_map = gymscannercoordinates()
gym_map = filter_for_geofence(gym_map, args.geofence, args.fencename)
log.info("There are {} gyms in scan with fence {}".format(str(len(gym_map)), str(args.fencename)))
streams = []

initialPosition = location(args)
if args.radius is not None:
    filtered = [x for x in gym_map if vincenty(initialPosition, x["coordinates"]).m < args.radius]
    gym_map = filtered

while len(gym_map) > 0:
    prev = gym_map[0]
    stream = [prev]
    del gym_map[0]
    distance = 0
    while len(gym_map) > 0:
        next_gym = find_closest(gym_map, prev)
        distance += vincenty(prev["coordinates"], next_gym["coordinates"]).m
        if distance > MAX_LEN:
            streams.append(stream)
            log.info("Created stream " + str(len(streams)) + ", with " + str(
                len(stream)) + " gyms, length " + str(
                int(length_of_route(stream))) + " meters")
            stream = []
            distance = 0
        stream.append(next_gym)
        distance += 250  # add 250 m per gym
        prev = next_gym
    log.info("Created stream " + str(len(streams)) + ", with " + str(
        len(stream)) + " gyms, length " + str(
        int(length_of_route(stream))) + " meters")
    streams.append(stream)

scanners = []
threads = []
for stream in streams:
    scanner = worker_manager.get_worker()
    scanners.append(scanner)
    thread = Thread(target=beh_safe_do_gym_scan, args=(scanner, gym_moves_generator(stream)))
    threads.append(thread)
    thread.start()
    time.sleep(2)

for thread in threads:
    thread.join()

for scanner in scanners:
    worker_manager.free_worker(scanner)

log.info("exiting scanner")
sys.exit()
