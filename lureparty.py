import json
from threading import Thread

from queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from apiwrapper import CodenameResult
from argparser import std_config, load_proxies, parse_unicode, add_search_rest, add_webhooks
from geography import *
from gymdbsql import set_args
from lureworker import LureWorker, LureCounter
from scannerutil import install_thread_excepthook, chunks, stop_at_datetime, start_at_datetime

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("pogoservice").setLevel(logging.INFO)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)

'''
Schema changes:
alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
alter table gym add column gymscanner smallint null;
'''
parser = std_config("std_lureparty")
add_search_rest(parser)
add_webhooks(parser)
parser.add_argument('-ps', '--pokestops', default=None, action='append',
                    help='Pokestops to lure')
parser.add_argument('-jlo', '--json-locations', type=parse_unicode,
                    help='Json file with luring descriptions')
parser.add_argument('-ow', '--owner', type=parse_unicode,
                    help='Database owner of lures')
parser.add_argument('-bn', '--base-name', default=None, action='append',
                    help='Base name(s) of accounts for branding')
parser.add_argument('-nl', '--num-lures', default=24,
                    help='Number of lures to place before exiting')
parser.add_argument('-b64', '--base64', default=False,
                    help='Use base64 with number')
parser.add_argument('-stop', '--stop-at', default=None,
                    help='Time of day to stop in 24-hr clock: eg 18:02')

args = parser.parse_args()
args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}
load_proxies(args)
set_args(args)
set_account_db_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

account_manager = AccountManager(args.owner, True, args.accountcsv, (), args, [], [],
                                 Queue(), {})

LURE_COUNT = args.owner + '_lure_count.txt'
if os.path.isfile(LURE_COUNT):
    with open(LURE_COUNT, 'r') as f:
        for line in f:
            lure_count = int(line)
            break
else:
    lure_count = 0
if lure_count > args.num_lures:
    log.info("Target lure count reached, exiting")
    sys.exit(0)

LURE_FILE = 'lure_number.txt'
if os.path.isfile(LURE_FILE):
    with open(LURE_FILE, 'r') as f:
        for line in f:
            idx = int(line)
            break
else:
    idx = 1

lock = Lock()

namecycler = cycle(args.base_name)
use_b64 = args.base64

stop_at = None
if args.stop_at:
    dur_str = "100:00:00"
    h, m = map(int, args.stop_at.split(':'))
    stop_at = datetime.now().replace(hour=h, minute=m)
    msg = "Stopping at {}".format(str(stop_at))
    if stop_at < datetime.now():
        stop_at += timedelta(days=1)
        msg = "Stopping at {} (tomorrow)".format(str(stop_at))
    log.info(msg)


def fix_branding(account):
    global idx
    codename = account.account_info().get("codename", None)
    if codename:
        for baseName in args.base_name:
            if codename.startswith(baseName):
                log.info("Account already branded to {}, not doing anything".format(account["codename"]))
                return

    with lock:
        s = str(idx)
        b64s = s.encode('base64').replace("=", "").rstrip() if use_b64 else s
        branded_name = next(namecycler) + b64s
        idx += 1
        with open(LURE_FILE, "w") as text_file:
            text_file.write(str(idx))
    res = account.do_claim_codename(branded_name)
    result = CodenameResult(res)
    if result.ok():
        log.info("Account branded to {}".format(branded_name))
    else:
        log.info("Account NOT branded to ->{}<-".format(branded_name))
    return account


def deploy_more_lures(lure_dropped):
    global lure_count
    if lure_dropped:
        lure_count += 1
    if lure_count > args.num_lures:
        log.info("Target lure count reached, exiting")
        return False
    if stop_at and datetime.now() > stop_at:
        log.info("Reached stop-at time, exiting")
        return False
    return True


def will_start_now(json_location):
    start = json_location["start"]
    end = json_location["end"]
    start_at = start_at_datetime(start)
    stop_time = stop_at_datetime(start, end)
    now = datetime.now()
    return start_at < now < stop_time

def after_stop(json_location):
    start = json_location["start"]
    end = json_location["end"]
    stop_time = stop_at_datetime(start, end)
    now = datetime.now()
    return now > stop_time


def safe_lure_one_json_worker(json_location, route_section, counter):
    while True:
        start = json_location["start"]
        end = json_location["end"]
        name_ = json_location["name"]
        days = json_location["days"]
        start_at = start_at_datetime(start)
        stop_time = stop_at_datetime(start, end)
        now = datetime.now()

        if not will_start_now(json_location):
            if after_stop(json_location):
                sleep_dur = ((start_at + timedelta(days=1)) - now).total_seconds()
            else:
                sleep_dur = (start_at - now).total_seconds()
            log.info("Sleeping for {}".format(str(sleep_dur)))
            if sleep_dur < 0:
                sleep_dur = abs(sleep_dur)
            log.info("{} outside running period from {} until {}, sleeping {} seconds".format(name_, start_at, stop_time, sleep_dur))
            time.sleep(sleep_dur)

        weekday = str(datetime.today().weekday())
        if weekday not in days:
            tomorrow = datetime.now() + timedelta(days=1)
            tomorrow_morning = tomorrow.replace(hour=0, minute=1)
            seel_dur = (tomorrow_morning - now).total_seconds()
            log.info("Not today, waiting {} seconds until tomorrow".format(seel_dur))
            time.sleep(seel_dur)
        else:
            log.info("{} running until {}".format(name_, stop_time))
            try:
                ld = LureWorker(account_manager, fix_branding, lambda lure_dropped: datetime.now() < stop_time, counter)
                ld.lure_json_worker_positions(route_section)
                time.sleep(60)
            except OutOfAccounts:
                log.warn("No more accounts, exiting")
                return
            except Exception as e:
                log.exception(e)
                time.sleep(12)


threads = []

if args.json_locations:
    with open(args.json_locations) as data_file:
        json_config = json.load(data_file)

        routes = json_config["routes"]
        for json_loc in json_config["schedules"]:
            counter = LureCounter(json_loc)

            route_name = json_loc["route"]
            worker_route = routes[route_name]

            for idx, route in enumerate(chunks(worker_route, 6)):
                name = "Thread-" + json_loc["name"][:14] + "-" + str(idx)
                the_thread = Thread(name=name, target=lambda: safe_lure_one_json_worker(json_loc, route, counter))
                the_thread.start()
                if will_start_now(json_loc):
                    time.sleep(15)
                threads.append(the_thread)

for thread in threads:
    thread.join()
