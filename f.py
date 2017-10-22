from queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, location_parse, parse_unicode, add_search_rest, add_webhooks
from fw import FeedWorker, berry_location
from geography import *
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude, add_gmaps_altitude
from pogom.utils import gmaps_reverse_geolocate
from scannerutil import install_thread_excepthook, stop_at_datetime

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
parser = std_config("generic_feeder")
add_search_rest(parser)
add_webhooks(parser)
parser.add_argument('-llocs', '--lowfeed-locations', type=parse_unicode, default=[],
                    help='Location, can be an address or coordinates.')
parser.add_argument('-locs', '--locations', type=parse_unicode, default=[],
                    help='Location, can be an address or coordinates.')
parser.add_argument('-hlocs', '--heavy-locations', type=parse_unicode, default=[],
                    help='Location, can be an address or coordinates.')
parser.add_argument('-tr', '--trainers', type=parse_unicode,
                    help='Trainers required for feeding.', action='append')
parser.add_argument('-ow', '--system-id', type=parse_unicode,
                    help='Database owner of lures')
parser.add_argument('-hvy', '--heavy-defense', type=parse_unicode,
                    help='heacy defense', default=False)
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

locs = [with_gmaps_altitude(location_parse(x), args.gmaps_key) for x in args.locations.strip().split(' ')]
position = locs[0]
args.player_locale = gmaps_reverse_geolocate(
    args.gmaps_key,
    args.locale,
    str(position[0]) + ', ' + str(position[1]))


account_manager = AccountManager(args.system_id, True, args, [], [], Queue(), {})
account_manager.initialize(args.accountcsv, ())


stop_at = None
if args.stop_at:
    stop_at = stop_at_datetime( args.stop_at)
    msg = "Stopping at {}".format(str(stop_at))
    log.info(msg)


def termination_condition():
    if stop_at and datetime.now() > stop_at:
        log.info("Reached stop-at time, exiting")
        return True
    return False

log.info("Using locations {}".format(str(args.locations)))

threads = []

for loc in locs:
    the_thread = berry_location(loc, FeedWorker(account_manager, termination_condition, args.trainers, True, False))
    time.sleep(10)
    threads.append(the_thread)

if args.heavy_locations:
    heavy_locs = add_gmaps_altitude(args, args.heavy_locations)
    for loc in heavy_locs:
        the_thread = berry_location(loc, FeedWorker(account_manager, termination_condition, args.trainers, True, True))
        time.sleep(120)
        threads.append(the_thread)

if args.lowfeed_locations:
    for loc in add_gmaps_altitude(args, args.lowfeed_locations):
        the_thread = berry_location(loc, FeedWorker(account_manager, termination_condition, args.trainers, False, True))
        time.sleep(10)
        threads.append(the_thread)

for thread in threads:
    thread.join()


