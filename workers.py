import logging
import sys
import time
import unittest
from threading import Lock

from geopy.distance import vincenty
from queue import PriorityQueue

from management_errors import NoMoreWorkers
from pogoservice import BanChecker, NetworkIssueRetryer, \
    WorkingTimeScheduler, AccountReplacer, BlindChecker, TravelTime, CaptchaChecker, ApiDelay

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


class WorkerQueueManager(object):
    def __init__(self, account_manager, fast_speed, slow_speed, num_queues):
        self.account_manager = account_manager
        self.worker_queues = []
        for i in range(num_queues):
            account = account_manager.get_account()
            worker = wrap_account(account, account_manager)
            self.worker_queues.append(WorkerQueue(worker, fast_speed, slow_speed))
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

    def time_to_service(self, location):
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
    api_delayed = ApiDelay(replacer)
    retryer = NetworkIssueRetryer(api_delayed)
    ban_checker = BanChecker(retryer, account_manager, replacer)
    captcha_checker = CaptchaChecker(ban_checker, account_manager)
    blind_checker = BlindChecker(captcha_checker, account_manager, replacer)
    scheduler = WorkingTimeScheduler(blind_checker, account_manager.args.account_search_interval, replacer)
    return TravelTime(scheduler)


def wrap_account_no_replace(account, account_manager):
    api_delayed = ApiDelay(account)
    retryer = NetworkIssueRetryer(api_delayed)
    ban_checker = BanChecker(retryer, account_manager, None)
    captcha_checker = CaptchaChecker(ban_checker, account_manager)
    return TravelTime(captcha_checker)


def wrap_accounts_minimal(account, account_manager):
    api_delayed = ApiDelay(account)
    retryer = NetworkIssueRetryer(api_delayed)
    captcha_checker = CaptchaChecker(retryer, account_manager)
    return TravelTime(captcha_checker)

class DummyAccount(object):
    def most_recent_position(self):
        return (2.0, 3.0, 4)


class DummyAccount2(object):
    def most_recent_position(self):
        return ()


class DummyArgs:
    account_search_interval = 299

class DummyAccountManager:
    def __init__(self, account):
        self.account = account
        self.args = DummyArgs()

    def get_account(self):
        return self.account


class WorkerQueue_ServiceTime(unittest.TestCase):
    def test(self):
        manager = DummyAccountManager(DummyAccount())
        worker = wrap_account(DummyAccount(), manager)
        queue = WorkerQueue(worker,15,9)
        queue.enqueue("ABCD", (2.1, 3.1))
        queue.enqueue("EFG-ENC", (2.2, 3.2))
        service = queue.time_to_service((2.3, 3.3))
        d1 = vincenty((2.0, 3.0, 4), (2.1, 3.1)).m
        d2 = vincenty((2.1, 3.1), (2.2, 3.2)).m
        d3 = vincenty((2.2, 3.2), (2.3, 3.3)).m
        print "distance is " + str(d1 + d2 + d3)
        self.assertEqual(4711.4780730903, service)
