import json
from threading import Thread

from flask import Flask, request
from flask import abort
from queue import Queue

from accountdbsql import set_account_db_args, load_accounts_for_lures
from accounts import *
from apiwrapper import CodenameResult
from argparser import std_config, parse_unicode, add_search_rest, add_webhooks, location_parse, \
    add_geofence, setup_proxies
from geofence import group_by_geofence
from geography import *
from gymdbsql import set_args, pokestops
from luredbsql import set_lure_db_args, lures, db_move_to_levelup, db_move_to_trash
from lureworker import LureWorker, FileLureCounter, DbLureCounter
from pogom.apiRequests import set_goman_hash_endpoint
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.proxy import check_proxies
from scannerutil import install_thread_excepthook, chunks, stop_at_datetime, start_at_datetime, is_blank, setup_logging

'''
Schema changes:
alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
alter table gym add column gymscanner smallint null;
'''
parser = std_config("std_lureparty")
add_search_rest(parser)
add_webhooks(parser)
add_geofence(parser)
parser.add_argument('-ps', '--pokestops', default=None, action='append',
                    help='Pokestops to lure')
parser.add_argument('-jlo', '--json-locations', type=parse_unicode,
                    help='Json file with luring descriptions')
parser.add_argument('-rl', '--route-length', default=5,
                    help='Length of the luring routes to use')
parser.add_argument('-ow', '--system-id', type=parse_unicode,
                    help='Database owner of lures')
parser.add_argument('-bn', '--base-name', default=None, action='append',
                    help='Base name(s) of accounts for branding')
parser.add_argument('-nl', '--num-lures', default=24,
                    help='Number of lures to place before exiting')
parser.add_argument('-lurdur', '--lure-duration', default=30,
                    help='The number of minutes lures last')
parser.add_argument('-b64', '--base64', default=False,
                    help='Use base64 with number')
parser.add_argument('-stop', '--stop-at', default=None,
                    help='Time of day to stop in 24-hr clock: eg 18:02')
parser.add_argument('-host', '--host', default="127.0.0.1",
                    help='port for lure dump server')
parser.add_argument('-p', '--port', default=None,
                    help='port for lure dump server')


app = Flask(__name__, static_url_path='')


args = parser.parse_args()
args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

setup_logging(args.system_id)
setup_proxies(args)
if args.overflow_hash_key:
    set_goman_hash_endpoint(args.overflow_hash_key)

set_args(args)
set_account_db_args(args)
set_lure_db_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

db_move_to_levelup(args.system_id, "forlevelup")
db_move_to_trash(args.system_id, "trash")
account_manager = AccountManager(args.system_id, True, args, [], [], Queue(), {})
account_manager.loader = load_accounts_for_lures
account_manager.initialize(args.accountcsv, ())
account_manager.remove_accounts_without_lures()

LURE_COUNT = args.system_id + '_lure_count.txt'
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

log.info("Branding sequence number is {}".format(str(idx)))

lock = Lock()


namecycler = None
if args.base_name:
    namecycler = cycle(args.base_name)
else:
    log.warn("No branding configured")

use_b64 = args.base64

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


def fix_branding(worker):
    global idx
    info = worker.account_info()
    codename = info.get("codename", None)
    if info["remaining_codename_claims"] == 0:
        log.info("Account has no more name changes, existing trainer name is {}".format(codename))
        return worker

    if not namecycler:
        return worker

    if codename:
        for baseName in args.base_name:
            if codename.startswith(baseName):
                log.info("Account already branded to {}, not doing anything".format(worker["codename"]))
                return worker

    with lock:
        s = str(idx)
        b64s = s.encode('base64').replace("=", "").rstrip() if use_b64 else s
        branded_name = next(namecycler) + b64s
        idx += 1
        with open(LURE_FILE, "w") as text_file:
            text_file.write(str(idx))
    res = worker.do_claim_codename(branded_name)
    result = CodenameResult(res)
    if result.ok():
        log.info("Account branded to {}".format(branded_name))
    else:
        log.info("Account NOT branded to ->{}<-".format(branded_name))
    return worker


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
        if is_blank(days):
            log.info("No days sceheduled for {}, terminating thread".format(name_))
            return
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
                ld = LureWorker(account_manager, fix_branding, lambda lure_dropped: datetime.now() < stop_time, counter,  args.lure_duration)
                as_coordinates = [location_parse(x) for x in route_section]
                ld.lure_json_worker_positions(as_coordinates)
                time.sleep(60)
            except OutOfAccounts:
                log.warn("No more accounts, exiting")
                return
            except Exception as e:
                log.exception(e)
                time.sleep(12)


@app.route('/lurebomb/<user>/', methods=['GET'])
def index(user):
    return app.send_static_file("html/lureparty.html")


@app.route('/lurebomb/<user>/lurebomb', methods=['POST'])
def release_accounts(user):
    projectpath = request.form['Position1']
    return lure_bomb_get( user, projectpath, 120)


@app.route('/lures/<user>/<position>/<minutes>', methods=['GET'])
def lure_bomb_get(user, position, minutes, radius=50):
    parsed = location_parse(position)
    pos = with_gmaps_altitude(parsed, args.gmaps_key)
    log.info("Received luring request for {} at {} for {} minutes".format(user, str(pos), str(minutes)))

    lures1 = lures(user)
    if len(lures1) == 0:
        abort(404)
    if pos is None:
        return "Missing coordinates for luring. Ensure page has location access and use a proper browser (safari/chromet etc, not the facebook browser)"
    max_lures = lures1[0]["max_lures"]
    current_lures = lures1[0].get("lures", 0)
    remaining_lures = max_lures - current_lures
    if max_lures <= current_lures:
        return "All {} lures are spent".format(lures1.max_lures)
    ld = LureWorker(account_manager, fix_branding, should_continue(int(minutes)), DbLureCounter(user), args.lure_duration)
    a_thread = Thread(target=lambda: ld.lure_bomb(pos, radius))
    a_thread.start()
    db_move_to_levelup(args.system_id, "forlevelup")
    db_move_to_trash(args.system_id, "trash")

    return "<h2>Luring at {}, be a little patitent. You have {} lures left</h2>".format(str(pos), str(remaining_lures))

def should_continue(minutes_to_run=120):
    end_at = datetime.now() + timedelta(minutes=minutes_to_run)

    def cont(lure_dropped):
        return datetime.now() < end_at
    return cont

def run_server():
    app.run(threaded=True, host=args.host, port=args.port)


threads = []

if args.port:
    the_thread = Thread(name="LureServer", target=run_server)
    the_thread.start()

if args.geofence:
    geofence_stops = group_by_geofence(pokestops(), args.geofence, args.fencename)
else:
    geofence_stops = defaultdict(list)

num_proxies = len(args.proxy) if args.proxy else 1
if args.json_locations:
    log.info("Geofences are: {}".format(str(geofence_stops.keys())))
    with open(args.json_locations) as data_file:
        try:
            json_config = json.load(data_file)
        except ValueError:
            log.error("Failed to load JSON, malformed file. Use an online JSON validator to check it")
            raise

        routes = json_config["routes"]
        for json_loc in json_config["schedules"]:
            counter = FileLureCounter(json_loc)

            worker_idx = 0
            route_names = json_loc["route"]
            for route_name in route_names.split(","):
                if route_name in geofence_stops:
                    worker_route = geofence_stops[route_name]
                else:
                    worker_route = routes[route_name]
                for route in chunks(worker_route, int(args.route_length)):
                    name = json_loc["name"][:14] + "-" + str(worker_idx)
                    worker_idx += 1
                    the_thread = Thread(name=name, target=lambda: safe_lure_one_json_worker(json_loc, route, counter))
                    the_thread.start()
                    if will_start_now(json_loc) and (not args.overflow_hash_key or worker_idx % num_proxies == 0):
                        time.sleep(15)

                    threads.append(the_thread)


for thread in threads:
    thread.join()
