import logging
import sys
import time
import unittest
from threading import Lock

from geopy.distance import vincenty
from pgoapi.exceptions import HashingOfflineException, \
    NianticThrottlingException, NianticOfflineException, AuthException, \
    NotLoggedInException, BannedAccountException, NianticIPBannedException
from queue import PriorityQueue
from requests.exceptions import ChunkedEncodingError

from getmapobjects import cells_with_pokemon_data, \
    celldiff, update_fort_locations
from management_errors import NoMoreWorkers, TooFarAway, SkippedDueToOptional
from pogom.account import TooManyLoginAttempts
from pogom.transform import jitter_location
from pogoservice import EmptyResponse, CaptchaRequired, BanChecker, NetworkIssueRetryer, \
    WorkingTimeScheduler, AccountReplacer, DelegatingPogoService, BlindChecker, TravelTime, CaptchaChecker

log = logging.getLogger(__name__)

'''
A worker that delegates to an underlying account. The underlying account is
dynamic and may be replaced based on captchas, sleep intervals errors or
similar. Given enough underlying accounts, a worker will normally not fail.

Provide scan method that obey api and KPH speed restrictions, suspending thread
if needed. Client code does not need to know about KPH or 10 second sleep
limits.

Based on distance between previous location, new location and KPH, will
determine earliest legal time scan can be performed and use this.

Transparently handles captchas so clients dont have to see them

'''


class WorkerManager:
    def __init__(self, account_manager, fast_speed, slow_speed):
        self.account_manager = account_manager
        self.free_workers = []
        self.discarded_workers = []
        self.fast_speed = fast_speed
        self.slow_speed = slow_speed
        self.lock = Lock()

    def is_scanning_active(self):
        return self.account_manager.size() > 0

    def get_worker(self):
        self.lock.acquire()
        try:
            if len(self.free_workers) == 0 and self.account_manager.has_free():
                w = Worker(self.account_manager, self.fast_speed)
                return w
            if len(self.free_workers) == 0:
                raise NoMoreWorkers

            worker = self.free_workers[0]
            del self.free_workers[0]
            return worker
        finally:
            self.lock.release()

    def get_worker_for_location(self, location, priority_encounter, optional_encounter):
        self.lock.acquire()
        try:
            if len(self.free_workers) == 0 and self.account_manager.has_free():
                w = Worker(self.account_manager, self.fast_speed)
                return w
            if len(self.free_workers) == 0:
                raise NoMoreWorkers
            nearest_worker = sys.maxint
            index = -1
            withoutpos = 0
            for idx, worker in enumerate(self.free_workers):
                info = worker.account_info()
                seconds_to_reach = info.time_to_location(location, self.slow_speed)
                if seconds_to_reach and seconds_to_reach < nearest_worker:
                    nearest_worker = seconds_to_reach
                    index = idx

            if withoutpos == 0:  # no free capacity, make some decisions
                if nearest_worker > 100 and optional_encounter:
                    raise SkippedDueToOptional(nearest_worker)
                if nearest_worker > 3000 and priority_encounter:
                    log.warn(
                        "Priority encounter and closest CP/IV worker was "
                        + str(nearest_worker) +
                        " meters away from scan point. Increase workers, toggle priorities  or "
                        "decrease number of scan-iv values")
                if nearest_worker > 2000 and not priority_encounter:
                    raise TooFarAway(nearest_worker)

            worker = self.free_workers[index]
            del self.free_workers[index]
            return worker
        finally:
            self.lock.release()

    def discard_worker(self, scanner):
        log.warn(
            "Discarding worker due to (assumed) permanent failures" + scanner)
        self.discarded_workers.append(scanner)

    def free_worker(self, worker):
        self.lock.acquire()
        try:
            if worker not in self.discarded_workers:
                self.free_workers.append(worker)
        finally:
            self.lock.release()


class WorkerQueueManager(object):
    def __init__(self, account_manager, fast_speed, slow_speed, num_queues):
        self.account_manager = account_manager
        self.worker_queues = []
        for i in range(num_queues):
            self.worker_queues.append(WorkerQueue(Worker(account_manager, fast_speed), fast_speed, slow_speed))
        self.discarded_worker_queues = []
        self.lock = Lock()

    def is_scanning_active(self):
        return len(self.worker_queues) > 0

    def get_worker_for_location(self, location, priority_encounter, optional_encounter):
        self.lock.acquire()
        try:
            optimal_queue = sys.maxint
            idx_of_optimal = -1
            for idx, worker in enumerate(self.worker_queues):
                time_to_service = worker.time_to_service(location, optional_encounter, priority_encounter)
                if optimal_queue > time_to_service:
                    optimal_queue = time_to_service
                    idx_of_optimal = idx

            if idx_of_optimal < 0:
                raise NoMoreWorkers
            return self.worker_queues[idx_of_optimal]
        finally:
            self.lock.release()

    def discard_worker(self, scanner):
        log.warn("Discarding worker due to (assumed) permanent failures" + scanner)
        self.discarded_worker_queues.append(scanner)

    def free_worker(self, worker):
        ''' no-op'''


class QueueEntry:
    def __init__(self, location, encounter_id):
        self.location = location
        self.encounter_id = encounter_id


class WorkerQueue:
    queue = PriorityQueue()
    ''' Represents a queue of actions waiting for a given worker '''

    def __init__(self, worker, fast_speed, slow_speed):
        self.slow_speed = slow_speed
        self.fast_speed = fast_speed
        self.worker = worker

    def __str__(self):
        return "WorkerQueue with worker {}, queuelen={}".format(str(self.worker),
                                                                str(self.queue.qsize()))

    def time_to_service(self, location, optional_target, priority_target):
        if self.queue.qsize() > 5:
            return sys.maxint  # dont even consider it, we're probably lagging all across the board

        time_to_service = 0
        currentpos = self.worker.position()
        if not currentpos:
            return 0

        # only considers time as last element in list right now. Deal with priorities later.
        # And consider high-pri items vs zero travel time
        # also consider not moving when encountering close-up
        for queueEntry in self.queue.queue:
            time_to_service += self.travel_time(currentpos, queueEntry.location)
            time_to_service += 3  # for the encounter.
            currentpos = queueEntry.location
        time_to_service += self.travel_time(currentpos, location)
        return time_to_service

    def travel_time(self, current_positon, next_position):
        if not current_positon or current_positon[0] is None:
            return
        distance = vincenty(current_positon, next_position).m
        return self.__sleep_seconds(distance)[1]

    def __sleep_seconds(self, distance):
        slow_seconds = distance / self.slow_speed
        fast_seconds = distance / self.fast_speed

        if slow_seconds < (fast_seconds + 30):
            return "slow", slow_seconds
        else:
            return "fast", fast_seconds

    def blocking_wait(self, encounter_id, location):
        self.enqueue(encounter_id, location)
        self.__wait_for(encounter_id)

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, location):
        self.blocking_wait(encounter_id, location)
        return self.worker.do_encounter_pokemon(encounter_id, spawn_point_id, location)

    def enqueue(self, encounter_id, location):
        if len(self.queue.queue) > 5:
            raise NoMoreWorkers
        self.queue.put(QueueEntry(location, encounter_id))

    def __wait_for(self, encounter_id):
        while self.queue.queue[0].encounter_id != encounter_id:
            time.sleep(3)
        self.queue.get()
        return self.worker


class RocketPool(object):
    def __init__(self, account_manager, fast_speed, slow_speed):
        self.account_manager = account_manager

    def get(self):
        acct = self.account_manager.get_account()
        return wrap_account(acct, self.account_manager)


def wrap_account(account, account_manager):
    replacer = AccountReplacer(account, account_manager)
    retryer = NetworkIssueRetryer(replacer)
    ban_checker = BanChecker(retryer, account_manager, replacer)
    captcha_checker = CaptchaChecker(ban_checker, account_manager)
    blind_checker = BlindChecker(captcha_checker, account_manager, replacer)
    scheduler = WorkingTimeScheduler(blind_checker, account_manager.args.account_search_interval, replacer)
    return TravelTime(scheduler)


def wrap_account_no_replace(account, account_manager):
    retryer = NetworkIssueRetryer(account)
    ban_checker = BanChecker(retryer, account_manager, None)
    captcha_checker = CaptchaChecker(ban_checker, account_manager)
    return TravelTime(captcha_checker)


class Worker(DelegatingPogoService):
    def update_position(self, position):
        return self.account.update_position(position)

    def do_set_favourite(self, pokemon_uid, favourite):
        return self.account.do_set_favourite(pokemon_uid, favourite)

    def account_info(self):
        return self.account.account_info()

    def most_recent_position(self):
        return self.account.most_recent_position()

    def __init__(self, account_manager, fast_speed):
        DelegatingPogoService.__init__(self, wrap_account(account_manager.get_account(), account_manager))
        self.account = self.target
        self.account_manager = account_manager
        self.slow_speed = 9  # 32.5kmh
        self.fast_speed = fast_speed  # 90 kmh
        self.lock = Lock()

    def do_encounter_pokemon(self, encounter_id, spawn_point_id, step_location):
        with self.lock:
            return self.__do_with_backoff(
                lambda: self.__do_with_error_handling(
                    lambda: self.account.do_encounter_pokemon(encounter_id, spawn_point_id, step_location)))

    def do_get_inventory(self, timestamp_millis):
        with self.lock:
            return self.__do_with_backoff(
                lambda: self.__do_with_error_handling(
                    lambda: self.account.do_get_inventory(timestamp_millis)))

    def do_get_map_objects(self, position):
        with self.lock:
            return self.__do_with_backoff(
                lambda: self.__do_with_error_handling(
                    lambda: self.__get_map_objects(position)))

    def __get_map_objects(self, position):
        scan_location = jitter_location(position)
        return self.account.do_get_map_objects(scan_location)

    def do_gym_get_info(self, pos, gym_position, gym_id):
        with self.lock:
            return self.__do_with_backoff(
                lambda: self.__do_with_error_handling(
                    lambda: self.__get_gym_details(pos, gym_position, gym_id)))

    def __get_gym_details(self, pos, gym_position, gym_id):
        scan_location = jitter_location(pos)
        # self.account.set_position(scan_location)
        response = self.account.do_gym_get_info(jitter_location(pos), gym_position, gym_id)
        responses_ = response['responses']
        result = responses_['GYM_GET_INFO']
        if result is None:
            self.__log_error("NO GYM RESULT FOR " + str(gym_id))
        if result['result'] == 2:
            self.__log_error("Trouble: GYM IS TOO FAR AWAY" +
                             str(gym_pos) +
                             str(self.position()))
        if "gym_status_and_defenders" not in result:
            self.__log_error("Trouble: no gym state")
        return result

    def __do_with_error_handling(self, action):
        """Return None if action failed and requires retry"""
        try:
            return action()
        except NianticIPBannedException:
            self.account = wrap_account(self.account_manager.ip_banned(self.account), self.account_manager)
        except BannedAccountException:
            self.account = wrap_account(self.account_manager.replace_banned(self.account.account_info()),
                                        self.account_manager)
        except TooManyLoginAttempts:
            self.account = wrap_account(self.account_manager.replace_banned(self.account.account_info()),
                                        self.account_manager)
        except NotLoggedInException:
            self.__log_error("Not logged in, logging in")
            time.sleep(2)
            self.__do_force_login()
        except EmptyResponse:
            self.__log_warning("Respone is empty, retry in 10 seconds")
            time.sleep(10)
        except AuthException:
            self.__log_error("Auth failed, trying to log in")
            time.sleep(2)
            self.__do_force_login()
        except CaptchaRequired as e:
            self.account = wrap_account(self.account_manager.solve_captcha(self.account,
                                                                           e.captcha_url), self.account_manager)

    def __do_with_backoff(self, thefunc):
        # todo: make a fancier version,
        # incorporate some of the stanard rocketmap retry counters
        result = thefunc()
        if result is None:
            time.sleep(12)
            result = thefunc()
            if result is None:
                time.sleep(20)
                result = thefunc()
                if result is None:
                    time.sleep(60)
                    result = thefunc()
        return result

    def __do_force_login(self):
        try:
            self.account_info().force_login()
        except BannedAccountException:
            self.__log_error("Banned accouhnt3")
            self.account = wrap_account(self.account_manager.replace_banned(self.account.account_info()),
                                        self.account_manager)
        except NianticThrottlingException:
            self.__log_error("Being asked to cool down")
            time.sleep(2)  # block main thread for a few seconds.
        except NianticOfflineException:
            self.__log_warning("Niantic offline")
            time.sleep(2)  # block main thread for a few seconds.
        except EmptyResponse:
            self.__log_warning("Niantic offline")
            time.sleep(2)  # block main thread for a few seconds.
        except HashingOfflineException:
            self.__log_warning("Hashing offline")
            time.sleep(2)
        except TooManyLoginAttempts:
            self.account = \
                wrap_account(self.account_manager.replace_unable_to_login(self.account), self.account_manager)
        except ChunkedEncodingError:
            '''ignore silently'''
        except NotLoggedInException:
            self.account = \
                wrap_account(self.account_manager.replace_unable_to_login(self.account), self.account_manager)
        except AuthException:
            self.account = \
                wrap_account(self.account_manager.replace_unable_to_login(self.account), self.account_manager)

    def __log_error(self, msg):
        log.error("%s:" + msg, self.name())

    def __log_warning(self, msg):
        log.warn("%s:" + msg, self.name())

    def __log_info(self, msg):
        log.info("%s:" + msg, self.name())

    def name(self):
        if self.account:
            return self.account.name()
        else:
            return "Worker without account"

    def position(self):
        if self.account:
            position = self.account.most_recent_position()
            if position and position[0]:
                return position

    def has_position(self):
        return self.account and self.account.has_position()

    prev_cells_with_pokemon = None

    def process_map_objects(self, l5obj):
        cells_with_pokemon = cells_with_pokemon_data(l5obj)
        update_fort_locations(cells_with_pokemon, l5obj)

        result = celldiff(self.prev_cells_with_pokemon, cells_with_pokemon)
        self.prev_cells_with_pokemon = cells_with_pokemon
        return result

    def __str__(self):
        return str(self.account)


class DummyAccount(object):
    def most_recent_position(self):
        return (2.0, 3.0, 4)


class DummyAccount2(object):
    def most_recent_position(self):
        return ()


class DummyAccountManager:
    def __init__(self, account):
        self.account = account

    def get_account(self):
        return self.account


class AccountWithPosition(unittest.TestCase):
    def test(self):
        w = Worker(DummyAccountManager(DummyAccount()), 9)
        self.assertTrue(w.has_position())


class AccountWithoutPosition(unittest.TestCase):
    def test(self):
        w = Worker(DummyAccountManager(DummyAccount2()), 9)
        self.assertFalse(w.has_position())


class WorkerQueue_ServiceTime(unittest.TestCase):
    def test(self):
        worker = Worker(DummyAccountManager(DummyAccount()), 10)
        queue = WorkerQueue(worker)
        queue.enqueue("ABCD", (2.1, 3.1))
        queue.enqueue("EFG-ENC", (2.2, 3.2))
        service = queue.time_to_service((2.3, 3.3))
        d1 = vincenty((2.0, 3.0, 4), (2.1, 3.1)).m
        d2 = vincenty((2.1, 3.1), (2.2, 3.2)).m
        d3 = vincenty((2.2, 3.2), (2.3, 3.3)).m
        print "distance is " + str(d1 + d2 + d3)
        self.assertEqual(4711.4780730903, service)
