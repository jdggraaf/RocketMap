import csv
import datetime
import logging
from Queue import Queue

from concurrent.futures import ThreadPoolExecutor, as_completed

from accountdbsql import set_account_db_args, db_find_allocatable_by_level, db_roll_allocated_date_forward
from accounts import AccountManager
from argparser import std_config, add_threads_per_proxy, add_system_id, add_use_account_db, add_search_rest, \
    location_parse, add_webhooks, load_proxies
from argutils import thread_count
from common_blindcheck import check_worker_for_future
from accountmanager import args

log = logging.getLogger(__name__)

load_proxies(args)


args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}
set_account_db_args(args)

account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {}, replace_warned=False)


# account_manager.initialize(args.accountcsv, ())

def find_accounts():
    temp_ban_time = datetime.datetime.now() - datetime.timedelta(days=10)
    warn_time = datetime.datetime.now() - datetime.timedelta(days=10)
    blind_time = datetime.datetime.now() - datetime.timedelta(days=10)

    pool = ThreadPoolExecutor(thread_count(args))  # for many urls, this should probably be

    allocatable = db_find_allocatable_by_level(temp_ban_time, warn_time, blind_time, datetime.datetime.now(),
                                               args.min_level, args.max_level)
    result = []

    requred_accounts = int(args.count)
    futures = []

    account_iter = iter(allocatable)

    def next_account():
        return account_manager.add_account(next(account_iter))

    location = location_parse(args.location)
    for idx in range(0, requred_accounts):
        futures.append(
            pool.submit(lambda: check_worker_for_future(next_account(), account_manager, location_to_use=location)))

    completed = as_completed(futures)
    future_pos = 0
    while len(result) < requred_accounts:
        if future_pos > len(futures):
            raise AssertionError("This should not happen, maybe previous error is making it happen")
        r = futures[future_pos].result()
        if r[0]:
            result.append(r[1])
        else:
            db_roll_allocated_date_forward(r[1])
            futures.append(
                pool.submit(lambda: check_worker_for_future(next_account(), account_manager, location_to_use=location)))
        future_pos += 1
    return result


def write_rocketmap_accounts_file(accounts):
    from collections import OrderedDict
    ordered_fieldnames = OrderedDict(
        [('provider', None), ('username', None), ('password', None)])
    with open(args.accountcsv, 'wb') as fou:
        dw = csv.DictWriter(fou, delimiter=',', fieldnames=ordered_fieldnames, extrasaction='ignore')
        for acct in accounts:
            dw.writerow(acct)

def as_map(account):
    res = {"username": account.name(), "password": account.password, "provider": account.auth_service}
    return res

def write_monocle_accounts_file(accounts):
    from collections import OrderedDict
    ordered_fieldnames = OrderedDict(
        [('provider', None), ('username', None), ('password', None), ('model', None), ('iOS', None), ('id', None)])
    with open(args.accountcsv, 'wb') as fou:
        dw = csv.DictWriter(fou, delimiter=',', fieldnames=ordered_fieldnames)
        dw.writeheader()
        for acct in accounts:
            dw.writerow(as_map(acct))


accts = find_accounts()
if args.format == "monocle":
    write_monocle_accounts_file(accts)
else:
    write_rocketmap_accounts_file(accts)

