import codecs
import copy
import logging
import os
import sys
import unittest

from flask import json
from geopy.distance import vincenty

from gymdbsql import pokestop_coordinates

log = logging.getLogger(__name__)

def pmdata():
    pokemon = os.path.dirname(os.path.abspath(os.path.realpath(__file__))) + "/docs/pokemon.min.json"
    with open(pokemon) as data_file:
        # just open the file...
        input_file = file(pokemon, "r")
        # read the file and decode possible UTF-8 signature at the beginning
        # which can be the case in some files.
        return json.loads(input_file.read().decode("utf-8-sig"))
        # return json.load(data_file, encoding="utf8")

pokemons = pmdata()


def find_id_of_name(name):
    for id, pokemon in pokemons.iteritems():
        if "Nidoran" in pokemon["name"] and "Nidoran" in name:
            return int(id)
        if pokemon["name"] == name:
            return int(id)
    raise ValueError("Could not find {}".format( name))


def names_to_ids(names):
    result = set()
    for name in names:
        result.add( find_id_of_name(name))
    return  result


def can_be_seen():
    list = ['Pidgey', 'Rattata', 'Ekans', 'Sandshrew', u"Nidoran\u2642", 'Zubat', 'Oddish', 'Paras', 'Meowth', 'Psyduck',
            'Poliwag', 'Bellsprout', 'Tentacool', 'Geodude', 'Magnemite', 'Krabby', 'Goldeen', 'Staryu', 'Magikarp',
            'Sentret', 'Ledyba', 'Spinarak', 'Natu', 'Marill', 'Hoppip', 'Sunkern', 'Wooper', 'Murkrow', 'Snubbull',
            'Slugma']
    return names_to_ids(list)


def starters_with_evolutions():
    list = ['Bulbasaur', 'Ivysaur', 'Venusaur', 'Squirtle', 'Wartortle', 'Blastoise', 'Charmander', 'Charmeleon',
            'Charizard']
    return names_to_ids(list)


def can_not_be_seen():  # todo add 'Evolved Pokemon'
    list = ['Bulbasaur', 'Ivysaur', 'Venusaur', 'Squirtle', 'Wartortle', 'Blastoise', 'Charmander', 'Charmeleon',
            'Charizard', 'Caterpie', 'Weedle', 'Spearow', 'Clefairy', 'Vulpix', 'Jigglypuff',
            'Venonat', 'Mankey', 'Growlithe', 'Abra',
            'Slowpoke', 'Shellder', 'Gastly', 'Onix', 'Drowzee', 'Voltorb', 'Koffing', 'Chansey', 'Tangela', 'Horsea',
            'Mr. Mime', 'Scyther', 'Magmar', 'Lapras', 'Eevee',
            'Porygon', 'Omanyte', 'Kabuto', 'Aerodactyl', 'Snorlax', 'Dratini', 'Hoothoot', 'Chinchou', 'Mareep',
            'Sudowoodo', 'Aipom', 'Yanma', 'Unown', 'Wobbuffet', 'Girafarig', 'Shuckle', 'Sneasel', 'Teddiursa',
            'Swinub', 'Remoraid', 'Houndour', 'Stantler', 'Larvitar','Machop']
    return names_to_ids(list)


class NoPokemonFoundPossibleSpeedViolation:
    def __init__(self):
        pass


def cells_with_pokemon_data(response):
    cells = __get_map_cells(response)
    result = []
    for cell in cells:
        if len(cell.wild_pokemons) > 0 or len(cell.catchable_pokemons) > 0 or len(cell.nearby_pokemons):
            result.append( cell)
    return result


def update_fort_locations(cells, map_objects):
    for cell in cells:
        for pkmn in nearby_pokemon_from_cell(cell):
            if 'fort_id' in pkmn:
                fort_id = pkmn['fort_id']
                fort1 = find_fort(map_objects, fort_id)
                if fort1:
                    pkmn['latitude'] = fort1['latitude']
                    pkmn['longitude'] = fort1['longitude']
                else:
                    log.warning("Fort {} referenced but not in payload".format(fort_id))


def wild_pokemon(response):
    cells = __get_map_cells(response)
    wilds = []
    for cell in cells:
        for wild in cell.get('wild_pokemons', []):
            wilds.append(wild)
    return wilds


def celldiff(old_cells, new_cells):
    result = copy.deepcopy(new_cells)
    if not old_cells:
        return result
    for new_cell in result:
        cell_id = new_cell['s2_cell_id']

        old_cell = [x for x in old_cells if x['s2_cell_id'] == cell_id]
        if len(old_cell) == 0:
            continue

        for i, pkmn in enumerate(nearby_pokemon_from_cell(new_cell)):
            pokemon_in_old_cell = [x for x in nearby_pokemon_from_cell(old_cell[0]) if x['encounter_id'] == pkmn['encounter_id']]
            if pokemon_in_old_cell:
                del new_cell.nearby_pokemons[i]
        for i, catchable in enumerate(catchable_pokemon_from_cell(new_cell)):
            pokemon_in_old_cell = [x for x in catchable_pokemon_from_cell(old_cell[0]) if x['encounter_id'] == catchable['encounter_id']]
            if pokemon_in_old_cell:
                del new_cell.catchable_pokemons[i]

        for i, cell in enumerate(result):
            if len(nearby_pokemon_from_cell(cell)) == 0 and len(catchable_pokemon_from_cell(cell)) == 0:
                del result[i]

    onlychanges = [x for x in result if not (len(nearby_pokemon_from_cell(x)) == 0 and len(catchable_pokemon_from_cell(x)) == 0)]
    return onlychanges


def catchable_pokemon(response):
    cells = __get_map_cells(response)
    wilds = []
    for cell in cells:
        for wild in cell.catchable_pokemons:
            wilds.append(wild)
    return wilds


def nearby_pokemon(response):
    return nearby_pokemon_from_cells(__get_map_cells(response))


def nearest_nearby_pokemon(map_objects):
    len = sys.maxint
    fort_id = None
    for nearby in nearby_pokemon(map_objects):
        if "distance_in_meters" not in nearby:
            print str(nearby)
        elif nearby["distance_in_meters"] < len:
            len = nearby["distance_in_meters"]
            fort_id = nearby["fort_id"]

    fort = find_fort(map_objects, fort_id)
    if not fort:
        return pokestop_coordinates(fort_id)

    return fort["latitude"], fort["longitude"]


def encounter_capture_probablity(encounter_response):
    resp = encounter_response.get("responses", {}).get("ENCOUNTER", {}).get("capture_probability", None)
    if not resp:
        print str(encounter_response)
    return resp

def nearby_pokemon_from_cells(cells):
    wilds = []
    for cell in cells:
        for wild in nearby_pokemon_from_cell(cell):
            wilds.append(wild)
    return wilds


def nearby_pokemon_from_cell(cell):
    return cell.nearby_pokemons


def catchable_pokemon_from_cell(cell):
    return cell.catchable_pokemons


def all_pokemon_pokedex_ids(map_objects):
    result = []
    cells = __get_map_cells(map_objects)
    for cell in cells:
        result += map(lambda x: x["pokemon_id"], catchable_pokemon_from_cell(cell))
        result += map(lambda x: x["pokemon_id"], nearby_pokemon_from_cell(cell))
    return result


def find_catchable_encounter(map_objects, encounter_id):
    pokemons = catchable_pokemon(map_objects)
    for pokemon in pokemons:
        if encounter_id == pokemon["encounter_id"]:
            return pokemon


def s2_cell_ids(response):
    cells = response["responses"]["GET_MAP_OBJECTS"]["map_cells"]
    return s2_cell_ids_from_cells(cells)


def inventory_item_data(response, type):
    inv = response["responses"].get("GET_INVENTORY",{})
    resp = inv.get("inventory_delta",{}).get("inventory_items",[])
    return [x['inventory_item_data'] for x in resp if type in x['inventory_item_data']]


def inventory_pokemon(response):
    return inventory_item_data(response, "pokemon_data")


def inventory_discardable_pokemon(worker, buddy_id):
    inv_pokemon = worker.account_info().pokemons
    nonfavs = [id for id,pokemon in inv_pokemon.iteritems() if is_discardable(id,pokemon, buddy_id)]
    return nonfavs


def pokemon_uids(map_objects):
    inv_pokemon = inventory_pokemon(map_objects)
    uids = [x["pokemon_data"]["id"] for x in inv_pokemon]
    return uids


def pokemon_by_uid(map_objects, uid):
    inv_pokemon = inventory_pokemon(map_objects)
    matchin = [x for x in inv_pokemon if x["pokemon_data"]["id"] == uid]
    return matchin

def is_discardable(pokemon_id, pkmn, buddy_id):
    return pkmn.get("favorite",0) != 0 and \
           not pkmn.get("deployed_fort_id")  \
            and buddy_id != pokemon_id

def is_keeper(pkmn):
    return pkmn["pokemon_id"] == 64

def regular_nonfav(response):
    inv_pokemon = inventory_pokemon(response)
    nonfavs = [x for x in inv_pokemon if "favourite" not in x["pokemon_data"] and "is_egg" not in x[
        "pokemon_data"] and "deployed_fort_id" not in x["pokemon_data"]]
    return nonfavs

def pokestop_detail(details_response):
    return details_response["responses"]["FORT_DETAILS"]


def s2_cell_ids_from_cells(cells):
    cellIds = []
    for cell in cells:
        id = cell.get("s2_cell_id")
        if id:
            cellIds.append( id)
    return cellIds


def parse_gyms(map_objects):
    return [candidate for candidate in forts(map_objects) if candidate.type == 0]

def parse_pokestops(map_objects):
    return [candidate for candidate in forts(map_objects) if candidate.type == 1]

def nearest_pokstop(map_objects, pos):
    result = None
    closest = sys.maxint
    for pokestop in parse_pokestops(map_objects):
        distance = vincenty(pos, (pokestop["latitude"], pokestop["longitude"])).m
        if distance < closest:
            result = pokestop
            closest = distance
    return closest, result


def raid_gyms(map_objects, pos):
    gyms = inrange_gyms(map_objects, pos)
    return [candidate for candidate in gyms if candidate["raid_info"]["raid_level"] > 0]


def inrange_gyms(map_objects, pos):
    return fort_within_distance(parse_gyms(map_objects), pos, 750)


def inrange_pokstops(map_objects, pos):
    return fort_within_distance(parse_pokestops(map_objects), pos, 34)


def pokstops_within_distance(map_objects, pos, m):
    return fort_within_distance(parse_pokestops(map_objects), pos, m)


def fort_within_distance(forts, pos, m):
    items = []
    for fort in forts:
        distance = vincenty(pos, (fort.latitude, fort.longitude)).m
        if distance < m:
            items.append((distance,fort))
    items.sort()
    result = []
    for item in items:
        result.append(item[1])
    return result


def find_fort( map_objects, fort_id):
    for cell in __get_map_cells(map_objects):
        forts = cell.get('forts', [])
        for fort in forts:
            if fort["id"] == fort_id:
                return fort




def forts(map_dict):
    forts = []
    cells = __get_map_cells( map_dict)
    for cell in cells:
        forts += cell.forts
    return forts


def __check_speed_violation(cells):
    if sum(len(cell.keys()) for cell in cells) == len(cells) * 2:
        raise NoPokemonFoundPossibleSpeedViolation


def match_pokemon_in_result(response, pkmn_ids):
    cells = __get_map_cells(response)
    # __check_speed_violation(cells)
    found = [ x.pokemon_id for x in catchable_pokemon(response) if  x.pokemon_id in pkmn_ids]
    found += [ x.pokemon_id for x in nearby_pokemon(response) if  x.pokemon_id in pkmn_ids]
    log.info("Found {} of the specified IDs {}".format(len(found), found))
    return len(found)


def __get_map_cells(response):
    responses_ = response['responses']
    objects_ = responses_['GET_MAP_OBJECTS']
    return objects_.map_cells



class GMO_shadowbans(unittest.TestCase):
    def test(self):
        self.assertEqual(30, len(can_be_seen()))
        not_seen = can_not_be_seen()
        self.assertEqual(57, len(not_seen))
        self.assertTrue( 3 in not_seen)
