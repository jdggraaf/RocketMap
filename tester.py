from pgoapi.protos.pogoprotos.map.fort.fort_data_pb2 import FortData
from queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from argparser import std_config, load_proxies, add_system_id, add_use_account_db, setup_proxies
from behaviours import beh_spin_pokestop, beh_spin_pokestop_raw
from geofence import get_geofences
from geography import *
from getmapobjects import inrange_pokstops, catchable_pokemon_by_distance, catchable_pokemon, \
    inventory_discardable_pokemon, inrange_gyms
from gymdbsql import set_args
from pogoservice import TravelTime
from pokestoproutesv2 import double_xp_1, double_xp_2, routes_p1, xp_p1, initial_130_stops
from gymdbsql import pokestops
from scannerutil import install_thread_excepthook, setup_logging, equi_rect_distance
from workers import wrap_account_no_replace
import s2sphere

setup_logging()
log = logging.getLogger(__name__)


r = s2sphere.RegionCoverer()
p1 = s2sphere.LatLng.from_degrees(33, -122)
p2 = s2sphere.LatLng.from_degrees(33.1, -122.1)
cell_ids = r.get_covering(s2sphere.LatLngRect.from_point_pair(p1, p2))
parser = std_config("gymscanner")
add_system_id(parser)
add_use_account_db(parser)
args = parser.parse_args()
setup_proxies(args)


set_args(args)
set_account_db_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

'''
g0g3m3sh89896:&PB&nQ3YH used 31. oc
g0g3m3sh89912:SaH7QKq@C used 9 nov
g0g3m3sh89863:kt#9r&MnG used 20 nov
g0g3m3sh89910:T!2YAMu2k
g0g3m3sh89905:uYRST9@Bg
g0g3m3sh89919:MKG#6s!E3
g0g3m3sh89890:#MBYk6uAa
g0g3m3sh89864:3&mjdpE^s
'''


start = initial_130_stops.get("hamburg")
x1 = routes_p1.get("hamburg")

print precise_coordinate_string(start[0][0])
print precise_coordinate_string(start[-1][0])
print precise_coordinate_string(x1[-1][0])
x1 = double_xp_1.get("hamburg")
x2 = double_xp_2.get("hamburg")
total = 0
for s in x1:
    total += len(s[1])
for s in x2:
    total += len(s[1])
print "stops"
for s in x1:
    for item in s[1]:
        print '"' + precise_coordinate_string(item) + '",'
account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {}, replace_warned=False)
l5account = account_manager.add_account({"username":"g0g3m3sh89863","password":"kt#9r&MnG","provider":"ptc"})
#l5account = account_manager.add_account({"username":"g0g3m3sh89615","password":"x@WtEnv6n","provider":"ptc"})
#l5account = account_manager.add_account({"username":"g0g3m3sh89912","password":"SaH7QKq@C","provider":"ptc"})
# l5account = account_manager.add_account({"username":"g0g3m3sh89896","password":"&PB&nQ3YH","provider":"ptc"})
worker = wrap_account_no_replace(l5account, account_manager, 25)

stop_1=(((59.908411, 10.7760670), "1707f1339e454eaba3e69ff443729f9d.16"),((59.910564, 10.7613160), "f3650447ae6048599655b034b478d5f7.16"))
stop_2=(((59.907937, 10.7765390), "07ae3ee4757f4049b13811dabed2fdc0.11"),((59.910775, 10.7607610), "d64de98d135d48bd884fa300c6ba53e4.16"))
stop_3=(((59.907934, 10.7759700), "9f9196aa884949bc88bf7fdb32e4ec5f.16"),((59.911297, 10.7577550), "783677808da1472b8ee1ac9ed02fc65d.11"))
stop_4=(((59.904540, 10.7665610), "787da22ba5e54bf094210927b6716b85.16"),((59.909709, 10.7473550), "49b6c17d820d42a7ab43b7b8075da170.16"))
stop_5=(((59.904737, 10.7671730), "3d903c4baf0a46e3a74821168092cf11.16"),((59.908853, 10.7559040), "04fb2625ff6345ba956d5bb12c557940.16"))
stop_6_3km=(((59.907326, 10.7853680), "09abf40d1abf413990a4ff12f81734fb.16"),((59.912380, 10.7312300), "10e4324fc7684c3594b976a4b114d312.16"))

#travel_time = worker.getlayer(TravelTime)

pos = (59.899319, 10.836807, 50)
map_objects = worker.do_get_map_objects(pos)
pokestops = inrange_pokstops(map_objects, pos)
gyms = inrange_gyms(map_objects, pos)

gym = gyms[0]
worker.do_spin_pokestop(gym ,pos)

pokemons = inventory_discardable_pokemon(worker)
worker.do_transfer_pokemon(pokemons)


to_use = stop_5
start = to_use[0]
end = to_use[1]
player_position = start[0]
login = worker.login(player_position)
l5obj = worker.do_get_map_objects(player_position)
stop= inrange_pokstops(l5obj, player_position)[0]
fort = FortData()
fort.latitude = end[0][0]
fort.longitude = end[0][1]
fort.id = end[1]

beh_spin_pokestop(worker, l5obj, player_position, start[1])
# l5obj = worker.do_get_map_objects(end[0])
# objs = worker.do_get_map_objects(end[0])
# print "catchanbleA {}".format(str(len(catchable_pokemon(objs))))

beh_spin_pokestop_raw(worker, fort, end[0])
objs = worker.do_get_map_objects(end[0])
print "catchanbleB {}".format(str(len(catchable_pokemon(objs))))
objs = worker.do_get_map_objects(end[0])
print "catchanbleC {}".format(str(len(catchable_pokemon(objs))))
objs = worker.do_get_map_objects(end[0])
print "catchanbleD {}".format(str(len(catchable_pokemon(objs))))


