import datetime
import time
from Queue import Queue

import logging

from concurrent.futures import ThreadPoolExecutor, as_completed

from accountdbsql import set_account_db_args, db_set_logged_in_stats, db_set_warned, db_set_perm_banned
from accounts import AccountManager
from argparser import std_config, location_parse, add_threads_per_proxy
from argutils import thread_count
from inventory import egg_count, lure_count
from pogom.account import LoginSequenceFail
from scannerutil import setup_logging
from workers import wrap_accounts_minimal
setup_logging()
log = logging.getLogger(__name__)

parser = std_config("accountmanager")
add_threads_per_proxy(parser, 2)
parser.add_argument('-on', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-force', '--force-system-id',
                    help='Force the accounts to the system id regardless of previous value',
                    default=False)
parser.add_argument('-lg', '--login', action='store_true', default=False,
                    help='Login enough to find level and inventory (but not shadowban)')
parser.add_argument('-lvl', '--level', default=30,
                    help='Level of the loaded accounts  (meaningless with --login)')
parser.add_argument('-ad', '--allocation-duration', default=None,
                    help='If set, the accounts will be allocated from now() and the specified number of hours')

args = parser.parse_args()
args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

set_account_db_args(args)


def set_account_level(accounts):
    if args.level and accounts:
        for acc in accounts:
            acc["level"] = args.level

monocle_accounts = AccountManager.load_accounts(args.accountcsv)
if not args.login:
    set_account_level(monocle_accounts)
duration = datetime.timedelta(hours=int(args.allocation_duration)) if args.allocation_duration else None
AccountManager.insert_accounts(monocle_accounts, args.system_id, duration, args.force_system_id)

account_manager = AccountManager(args.system_id, False, args, [], [], Queue(), {}, replace_warned=False)
account_manager.initialize( args.accountcsv, [])


def proceed(worker):
    info = worker.account_info()
    warning_ = info["warning"]
    level = info["level"]
    eggs = egg_count(worker)
    lures = lure_count(worker)
    db_set_logged_in_stats(info.username, lures, eggs, level)
    log.info("{} level {}, {} lures {} eggs".format(worker.name(), level, lures, eggs))
    if warning_:
        db_set_warned(info, datetime.datetime.now())
    return False


location = location_parse(args.location)


def check_one_account(wrapped, delay):
    try:
        time.sleep( delay)
        return wrapped.login(location, proceed)
    except LoginSequenceFail:
        db_set_perm_banned(wrapped.account_info(), datetime.datetime.now())
    except Exception:
        log.exception("Something bad happened")

def check_account(delay):
    wrapped = wrap_accounts_minimal(account_manager.get_account(False), account_manager)
    try:
        time.sleep( delay)
        return wrapped.login(location, proceed)
    except LoginSequenceFail:
        db_set_perm_banned(wrapped.account_info(), datetime.datetime.now())
    except Exception:
        log.exception("Something bad happened")

num_proxies = len(args.proxy) if args.proxy else 1

if args.login:
    with ThreadPoolExecutor(thread_count(args)) as pool:
        futures = []

        for counter in range(0, account_manager.size()):
            futures.append(pool.submit(lambda: check_account(4 if num_proxies < counter < (num_proxies*2) else 0)))

        results = [r.result() for r in as_completed(futures)]
        print str(results)


print ("Done")





