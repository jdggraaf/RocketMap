import logging
import random

import time

import datetime

from geopy.distance import vincenty

from apiwrapper import EncounterPokemon
from geography import center_geolocation
from getmapobjects import inrange_pokstops, forts, \
    inventory_discardable_pokemon, catchable_pokemon, find_pokestop
from gymdb import update_gym_from_details
from accountdbsql import db_set_account_level, db_set_egg_count, db_set_lure_count
from gymdbsql import do_with_backoff_for_deadlock, create_or_update_gym_from_gmo2
from inventory import total_iventory_count, egg_count, lure_count
from management_errors import GaveUpApiAction
from pokemon_catch_worker import PokemonCatchWorker, WorkerResult

log = logging.getLogger(__name__)

L20_ITEM_LIMITS = {
    1: 5,  # Poke Ball
    2: 25,  # Great Ball
    3: 170,  # Ultra Ball
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 0,  # Max Potion
    201: 0,  # Revive
    202: 0,  # Max Revive
    701: 0,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 0,  # Pinap Berry
    1101: 0,  # Sun stone
    1103: 0,  # Metal coat
    1105: 0,  # Upgrade
    1104: 0  # Dragon scale
}

L12_ITEM_LIMITS = {
    1: 25,  # Poke Ball
    2: 150,  # Great Ball
    3: 25,  # Ultra Ball. Ensure that we keep some because we play level 20 with these limits
    101: 0,  # Potion
    102: 0,  # Super Potion
    103: 0,  # Hyper Potion
    104: 0,  # Max Potion
    201: 0,  # Revive
    202: 0,  # Max Revive
    701: 0,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 0,  # Pinap Berry
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
    701: 0,  # Razz Berry
    702: 0,  # Bluk Berry
    703: 0,  # Nanab Berry
    704: 0,  # Wepar Berry
    705: 0,  # Pinap Berry
    1101: 0,  # Sun stone
    1103: 0,  # Metal coat
    1105: 0,  # Upgrade
    1104: 0  # Dragon scale
}


def random_zleep(lower, upper):
    ms = int(random.uniform(lower, upper))
    log.info("Sleeping(4) for {}ms".format(str(ms)))
    time.sleep(float(ms) / 1000)


def beh_clean_bag(pogoservice):
    beh_clean_bag_with_limits(pogoservice, L20_ITEM_LIMITS)


def beh_clean_bag_with_limits(pogoservice, limits):
    rec_items = {}
    log.info("Bag cleaning started")
    for item, count in pogoservice.account_info()["items"].iteritems():
        if item in limits and count > limits[item]:
            discard = count - limits[item]
            if discard > 50:
                rec_items[item] = int(random.uniform(50, discard))
            else:
                rec_items[item] = discard

    removed = 0
    for item, count in rec_items.items():
        # random_zleep(100, 1000)
        result = pogoservice.do_recycle_inventory_item(item_id=item, count=count)
        if result:
            removed += count
    log.info("Bag cleaning Removed {} items".format(str(removed)))


def beh_catch_all_nearby_pokemon(pogoservice, pos, map_objects, encountered):
    for catchable_ in catchable_pokemon(map_objects):
        encounter_id = catchable_.encounter_id
        spawn_point_id = catchable_.spawn_point_id
        if encounter_id not in encountered:
            res = beh_catch_pokemon(pogoservice, pos, encounter_id, spawn_point_id)
            if res == WorkerResult.ERROR_NO_BALLS:
                log.info("Worker is out of pokeballs")
                break
            encountered.add(encounter_id)  # sort-of leaks memory
        rnd_sleep(15)


def beh_catch_pokemon(pogoservice, position, encounter_id, spawn_point_id):
    encounter_response = pogoservice.do_encounter_pokemon(encounter_id, spawn_point_id, position)
    probablity = EncounterPokemon(encounter_response, encounter_id).probability()
    return beh_catch_encountered_pokemon(pogoservice, position, encounter_id,probablity)


def beh_catch_encountered_pokemon(pogoservice, position, encounter_id, spawn_point_id, probablity):
    start_catch_at = datetime.datetime.now()

    if probablity:
        catch_rate_by_ball = [0] + list(probablity.capture_probability)
        level = pogoservice.account_info()["level"]

        pcw = PokemonCatchWorker(position, spawn_point_id, pogoservice)
        elements = pogoservice.account_info()["items"]
        catch = pcw.do_catch(encounter_id, catch_rate_by_ball, elements)
        if catch == WorkerResult.ERROR_NO_BALLS:
            return catch
        if catch:
            log.info("{} level {} caught pokemon {} in {}".format(str(pogoservice.name()), str(level), str(catch),
                                                                  str(datetime.datetime.now() - start_catch_at)))
        return catch
    else:
        log.warn("Encounter did not succeed")


def random_sleep_z(lower, upper, client):
    ms = int(random.uniform(lower, upper))
    time.sleep(float(ms) / 1000)


def beh_spin_nearby_pokestops(pogoservice, map_objects, position):
    spun = 0
    if map_objects:
        pokestops = inrange_pokstops(map_objects, position)
        for idx, pokestop in enumerate(pokestops):
            if pokestop.cooldown_complete_timestamp_ms > 0:
                log.debug('Pokestop is in cooldown, ignoring')
            else:
                pogoservice.do_pokestop_details(pokestop)
                if idx > 0:
                    idx_ = idx * 300
                    log.info("Random sleeping at least {}ms for additional stops".format(idx_))
                    random_sleep_z(idx_, idx_ + 100, "pokestop_details")  # Do not let Niantic throttle

                spin_response = pogoservice.do_spin_pokestop(pokestop, position)
                result = spin_response['responses']['FORT_SEARCH'].result
                if result == 2:
                    fort_location = (pokestop.latitude, pokestop.longitude)
                    closer = center_geolocation([fort_location, position])
                    time.sleep(1)
                    result = pogoservice.do_spin_pokestop(pokestop, closer)
                    if result == 2:
                        log.error("Still unable to spin pokestop:{}".format(str(pokestop)))

                spun += 1
    return spun

def beh_spin_pokestop(pogoservice, map_objects, player_position, pokestop_id):
    if map_objects:
        pokestop = find_pokestop(map_objects, pokestop_id)
        if pokestop.cooldown_complete_timestamp_ms > 0:
            cooldown = datetime.datetime.fromtimestamp(pokestop.cooldown_complete_timestamp_ms / 1000)
            if cooldown > datetime.datetime.now():
                log.info('Pokestop is in cooldown until {}, ignoring'.format(str(cooldown)))
                return
        log.info("Details")
        pogoservice.do_pokestop_details(pokestop)
        log.info("Spinning pokestop")
        spin_response = pogoservice.do_spin_pokestop(pokestop, player_position)
        result = spin_response['responses']['FORT_SEARCH'].result
        if result == 2:
            stop_pos = (pokestop.latitude,pokestop.longitude)
            dist = vincenty(stop_pos, player_position).m
            log.error("Too far away from stop, {}m. this should not happen".format(str(dist)))
        log.info("Spun pokestop")
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


def beh_handle_level_up(worker, previous_level):
    new_level = worker.account_info()["level"]

    if previous_level and new_level != previous_level:
        # rnd_sleep(2)
        log.info("{} Leveled up from {} to level {}".format(str(worker.account_info().username), str(previous_level),
                                                            str(new_level)))
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


def beh_aggressive_bag_cleaning(worker, item_limits):
    total = total_iventory_count(worker)
    if total > 300:
        log.info("Aggressive bag cleaning with inventory {}".format(str(total)))
        beh_clean_bag_with_limits(worker, item_limits)


def discard_random_pokemon(worker):
    nonfavs = inventory_discardable_pokemon(worker, worker.account_info()["buddy"])

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
    nonfavs = inventory_discardable_pokemon(worker, worker.account_info()["buddy"])

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
