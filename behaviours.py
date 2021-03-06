import datetime
import logging
import random
import time

from geopy.distance import vincenty

from accountdbsql import db_set_account_level, db_set_egg_count, db_set_lure_count
from geography import move_towards
from getmapobjects import inrange_pokstops, inventory_discardable_pokemon, catchable_pokemon, find_pokestop, \
    inrange_pokstops_and_gyms, parse_gyms
from gymdb import update_gym_from_details
from gymdbsql import do_with_backoff_for_deadlock, create_or_update_gym_from_gmo2
from inventory import total_iventory_count, egg_count, lure_count, inventory
from management_errors import GaveUpApiAction
from pogoservice import TravelTime
from pokemon_catch_worker import PokemonCatchWorker, WorkerResult
from pokemon_data import pokemon_name
from scannerutil import distance_to_fort, fort_as_coordinate

log = logging.getLogger(__name__)


L20_ITEM_LIMITS = {
    1: 20,  # Poke Ball
    2: 50,  # Great Ball
    3: 170,  # Ultra Ball
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 0,  # Max Potion
    201: 0,  # Revive
    202: 0,  # Max Revive
    701: 20,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 70,  # Pinap Berry
    1101: 0,  # Sun stone
    1103: 0,  # Metal coat
    1105: 0,  # Upgrade
    1104: 0  # Dragon scale
}

L12_ITEM_LIMITS = {
    1: 20,  # Poke Ball
    2: 150,  # Great Ball
    3: 70,  # Ultra Ball. Ensure that we keep some because we play level 20 with these limits
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 0,  # Max Potion
    201: 0,  # Revive
    202: 0,  # Max Revive
    701: 20,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 70,  # Pinap Berry
    1101: 0,  # Sun stone
    1103: 0,  # Metal coat
    1105: 0,  # Upgrade
    1104: 0  # Dragon scale
}

PHASE_0_ITEM_LIMITS = {
    1: 200,  # Poke Ball
    2: 50,  # Great Ball. Ensure that we keep some because we play level 12 with these limits
    3: 0,  # Ultra Ball
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 0,  # Max Potion
    201: 0,  # Revive
    202: 0,  # Max Revive
    701: 20,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 70,  # Pinap Berry
    1101: 0,  # Sun stone
    1103: 0,  # Metal coat
    1105: 0,  # Upgrade
    1104: 0  # Dragon scale
}


def random_zleep(lower, upper):
    ms = int(random.uniform(lower, upper))
    log.info("Sleeping(4) for {}ms".format(str(ms)))
    time.sleep(float(ms) / 1000)



'''
2017-11-23 15:45:13,293 [    Thread-7][  stopmanager][    INFO][29419378] Inventory:
{1105: 0, 1: 9, 2: 12, 3: 0, 101: 23, 902: 6, 103: 53, 104: 20, 201: 72, 301: 5, 401: 9, 102: 6, 501: 6, 901: 1, 1401: 1, 705: 0, 701: 0, 703: 72}
'''


def beh_clean_bag_with_limits(pogoservice, limits, aggressive=False):
    rec_items = {}
    for item, count in pogoservice.account_info()["items"].items():
        if item in limits and count > limits[item]:
            discard = count - limits[item]
            if discard > 50 and not aggressive:
                rec_items[item] = int(random.uniform(50, discard))
            else:
                rec_items[item] = discard

    removed = 0
    for item, count in list(rec_items.items()):
        # random_zleep(100, 1000)
        result = pogoservice.do_recycle_inventory_item(item_id=item, count=count)
        if result:
            removed += count
    log.info("Bag cleaning Removed {} items".format(str(removed)))


def beh_catch_encountered_pokemon(pogoservice, position, encounter_id, spawn_point_id, probablity, pokemon_id, is_vip=False, fast=False):
    start_catch_at = datetime.datetime.now()

    if probablity:
        name = pokemon_name(pokemon_id)
        catch_rate_by_ball = [0] + list(probablity.capture_probability)
        level = pogoservice.account_info()["level"]

        pogoservice.add_log(name)
        pcw = PokemonCatchWorker(position, spawn_point_id, pogoservice, fast)
        elements = pogoservice.account_info()["items"]
        catch = pcw.do_catch(encounter_id, catch_rate_by_ball, elements, is_vip)
        if catch == WorkerResult.ERROR_NO_BALLS:
            return catch
        if catch:
            log.info("{} level {} caught {} id {} in {}".format(str(pogoservice.name()), str(level), name, str(catch),
                                                                str(datetime.datetime.now() - start_catch_at)))
        return catch
    else:
        log.warn("Encounter did not succeed")


def random_sleep_z(lower, upper, client):
    ms = int(random.uniform(lower, upper))
    time.sleep(float(ms) / 1000)


def beh_spin_nearby_pokestops(pogoservice, map_objects, position, range_m=39, blacklist=None, exclusions = {}):
    spun = []
    spinning_distance_m = 39
    travel_time = pogoservice.getlayer(TravelTime)
    old_speed = travel_time.get_speed()
    if map_objects:
        pokestops = inrange_pokstops_and_gyms(map_objects, position, range_m)
        for idx, pokestop in enumerate(pokestops):
            if blacklist and pokestop.id in blacklist:
                pass
            if exclusions and pokestop.id in exclusions:
                pass
            elif pokestop.cooldown_complete_timestamp_ms > 0:
                log.debug('Pokestop is in cooldown, ignoring')
            else:
                dist_to_stop = distance_to_fort( position, pokestop )
                if dist_to_stop > spinning_distance_m:
                    m_to_move = dist_to_stop - spinning_distance_m
                    log.info("Stop is {}m away, moving {}m closer".format(str(dist_to_stop), str(m_to_move)))
                    travel_time.use_slow_speed()
                    position = move_towards(position, fort_as_coordinate(pokestop), m_to_move)
                elif idx > 0:
                    idx_ = min(idx, 2) * 200
                    log.info("Random sleeping at least {}ms for additional stops".format(idx_))
                    random_sleep_z(idx_, idx_ + 100, "pokestop_details")  # Do not let Niantic throttle
                res = beh_spin_pokestop_raw(pogoservice, pokestop, position)
                if res == 1:
                    spun.append(pokestop.id)
    travel_time.set_fast_speed(old_speed)
    return spun


def beh_spin_pokestop(pogoservice, map_objects, player_position, pokestop_id):
    if map_objects:
        pokestop = find_pokestop(map_objects, pokestop_id)
        if not pokestop:
            log.warning("Could not find pokestop {}, might be removed from game".format(pokestop_id))
            return
        if pokestop.cooldown_complete_timestamp_ms > 0:
            cooldown = datetime.datetime.fromtimestamp(pokestop.cooldown_complete_timestamp_ms / 1000)
            if cooldown > datetime.datetime.now():
                log.info('Pokestop is in cooldown until {}, ignoring'.format(str(cooldown)))
                return
        return beh_spin_pokestop_raw(pogoservice, pokestop, player_position)
    else:
        log.warning("No mapobjects. learn python please")


def beh_spin_pokestop_raw(pogoservice, pokestop, player_position):
    pogoservice.do_pokestop_details(pokestop)
    spin_response = pogoservice.do_spin_pokestop(pokestop, player_position)
    result = spin_response['responses']['FORT_SEARCH'].result
    attempt = 0
    if result == 4:
        beh_aggressive_bag_cleaning(pogoservice)
        spin_response = pogoservice.do_spin_pokestop(pokestop, player_position)
        result = spin_response['responses']['FORT_SEARCH'].result

    while result == 2 and attempt < 6:
        stop_pos = (pokestop.latitude,pokestop.longitude)
        dist = vincenty(stop_pos, player_position).m
        if dist > 40:
            log.error("Too far away from stop, {}m. this should not happen".format(str(dist)))
            return result  # give up
        if attempt == 0:
            if player_position != stop_pos:
                player_position = move_towards(player_position, stop_pos, 1)
        if attempt == 2:
            objs = pogoservice.do_get_map_objects(player_position)
            log.info ("Extra gmo gave catchanble {}".format(str(len(catchable_pokemon(objs)))))
        time.sleep(1)  # investigate if really needed
        attempt += 1
        spin_response = pogoservice.do_spin_pokestop(pokestop, player_position)
        result = spin_response['responses']['FORT_SEARCH'].result
        log.info("{} attempt spinning gave result {}".format(str(attempt), str(result)))

    return result

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
            gyms = parse_gyms(map_objects)
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
                print("gym " + gym_id + "was not found at location " + str(last_scanned_position))

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
            gyms = parse_gyms(pogoservice.do_get_map_objects(current_position))
        except GaveUpApiAction:  # this should not really happen
            log.error("Giving up on location {} for gym {}".format(str(current_position), gym_id))
            continue
        if gyms is not None:
            try:
                gmo_gym = next(x for x in gyms if x["id"] == gym_id)
                beh_process_single_gmo_gym_no_dups(pogoservice, seen_gyms, gmo_gym, current_position)
            except StopIteration:
                print("gym " + gym_id + "was not found at location " + str(last_scanned_position))

        last_scanned_position = current_position
        time.sleep(delay)


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random.random() * 2)
    time.sleep(random_)


def beh_handle_level_up(worker, previous_level):
    new_level = int(worker.account_info()["level"])

    if previous_level and new_level != previous_level:
        worker.do_collect_level_up(new_level)

    if new_level != previous_level:
        db_set_account_level(worker.account_info().username, new_level)
        db_set_egg_count(worker.account_info().username, egg_count(worker))
        db_set_lure_count(worker.account_info().username, lure_count(worker))
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

        b = pogoservice.do_gym_get_info(current_position, gym_pos, gym_id)
        __log_info(pogoservice, "Sending gym {} to db".format(gym_id))
        update_gym_from_details(b)
    except GaveUpApiAction:
        time.sleep(20)
        __log_error(pogoservice, "Gave up on gym " + gym_id + " " + str(current_position))
        pass
    time.sleep(2 + random.random())


def beh_random_bag_cleaning(worker, item_limits):
    total = total_iventory_count(worker)
    if total > 310 and random.random() > 0.3:
        beh_clean_bag_with_limits(worker, item_limits)
    elif total > 320:
        beh_clean_bag_with_limits(worker, item_limits)


def beh_aggressive_bag_cleaning(worker):
    level = worker.account_info()["level"]
    item_limits = PHASE_0_ITEM_LIMITS if level < 12 else L12_ITEM_LIMITS if (12 < level < 21) else L20_ITEM_LIMITS

    total = total_iventory_count(worker)
    if total > 300:
        log.info("Aggressive bag cleaning with {} items in inventory: {}".format(str(total), str(inventory(worker))))
        beh_clean_bag_with_limits(worker, item_limits, aggressive=True)


def discard_random_pokemon(worker):
    nonfavs = inventory_discardable_pokemon(worker)

    maxtrans = int(random.random() * len(nonfavs))
    transfers = set()
    samples = random.sample(nonfavs, maxtrans)
    for item in samples:
        transfers.add(item["pokemon_data"]["id"])
    if len(transfers) > 0:
        log.info("{} is believed to have discardable pokemons {}".format(worker.name(), str(
            [x["pokemon_data"]["id"] for x in nonfavs])))
        rnd_sleep(10)
        rval = worker.do_transfer_pokemon(list(transfers))
        rnd_sleep(10)
        return rval


def discard_all_pokemon(worker):
    nonfavs = inventory_discardable_pokemon(worker)

    transfers = set(nonfavs)
    if len(transfers) > 0:
        log.info("{} is believed (2)to have discardable pokemons {}".format(worker.name(), str([x for x in nonfavs])))
        rnd_sleep(2)
        rval = worker.do_transfer_pokemon(list(transfers))
        rnd_sleep(2)
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
candy12 = {10, 13, 16}


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


def determine_behaviour(pos, get_map_objects):
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
