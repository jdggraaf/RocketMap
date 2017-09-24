import datetime
import logging
import random
from collections import Counter
from threading import Thread
from time import sleep

from accounts import OutOfAccounts
from getmapobjects import get_player_level, inventory_elements_by_id, inrange_gyms
from pogom.apiRequests import feed_pokemon, set_player_team
from pogoservice import CaptchaRequired, NetworkIssueRetryer
from workers import wrap_account_no_replace

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)

'''
Feed with queue controlling workers


'''


class BasicFeeder(object):

    def __init__(self, account_manager, termination_condition):
        self.account_manager = account_manager
        self.collected = {}
        self.termination_checker = termination_condition
        self.worker = None
        self.inventory = None
        self.feedings = Counter()
        self.next_battle_helper = datetime.datetime.now()
        self.gym_name ="(unknown gym name)"
        self.running = True
        self.passives = {}

    def has_berries(self, inventory):
        # 701 = razz, 703 = nanab, 705 = pinap, 706 = golden razz
        return inventory.get(701, 0) != 0 or inventory.get(703, 0) != 0 or inventory.get(705, 0) != 0

    def pokemon_needing_motivation(self, g_gym_info):
        defenders = g_gym_info["responses"]["GYM_GET_INFO"]["gym_status_and_defenders"]["gym_defender"]
        result = [x["motivated_pokemon"]["pokemon"]["id"] for x in defenders if
                  x.get("motivated_pokemon", {}).get("motivation_now", 1.0) < 0.6]
        result_more = [x["motivated_pokemon"]["pokemon"]["id"] for x in defenders if
                  x.get("motivated_pokemon", {}).get("motivation_now", 1.0) < 0.5]
        return result + result_more

    def trainers_in_gym(self, g_gym_info):
        defenders = g_gym_info["responses"]["GYM_GET_INFO"]["gym_status_and_defenders"]["gym_defender"]
        result = [x["trainer_public_profile"]["name"] for x in defenders]
        return result

    def berry_to_use(self, inventory):
        # 701 = razz, 703 = nanab, 705 = pinap, 706 = golden razz
        if inventory.get(701, 0) != 0:
            return 701
        if inventory.get(703, 0) != 0:
           return 703
        if inventory.get(705, 0) != 0:
           return 705
        return None

    def replace_account(self, pos):
        worker = wrap_account_no_replace(self.account_manager.get_account(), self.account_manager)
        worker.account_info().update_position(pos)
        self.worker = worker
        self.inventory = None
        self.feedings = Counter()
        return worker

    def safe_get_map_objects(self, pos):
        try:
            return self.worker.do_get_map_objects(pos)
        except CaptchaRequired:
            self.worker = NetworkIssueRetryer(self.account_manager.get_account_with_lures())
            return self.safe_get_map_objects(pos)

    def worker_with_map_objects(self, pos, team):
        if self.worker is None:
            self.replace_account(pos)

        map_objects = self.safe_get_map_objects(pos)
        if not self.inventory:
            self.inventory = inventory_elements_by_id(map_objects)

        while self.worker.account_info()["team"] != 0 and self.worker.account_info()["team"] != team:
            if self.worker:
                log.info("Skipping {}, wrong team on gym {}".format(self.worker.name(), self.gym_name))
            self.replace_account(pos)
            map_objects = self.safe_get_map_objects(pos)
            self.inventory = inventory_elements_by_id(map_objects)

        if self.worker.account_info()["team"] == 0:
            sleep(10)
            res = set_player_team(self.worker.get_raw_api(),self.worker.account_info(), 1) # mystic
            sleep(5)
        sleep(2)
        if self.worker.name() not in self.collected:
            level = get_player_level(map_objects)
            self.worker.do_collect_level_up(level)
            self.collected[self.worker.name()] = self.worker
            sleep(10)
            map_objects = self.safe_get_map_objects(pos)

        by_id = inventory_elements_by_id(map_objects)
        while not self.has_berries(by_id):
            log.info("No berries in inventory for worker {}, replacing".format(self.worker.name()))
            self.replace_account(pos)
            map_objects = self.safe_get_map_objects(pos)
            self.inventory = inventory_elements_by_id(map_objects)
            by_id = inventory_elements_by_id(map_objects)
        return map_objects


class FeedWorker(BasicFeeder):

    def __init__(self, account_manager, termination_condition, trainers, heavy):
        BasicFeeder.__init__(self, account_manager, termination_condition)
        self.trainers = trainers
        self.next_battle_helper = datetime.datetime.now()
        self.heavy_defense = heavy

    def berry_positions(self, positions):
        first_time = True
        while self.running:
            need_motivation = []
            seconds = 3600
            for pos in positions:
                if self.termination_checker():
                    return True
                next_seconds = self.berry_gym(pos, first_time)
                seconds = min(next_seconds, seconds)

            for x in self.feedings:
                if self.feedings[x] == 2:
                    log.info("Acount has been feeding enough, changing account for {}".format(self.gym_name))
                    self.replace_account(positions[0])
            first_time = False

            next_check = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
            log.info("Waiting {} seconds until {} for next event".format(str(seconds), str(next_check)))
            sleep(seconds)

    def contains_trainers(self, gym_info):
        trainers_in_gym = self.trainers_in_gym(gym_info)
        for x in self.trainers:
            if x == "*" or x in trainers_in_gym:
                return True
        return False

    def berry_gym(self, pos, first_time):
        map_objects = self.worker_with_map_objects(pos=pos, team=1)
        gyms = inrange_gyms(map_objects, pos)
        if len(gyms) == 0:
            if self.worker:
                log.info("Worker {} not seeing any gyms, skipping".format(self.worker.name()))
            self.replace_account(pos)
            map_objects = self.worker_with_map_objects(pos=pos, team=1)
            gyms = inrange_gyms(map_objects, pos)

        if len(gyms) == 0:
            log.info("Worker {} not seeing any gyms, at this coordinate, exiting".format(self.worker.name()))
            self.running = False
            return 10

        gym = gyms[0]
        id_ = gym["id"]
        if gym.get("owned_by_team", 0) != 1:
            if id_ not in self.passives:
                log.info("{} is being held by the wrong team. Waiting for the good guys".format(self.gym_name))
                self.passives[id_] = id_
            return 120

        if first_time:
            log.info(
                "There are {} gyms in range for {} at {}.".format(str(len(gyms)), self.worker.name(),
                                                                               str(pos)))

        gym_pos = gym['latitude'], gym['longitude']

        gym_get_info = self.worker.do_gym_get_info(pos, gym_pos, id_)
        gym_get_info_data = gym_get_info["responses"]["GYM_GET_INFO"]
        self.gym_name = gym_get_info_data["name"].encode('utf-8')
        gym_status_and_defenders = gym_get_info_data["gym_status_and_defenders"]
        pokemon_for_proto = gym_status_and_defenders["pokemon_fort_proto"]
        raid_info = pokemon_for_proto.get("raid_info", {})
        raid_battle = datetime.datetime.fromtimestamp(raid_info.get("raid_battle_ms", 1) / 1000)
        raid_end = datetime.datetime.fromtimestamp(raid_info.get("raid_end_ms", 1) / 1000)

        if raid_battle < datetime.datetime.now() < raid_end:
            diff = (raid_end - datetime.datetime.now()).total_seconds()
            if id_ not in self.passives:
                log.info("Gym {} is closed for raid until {}, sleeping {}".format(self.gym_name, str(raid_end), diff))
                self.passives[id_] = id
            return diff

        if "is_in_battle" in pokemon_for_proto:
            if datetime.datetime.now() > self.next_battle_helper:
                helper_end = datetime.datetime.now() + datetime.timedelta(minutes=30)
                self.next_battle_helper = helper_end
                log.info("Starting battle helper to work with gym {} under attack".format(self.gym_name))
                ld = FeedWorker(self.account_manager, lambda: datetime.datetime.now() > helper_end, self.trainers, self.heavy_defense)
                ld.next_battle_helper = helper_end
                berry_location( pos, ld)
                if self.heavy_defense:
                    log.info("Starting additional battle helper to work with gym {} under attack".format(self.gym_name))
                    heavy = FeedWorker(self.account_manager, lambda: datetime.datetime.now() > helper_end, self.trainers,
                                    self.heavy_defense)
                    heavy.next_battle_helper = helper_end
                    berry_location(pos, heavy)
                    heavy2 = FeedWorker(self.account_manager, lambda: datetime.datetime.now() > helper_end, self.trainers,
                                    self.heavy_defense)
                    heavy2.next_battle_helper = helper_end
                    berry_location(pos, heavy2)

        need_motivation = self.pokemon_needing_motivation( gym_get_info)

        if not self.contains_trainers( gym_get_info):
            if id_ not in self.passives:
                log.info("Trainers not in gym {}, waiting".format(self.gym_name))
                self.passives[id_] = id_
            return 1800 + random.uniform(0, 900)

        if id_ in self.passives: del self.passives[id_]

        if len(need_motivation) > 0:
            sleep(5)
        for pokemon in need_motivation:
            berry = self.berry_to_use(self.inventory)
            if not berry:
                log.info("No more berries on account, replacing")
                self.replace_account(pos)
            count = self.inventory.get(berry)
            ret = feed_pokemon(self.worker.get_raw_api(), self.worker.account_info(), berry, pokemon, id_, pos,
                               count)
            self.inventory[berry] -= 1
            log.info("Fed pokemon {} berry {} ({} remaining) on gym {}".format(pokemon, berry, self.inventory[berry], self.gym_name))
            if ret["responses"]["GYM_FEED_POKEMON"].SUCCESS != 1:
                print "Not successful " + (str(ret))
            else:
                self.feedings[pokemon] += 1
            sleep(2)

        return 12 if len(need_motivation) > 0 else 120


def berry_location(loc, worker):
    the_thread = Thread(target=lambda: safe_berry_one_position(loc, worker))
    the_thread.start()
    return the_thread


def safe_berry_one_position(pos, worker):
    while True:
        try:
            if worker.berry_positions([pos]):
                return
            sleep(60)
        except OutOfAccounts:
            log.warn ("No more accounts, exiting")
            return
        except Exception as e:
            log.exception(e)
            sleep(12)
