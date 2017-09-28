import random
from threading import Thread

from queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies
from geography import *
from getmapobjects import inrange_pokstops
from gymdbsql import set_args
from scannerutil import install_thread_excepthook
from workers import wrap_account

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
alter table gym add column gymscanner smallint null;
'''
parser = std_config("gymscanner")
args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)


install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

account_manager = AccountManager("tutorialCompleter", False, args, [], [], Queue(), {})
account_manager.initialize(args.accountcsv, ())

pos = (59.926148, 10.703277)


def do_one():
    while True:
        account = account_manager.get_account()
        worker = wrap_account(account, account_manager)
        map_objects = worker.do_get_map_objects(pos)
        pokestops = inrange_pokstops(map_objects, pos)
        for pokestop in pokestops:
            worker.do_pokestop_details(pokestop)
            time.sleep(random.uniform(0.8, 1.8))  # Do not let Niantic throttle
            worker.do_spin_pokestop(pokestop, pos)
            break

threads = []
for i in range(0, 14):
    thread = Thread(target=do_one)
    threads.append(thread)
    thread.start()

for thread in threads:
    thread.join()
