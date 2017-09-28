from Queue import Queue

from accountdbsql import set_account_db_args
from accounts import *
from apiwrapper import EncounterPokemon
from argparser import std_config, load_proxies, add_system_id, add_use_account_db
from behaviours import beh_spin_nearby_pokestops, beh_catch_pokemon, discard_all_pokemon
from geography import *
from getmapobjects import catchable_pokemon, parse_pokestops, parse_gyms, inrange_pokstops, inrange_gyms
from gymdbsql import set_args
from pogom.utils import gmaps_reverse_geolocate
from scannerutil import install_thread_excepthook, setup_logging

setup_logging()
log = logging.getLogger(__name__)



import s2sphere

r = s2sphere.RegionCoverer()
p1 = s2sphere.LatLng.from_degrees(33, -122)
p2 = s2sphere.LatLng.from_degrees(33.1, -122.1)
cell_ids = r.get_covering(s2sphere.LatLngRect.from_point_pair(p1, p2))

'''

http://s2map.com/#order=latlng&mode=polygon&s2=false&points=5062448961839693824

Schema changes:
alter table gymmember add column first_seen datetime null;
alter table gymmember add column last_no_present datetime null;
alter table gym add column gymscanner smallint null;
'''
parser = std_config("gymscanner")
add_system_id(parser)
add_use_account_db(parser)
args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)



install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []

p1 = (59.875931, 10.842117)
p2 = (59.89763532039168, 10.845424944417772)
distance = vincenty(p1, p2).m
print distance

#Q pos = (59.902942, 10.8763150)
#pos = (59.900179, 10.860831)
#pos= (59.926051, 10.703054)   # frognerparken 4 stop
pos = (59.934373, 10.718340)
#pos = (59.935523, 10.719660)   # marienlyst
#pos=(59.924915,10.70725) # akerbrygge
# pos = (59.852992,10.785)

args.player_locale = gmaps_reverse_geolocate(
    args.gmaps_key,
    args.locale,
    str(pos[0]) + ', ' + str(pos[1]))

'''
Call 5062448961839693824
59.90692377930243,10.722713849061687
'''

account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {}, replace_warned=False)

l5account = account_manager.add_account({"username":"PauTnrnsRmdL","password":"YIEzRkLDJhiu","provider":"ptc"})
# ptc,AysOuDaFend,Freedom4@ll
#l5account = Account2("PauTnrnsRmdL", "YIEzRkLDJhiu", "ptc", args, 7200, 1800, cycle(args.hash_key), None, {}, None, account_manager)
login = l5account.login(pos)
l5obj = l5account.do_get_map_objects(pos)

discard_all_pokemon(l5account)

'''
inventory = l5account.account_info()["items"]
if 301 in inventory:
    rest = l5account.do_use_lucky_egg()
    print (rest)
'''
gyms = inrange_gyms(l5obj, pos)
gym = gyms[0]
raid_info = gym.raid_info
raid_seed = raid_info.raid_seed

gym_pos = gym.latitude, gym.longitude
gd = l5account.do_gym_get_info(pos, gym_pos, gym.id)
gym_get_info_data = gd["responses"]["GYM_GET_INFO"]
gym_status_and_defenders = gym_get_info_data.gym_status_and_defenders
raid_info = gym_status_and_defenders.pokemon_fort_proto.raid_info
raid_end = datetime.fromtimestamp(raid_info.raid_end_ms / 1000)
raid_battle = datetime.fromtimestamp(raid_info.raid_battle_ms / 1000)
raid_spawn = datetime.fromtimestamp(raid_info.raid_spawn_ms / 1000)

scan_catchable = catchable_pokemon(l5obj)
first = scan_catchable[0]
print (str(first.pokemon_id))
encounter = l5account.do_encounter_pokemon(first.encounter_id, first.spawn_point_id, pos)
api = EncounterPokemon(encounter, first.encounter_id)
res = api.contains_expected_encounter()
pokemon = beh_catch_pokemon(l5account, l5obj, pos, first.encounter_id, first.spawn_point_id)
discard_all_pokemon(l5account)

#res = l5account.do_claim_codename("us02mn45321")
l = inrange_pokstops(l5obj,pos)
l5obj = l5account.do_get_map_objects(pos)
print str(l)

beh_spin_nearby_pokestops(l5account, l5obj, pos)
time.sleep(10)
l5obj = l5account.do_get_map_objects(pos)
l5obj = l5account.do_get_map_objects(pos)
beh_spin_nearby_pokestops(l5account, l5obj, pos)


api2 = l5account.get_raw_api()
req2 = api2.create_request()
fort_details_response2 = req2.get_inventory(
    timestamp_millis=0)
fort_details_response2 = req2.call()


print str(parse_gyms(l5obj))

l6account = Account2("ActOuDaGum", "Freedom4@ll", "ptc", args, 7200, 1800, cycle(args.hash_key), None, {}, None)
l6obj = l6account.do_get_map_objects(pos)
print str(parse_pokestops(l6obj))
api2 = l5account.get_raw_api()
req2 = api2.create_request()




