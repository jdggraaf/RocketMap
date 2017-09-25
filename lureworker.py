import datetime
import logging
import os
from threading import Lock
from time import sleep

from accountdbsql import db_consume_lures
from argparser import location_parse
from getmapobjects import get_player_level, inventory_elements_by_id, pokstops_within_distance, pokestop_detail
from pogoservice import CaptchaRequired, NetworkIssueRetryer
from workers import wrap_account_no_replace

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)


class LureCounter(object):
    def __init__(self, json_location):
        self.max_lures = json_location.get("max_lures", None)
        self.current_count_file = json_location['name'] + '_lure_count.txt'
        self.lure_count = self.load_lure_count()
        self.lock = Lock()

    def load_lure_count(self):
        if os.path.isfile(self.current_count_file):
            with open(self.current_count_file, 'r') as f:
                for line in f:
                    self.lure_count = int(line)
                    return self.lure_count
        return 0

    def write_lure_count(self):
        with open(self.current_count_file, 'w') as f:
            f.write(str(self.lure_count))

    def use_lure(self):
        with self.lock:
            self.lure_count += 1
            self.write_lure_count()

    def has_more_lures(self):
        return self.lure_count < self.max_lures if self.max_lures else True


class LureWorker(object):
    """A lure dropper that drops lures on one or more locations with a single account (worker).
       Use with multiple positions to quickly empty account for lures
    """

    def __init__(self, account_manager, brander, deploy_more_lures, lure_counter):
        self.account_manager = account_manager
        self.brander = brander
        self.collected = {}
        self.deploy_more_lures = deploy_more_lures
        self.worker = None
        self.inventory = None
        self.stop_names = {}
        self.lured_msg = {}
        self.running = True
        self.lure_counter = lure_counter
        self.next_lure_at = {}

    def replace_worker(self, new_worker):
        self.worker = new_worker
        self.inventory = None

    def safe_get_map_objects(self, pos):
        try:
            objects = self.worker.do_get_map_objects(pos)
            if not self.inventory:
                self.inventory = inventory_elements_by_id(objects)
            return objects
        except CaptchaRequired:
            self.replace_worker(NetworkIssueRetryer(self.account_manager.get_account_with_lures()))
            return self.safe_get_map_objects(pos)

    def worker_with_map_objects(self, pos):
        self.get_worker_with_nonzero_lures(pos)

        map_objects = self.safe_get_map_objects(pos)
        sleep(2)
        if self.worker.name() not in self.collected:
            level = get_player_level(map_objects)
            self.worker.do_collect_level_up(level)
            self.collected[self.worker.name()] = self.worker
            sleep(10)
            map_objects = self.safe_get_map_objects(pos)

        while self.inventory.get(501, 0) == 0:
            log.info("No lures in inventory for worker {}, replacing".format(self.worker.name()))
            db_consume_lures(self.worker.name())
            self.worker = None
            self.get_worker_with_nonzero_lures(pos)
            map_objects = self.safe_get_map_objects(pos)
        return map_objects

    def get_account_with_lures(self, pos):
        worker = wrap_account_no_replace(self.account_manager.get_account(), self.account_manager)
        worker.account_info().update_position(pos)
        if worker.account_info().lures == 0:
            return self.get_account_with_lures(pos)
        return self.brander(worker)

    def get_worker_with_nonzero_lures(self, pos):
        while self.worker is None or self.worker.account_info().lures == 0:
            if self.worker:
                log.info("Skipping {}, lures are spent".format(self.worker.name()))
            self.replace_worker(self.get_account_with_lures(pos))

    def replace_account(self, pos, worker):
        retryer = wrap_account_no_replace(self.account_manager.mark_lures_consumed(worker.name()), self.account_manager)
        retryer.account_info().update_position(pos)
        return retryer

    def sort_by_time(self, route):
        ordered = []
        for item in route:
            parsed_loc = location_parse(item)
            if parsed_loc in self.next_lure_at:
                ordered.append((self.next_lure_at[parsed_loc], item))
            else:
                ordered.append((datetime.datetime.now() + datetime.timedelta(minutes=3), item))

        by_time = sorted(ordered, key=lambda tup: tup[0])
        log.debug("Route metrics {}".format(str(by_time)))
        return [x[1] for x in by_time]

    def lure_json_worker_positions(self, route):
        first_time = True
        self.should_run(False)

        while self.running and self.lure_counter.has_more_lures():
            route_to_use = route if first_time else self.sort_by_time(route)

            initial_pos = location_parse(route_to_use[0])
            pokestop = self.pokestop_at_coordinate(initial_pos)
            if not pokestop:
                self.get_worker_with_nonzero_lures(initial_pos)
            else:
                if self.is_lured_by_us(initial_pos):
                    self.wait_for_lure_to_expire(pokestop, initial_pos)
                else:
                    self.sleep_for_one_expiration_period(initial_pos)

            for pos in route_to_use:
                if not self.should_run(lure_dropped=False):
                    return
                self.lure_one_position_once(location_parse(pos), first_time)

            first_time = False

    def should_run(self, lure_dropped):
        if not self.deploy_more_lures(lure_dropped):
            self.running = False
            return False
        return True

    def lure_one_position_once(self, pos, first_time):
        pokestop = self.pokestop_at_coordinate(pos)

        if not pokestop:
            if self.worker:
                log.info("Worker {} not seeing any pokestops at {}, skipping".format(self.worker.name(), str(pos)))
            self.get_worker_with_nonzero_lures(pos)
            return

        if first_time:
            self.log_first_time_pokestop_info(pokestop)

        if "lure_info" not in pokestop:
            counter = 0
            placed_lure = self.lure_single_stop(pokestop, pos)
            while self.running and not placed_lure and counter < 5:
                sleep(30)
                placed_lure = self.lure_single_stop(pokestop, pos)
                counter += 1

    def pokestop_at_coordinate(self, initial_pos):
        map_objects = self.worker_with_map_objects(pos=initial_pos)
        pokestops = pokstops_within_distance(map_objects, initial_pos, 40)
        return pokestops[0] if len(pokestops) > 0 else None

    @staticmethod
    def lowest_date(current, other):
        if other is None:
            return current
        if current is None:
            return other
        return other if other < current else current

    def lure_single_stop(self, pokestop, pos):
        if "lure_info" not in pokestop:
            lure, pokestop_name = self.lure_stop(pokestop)
            if lure == 4:
                log.info("Replacing worker {} due to code 4, stop {}".format(self.worker.name(), pokestop_name))
                db_consume_lures(self.worker.name())
                self.worker_with_map_objects(pos=pos)
                return False
            elif lure == 2:  # already luredx
                log.info("Pokestop {} is lured(1)".format(pokestop["id"]))
                pass
            elif lure == 3:  # already lured
                log.error("Too far away")
                # raise ValueError("Too far away ??")
            else:
                self.inventory[501] -= 1
                log.info("Added lure to pokestop {}".format(pokestop_name))
                self.lure_counter.use_lure()
                self.next_lure_at[pos] = datetime.datetime.now() + datetime.timedelta(minutes=30)

                self.should_run(lure_dropped=True)
                return True
            sleep(10)
        else:
            return False

    def time_of_lure_expiry(self, next_lure_expiry, pokestop):
        expires_at = datetime.datetime.fromtimestamp(pokestop["lure_info"]["lure_expires_timestamp_ms"] / 1000)
        thrity_seconds_from_now = (datetime.datetime.now() + datetime.timedelta(seconds=30))
        if expires_at <= thrity_seconds_from_now:
            expires_at = thrity_seconds_from_now
        next_lure_expiry = self.lowest_date(next_lure_expiry, expires_at)
        if self.lured_msg.get(pokestop["id"], None) != expires_at:
            self.lured_msg[pokestop["id"]] = expires_at
            log.info("Pokestop {} is lured until {}".format(str(self.stop_names[pokestop["id"]]), str(expires_at)))
        return next_lure_expiry

    def is_lured_by_us(self, pos):
        return pos in self.next_lure_at and datetime.datetime.now() < self.next_lure_at[pos]

    def wait_for_lure_to_expire(self, first_stop, pos):
        if "lure_info" in first_stop:
            log.info("First pokestop in route, waiting for existing lure to expire")

        while first_stop and "lure_info" in first_stop:
            self.sleep_for_one_expiration_period(first_stop)
            map_objects = self.safe_get_map_objects(pos)
            stops = pokstops_within_distance(map_objects, pos, 40)
            first_stop = stops[0] if len(stops) > 0 else None

    def sleep_for_one_expiration_period(self, first_stop):
        if "lure_info" in first_stop:
            expires_at = datetime.datetime.fromtimestamp(first_stop["lure_info"]["lure_expires_timestamp_ms"] / 1000)
            thrity_seconds_from_now = (datetime.datetime.now() + datetime.timedelta(seconds=30))
            if expires_at <= thrity_seconds_from_now:
                expires_at = thrity_seconds_from_now
            seconds = (expires_at - datetime.datetime.now()).seconds
            sleep(seconds)

    def log_first_time_pokestop_info(self, pokestop):
        details = pokestop_detail(self.worker.do_pokestop_details(pokestop))
        pokestop_name = details.get("name", str(details)).encode('utf-8')
        self.stop_names[pokestop["id"]] = pokestop_name
        log.info("Pokestop {} served by {}".format(pokestop_name, self.worker.name()))
        sleep(2)

    def lure_stop(self, pokestop):
        stop_pos = (pokestop["latitude"], pokestop["longitude"])
        pokestop_details = pokestop_detail(self.worker.do_pokestop_details(pokestop))
        sleep(3)
        lure = self.worker.do_add_lure(pokestop, stop_pos)
        pokestop_name = pokestop_details.get("name", str(pokestop_details)).encode('utf-8')
        return lure, pokestop_name
