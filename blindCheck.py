# Modified version of CaptCharmander <https://github.com/jasondinh/CaptCharmander/> to check for account warnings
# blind pokemons see: slugma, murkrow,natu,sentret,geodude,poliwag,psyduck,magnemite(?),magikarp, snubbull,staryu(?),
# sunekrn(?) paras: maybe
# [23,46,218,198,177,161,74,60,54,81,129,209,120,191]
# !/usr/bin/python
# https://www.reddit.com/r/pokemongodev/comments/6cng1n/current_guess_at_shadowbanned_pokemon/
# -*- coding: utf-8 -*-
import logging
from Queue import Queue
from threading import Thread, Lock

from datetime import datetime
from pgoapi.exceptions import BannedAccountException

from accountdbsql import set_account_db_args,db_increment_banned, db_set_blinded
from accounts import AccountManager, OutOfAccounts
from argparser import std_config, load_proxies, location, add_webhooks, add_search_rest
from getmapobjects import match_pokemon_in_result, NoPokemonFoundPossibleSpeedViolation, can_not_be_seen
from gymdbsql import set_args
from management_errors import GaveUp
from pogom.account import TooManyLoginAttempts
from pogom.fnord_altitude import with_gmaps_altitude
from pogoservice import EmptyResponse
from scannerutil import setup_logging
from workers import wrap_account, wrap_account_no_replace

setup_logging()
log = logging.getLogger(__name__)

usernames = []
passwords = []

cannot_be_seen_when_shadowbanned = can_not_be_seen()

lock = Lock()


def write_to_file(filename, username, password):
    with lock:
        with open(filename, "a") as my_file:
            my_file.write("ptc,{},{}\n".format(username, password))


def safe_check_workers(workers, location_to_use, accounts_file):
    for worker in workers:
        try:
            check_worker(worker, location_to_use, accounts_file)
        except OutOfAccounts:
            log.info("Worker done")
            pass
        except GaveUp:
            logging.error("Gave up worker {}".format(str(worker)))
        except:
            logging.exception("Outer worker catch block caught exception")


def try_a_couple_of_times(worker, location_to_use):
    try:
        return worker.do_get_map_objects(location_to_use)
    except (EmptyResponse,TooManyLoginAttempts,NoPokemonFoundPossibleSpeedViolation):
        pass


def check_worker(worker, location_to_use, accounts_file):
    banned = False
    try:
        response = try_a_couple_of_times(worker,location_to_use)
    except BannedAccountException:
        banned = True

    if banned or not response:
        log.info("{} is banned".format(worker.name()))
        if args.using_account_db:
            db_increment_banned(worker.name())
        else:
            write_to_file(accounts_file + "_banned.csv", worker.account_info().username, worker.account_info().password)
    else:
        count = match_pokemon_in_result(response, cannot_be_seen_when_shadowbanned)
        if count > 0:
            log.info("{} is clean".format(worker.name()))
            if not args.using_account_db:
                write_to_file(accounts_file + "_clean.csv", worker.account_info().username, worker.account_info().password)
        else:
            log.info("{} may be blinded".format(worker.account_info().username))
            if args.using_account_db:
                db_set_blinded(worker.name(), datetime.now())
            else:
                write_to_file(accounts_file + "_blinded.csv", worker.account_info().username, worker.account_info().password)


parser = std_config("blind_check")
add_webhooks(parser)
add_search_rest(parser)
parser.add_argument('-t', '--threads',
                    help='threads',
                    type=int, default=5)
parser.add_argument('-node-name', '--node-name',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default="blindCheck")
parser.add_argument('-uad', '--using-account-db',
                    help='Indicates if the application wil enter accounts into account database',
                    default=False)


args = parser.parse_args()
args.player_locale = {'country': 'NO', 'language': 'no', 'timezone': 'Europe/Oslo'}


load_proxies(args)
set_args(args)
set_account_db_args(args)

account_manager = AccountManager(args.node_name, args.using_account_db, args.accountcsv, (), args, [], [], Queue(), {})

num_threads = args.threads

if args.proxy and len(args.proxy) > 0:
    num_threads = max(1, len(args.proxy)) * args.threads

split = []
for x in range(0, num_threads):
    split.append([])

for pos in range(0, account_manager.size()):
    account = wrap_account_no_replace(account_manager.get_account(),account_manager)
    split[pos % num_threads].append(account)

loc = with_gmaps_altitude(location(args),args.gmaps_key)
print "Checking at {}".format(str(loc))
threads = []
for x in split:
    thread = Thread(target=safe_check_workers, args=(x, loc, args.accountcsv))
    threads.append(thread)
    thread.start()

for thread in threads:
    thread.join()

    print "Done checking all"
