# Modified version of CaptCharmander <https://github.com/jasondinh/CaptCharmander/> to check for account warnings
# blind pokemons see: slugma, murkrow,natu,sentret,geodude,poliwag,psyduck,magnemite(?),magikarp, snubbull,staryu(?),
# sunekrn(?) paras: maybe
# [23,46,218,198,177,161,74,60,54,81,129,209,120,191]
# !/usr/bin/python
# https://www.reddit.com/r/pokemongodev/comments/6cng1n/current_guess_at_shadowbanned_pokemon/
# -*- coding: utf-8 -*-
import logging
from Queue import Queue
from threading import Thread
from time import sleep

import datetime

from accountdbsql import set_account_db_args, db_set_logged_in_stats, db_set_warned
from accounts import AccountManager, OutOfAccounts
from argparser import std_config, load_proxies, location, add_webhooks, add_search_rest, add_threads_per_proxy, \
    add_system_id, add_use_account_db
from common_blindcheck import check_worker
from getmapobjects import can_not_be_seen
from gymdbsql import set_args
from inventory import egg_count, lure_count
from management_errors import GaveUp
from pogom.fnord_altitude import with_gmaps_altitude
from scannerutil import setup_logging
from workers import wrap_account_no_replace

setup_logging()
log = logging.getLogger(__name__)

parser = std_config("blind_check")
add_webhooks(parser)
add_search_rest(parser)
add_threads_per_proxy(parser)
add_system_id(parser)
add_use_account_db(parser)
parser.add_argument('-lo', '--login-only', action='store_true', default=False,
                    help='Login enough to find warning status, tempban status, level and inventory (but not shadowban)')

args = parser.parse_args()
args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}

usernames = []
passwords = []

cannot_be_seen_when_shadowbanned = can_not_be_seen()


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


def safe_check_workers(workers, location_to_use, accounts_file):
    for worker in workers:
        try:
            check_worker(worker, location_to_use, accounts_file, args.use_account_db, args.login_only, proceed)
        except OutOfAccounts:
            log.info("Worker done")
            pass
        except GaveUp:
            logging.error("Gave up worker {}".format(str(worker)))
        except:
            logging.exception("Outer worker catch block caught exception")


load_proxies(args)
set_args(args)
set_account_db_args(args)

account_manager = AccountManager(args.system_id, args.use_account_db, args, [], [], Queue(), {}, replace_warned=False)
account_manager.initialize(args.accountcsv, ())

num_threads = args.threads_per_proxy

if args.proxy and len(args.proxy) > 0:
    num_threads = max(1, len(args.proxy)) * args.threads_per_proxy

split = []
for x in range(0, num_threads):
    split.append([])

for pos in range(0, account_manager.size()):
    account = wrap_account_no_replace(account_manager.get_account(),account_manager)
    split[pos % num_threads].append(account)

loc = with_gmaps_altitude(location(args),args.gmaps_key)
print "Checking at {}".format(str(loc))
threads = []
pause_at = len( args.proxy) if args.proxy else 1

for idx,x in enumerate(split):
    thread = Thread(target=safe_check_workers, args=(x, loc, args.accountcsv))
    threads.append(thread)
    if (idx % pause_at) == 0:
        sleep(5)
    thread.start()

for thread in threads:
    thread.join()

    print "Done checking all"
