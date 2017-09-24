import logging
import math
import random
import sys
import time
from datetime import datetime, timedelta, datetime as dt

from geopy.distance import vincenty
from pgoapi import PGoApi
from pgoapi import utilities as util
from pgoapi.exceptions import HashingOfflineException, NianticThrottlingException, BannedAccountException, \
    BadHashRequestException, HashingTimeoutException
from pgoapi.exceptions import HashingQuotaExceededException, NianticOfflineException
from pgoapi.utilities import f2i
from requests.exceptions import ChunkedEncodingError

from apiwrapper import ReleasePokemon
from getmapobjects import inventory_player_stats, cells_with_pokemon_data, can_not_be_seen, nearby_pokemon_from_cell, \
    catchable_pokemon_from_cell
from accountdbsql import db_update_account
from management_errors import GaveUpApiAction
from pogom.account import check_login, TooManyLoginAttempts, LoginSequenceFail
from pogom.utils import generate_device_info
from scannerutil import pogo_api_version, nice_coordinate_string, nice_number

log = logging.getLogger("pogoserv")


class PogoService(object):
    def __init__(self):
        raise NotImplementedError("This is an abstract method.")

    def do_gym_get_info(self, position, gym_position, gym_id):
        raise NotImplementedError("This is an abstract method.")

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        raise NotImplementedError("This is an abstract method.")

    def do_get_map_objects(self, position):
        raise NotImplementedError("This is an abstract method.")

    def do_get_inventory(self, timestamp_millis):
        raise NotImplementedError("This is an abstract method.")

    def do_spin_pokestop(self, fort, step_location):
        raise NotImplementedError("This is an abstract method.")

    def do_pokestop_details(self, fort):
        raise NotImplementedError("This is an abstract method.")

    def do_collect_level_up(self, current_player_level):
        raise NotImplementedError("This is an abstract method.")

    def do_transfer_pokemon(self, pokemon_ids):
        raise NotImplementedError("This is an abstract method.")

    def do_add_lure(self, fort, step_location):
        raise NotImplementedError("This is an abstract method.")

    def do_recycle_inventory_item(self, item_id, count):
        raise NotImplementedError("This is an abstract method.")

    def do_set_favourite(self, pokemon_uid, favourite):
        raise NotImplementedError("This is an abstract method.")

    def do_use_item_encounter(self, berry_id, encounter_id, spawn_point_guid):
        raise NotImplementedError("This is an abstract method.")

    def do_catch_pokemon(self, encounter_id, pokeball, normalized_reticle_size, spawn_point_id, hit_pokemon,
                         spin_modifier, normalized_hit_position):
        raise NotImplementedError("This is an abstract method.")

    def get_raw_api(self):
        raise NotImplementedError("This is an abstract method.")

    def most_recent_position(self):  # prolly shouldnt be here
        raise NotImplementedError("This is an abstract method.")

    def name(self):  # prolly shouldnt be here
        raise NotImplementedError("This is an abstract method.")

    def update_position(self, position):  # prolly shouldnt be here
        raise NotImplementedError("This is an abstract method.")

    def account_info(self):  # prolly shouldnt be here
        raise NotImplementedError("This is an abstract method.")

    def do_claim_codename(self, name):  # prolly shouldnt be here
        raise NotImplementedError("This is an abstract method.")


class DelegatingPogoService(PogoService):
    def do_claim_codename(self,name):
        return self.target.do_claim_codename(name)

    # noinspection PyMissingConstructor
    def __init__(self, target):
        self.target = target

    def find_account_replacer(self):
        trgt = self.target
        while trgt and not isinstance(trgt, AccountReplacer) and not isinstance(trgt, Account2):
            trgt = trgt.target
        return trgt

    def do_gym_get_info(self, position, gym_position, gym_id):
        return self.target.do_gym_get_info(position, gym_position, gym_id)

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        return self.target.do_encounter_pokemon(encounter_id, spawn_point_id, step_location)

    def do_get_inventory(self, timestamp_millis):
        return self.do_get_inventory(timestamp_millis)

    def do_get_map_objects(self, position):
        return self.target.do_get_map_objects(position)

    def do_spin_pokestop(self, fort, step_location):
        return self.target.do_spin_pokestop(fort, step_location)

    def do_pokestop_details(self, fort):
        return self.target.do_pokestop_details(fort)

    def do_collect_level_up(self, current_player_level):
        return self.target.do_collect_level_up(current_player_level)

    def do_transfer_pokemon(self, pokemon_ids):
        return self.target.do_transfer_pokemon(pokemon_ids)

    def do_add_lure(self, fort, step_location):
        return self.target.do_add_lure(fort, step_location)

    def do_recycle_inventory_item(self, item_id, count):
        return self.target.do_recycle_inventory_item(item_id, count)

    def do_set_favourite(self, pokemon_uid, favourite):
        return self.target.do_set_favourite(pokemon_uid, favourite)

    def do_catch_pokemon(self, encounter_id, pokeball, normalized_reticle_size, spawn_point_id, hit_pokemon,
                         spin_modifier, normalized_hit_position):
        return self.target.do_catch_pokemon(encounter_id, pokeball, normalized_reticle_size, spawn_point_id,
                                            hit_pokemon,
                                            spin_modifier, normalized_hit_position)

    def do_use_item_encounter(self, berry_id, encounter_id, spawn_point_guid):
        return self.target.do_use_item_encounter(berry_id, encounter_id, spawn_point_guid)

    def get_raw_api(self):
        return self.target.get_raw_api()

    def most_recent_position(self):
        return self.target.most_recent_position()

    def name(self):
        return self.target.name()

    def update_position(self, position):
        return self.target.update_position(position)

    def account_info(self):
        return self.target.account_info()


'''
Account encapsulates an account, obeying the basic 10 second restrictions and gym
interaction speed restrictions. Clients that come in too fast will block until
acceptable interaction speeds have been achieved.

Non-goal: The account class does not obey speed restrictions for moving the
search area. Clients of this class are responsible for the movement speed.
'''


class Account2(PogoService):
    """An account"""

    def update_position(self, position):
        return self.__update_position(position)

    def account_info(self):
        return self

    def get_raw_api(self):
        return self.pgoApi

    def __init__(self, username, password, auth_service, args, search_interval,
                 rest_interval, hash_generator, proxy_supplier, db_data, account_manager):
        self.proxySupplier = proxy_supplier
        if proxy_supplier is not None:
            self.current_proxy = proxy_supplier(None)
        else:
            self.current_proxy = None
        self.account_manager = account_manager
        self.most_recent_get_map_objects = None
        self.lures = db_data.get("lures", None)
        self.rest_until = db_data.get("rest_until", None)
        self.last_allocated = db_data.get("last_allocated", None)
        self.banned = db_data.get("banned", None)
        self.blinded = db_data.get("blinded", None)
        self.times_blinded = db_data.get("times_blinded", 0)
        self.behaviour = db_data.get("behaviour", None)
        self.level = db_data.get("level", None)
        self.allocated = False
        self.username = username
        self.password = password
        self.auth_service = auth_service
        self.args = args
        self.search_interval = search_interval  # todo. use
        self.rest_interval = rest_interval  # todo. use
        self.hashGenerator = hash_generator
        self.failures = 0
        self.consecutive_fails = 0
        identifier = username + password + "fnord"
        self.pgoApi = PGoApi(device_info=(generate_device_info(identifier)))
        self.next_get_map_objects = self.timestamp_ms()
        self.next_gym_details = self.timestamp_ms()
        self.next_encounter = self.timestamp_ms()
        self.last_api = dt.now()
        self.first_login = True
        self.last_location = None
        self.first_map_objects = None
        self.positioned_at = None
        self.remote_config = None
        self.captcha = None
        self.last_active = None
        self.last_location = None
        self.start_time = time.time()
        self.warning = None
        self.tutorials = []
        self.items = {}
        self.pokemons = {}
        self.incubators = []
        self.eggs = []
        self.level = 0
        self.spins = 0
        self.session_spins = 0
        self.walked = 0.0
        self.last_timestamp_ms = 0
        self.remote_config = None
        self.codename = None
        self.team = None

    def reset_defaults(self):
        self['start_time'] = time.time()
        self['warning'] = None
        self['tutorials'] = []
        self['items'] = {}
        self['pokemons'] = {}
        self['incubators'] = []
        self['eggs'] = []
        self['level'] = 0
        self['spins'] = 0
        self['session_spins'] = 0
        self['walked'] = 0.0
        self['last_timestamp_ms'] = 0

    def rest_until(self, when):
        self.rest_until = when

    def set_banned(self):
        if not self.banned:
            self.banned = 1
        else:
            self.banned += 1
        self.set_extra_resting()

    def clear_banned(self):
        if self.banned and self.banned > 0:
            self.banned = 0
            return True
        return False

    def is_resting(self):
        if self.rest_until:
            return self.rest_until > dt.now()

    def is_allocated(self):
        return self.allocated

    def set_resting(self):
        log.info("{} being sent to rest for {} seconds".format(self.username, str(self.rest_interval)))
        self.rest_until = dt.now() + timedelta(seconds=self.rest_interval)

    def set_extra_resting(self):
        to_rest = max(12*3600, self.rest_interval)
        self.rest_until = dt.now() + timedelta(seconds=to_rest)

    def is_banned(self):
        return self.banned and self.banned > 3

    def tryallocate(self):
        if not self.allocated and not self.is_resting() and not self.is_banned():  # currently this is guarded by the lock in account manager
            self.allocated = True
            self.last_allocated = datetime.now()
            return True

    def is_within_existing_alloc_window(self):
        return self.last_allocated and datetime.now() < (self.last_allocated + timedelta(seconds=self.search_interval))

    def try_reallocate(self):
        if not self.allocated and not self.is_banned() and self.is_within_existing_alloc_window():  # currently this is guarded by the lock in account manager
            self.allocated = True
            return True
        return False

    def free(self):
        if not self.allocated:
            raise ValueError("Attempting to release account {} that was not allocated ?".format(self.username))
        self.allocated = False

    def is_available(self):
        return not self.is_resting() and not self.is_allocated()

    def __login_if_needed(self):
        # Activate hashing server
        self.__update_proxies()
        check_login(self.args, self, self.pgoApi, self.current_proxy)
        if self.first_login:
            self.first_login = False

            # Check tutorial completion.
            ''' todo: correlate with account
            if self.args.complete_tutorial:
                tutorial_state = get_tutorial_state(self.args, self.pgoApi, self)

                if not all(x in tutorial_state
                           for x in (0, 1, 3, 4, 7)):
                    log.info('Completing tutorial steps for %s.', self.name())
                    complete_tutorial(self.pgoApi, self, tutorial_state)
                else:
                    log.debug('Account %s already completed tutorial.',
                              self.name())
            '''

    STATUS_CODES = {
        0: 'UNKNOWN',
        1: 'OK',
        2: 'OK_RPC_URL_IN_RESPONSE',
        3: 'BAD_REQUEST',
        4: 'INVALID_REQUEST',
        5: 'INVALID_PLATFORM_REQUEST',
        6: 'REDIRECT',
        7: 'SESSION_INVALIDATED',
        8: 'INVALID_AUTH_TOKEN'
    }

    def force_login(self):
        self.__update_proxies()
        self.pgoApi.login(self.auth_service, self.username, self.password)
        log.info(self.username + " called login API")

    def as_map(self):
        return {"username": self.username, "password": self.password,
                "auth_service": self.auth_service}

    def most_recent_position(self):
        return self.pgoApi.get_position()

    def time_of_most_recent_position(self):
        return self.positioned_at

    def __update_position(self, position):
        self.set_position(position)
        self.pgoApi.set_position(*position)

    def __update_proxies(self):
        self.pgoApi.activate_hash_server(next(self.hashGenerator))
        if self.proxySupplier is not None:
            self.current_proxy = self.proxySupplier(self.current_proxy)

            if self.current_proxy is not None:
                log.debug("Using proxy " + self.current_proxy)
                self.pgoApi.set_proxy(
                    {'http': self.current_proxy, 'https': self.current_proxy})

    @staticmethod
    def timestamp_ms():
        return time.time() * 1000

    @staticmethod
    def __block_for_get_map_objects(self):
        target = max(self.next_get_map_objects, self.next_gym_details)
        current_timestamp = self.timestamp_ms()
        if current_timestamp < target:
            ms_sleep = target - current_timestamp
            to_sleep = math.ceil(ms_sleep / float(1000))
            log.info("Account blocker waiting for {}s".format(to_sleep))
            time.sleep(to_sleep)

    def __block_for_gym_requests(self):
        if self.timestamp_ms() < self.next_gym_details:
            mssleep = self.next_gym_details - self.timestamp_ms()
            time.sleep(math.ceil(mssleep / float(1000)))

    def __block_for_encounter(self):
        if self.timestamp_ms() < self.next_encounter:
            mssleep = self.next_encounter - self.timestamp_ms()
            time.sleep(math.ceil(mssleep / float(1000)))

    def __print_gym(self, gym):
        if gym is None:
            print "Gym is None"
            return
        return str(gym)

    @staticmethod
    def __print_gym_name(gym):
        if gym is None:
            return "(No gym found)"
        name_ = None
        if "name" in gym:
            name_ = gym["name"]
        if name_ is None:
            return "(No name)"
        return name_

    def do_use_item_encounter(self, berry_id, encounter_id, spawn_point_guid):
        return self.pgoApi.use_item_encounter(
            item=berry_id,
            encounter_id=encounter_id,
            spawn_point_guid=spawn_point_guid
        )

    def do_catch_pokemon(self, encounter_id, pokeball, normalized_reticle_size, spawn_point_id, hit_pokemon,
                         spin_modifier, normalized_hit_position):
        response_dict = self.pgoApi.catch_pokemon(
            encounter_id=encounter_id,
            pokeball=pokeball,
            normalized_reticle_size=normalized_reticle_size,
            spawn_point_id=spawn_point_id,
            hit_pokemon=hit_pokemon,
            spin_modifier=spin_modifier,
            normalized_hit_position=normalized_hit_position
        )
        return response_dict

    def do_set_favourite(self, pokemon_uid, favourite):
        self.__update_proxies()
        req = self.pgoApi.create_request()
        x = req.set_favorite_pokemon(pokemon_id=pokemon_uid, is_favorite=favourite)
        x = req.call()
        if self.is_empty_response(x, pokemon_uid):
            raise EmptyResponse(x)
        if self.is_empty_response_100(x):
            raise EmptyResponse(x)
        self.log_if_not_ok_response(x)
        return x

    def do_claim_codename(self, name):
        self.__update_proxies()
        self.__login_if_needed()
        req = self.pgoApi.create_request()
        x = req.claim_codename(codename=name)
        x = req.call()
        return x

    def do_gym_get_info(self, position, gym_position, gym_id):
        try:
            self.__update_proxies()
            self.__update_position(position)
            self.__login_if_needed()
            self.__update_position(self.last_location)  # redundant ?
            req = self.pgoApi.create_request()
            self.__block_for_gym_requests()
            self.last_api = datetime.now()
            x = req.gym_get_info(gym_id=gym_id,
                                 player_lat_degrees=f2i(position[0]),
                                 player_lng_degrees=f2i(position[1]),
                                 gym_lat_degrees=gym_position[0],
                                 gym_lng_degrees=gym_position[1])

            x = req.check_challenge()
            x = req.get_hatched_eggs()
            x = req.get_inventory()
            x = req.check_awarded_badges()
            x = req.download_settings()
            x = req.get_buddy_walked()
            x = req.call()

            if self.is_empty_response(x, gym_id):
                raise EmptyResponse(x)
            if self.is_empty_response_100(x):
                raise EmptyResponse(x)
            return x

        except Exception as e:
            print('Exception while downloading gym details: %s', repr(e))
            raise
        finally:
            self.next_get_map_objects = self.timestamp_ms() + 10000
            self.next_gym_details = \
                (self.timestamp_ms() + 2000) + random.random() * 1000

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        self.__update_proxies()
        self.__update_position(step_location)
        self.__login_if_needed()

        self.__block_for_encounter()
        req30 = self.pgoApi.create_request()
        encounter_result2 = req30.encounter(encounter_id=encounter_id,
                                            spawn_point_id=spawn_point_id,
                                            player_latitude=step_location[
                                                0],
                                            player_longitude=step_location[
                                                1])
        encounter_result2 = req30.check_challenge()
        encounter_result2 = req30.get_hatched_eggs()
        encounter_result2 = req30.get_inventory()
        encounter_result2 = req30.check_awarded_badges()
        encounter_result2 = req30.download_settings()
        encounter_result2 = req30.get_buddy_walked()
        encounter_result2 = req30.call()
        self.next_encounter = (self.timestamp_ms() + 2000) + random.random() * 1000

        log.info(self.username + " called encounter API")

        if self.is_empty_response(encounter_result2, encounter_id):
            log.warn("Payload is " + str(encounter_result2))
            raise EmptyResponse(encounter_result2)
        if self.is_empty_response_100(encounter_result2):
            raise EmptyResponse(encounter_result2)
        if encounter_result2 is None:
            return
        if encounter_result2['status_code'] == 100:
            log.error(
                'Status code 100 usually indicates missing hash key '
                'or coordinate rounding bug in pogo')

        return encounter_result2

    def do_get_inventory(self, timestamp_millis=0):
        req2 = self.pgoApi.create_request()
        req2.get_inventory(timestamp_millis=timestamp_millis)
        inventory_response = req2.call()
        return inventory_response

    def do_get_map_objects(self, position):
        try:
            if position is None:
                sys.exit("need position")
            self.__update_proxies()
            self.__update_position(position)
            self.__login_if_needed()

            lat = position[0]
            lng = position[1]
            cellIds = util.get_cell_ids(lat=lat, long=lng, radius=500)
            cellIdsTs = {}
            for cid in cellIds:
                if cid not in cellIdsTs:
                    cellIdsTs[cid] = 0
            timestamps = [cellIdsTs[cid] for cid in cellIds]

            req = self.pgoApi.create_request()

            self.__block_for_get_map_objects(self)
            self.last_api = datetime.now()
            map_objects = req.get_map_objects(cell_id=cellIds,
                                              since_timestamp_ms=timestamps,
                                              latitude=lat,
                                              longitude=lng)
            map_objects = req.check_challenge()
            map_objects = req.get_hatched_eggs()
            map_objects = req.get_inventory()
            map_objects = req.check_awarded_badges()
            map_objects = req.download_settings()
            map_objects = req.get_buddy_walked()
            map_objects = req.call()
            log.debug(self.username + " called get_map_objects API at (" + str(lat) + "," + str(lng) + ")")

            if self.is_empty_response_100(map_objects):
                raise EmptyResponse(map_objects)
            if not self.has_captcha(map_objects) and not self.most_recent_get_map_objects and self.account_manager:
                self.account_manager.update_initial_inventory(self, inventory_player_stats(map_objects))
            self.most_recent_get_map_objects = map_objects
            return map_objects
        finally:
            self.next_get_map_objects = self.timestamp_ms() + 10000
            self.next_gym_details = \
                (self.timestamp_ms() + 3000) + random.random() * 1000

    def has_position(self):
        return self.most_recent_position() and self.most_recent_position()[0]

    def time_to_location(self, location, meters_per_second_speed):
        if not self.get_position():
            return 0
        distance = vincenty(self.get_position(), location).m
        seconds_since_last_use = dt.now() - self.time_of_most_recent_position()
        remaining_m = distance - (seconds_since_last_use.total_seconds() * meters_per_second_speed)
        if remaining_m > 0:
            return remaining_m / meters_per_second_speed
        else:
            return 0

    def get_position(self):
        return self.last_location

    def set_position(self, position):
        self.positioned_at = datetime.now()
        self.last_location = position

    def log_if_not_ok_response(self, response_dict):
        sc = response_dict['status_code']
        if sc != 1:
            log.warn("Response status code is not OK" + str(self.STATUS_CODES[sc]))

    @staticmethod
    def is_empty_response(response_dict, request):
        status_code_ = response_dict['status_code'] == 3
        if status_code_:
            log.warn("Response is empty (status 3) for " + str(request))
        return status_code_

    @staticmethod
    def is_empty_response_100(response_dict):
        status_code_ = response_dict['status_code'] == 100
        if status_code_:
            log.warn("Response is empty(2) " + str(response_dict))
        return status_code_

    @staticmethod
    def has_captcha(response_dict):
        responses_ = response_dict['responses']
        if 'CHECK_CHALLENGE' not in responses_:
            return False
        captcha_url = responses_['CHECK_CHALLENGE']['challenge_url']
        return len(captcha_url) > 1

    def name(self):
        return self.username

    def status_name(self):
        return self.username

    def status_data(self):
        return {
            'type': 'Worker',
            'message': 'Idle',
            'success': 0,
            'fail': 0,
            'noitems': 0,
            'skip': 0,
            'captcha': 0,
            'username': self.username,
            'proxy_display': '',
            'proxy_url': self.current_proxy,
        }

    def get(self, key, default):
        val = self[key]
        if val:
            return val
        return default

    def __getitem__(self, key):
        if key == 'username' or key == 0:
            return self.username
        if key == 'password' or key == 1:
            return self.password
        if key == 'auth_service' or key == 2:
            return self.auth_service
        if key == 'last_active' or key == 3:
            return self.last_active
        elif key == 'last_location' or key == 4:
            return self.last_location
        elif key == 'captcha' or key == 5:
            return self.captcha
        elif key == 'last_timestamp_ms' or key == 6:
            return self.last_timestamp_ms
        elif key == 'warning' or key == 7:
            return self.warning
        elif key == 'remote_config' or key == 8:
            return self.remote_config
        elif key == 'pokemons' or key == 9:
            return self.pokemons
        elif key == 'walked' or key == 10:
            return self.walked
        elif key == 'start_time' or key == 11:
            return self.start_time
        elif key == 'tutorials' or key == 12:
            return self.tutorials
        elif key == 'items' or key == 13:
            return self.items
        elif key == 'incubators' or key == 14:
            return self.incubators
        elif key == 'eggs' or key == 15:
            return self.eggs
        elif key == 'level' or key == 16:
            return self.level
        elif key == 'spins' or key == 17:
            return self.spins
        elif key == 'session_spins' or key == 18:
            return self.session_spins
        elif key == 'remote_config' or key == 19:
            return self.remote_config
        elif key == 'buddy' or key == 20:
            return self.buddy
        elif key == 'codename' or key == 21:
            return self.codename
        elif key == 'team' or key == 22:
            return self.team
        elif key == 23:
            raise StopIteration
        raise ValueError("Unable to get key {}".format(key))

    def __setitem__(self, key, item):
        if key == 'last_active':
            self.last_active = item
        elif key == 'last_location':
            self.last_location = item
        elif key == 'warning':
            self.warning = item
        elif key == 'tutorials':
            self.tutorials = item
        elif key == 'buddy':
            self.buddy = item
        elif key == 'last_timestamp_ms':
            self.last_timestamp_ms = item
        elif key == 'start_time':
            self.start_time = item
        elif key == 'warning':
            self.warning = item
        elif key == 'tutorials':
            self.tutorials = item
        elif key == 'items':
            self.items = item
        elif key == 'pokemons':
            self.pokemons = item
        elif key == 'incubators':
            self.incubators = item
        elif key == 'eggs':
            self.eggs = item
        elif key == 'level':
            self.level = item
        elif key == 'spins':
            self.spins = item
        elif key == 'session_spins':
            self.session_spins = item
        elif key == 'walked':
            self.walked = item
        elif key == 'remote_config':
            self.remote_config = item
        elif key == 'codename':
            self.codename = item
        elif key == 'team':
            self.team = item
        else:
            raise ValueError("Unable to set key {}".format(key))

    def __str__(self):
        return self.username

    # todo: use ?
    def update_response_failure_state__(self, response_dict):
        if not response_dict:
            self.failures += 1
            self.consecutive_fails += 1
            return True
        else:
            return False

    # Return True if distance between two locs is less than distance in km.
    def in_radius(self, loc1, loc2, distance):
        return self.__equi_rect_distance(loc1, loc2) < distance

    # Return equirectangular approximation distance in km.
    @staticmethod
    def __equi_rect_distance(loc1, loc2):
        R = 6371  # Radius of the earth in km.
        lat1 = math.radians(loc1[0])
        lat2 = math.radians(loc2[0])
        x = (math.radians(loc2[1]) - math.radians(loc1[1])
             ) * math.cos(0.5 * (lat2 + lat1))
        y = lat2 - lat1
        return R * math.sqrt(x * x + y * y)

    def do_pokestop_details(self, fort):
        self.__update_proxies()
        self.__login_if_needed()
        fort_details = self.__pokestop_details_request(fort)
        time.sleep(random.uniform(0.8, 1.8))  # Do not let Niantic throttle
        return fort_details

    def do_spin_pokestop(self, fort, step_location):
        self.__update_proxies()
        self.__update_position(step_location)
        self.__login_if_needed()

        spinning_radius = 0.04

        if self.in_radius((fort['latitude'], fort['longitude']), step_location,
                          spinning_radius):
            log.debug('Attempt to spin Pokestop (ID %s)', fort['id'])

            spin_response = self.__spin_pokestop_request(fort, step_location)
            time.sleep(random.uniform(2, 4))  # Do not let Niantic throttle

            if self.has_captcha(spin_response):
                return

            # todo: this class should not be doing this logic
            spin_result = spin_response['responses']['FORT_SEARCH']['result']
            if spin_result is 1:
                log.debug('Successful Pokestop spin.')
                return spin_response
            elif spin_result is 2:
                log.debug('Pokestop was not in range to spin.')
            elif spin_result is 3:
                log.debug('Failed to spin   Pokestop. Has recently been spun.')
            elif spin_result is 4:
                log.debug('Failed to spin Pokestop. Inventory is full.')
            elif spin_result is 5:
                log.debug('Maximum number of Pokestops spun for this day.')
            else:
                log.debug('Failed to spin a Pokestop. Unknown result %d.', spin_result)

    def __pokestop_details_request(self, fort):
        self.__update_proxies()
        req = self.pgoApi.create_request()
        req.fort_details(
            fort_id=fort['id'],
            latitude=fort['latitude'],
            longitude=fort['longitude'])
        fort_details_response = req.call()
        return fort_details_response

    def __spin_pokestop_request(self, fort, step_location):
        try:
            self.__update_proxies()
            req = self.pgoApi.create_request()
            spin_pokestop_response = req.fort_search(
                fort_id=fort['id'],
                fort_latitude=fort['latitude'],
                fort_longitude=fort['longitude'],
                player_latitude=step_location[0],
                player_longitude=step_location[1])
            spin_pokestop_response = req.check_challenge()
            spin_pokestop_response = req.get_hatched_eggs()
            spin_pokestop_response = req.get_inventory()
            spin_pokestop_response = req.check_awarded_badges()
            spin_pokestop_response = req.download_settings()
            spin_pokestop_response = req.get_buddy_walked()
            spin_pokestop_response = req.call()
            log.info(self.username + " called spin_pokestop API")

            return spin_pokestop_response

        except Exception as e:
            log.warning('Exception while spinning Pokestop: %s', repr(e))
            return False

    def do_collect_level_up(self, current_level):
        self.__update_proxies()
        self.__login_if_needed()

        request = self.pgoApi.create_request()
        request.level_up_rewards(level=current_level)
        response_dict = request.call()

        if 'status_code' in response_dict and response_dict['status_code'] == 1:
            data = (response_dict
                    .get('responses', {})
                    .get('LEVEL_UP_REWARDS', {})
                    .get('items_awarded', []))

            for item in data:
                log.info('level_up_reward {}'.format(str(item)))
        return "OK"

    error_codes = {
        0: 'UNSET',
        1: 'SUCCESS',
        2: 'POKEMON_DEPLOYED',
        3: 'FAILED',
        4: 'ERROR_POKEMON_IS_EGG',
        5: 'ERROR_POKEMON_IS_BUDDY'
    }

    def do_transfer_pokemon(self, pokemon_ids):
        req = self.pgoApi.create_request()
        log.info("{} transfering pokemons {}".format(self.username, str(pokemon_ids)))
        req.release_pokemon(pokemon_ids=pokemon_ids)
        rp = ReleasePokemon(req.call())
        return rp.ok()

    def do_add_lure(self, fort, step_location):
        try:
            self.__update_proxies()
            req = self.pgoApi.create_request()
            add_lure_response = req.add_fort_modifier(
                modifier_type=501,
                fort_id=fort['id'],
                player_latitude=step_location[0],
                player_longitude=step_location[1])
            add_lure_response = req.call()

            result_ = add_lure_response["responses"]["ADD_FORT_MODIFIER"]['result']
            return result_

        except Exception as e:
            log.warning('Exception while adding lure to Pokestop: %s', repr(e))
            return False

    @staticmethod
    def random_sleep(seconds):
        time.sleep(seconds + int(random.random() * 3))

    def do_recycle_inventory_item(self, item_id, count):
        request = self.pgoApi.create_request()
        request.recycle_inventory_item(item_id=item_id, count=count)
        responses = request.call()

        try:
            if responses['responses']['RECYCLE_INVENTORY_ITEM']['result'] != 1:
                log.warning("Failed to remove item {}", item_id)
            else:
                return count
        except KeyError:  # todo align with error handling in general
            log.warning("Failed to remove item {}", item_id)


class WorkingTimeScheduler(DelegatingPogoService):
    def __init__(self, pogoservice, search_interval, account_replacer):
        DelegatingPogoService.__init__(self, pogoservice)
        self.search_interval = search_interval
        self.account_replacer = account_replacer
        self.replace_at = datetime.now() + timedelta(seconds=self.randomized_search_interval())

    def randomized_search_interval(self):
        return self.search_interval + (100 * random.random())

    def do_get_map_objects(self, position):
        if datetime.now() > self.replace_at:
            self.account_replacer.replace_for_sleep()
            self.replace_at = datetime.now() + timedelta(seconds=self.randomized_search_interval())

        return self.target.do_get_map_objects(position)


cannot_be_seen_when_shadowbanned = can_not_be_seen()


class BanChecker(DelegatingPogoService):
    def __init__(self, pogoservice, account_manager, replacer):
        DelegatingPogoService.__init__(self, pogoservice)
        self.account_manager = account_manager
        self.pogoservice = pogoservice
        self.account_replacer = replacer

    @staticmethod
    def is_empty_status_3_response(response_dict):
        status_code_ = response_dict['status_code'] == 3
        return status_code_

    def do_get_map_objects(self, position):
        while True:
            loginfail = False
            toomanylogins = False
            try:
                objects = super(BanChecker, self).do_get_map_objects(position)
            except EmptyResponse as e:
                objects = e.api_result
            except TooManyLoginAttempts:
                toomanylogins = True
            except LoginSequenceFail:
                loginfail = True


            if loginfail or toomanylogins or self.is_empty_status_3_response(objects):
                log.warning(
                    "{} appears to be banned for {} time".format(self.name(), str(self.account_info().banned)))
                if self.account_replacer:
                    self.account_replacer.replace_banned()
                else:
                    raise BannedAccountException
            else:
                if self.account_info().clear_banned() and self.account_manager.usingdb:
                    db_update_account(self.account_info())
                return objects



class CaptchaChecker(DelegatingPogoService):
    def __init__(self, target, account_manager):
        super(CaptchaChecker, self).__init__(target)
        self.account_manager = account_manager

    def do_get_map_objects(self, position):
        return self.with_captcha_solve(lambda: super(CaptchaChecker, self).do_get_map_objects(position))

    def do_gym_get_info(self, position, gym_position, gym_id):
        return self.with_captcha_solve(
            lambda: super(CaptchaChecker, self).do_gym_get_info(position, gym_position, gym_id))

    def do_spin_pokestop(self, fort, step_location):
        return self.with_captcha_solve(lambda: super(CaptchaChecker, self).do_spin_pokestop(fort, step_location))

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        return self.with_captcha_solve(
            lambda: super(CaptchaChecker, self).do_encounter_pokemon(encounter_id, spawn_point_id, step_location))

    def with_captcha_solve(self, fn):
        objects = fn()
        captcha_uri = self.extract_captcha_uri(objects)
        if captcha_uri:
            self.account_manager.solve_captcha(self.account_info(), captcha_uri)
            return fn()
        return objects

    def extract_captcha_uri(self, response_dict):
        responses_ = response_dict['responses']
        if 'CHECK_CHALLENGE' not in responses_:
            log.error("{}:Expected CHECK_CHALLENGE not in response {}".format(self.name(), str(response_dict)))
            return
        captcha_url = responses_['CHECK_CHALLENGE']['challenge_url']

        if len(captcha_url) > 1:
            return captcha_url


class BlindChecker(DelegatingPogoService):
    def __init__(self, pogoservice, account_manager, replacer):
        DelegatingPogoService.__init__(self, pogoservice)
        self.account_manager = account_manager
        self.pogoservice = pogoservice
        self.account_replacer = replacer
        self.blinded = 0

    def do_get_map_objects(self, position):
        objects = super(BlindChecker, self).do_get_map_objects(position)
        if not self.seen_blinded(objects):
            self.blinded += 1
        if self.blinded > 120:
            log.error("Account is blinded {}".format(self.name()))
            if self.account_replacer:
                self.account_replacer.replace_blinded()
            else:
                raise BlindedAccount
            # retry. Might be better to throw an exception
            return super(BlindChecker, self).do_get_map_objects(position)
        return objects

    @staticmethod
    def seen_blinded(map_objects):
        for cell in cells_with_pokemon_data(map_objects):
            for pkmn in nearby_pokemon_from_cell(cell):
                pokemon_id = pkmn["pokemon_id"]
                if pokemon_id in cannot_be_seen_when_shadowbanned:
                    return True
            for pkmn in catchable_pokemon_from_cell(cell):
                pokemon_id = pkmn["pokemon_id"]
                if pokemon_id in cannot_be_seen_when_shadowbanned:
                    return True
        return False


class Humanization(DelegatingPogoService):
    '''Handles humanization and other time-related api constraints'''

    def __init__(self, pogoservice):
        DelegatingPogoService.__init__(self, pogoservice)
        self.pogoservice = pogoservice


class TravelTime(DelegatingPogoService):
    '''Handles travel time related constraint as well as minimum api delay'''

    def __init__(self, pogoservice, fast_speed=18):
        DelegatingPogoService.__init__(self, pogoservice)
        self.pogoservice = pogoservice
        self.next_map_objects = datetime.now()
        self.slow_speed = 9  # 32.5kmh
        self.fast_speed = fast_speed
        self.earliest_next_gmo = datetime.now()

    def do_get_map_objects(self, position):
        if self.account_info().get_position() is not None:
            self.__sleep_for_account_travel(self.account_info(), position)
        now = datetime.now()
        try:
            if now < self.earliest_next_gmo:
                to_sleep = (self.earliest_next_gmo - now).total_seconds()
                log.debug("Sleeping for api constraint {}".format(to_sleep))
                time.sleep(to_sleep)
            return super(TravelTime, self).do_get_map_objects(position)
        finally:
            self.earliest_next_gmo = datetime.now() + timedelta(seconds=10)

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        if self.account_info().get_position() is not None:
            self.__sleep_for_account_travel(self.account_info(), step_location)
        return super(TravelTime, self).do_encounter_pokemon(encounter_id, spawn_point_id, step_location)

    def time_to_location(self, location):
        if not self.account_info().get_position():
            return 0
        distance = vincenty(self.account_info().get_position(), location).m
        seconds_since_last_use = dt.now() - self.account_info().time_of_most_recent_position()
        remaining_m = distance - (seconds_since_last_use.total_seconds() * self.slow_speed)
        if remaining_m > 0:
            return remaining_m / self.slow_speed
        else:
            return 0

    def __log_info(self, msg):
        log.info("%s:" + msg, self.name())

    def __sleep_for_account_travel(self, account, next_location):
        if not account.has_position():
            return
        delay = self.time_to_location(next_location)
        if delay > 30:
            self.__log_info(
                "Moving {} from {} to {}, delaying {} seconds"
                    .format(account.name(),
                            nice_coordinate_string(account.get_position()),
                            nice_coordinate_string(next_location),
                            nice_number(delay)))
        time.sleep(delay)


class AccountReplacer(DelegatingPogoService):
    def __init__(self, pogo_service, account_manager):
        DelegatingPogoService.__init__(self, pogo_service)
        self.account_manager = account_manager

    def replace(self):
        newaccount = self.account_manager.get_account()
        newaccount.update_position(self.target.get_position())
        self.target = newaccount

    def replace_banned(self):
        info = self.target.account_info()
        info.set_banned()
        if self.account_manager.usingdb:
            db_update_account(info)
        return self.replace()

    def replace_blinded(self):
        info = self.target.account_info()
        info.blinded = datetime.now()
        info.times_blinded += 1
        if self.account_manager.usingdb:
            db_update_account(info)
        return self.replace()

    def replace_for_sleep(self):
        info = self.target.account_info()
        info.set_resting()
        if self.account_manager.usingdb:
            db_update_account(info)
        return self.replace()


class NetworkIssueRetryer(DelegatingPogoService):
    def __init__(self, pogoservice):
        DelegatingPogoService.__init__(self, pogoservice)
        self.pogoservice = pogoservice

    def do_set_favourite(self, pokemon_uid, favourite):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_set_favourite(pokemon_uid, favourite))

    def do_use_item_encounter(self, berry_id, encounter_id, spawn_point_guid):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_use_item_encounter(berry_id, encounter_id, spawn_point_guid))

    def do_catch_pokemon(self, encounter_id, pokeball, normalized_reticle_size, spawn_point_id, hit_pokemon,
                         spin_modifier, normalized_hit_position):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_catch_pokemon(encounter_id, pokeball,
                                                                      normalized_reticle_size,
                                                                      spawn_point_id, hit_pokemon, spin_modifier,
                                                                      normalized_hit_position))

    def account_info(self):
        return self.handle_intermittemnt_issues(lambda: super(NetworkIssueRetryer, self).account_info())

    def do_recycle_inventory_item(self, item_id, count):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_recycle_inventory_item(item_id, count))

    def do_pokestop_details(self, fort):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_pokestop_details(fort))


    def do_spin_pokestop(self, fort, step_location):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_spin_pokestop(fort, step_location))

    def do_collect_level_up(self, current_player_level):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_collect_level_up(current_player_level))

    def do_add_lure(self, fort, step_location):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_add_lure(fort, step_location))

    def do_transfer_pokemon(self, pokemon_ids):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_transfer_pokemon(pokemon_ids))

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_encounter_pokemon(encounter_id, spawn_point_id, step_location))

    def do_get_inventory(self, timestamp_millis):
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_get_inventory(timestamp_millis))

    def do_get_map_objects(self, position):
        # todo: find out who does jittering
        return self.handle_intermittemnt_issues(lambda: super(NetworkIssueRetryer, self).do_get_map_objects(position))

    def do_gym_get_info(self, pos, gym_position, gym_id):
        # todo: find out who does jittering
        return self.handle_intermittemnt_issues(
            lambda: super(NetworkIssueRetryer, self).do_gym_get_info(pos, gym_position, gym_id))

    def handle_intermittemnt_issues(self, action):
        return self.__do_with_backoff(lambda: self.__do_with_error_handling(action))

    def __do_with_error_handling(self, action):
        """Return None if action failed and requires retry"""
        try:
            return action()
        except HashingTimeoutException:
            self.__log_warning("HashingTimeoutException")
            time.sleep(30)  # block main thread for a few seconds.
        except BadHashRequestException:
            self.__log_warning("BadHashRequestException")
            time.sleep(30)  # block main thread for a few seconds.
        except NianticThrottlingException:
            self.__log_warning("Being asked to cool down")
            time.sleep(30)  # block main thread for a few seconds.
        except NianticOfflineException:
            self.__log_warning("Niantic offline")
            time.sleep(30)  # block main thread for a few seconds.
        except HashingOfflineException:
            self.__log_warning("Hashing offline")
            time.sleep(2)
        except HashingQuotaExceededException:
            self.__log_warning("Hashing quote exceeded, sleeping for 30 seconds")
            time.sleep(30)
        except ChunkedEncodingError:
            '''ignore silently'''

    @staticmethod
    def __do_with_backoff(thefunc):
        for i in [12,12,12,24,24,24,24,24,24,60,60,60]:
            result = thefunc()
            if result:
                return result
            time.sleep(i)
        raise GaveUpApiAction

    def __log_error(self, msg):
        log.error("%s:" + msg, self.name())

    def __log_warning(self, msg):
        log.warn("%s:" + msg, self.name())

    def __str__(self):
        return str(self.pogoservice)


class CaptchaRequired:
    """Indicates that the account requires a captcha solve"""

    def __init__(self, captcha_url):
        self.captcha_url = captcha_url


class EmptyResponse:
    """Status code 100 and no data"""

    def __init__(self, api_result):
        self.api_result = api_result

class BlindedAccount:

    def __init__(self, api_result):
        self.api_result = api_result
