import logging
import os.path
import sys
import time
from datetime import datetime,timedelta
from itertools import cycle
from threading import Lock

from accountdbsql import upsert_account, load_accounts, db_consume_lures, db_set_rest_time, db_set_banned, \
    db_set_account_level, db_set_blinded, db_update_account, db_set_behaviour
from management_errors import GaveUp
from pogoservice import Account2
from simplecaptcha import handle_captcha_url

log = logging.getLogger(__name__)


'''
Account encapsulates an account, obeying the basic 10 second restrictions and gym
interaction speed restrictions. Clients that come in too fast will block until
acceptable interaction speeds have been achieved.

Non-goal: The account class does not obey speed restrictions for moving the
search area. Clients of this class are responsible for the movement speed.



'''


class AccountManager:
    def __init__(self, name, using_db, accounts_file, settings_accounts, args,
                 account_failures, account_captchas, wh_queue, status):
        self.args = args
        self.name = name
        self.apiHashGenerator = cycle(args.hash_key)
        if "proxy" in self.args and self.args.proxy is not None:
            self.currentproxies = len(self.args.proxy)
            self.current_cycler = cycle(self.args.proxy)
        else:
            self.currentproxies = None
        self.usingdb = using_db
        if self.usingdb:
            log.info("Using account DB and file {}".format(accounts_file))
            self.accounts = self.__load_account_objects()
            if self.__upsert_file_accounts(accounts_file) or self.__upsert_commandline_accounts(settings_accounts):
                self.accounts = self.__load_account_objects()
        else:
            log.info("Using account file {}".format( accounts_file) )
            accts =  self.__load_accounts(accounts_file)
            self.accounts = self.__create_account_objects(accts)

        self.sort_accounts()
        self.pos = 0
        self.running = True
        self.captcha_key = args.captcha_key
        self.captcha_dsk = args.captcha_dsk
        self.wh_queue = wh_queue
        self.account_failures = account_failures
        self.status = status
        for acct in self.accounts:
            status[acct.username] = acct.status_data()
        self.account_captchas = account_captchas
        self.lock = Lock()

        if len(self.accounts) > 0:
            log.info("Account pool " + self.name + " active with " +
                     str(len(self.accounts)) + " accounts")

    def __create_account_objects(self, accts):
        result = []
        for idx, username in enumerate(accts[0]):
            pwd = accts[1][idx]
            authtype = accts[2][idx]
            account = Account2(username, pwd, authtype, self.args, 7200, 1800,
                              self.apiHashGenerator, self.proxy_supplier_to_use(), {}, self)
            result.append(account)
        return result

    def __load_account_objects(self):
        accounts = []
        all_accounts = load_accounts(self.name)
        for account in all_accounts:
            username = account["username"]
            password = account["password"]
            auth = account["auth"]
            created = Account2(username, password, auth, self.args, 7200, 1800,
                              self.apiHashGenerator, self.proxy_supplier_to_use(), account, self)
            accounts.append(created)
        return accounts

    def proxy_cycler(self):
        if len(self.args.proxy) != self.currentproxies:
            self.current_cycler = cycle(self.args.proxy)
        return self.current_cycler

    def proxy_supplier_to_use(self):
        if self.currentproxies is None:
            return None
        else:
            return self.proxy_supplier

    def has_free(self):
        return any(s.is_available() for s in self.accounts)

    def free_count(self):
        return len([s for s in self.accounts if s.is_available()])

    def proxy_supplier(self, current_proxy):
        if self.currentproxies is None:
            return None
        if current_proxy not in self.args.proxy:
            current_proxy = next(self.proxy_cycler())
        return current_proxy

    def update_initial_inventory(self, account, inventory_player_stats):
        if len(inventory_player_stats) == 0:  # ban or similar
            return
        player_stats_outer = inventory_player_stats[0]
        player_stats = player_stats_outer.get("player_stats", None)

        level = player_stats.get("level", None)
        if level and self.usingdb:
            db_set_account_level(account.username, level )

    def __upsert_file_accounts(self, accounts_file):
        accounts = self.__load_accounts(accounts_file)
        upserted = False
        if len(accounts) > 0 and len(self.accounts) == 0:
            log.info("First time inserting accounts to database, this may take a few minutes")
        for idx, username in enumerate(accounts[0]):
            if not any(username in s.username for s in self.accounts):
                pwd = accounts[1][idx]
                authtype = accounts[2][idx]
                upsert_account(username, pwd, authtype, self.name)
                upserted = True
        return upserted

    def __upsert_commandline_accounts(self, account_list):
        inserted = False
        if len(account_list) == 0:
            return inserted
        for acct in account_list:
            if not any(acct["username"] in s.username for s in self.accounts):
                upsert_account(acct["username"], acct["password"], acct["auth_service"], self.name)
                inserted = True
        return inserted

    def __get_replacement(self):
        if not self.has_free():
            return None
        new_account = self.get_account()
        return new_account

    def get_account(self):
        tries = 0
        while not self.has_free() and tries < 10:
            log.error("No more free accounts, all gone. Maybe some return?. Probably not")
            time.sleep(10)
            tries += 1
        if tries == 10:
            raise OutOfAccounts

        with self.lock:
            for account in self.accounts:
                if account.try_reallocate():
                    log.info("Reallocated {}".format( account))
                    db_update_account(account)
                    return account

            for account in self.accounts:
                if account.tryallocate():
                    if self.usingdb:
                        db_update_account(account)
                    num_free = self.free_count()
                    if num_free % 10 == 0:
                        log.info("There are {} accounts remaining in pool".format(str(num_free)))
                    return account
        raise OutOfAccounts

    def compare_account_dates(self, x, y):
        if x.last_allocated is None and y.last_allocated is None:
            return 0
        if x.last_allocated is None:
            return -1
        if y.last_allocated is None:
            return 1
        if x.last_allocated < y.last_allocated:
            return -1
        elif x.last_allocated == y.last_allocated:
            return 0
        else:
            return 1

    def sort_accounts(self):
        self.accounts.sort( cmp= self.compare_account_dates)

    def get_with_behaviour(self, behaviour):
        tries = 0
        while not self.has_free() and tries < 10:
            log.error("No more free accounts, all gone. Maybe some return?. Probably not")
            time.sleep(10)
            tries += 1
        if tries == 10:
            raise OutOfAccounts

        with self.lock:
            for account in self.accounts:
                if account.behaviour == behaviour and account.try_reallocate():
                    log.info("Reallocated {}".format( account))
                    # dont need to update, basically nothing changed
                    return account
            for account in self.accounts:
                if account.behaviour == behaviour and account.tryallocate():
                    db_update_account(account)
                    return account
            for account in self.accounts:
                if account.behaviour is None and account.tryallocate():
                    db_set_behaviour( account.name(), behaviour)
                    db_update_account(account)
                    return account

    def free_account(self, account):
        account.free()
        with self.lock:
            self.sort_accounts()

    def size(self):
        return len(self.accounts)

    def blinded(self, account_info):
        log.error("Account is blinded " + account_info.name())
        account_info.blinded = datetime.now()
        db_set_blinded(account_info.username, account_info.blinded)
        new_account = self.__get_replacement()
        if new_account is None:
            raise GaveUp
        return new_account

    def ip_banned(self, account):
        # if we have proxies we could consider just forcing a proxy change
        # for now, just add to failures and replace it
        # todo: Integrate with standard rm failure list and status
        # self.account_failures.append(account.as_map())
        # self.status[account.name()]['message'] = \
        log.error("IP appears to be banned for account " + account.name())
        get_account = self.get_account()
        if get_account is None:
            raise GaveUp
        return get_account

    def replace_banned(self, account_info):
        # todo: Integrate with standard rm failure list and status
        # self.account_failures.append(account.as_map())
        log.error("Account is banned " + account_info.name())
        account_info.set_banned()
        if self.usingdb:
            db_set_banned(account_info.username, account_info.banned)
        new_account = self.__get_replacement()
        if new_account is None:
            raise GaveUp
        return new_account

    def too_much_trouble(self, account_info):
        log.error(
            "Account is having too much trouble {} sending to cool off".format(
                account_info.name()))
        when = datetime.now() + timedelta(0, 0, 0, 0, 120, 0, 0)
        account_info.rest_until(when)
        if self.usingdb:
            db_set_rest_time(account_info.username, when)
        new_account = self.__get_replacement()
        if new_account is None:
            raise GaveUp
        log.info("{} replaced with {}".format(str(account_info), str(new_account)))
        return new_account

    def replace_for_sleep(self, current_account_info):
        current_account_info.set_resting()
        new_pogoservice = self.get_with_behaviour(current_account_info.behaviour)
        recent_position = current_account_info.most_recent_position()
        new_pogoservice.update_position(recent_position)
        self.free_account(current_account_info)
        log.info("{} replaced with {}".format(current_account_info.username, new_pogoservice.name()))
        return new_pogoservice

    def solve_captcha(self, account, captcha_url):
        handle_captcha_url(self.args, self.status[account.status_name()],
                           account.pgoApi,
                           account.as_map(),
                           self.account_failures, self.account_captchas,
                           self.wh_queue, captcha_url,
                           account.most_recent_position())
        time.sleep(4)  # avoid throttling
        return account

    def replace_unable_to_login(self, account):
        log.error(
            "Unable to login with " + account.name() + ", replacing account")
        replacement = self.__get_replacement()
        if replacement is None:
            raise GaveUp
        return replacement

    def mark_lures_consumed(self, username):   # todo: probably remove from account manager
        # account.consumed = True
        if self.usingdb:
            db_consume_lures(username)
        return self.get_account()

    @staticmethod
    def __load_accounts(accounts_file):  # can be moved back to utils
        username = []
        password = []
        auth_service = []

        if accounts_file is None:
            return username, password, auth_service

        if not os.path.isfile(accounts_file):
            log.error("The supplied filename " + accounts_file +
                      " does not exist")
            return username, password, auth_service
        # Giving num_fields something it would usually not get.
        num_fields = -1
        with open(accounts_file, 'r') as f:
            for num, line in enumerate(f, 1):

                fields = []

                # First time around populate num_fields with current field
                # count.
                if num_fields < 0:
                    num_fields = line.count(',') + 1

                csv_input = ['', '<username>', '<username>,<password>',
                             '<ptc/google>,<username>,<password>']

                # If the number of fields is differend this is not a CSV.
                if num_fields != line.count(',') + 1:
                    print(sys.argv[0] +
                          ": Error parsing CSV file on line " + str(num) +
                          ". Your file started with the following " +
                          "input, '" + csv_input[num_fields] +
                          "' but now you gave us '" +
                          csv_input[line.count(',') + 1] + "'.")
                    sys.exit(1)

                field_error = ''
                line = line.strip()

                # Ignore blank lines and comment lines.
                if len(line) == 0 or line.startswith('#'):
                    continue

                # If number of fields is more than 1 split the line into
                # fields and strip them.
                if num_fields > 1:
                    fields = line.split(",")
                    fields = map(str.strip, fields)

                # If the number of fields is one then assume this is
                # "username". As requested.
                if num_fields == 1:
                    # Empty lines are already ignored.
                    username.append(line)

                # If the number of fields is two then assume this is
                # "username,password". As requested.
                if num_fields == 2:
                    # If field length is not longer than 0 something is
                    # wrong!
                    if len(fields[0]) > 0:
                        username.append(fields[0])
                    else:
                        field_error = 'username'

                    # If field length is not longer than 0 something is
                    # wrong!
                    if len(fields[1]) > 0:
                        password.append(fields[1])
                    else:
                        field_error = 'password'

                # If the number of fields is three then assume this is
                # "ptc,username,password". As requested.
                if num_fields == 3:
                    # If field 0 is not ptc or google something is wrong!
                    if (fields[0].lower() == 'ptc' or
                            fields[0].lower() == 'google'):
                        auth_service.append(fields[0])
                    else:
                        field_error = 'method'

                    # If field length is not longer then 0 something is
                    # wrong!
                    if len(fields[1]) > 0:
                        username.append(fields[1])
                    else:
                        field_error = 'username'

                    # If field length is not longer then 0 something is
                    # wrong!
                    if len(fields[2]) > 0:
                        password.append(fields[2])
                    else:
                        field_error = 'password'

                if num_fields > 3:
                    print(('Too many fields in accounts file: max ' +
                           'supported are 3 fields. ' +
                           'Found {} fields').format(num_fields))
                    sys.exit(1)

                # If something is wrong display error.
                if field_error != '':
                    type_error = 'empty!'
                    if field_error == 'method':
                        type_error = (
                            'not ptc or google instead we got \'' +
                            fields[0] + '\'!')
                    print(sys.argv[0] +
                          ": Error parsing CSV file on line " + str(num) +
                          ". We found " + str(num_fields) + " fields, " +
                          "so your input should have looked like '" +
                          csv_input[num_fields] + "'\nBut you gave us '" +
                          line + "', your " + field_error +
                          " was " + type_error)
                    sys.exit(1)

        return username, password, auth_service


class OutOfAccounts:
    """We tried and we tried, but it's simply not going to work out between us...."""

    def __init__(self):
        pass


