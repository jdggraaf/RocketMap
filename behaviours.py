import logging
import random

import time

from apiwrapper import EncounterPokemon
from getmapobjects import inventory_elements_by_id, inrange_pokstops, forts, get_player_level, inventory_elements, \
    inventory_discardable_pokemon, catchable_pokemon
from gymdb import update_gym_from_details
from accountdbsql import db_set_account_level
from gymdbsql import do_with_backoff_for_deadlock, create_or_update_gym_from_gmo2
from management_errors import GaveUpApiAction
from pokemon_catch_worker import PokemonCatchWorker, WorkerResult

log = logging.getLogger(__name__)

ITEM_LIMITS = {
    1: 100,  # Poke Ball
    2: 50,  # Great Ball
    3: 100,  # Ultra Ball
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 40,  # Max Potion
    201: 0,  # Revive
    202: 40,  # Max Revive
    701: 20,  # Razz Berry
    702: 20,  # Bluk Berry
    703: 20,  # Nanab Berry
    704: 20,  # Wepar Berry
    705: 20,  # Pinap Berry
}


def beh_clean_bag(pogoservice, inventory_items):
    rec_items = {}
    limits = ITEM_LIMITS
    for item_dic in inventory_items:
        item_ = item_dic["item"]
        item = item_["item_id"]
        count = item_.get("count", 0)
        if item in limits and count > limits[item]:
            discard = count - limits[item]
            if discard > 50:
                rec_items[item] = random.uniform(50, discard)
            else:
                rec_items[item] = discard

    removed = 0
    for item, count in rec_items.items():
        random_sleep(2 + count / 3)
        result = pogoservice.do_recycle_inventory_item(item_id=item, count=count)
        if result:
            removed += count
    random_sleep(2)
    log.info("Removed {} items".format(str(removed)))


def beh_catch_all_nearby_pokemon(pogoservice, pos, map_objects, encountered):
    for catchable_ in catchable_pokemon(map_objects):
        encounter_id = catchable_["encounter_id"]
        spawn_point_id = catchable_["spawn_point_id"]
        if encounter_id not in encountered:
            res = beh_catch_pokemon(pogoservice, map_objects, pos, encounter_id, spawn_point_id)
            if res == WorkerResult.ERROR_NO_BALLS:
                log.info("Worker is out of pokeballs")
                break
            encountered.add(encounter_id)  # sort-of leaks memory
        rnd_sleep(15)


def beh_catch_pokemon(pogoservice, map_objects, position, encounter_id, spawn_point_id):
    encounter_response = pogoservice.do_encounter_pokemon(encounter_id, spawn_point_id, position)

    probablity = EncounterPokemon(encounter_response, encounter_id).probability()
    if probablity:
        catch_rate_by_ball = [0] + probablity['capture_probability']
        level = get_player_level(map_objects)

        pcw = PokemonCatchWorker(position, spawn_point_id, pogoservice)
        elements = inventory_elements_by_id(map_objects)
        catch = pcw.do_catch(encounter_id, catch_rate_by_ball, elements)
        if catch == WorkerResult.ERROR_NO_BALLS:
            return catch
        if catch:
            log.info("{} level {} caught pokemon {}".format(str(pogoservice.name()), str(level), str(catch)))
        return catch


def beh_spin_nearby_pokestops(pogoservice, map_objects, position):
    if map_objects:
        pokestops = inrange_pokstops(map_objects, position)
        for pokestop in pokestops:
            if "cooldown_complete_timestamp_ms" in pokestop:
                log.debug('Pokestop is in cooldown, ignoring')
            else:
                pogoservice.do_pokestop_details(pokestop)
                pogoservice.do_spin_pokestop(pokestop, position)
                rnd_sleep(10)  # Randomization must be controlled consistently, not some here and some there 2 seconds was


def beh_spin_nearby_pokestops_with_log_map(pogoservice, map_objects, position, previous_stops):
    if map_objects:
        pokestops = inrange_pokstops(map_objects, position)
        for pokestop in pokestops:
            if "cooldown_complete_timestamp_ms" in pokestop:
                log.debug('Pokestop is in cooldown, ignoring')
            else:
                fort_details = pogoservice.do_pokestop_details(pokestop)
                rnd_sleep(3)  # Randomization must be controlled consistently, not some here and some there 2 seconds was
                if pogoservice.do_spin_pokestop(pokestop, position):
                    fort_id = fort_details["responses"]["FORT_DETAILS"]["fort_id"]
                    urls = fort_details["responses"]["FORT_DETAILS"]["image_urls"]
                    if not fort_id in previous_stops:
                        previous_stops[fort_id] = urls
                    else:
                        plist = str(previous_stops[fort_id])
                        new_urls_string = str(urls)
                        if plist != new_urls_string:
                            log.error("URLS CHANGED FOR POKESTOP!! {} vs {}".format(plist, new_urls_string))
                            previous_stops[fort_id] = new_urls_string
                rnd_sleep(4)  # Randomization must be controlled consistently, not some here and some there 2 seconds was

def beh_safe_scanner_bot(pogoservice, moves_generator):
        try:
            beh_do_scanner_bot(pogoservice, moves_generator, 120)
        except:
            logging.exception("Outer worker catch block caught exception")


def beh_do_scanner_bot(pogoservice, moves_generator, delay):
    last_scanned_position = None
    for move in moves_generator:
        current_position = move['coordinates']
        gym_id = move['gym_id']
        try:
            map_objects = pogoservice.do_get_map_objects(current_position)
            gyms = forts(map_objects)
        except GaveUpApiAction:  # this should not really happen
            log.error("Giving up on location {} for gym {}".format(str(current_position), gym_id))
            continue
        if gyms is not None:
            try:
                gmo_gym = next(x for x in gyms if x["id"] == gym_id)
                do_with_backoff_for_deadlock(lambda: create_or_update_gym_from_gmo2(gym_id, gmo_gym))
                if gmo_gym is None:
                    log.error("get_map_objects did not give us gym")
            except StopIteration:
                print "gym " + gym_id + "was not found at location " + str(last_scanned_position)

        last_scanned_position = current_position

        time.sleep(2 + random.random())
        try:
            b = pogoservice.do_gym_get_info(current_position, current_position, gym_id)
            __log_info(pogoservice, "Sending gym {} to db".format(gym_id))
            update_gym_from_details(b)
        except GaveUpApiAction:
            time.sleep(20)
            __log_error(pogoservice, "Gave up on gym " + gym_id + " " + str(current_position))
            pass
        time.sleep(delay)


# noinspection PyBroadException
def beh_safe_do_gym_scan(pogoservice, moves_generator):
        try:
            beh_gym_scan(pogoservice, moves_generator, 0)
        except:
            logging.exception("Outer worker catch block caught exception")


def beh_gym_scan(pogoservice, moves_generator, delay):
    seen_gyms = set()
    last_scanned_position = None
    for move in moves_generator:
        current_position = move['coordinates']
        gym_id = move['gym_id']
        try:
            gyms = forts(pogoservice.do_get_map_objects(current_position))
        except GaveUpApiAction:  # this should not really happen
            log.error("Giving up on location {} for gym {}".format(str(current_position), gym_id))
            continue
        if gyms is not None:
            try:
                gmo_gym = next(x for x in gyms if x["id"] == gym_id)
                beh_process_single_gmo_gym_no_dups(pogoservice, seen_gyms, gmo_gym, current_position)
            except StopIteration:
                print "gym " + gym_id + "was not found at location " + str(last_scanned_position)

        last_scanned_position = current_position
        time.sleep(delay)


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random.random() * 2)
    time.sleep(random_)


def beh_handle_level_up(worker, previous_level, map_objects):
    new_level = get_player_level(map_objects)

    if new_level != previous_level:
        rnd_sleep(2)
        log.info("{} Leveled up from {} to level {}".format(str(worker.account_info().username), str(previous_level), str(new_level)))
        worker.do_collect_level_up(new_level)
        db_set_account_level(worker.account_info().username, new_level)
    return new_level


def beh_process_single_gmo_gym_no_dups(pogoservice, seen_gyms, gmo_gym, current_position):
    gym_id = gmo_gym["id"]

    if gym_id in seen_gyms:
        __log_debug(pogoservice, "Gym {} already processed by this worker".format(gym_id))
        return
    seen_gyms.add(gym_id)

    return beh_do_process_single_gmo_gym(pogoservice, gmo_gym, current_position)


def beh_do_process_single_gmo_gym(pogoservice, gmo_gym, current_position):
    gym_id = gmo_gym["id"]

    modified = do_with_backoff_for_deadlock(lambda: create_or_update_gym_from_gmo2(gym_id, gmo_gym))
    if gmo_gym is None:
        __log_error(pogoservice, "get_map_objects did not give us gym")
    if not modified:
        __log_debug(pogoservice, "Gym {} is not modified since last scan, skippings details".format(gym_id))
        return

    time.sleep(3 + random.random())
    try:
        gym_pos = gmo_gym['latitude'], gmo_gym['longitude']

        b = pogoservice.do_gym_get_info(current_position,gym_pos, gym_id)
        __log_info(pogoservice, "Sending gym {} to db".format(gym_id))
        update_gym_from_details(b)
    except GaveUpApiAction:
        time.sleep(20)
        __log_error(pogoservice, "Gave up on gym " + gym_id + " " + str(current_position))
        pass
    time.sleep(2 + random.random())


def beh_random_bag_cleaning(map_objects, worker):
    inventory = inventory_elements(map_objects)
    if len(inventory) > 250 and random.random() > 0.5:
        beh_clean_bag(worker, inventory)
    elif len(inventory) > 320:
        beh_clean_bag(worker, inventory)


def discard_random_pokemon(worker, map_objects):
    nonfavs = inventory_discardable_pokemon(map_objects, worker.account_info()["buddy"])
    log.info("{} is believed to have discardable pokemons {}".format(worker.name(), str([x["pokemon_data"]["id"] for x in nonfavs])))

    maxtrans = int(random.random() * len(nonfavs))
    transfers = set()
    samples = random.sample(nonfavs, maxtrans)
    for item in samples:
        transfers.add(item["pokemon_data"]["id"])
    if len(transfers) > 0:
        rnd_sleep(10)
        rval = worker.do_transfer_pokemon(list(transfers))
        rnd_sleep(10)
        return rval


def random_sleep(seconds):
    time.sleep(seconds + int(random.random() * 3))

def is_lowhalf(afl):
    ms = str(afl).split(".")[1]
    return ms.endswith('1') or ms.endswith('2') or ms.endswith('3') or ms.endswith('4') or ms.endswith('5')


def contains_two(afl):
    ms = str(afl).split(".")[1]
    return "2" in ms


candy_rares = {131, 147, 148, 246, 247}
real_rares = {113, 114, 143, 149, 201, 242, 248}


def is_candy_rare(pkmn):
    id_ = pkmn['pokemon_id']
    return id_ in candy_rares


def is_rare(pkmn):
    id_ = pkmn['pokemon_id']
    return id_ in real_rares



WORKER_STRATEGY = {
    0: 'UNSET',
    1: 'IMMOBILE_GMO_ONLY',
    2: 'IMMOBILE_POKESTOP_ONLY_PASSIVE',
    3: 'IMMOBILE_POKESTOP_ONLY_AGGRESSIVE',
    4: 'IMMOBILE_POKESTOP_AND_POKEMON_PASSIVE',
    5: 'IMMOBILE_POKESTOP_AND_POKEMON_AGGRESSIVE',
    6: 'LAZY_PLAYER_POKESTOP_AND_POKEMON_PASSIVE',  # sits on ass in chair, moves around occasionaly - maybe for rares
    7: 'LAZY_PLAYER_POKESTOP_AND_POKEMON_AGGRESSIVE'  # sits on ass in chair, moves around occasionaly - maybe for rares
}


def determine_behaviour(pos, get_map_objects, worker_number):
    pokstops = inrange_pokstops(get_map_objects, pos)
    if len(pokstops) == 0:
        return "IMM"
    if is_lowhalf(pos[1]):
        return "PS"
    else:
        return "PSA"


def is_pokestop(behaviour):
    return behaviour and "PS" in behaviour


def is_pokemon(behaviour):
    return behaviour and "PM" in behaviour


def is_aggressive_pokemon(behaviour):
    return behaviour and "PMA" in behaviour


def is_aggressive_pokestop(behaviour):
    return behaviour and "PSA" in behaviour


def __log_debug(pogoservice, msg):
    log.debug("%s:" + msg, pogoservice.name())


def __log_error(pogoservice, msg):
    log.error("%s:" + msg, pogoservice.name())


def __log_warning(pogoservice, msg):
    log.warn("%s:" + msg, pogoservice.name())


def __log_info(pogoservice, msg):
    log.info("%s:" + msg, pogoservice.name())
