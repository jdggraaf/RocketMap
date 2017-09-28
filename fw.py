import datetime
import logging
import random
from Queue import Empty, Queue
from collections import Counter
from threading import Thread
from time import sleep

from accounts import OutOfAccounts
from getmapobjects import inrange_gyms
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
        self.gym_name = "(unknown gym name)"
        self.running = True
        self.passives = {}
        self.first_berried = {}
        self.slaves = []
        self.replaced = 0

    @staticmethod
    def has_berries(inventory):
        # 701 = razz, 703 = nanab, 705 = pinap, 706 = golden razz
        return inventory.get(701, 0) != 0 or inventory.get(703, 0) != 0 or inventory.get(705, 0) != 0

    @staticmethod
    def pokemon_needing_motivation(g_gym_info):
        info_ = g_gym_info["responses"]["GYM_GET_INFO"]
        defenders = info_.gym_status_and_defenders.gym_defender
        result = [x.motivated_pokemon for x in defenders if
                  x.motivated_pokemon.motivation_now < 0.7]
        return result

    @staticmethod
    def trainers_in_gym(g_gym_info):
        info_ = g_gym_info["responses"]["GYM_GET_INFO"]
        defenders = info_.gym_status_and_defenders.gym_defender
        result = [x.trainer_public_profile.name for x in defenders]
        return result

    @staticmethod
    def berry_to_use(inventory):
        # 701 = razz, 703 = nanab, 705 = pinap, 706 = golden razz
        if inventory.get(701, 0) != 0:
            return 701
        if inventory.get(703, 0) != 0:
            return 703
        if inventory.get(705, 0) != 0:
            return 705
        return None


    def safe_get_map_objects(self, pos):
        try:
            return self.worker.do_get_map_objects(pos)
        except CaptchaRequired:
            self.worker = NetworkIssueRetryer(self.account_manager.get_account_with_lures())
            return self.safe_get_map_objects(pos)

    def replace_account(self, pos):
        self.replaced += 1
        if self.replaced % 20 == 0:
            log.warning("Sleeping 5 minutes because replaced over 20 account")
            sleep(300)
        worker = wrap_account_no_replace(self.account_manager.get_account(), self.account_manager)
        worker.account_info().update_position(pos)
        self.worker = worker
        self.inventory = None
        self.feedings = Counter()

        map_objects = self.safe_get_map_objects(pos)
        if not self.inventory:
            self.inventory = self.worker.account_info()["items"]

        map_objects = self.check_team(map_objects, pos, team=1)

        return worker

    def worker_with_map_objects(self, pos, team):
        if self.worker is None:
            self.replace_account(pos)

        map_objects = self.safe_get_map_objects(pos)
        if not self.inventory:
            self.inventory = self.worker.account_info()["items"]

        map_objects = self.check_team(map_objects, pos, team)

        if self.worker.account_info()["team"] == 0:
            sleep(10)
            set_player_team(self.worker.get_raw_api(), self.worker.account_info(), 1)  # mystic
            sleep(5)
        sleep(2)
        if self.worker.name() not in self.collected:
            level = self.worker.account_info()["level"]
            self.worker.do_collect_level_up(level)
            self.collected[self.worker.name()] = self.worker
            sleep(10)
            map_objects = self.safe_get_map_objects(pos)

        while not self.has_berries(self.inventory):
            log.info("No berries in inventory for worker {}, replacing".format(self.worker.name()))
            self.replace_account(pos)
            map_objects = self.safe_get_map_objects(pos)
            self.inventory = self.worker.account_info()["items"]
        return map_objects

    def check_team(self, map_objects, pos, team):
        while self.worker.account_info()["team"] != 0 and self.worker.account_info()["team"] != team:
            if self.worker:
                log.info("Skipping {}, wrong team on gym {}".format(self.worker.name(), self.gym_name))
            self.replace_account(pos)
            map_objects = self.safe_get_map_objects(pos)
            self.inventory = self.worker.account_info()["items"]
        return map_objects


class FeedWorker(BasicFeeder):
    def __init__(self, account_manager, termination_condition, trainers, defend, heavy):
        BasicFeeder.__init__(self, account_manager, termination_condition)
        self.defend = defend
        self.trainers = trainers
        self.next_battle_helper = datetime.datetime.now()
        self.heavy_defense = heavy
        self.good_pokemon = {242, 143, 208, 149, 80, 199, 197, 131, 134, 232, 181, 36}
        self.defense_duration = 60 if heavy else 30

    def berry_positions(self, positions):
        first_time = True
        while self.running:
            seconds = 3600
            for pos in positions:
                if self.termination_checker():
                    return True
                next_seconds = self.berry_gym(pos, first_time)
                seconds = min(next_seconds, seconds)

            for x in self.feedings:
                if self.feedings[x] == random.choice([2,3]):
                    log.info("Acount has been feeding enough, changing account for {}".format(self.gym_name))
                    self.replace_account(positions[0])
            first_time = False
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
        id_ = gym.id

        gym_pos = gym.latitude, gym.longitude
        gym_get_info = self.worker.do_gym_get_info(pos, gym_pos, id_)
        gym_get_info_data = gym_get_info["responses"]["GYM_GET_INFO"]
        self.gym_name = gym_get_info_data.name.encode('utf-8')

        if gym.owned_by_team != 1:
            if id_ not in self.passives:
                log.info("{} is being held by the wrong team. Waiting for the good guys".format(self.gym_name))
                self.passives[id_] = id_
            return 120

        if first_time:
            log.info(
                "There are {} gyms in range for {} at {}.".format(str(len(gyms)), self.worker.name(),
                                                                  str(pos)))

        gym_status_and_defenders = gym_get_info_data.gym_status_and_defenders
        pokemon_for_proto = gym_status_and_defenders.pokemon_fort_proto
        raid_info = pokemon_for_proto.raid_info
        raid_battle = datetime.datetime.fromtimestamp(raid_info.raid_battle_ms / 1000)
        raid_end = datetime.datetime.fromtimestamp(raid_info.raid_end_ms / 1000)

        if raid_battle < datetime.datetime.now() < raid_end:
            diff = (raid_end - datetime.datetime.now()).total_seconds()
            if id_ not in self.passives:
                log.info("Gym {} is closed for raid until {}, sleeping {}".format(self.gym_name, str(raid_end), diff))
                self.passives[id_] = id
            return diff

        if not self.contains_trainers(gym_get_info):
            if id_ not in self.passives:
                log.info("Trainers not in gym {}, waiting".format(self.gym_name))
                self.passives[id_] = id_
            return 1200 + random.uniform(0, 900)
        elif first_time:
            log.info("Trainers in gym {}".format(self.gym_name))


        need_motivation = self.pokemon_needing_motivation(gym_get_info)

        if id_ in self.passives:
            del self.passives[id_]

        if len(need_motivation) == 0:
            return 120

        if len(need_motivation) > 0:
            if id_ not in self.first_berried:
                self.first_berried[id_] = datetime.datetime.now()
            sleep(5)

        filtered_needy = self.filter_motivation(id_, need_motivation)

        if len(filtered_needy) > 0 or (pokemon_for_proto.is_in_battle and self.defend):
            if datetime.datetime.now() > self.next_battle_helper:
                helper_end = datetime.datetime.now() + datetime.timedelta(minutes=self.defense_duration)
                self.next_battle_helper = helper_end
                self.slaves = []
                log.info("STARTING FEEDER SLAVES for {} {}"
                         .format(self.gym_name, "Under ATTACK" if pokemon_for_proto.is_in_battle else ""))
                self.add_feed_slave(pos, helper_end)
                self.add_feed_slave(pos, helper_end)
                self.add_feed_slave(pos, helper_end)
                self.add_feed_slave(pos, helper_end)
                if self.heavy_defense:
                    log.info("Starting additional battle slaves to work with gym {} under attack".format(self.gym_name))
                    self.add_feed_slave(pos, helper_end)
                    self.add_feed_slave(pos, helper_end)
                    self.add_feed_slave(pos, helper_end)
                    self.add_feed_slave(pos, helper_end)
                    sleep(30)  # extra time for heavy defense

                sleep(70)  # give workers time to start

        for motivated_pokemon in filtered_needy:
            for slave_queue in self.slaves:
                slave_queue.put(motivated_pokemon)

            berry = self.berry_to_use(self.inventory)
            if not berry:
                log.info("No more berries on account, replacing")
                self.replace_account(pos)
            count = self.inventory.get(berry)
            ret = feed_pokemon(self.worker.get_raw_api(), self.worker.account_info(), berry,
                               motivated_pokemon.pokemon.id, id_, pos,
                               count)
            self.inventory[berry] -= 1
            log.info("Fed pokemon {}/{} berry {} ({} remaining) on gym {}".format(motivated_pokemon.pokemon.id,
                                                                                  motivated_pokemon.pokemon.pokemon_id,
                                                                                  berry,
                                                                                  self.inventory[berry],
                                                                                  self.gym_name))
            if ret["responses"]["GYM_FEED_POKEMON"].SUCCESS != 1:
                print "Not successful " + (str(ret))
            else:
                self.feedings[motivated_pokemon.pokemon.id] += 1
            sleep(2)

        return 12 if len(need_motivation) > 0 else 120

    def add_feed_slave(self, pos, helper_end):
        q = Queue()
        self.slaves.append(q)
        ld = FeedSlave(self.account_manager, lambda: datetime.datetime.now() > helper_end, q)
        ld.next_battle_helper = helper_end
        start_slave(pos, ld)
        sleep(5)
        return ld

    def filter_motivation(self, gym_id_, need_motivation):
        first_fed = self.first_berried[gym_id_]
        hours_in_gym = ((datetime.datetime.now() - first_fed).total_seconds()) / 3600
        return [x for x in need_motivation if self.should_feed(x, hours_in_gym)]

    def should_feed(self ,motivated_pokemon, hours_in_gym):
        pokemon = motivated_pokemon.pokemon
        if pokemon.owner_name in self.trainers:
            return random.uniform(0,1) < (5/hours_in_gym)
        if pokemon.pokemon_id in self.good_pokemon:
            return random.uniform(0,1) < (4/hours_in_gym)
        if pokemon.individual_attack == 15 and pokemon.individual_defense == 15 and pokemon.individual_stamina == 15:
            return random.uniform(0,1) < (3/hours_in_gym)
        return random.uniform(0, 1) < (2 / hours_in_gym)


class FeedSlave(BasicFeeder):
    def __init__(self, account_manager, termination_condition, queue):
        BasicFeeder.__init__(self, account_manager, termination_condition)
        self.queue = queue
        self.log = logging.getLogger("feedslave")

    def slave_task(self, pos):
        map_objects = self.worker_with_map_objects(pos=pos, team=1)
        gyms = inrange_gyms(map_objects, pos)
        if len(gyms) == 0:
            if self.worker:
                self.log.info("Worker {} not seeing any gyms, skipping".format(self.worker.name()))
            self.replace_account(pos)
            map_objects = self.worker_with_map_objects(pos=pos, team=1)
            gyms = inrange_gyms(map_objects, pos)

        if len(gyms) == 0:
            self.log.info("Worker {} not seeing any gyms, at this coordinate, exiting".format(self.worker.name()))
            self.running = False
            return 10

        gym = gyms[0]
        id_ = gym.id

        gym_pos = gym.latitude, gym.longitude
        gym_get_info = self.worker.do_gym_get_info(pos, gym_pos, id_)
        gym_get_info_data = gym_get_info["responses"]["GYM_GET_INFO"]
        self.gym_name = gym_get_info_data.name.encode('utf-8')

        while self.running:
            try:
                motivated_pokemon = self.queue.get(block=True, timeout=60)

                berry = self.berry_to_use(self.inventory)
                if not berry:
                    self.log.info("No more berries on account, replacing")
                    self.replace_account(pos)
                count = self.inventory.get(berry)
                ret = feed_pokemon(self.worker.get_raw_api(), self.worker.account_info(), berry,
                                   motivated_pokemon.pokemon.id, id_, pos,
                                   count)
                if berry in self.inventory:
                    self.inventory[berry] -= 1
                remaining = self.inventory.get(berry, 0)
                log.info("Fed pokemon {} berry {} ({} remaining) on gym {}".format(motivated_pokemon.pokemon.id, berry,
                                                                                   remaining,
                                                                                   self.gym_name))
                if ret["responses"]["GYM_FEED_POKEMON"].SUCCESS != 1:
                    self.log.warn("Not successful " + (str(ret)))
                else:
                    self.feedings[motivated_pokemon.pokemon.id] += 1
                sleep(2)

                self.queue.task_done()

                sleep(10)

                for x in self.feedings:
                    if self.feedings[x] == 2:
                        self.log.info("Acount has been feeding enough, changing account for {}".format(self.gym_name))
                        self.replace_account(pos)
            except Empty:
                if self.termination_checker():
                    self.running = False



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
            log.warn("No more accounts, exiting")
            return
        except Exception as e:
            log.exception(e)
            sleep(12)


def start_slave(loc, worker):
    the_thread = Thread(target=lambda: safe_slave_task(loc, worker))
    the_thread.start()
    return the_thread


def safe_slave_task(pos, worker):
    while True:
        try:
            if worker.slave_task(pos):
                return
            sleep(60)
        except OutOfAccounts:
            log.warn("No more accounts, exiting")
            return
        except Exception as e:
            log.exception(e)
            sleep(12)
