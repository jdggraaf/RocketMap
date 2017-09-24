import os
import sys

from queue import Queue, PriorityQueue

from accountdbsql import set_account_db_args
from accounts import AccountManager
from behaviours import beh_safe_scanner_bot
from workers import WorkerManager
from argparser import std_config,load_proxies,location,add_geofence
from geofence import filter_for_geofence
from gymdbsql import gymscannercoordinates,set_args, most_recent_trainer_gyms
from scannerutil import *
from itertools import cycle

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)


'''
Schema changes:
alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
'''

parser = std_config("gymwatcher")
add_geofence(parser)
parser.add_argument('-c', '--crooks',
                    help='Crooks',
                    action='append', default=[])

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
worker_manager = WorkerManager(account_manager, 9, 9)

seen_gyms = {}

running = True

def find_top_n(gyms, all_scanned_gyms, n):
    result = {}
    for gym in gyms:
        id_ = gym["gym_id"]
        if id_ not in all_scanned_gyms:
            result[id_] = gym
            all_scanned_gyms[id_] = gym
        if len(result) > n:
            break
    return result

all_scanned_gyms = {}
for crook in args.crooks:
    crook_gyms = most_recent_trainer_gyms(crook)
    gym_map = find_top_n(crook_gyms, all_scanned_gyms, 30)
    #gym_map = filter_for_geofence(gym_map, args.geofence, args.fencename)
    print "There are {} gyms in scan with fence {}".format(str(len(gym_map)), str(args.fencename))
    scanners = []
    threads = []
    for idx, stream in gym_map.iteritems():
        scanner = worker_manager.get_worker()
        scanners.append(scanner)
        thread = Thread(target=beh_safe_scanner_bot, args=(scanner, cycle([stream])))
        threads.append(thread)
        thread.start()
        time.sleep(2)

for thread in threads:
    thread.join()

for scanner in scanners:
    worker_manager.free_worker(scanner)

log.info("exiting scanner")
sys.exit()
