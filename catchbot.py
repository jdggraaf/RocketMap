import logging
import random
from Queue import Queue
from datetime import datetime, timedelta
from threading import Thread, Lock
from time import sleep

from accounts import AccountManager
from behaviours import beh_catch_pokemon, beh_handle_level_up
from getmapobjects import find_catchable_encounter
from management_errors import NoMoreWorkers
from workers import wrap_account

log = logging.getLogger(__name__)


class CatchBot():
    def __init__(self, args, instance_name, accounts_filename):
        self.args = args
        self.instance_name = instance_name
        self.accounts_filename = accounts_filename
        self.account_manager = None
        self.reset()
        self.threads = []
        self.lock = Lock()
        self.spawn = None

    def is_done(self):
        return not self.account_manager.has_free()

    def reset(self):
        self.account_manager = AccountManager(self.instance_name, True, self.args, [], [], Queue(), {})
        self.account_manager.initialize(self.accounts_filename, ())


    def start_threads(self, numthreads):
        for i in range(0, numthreads):
            t = Thread(target=self.__process_spawns, name='bot_thread_{}'.format(str(i).zfill(3)))
            t.daemon = True
            t.start()
            self.threads.append(t)

    def give_spawn(self, encounter_id, spawn_point_id, pos):
        with self.lock:
            if not self.spawn:
                self.spawn = encounter_id, spawn_point_id, pos
            else:
                log.info("Leveler bot is already running with different encounter")

    def __process_spawns(self):
        while not self.spawn:
            sleep(20)
        try:
            self.__process_one_spawn(self.spawn[0], self.spawn[1], self.spawn[2])
        except NoMoreWorkers:
            self.reset()



    def __process_one_spawn(self, encounter_id, spawn_point_id, pos):
        # assume available for 25 minutes
        until = datetime.now() + timedelta(minutes=25)

        while datetime.now() < until:
            account = self.account_manager.get_account()
            worker = wrap_account(account, self.account_manager)
            attempts = 0

            map_objects = worker.do_get_map_objects(pos)
            level = worker.account_info()["level"]

            rnd_sleep(4)
            while not find_catchable_encounter(map_objects, encounter_id) and attempts < 4:
                rnd_sleep(12)
                map_objects = worker.do_get_map_objects(pos)
                attempts += 1

            if attempts < 4:
                pokemon = beh_catch_pokemon(worker, pos, encounter_id, spawn_point_id)
                if pokemon:
                    return
                rnd_sleep(10)
            map_objects = worker.do_get_map_objects(pos)
            beh_handle_level_up(worker, level)
        self.spawn = None


def rnd_sleep(sleep_time):
    random_ = sleep_time + int(random.random() * 2)
    sleep(random_)
