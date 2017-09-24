from accountdbsql import set_account_db_args
from accounts import *
from behaviours import beh_clean_bag
from pokemon_catch_worker import PokemonCatchWorker, default_catch_rate_by_ball
from workers import WorkerManager
from geography import *
import json
from queue import Queue
from gymdb import update_gym_from_details, load_spawn_points
from argparser import std_config,location,load_proxies
from gymdbsql import set_args, pokemon_location, load_spawn_point, db_load_spawn_points_missing_s2, db_set_s2_cellid
from scannerutil import install_thread_excepthook
from threading import Thread
from s2sphere import Cell, CellId,LatLng, Point
from apiwrapper import EncounterPokemon
from base64 import b64encode
from getmapobjects import wild_pokemon, catchable_pokemon, nearby_pokemon, s2_cell_ids, cells_with_pokemon_data, \
    s2_cell_ids_from_cells, nearby_pokemon_from_cell, catchable_pokemon_from_cell, inventory_pokemon, inventory_pokedex, \
    inventory_candy, inventory_elements, inventory_player_stats, parse_pokestops, parse_gyms, nearest_pokstop, \
    inrange_pokstops, inventory_elements_by_id, nearest_nearby_pokemon, encounter_capture_probablity, __get_map_cells, \
    regular_nonfav, all_pokemon_pokedex_ids, get_player_level

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)



parser = std_config("gymscanner")
args = parser.parse_args()
load_proxies(args)
set_args(args)
set_account_db_args(args)

install_thread_excepthook()

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
queue = []



#Q pos = (59.902942, 10.8763150)
#pos = (59.900179, 10.860831)
#pos= (59.926051, 10.703054)   # frognerparken 4 stop
#pos = (59.935523, 10.719660)   # marienlyst
pos=(59.908326, 10.722739) # akerbrygge
'''
Call 5062448961839693824
59.90692377930243,10.722713849061687
'''

l5account = Account2("AahOuDaNos", "Freedom4@ll", "ptc", args, 7200, 1800, cycle(args.hash_key), None, {}, None)
l5obj = l5account.do_get_map_objects(pos)
print(get_player_level(l5obj))
print(str(catchable_pokemon(l5obj)))

pokemons = all_pokemon_pokedex_ids(l5obj)

pokemon = regular_nonfav(l5obj)
data_ = pokemon[3]["pokemon_data"]
id_ = data_["id"]
l5account.do_set_favourite(id_, True)
for cellid in s2_cell_ids(l5obj):
    print str(cellid)

l6account = Account2("ActOuDaGum", "Freedom4@ll", "ptc", args, 7200, 1800, cycle(args.hash_key), None, {}, None)
l6obj = l6account.get_map_objects(pos)
print(get_player_level(l5obj))

scan_wilds = wild_pokemon(l6obj)
scan_catchable = catchable_pokemon(l6obj)
scan_nearby = nearby_pokemon(l6obj)
wild6 = scan_wilds[0]
print str(wild6)



