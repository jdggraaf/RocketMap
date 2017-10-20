import logging
import os.path
import sys
import time
from csv import DictReader
from datetime import datetime, timedelta
from itertools import cycle
from threading import Lock

from accountdbsql import upsert_account, load_accounts, db_consume_lures, db_set_rest_time, db_set_temp_banned, \
    db_set_account_level, db_set_blinded, db_update_account, db_set_behaviour, db_set_warned, insert_account, \
    load_account, db_set_ios, db_set_model, db_set_device_id, \
    update_account_level, db_set_system_id, update_allocated, update_allocation_end, db_set_perm_banned
from management_errors import GaveUp
from pogoservice import Account2
from scannerutil import auth_service
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
    def __init__(self, name, using_db, args, account_failures, account_captchas, wh_queue, status,
                 replace_warned=False):
        self.args = args
        self.name = name
        self.apiHashGenerator = cycle(args.hash_key)
        self.apiLoginHashGenerator = cycle(args.login_hash_key) if args.login_hash_key else None
        if "proxy" in self.args and self.args.proxy is not None:
            self.currentproxies = len(self.args.proxy)
            self.current_cycler = cycle(self.args.proxy)
        else:
            self.currentproxies = None
        self.usingdb = using_db
        self.pos = 0
        self.running = True
        self.captcha_key = args.captcha_key
        self.captcha_dsk = args.captcha_dsk
        self.wh_queue = wh_queue
        self.account_failures = account_failures
        self.status = status
        self.account_captchas = account_captchas
        self.lock = Lock()
        self.failureLock = Lock()
        self.consecutive_failures = 0
        self.accounts = []
        self.search_interval = 7200
        self.rest_interval = 1800
        self.reallocate = True
        self.replace_warned = replace_warned

    def initialize(self, accounts_file, settings_accounts):
        file_accts = self.load_accounts(accounts_file)

        if self.usingdb:
            if file_accts:
                log.info("Upserting database accounts")
                allocation_period = timedelta(days=180)
                AccountManager.insert_accounts(file_accts, self.args.system_id, allocation_period)
            self.__upsert_commandline_accounts(settings_accounts)
            log.info("Loading database accounts")
            self.accounts = self.__load_db_account_objects()
        else:
            self.accounts = self.__create_account_objects(file_accts)
        self.sort_accounts()
        for acct in self.accounts:
            self.status[acct.username] = acct.status_data()
        if len(self.accounts) > 0:
            log.info("Account pool " + self.name + " active with " + str(len(self.accounts)) + " accounts")

    def __load_db_account_objects(self):
        result = []
        all_accounts = load_accounts(self.name)
        for account in all_accounts:
            result.append(self.create_account2(account))
        return result

    def add_account(self, account):
        acct = self.create_account2(account)
        self.accounts.append(acct)
        self.status[acct.username] = acct.status_data()
        return acct

    def create_account2(self, account):
        username = account["username"]
        password = account["password"]
        auth = auth_service(account)

        created = Account2(username, password, auth, self.args, self.search_interval, self.rest_interval,
                           self.apiHashGenerator, self.apiLoginHashGenerator, self.proxy_supplier_to_use(), account,
                           self)
        return created

    def __create_account_objects(self, accts):
        result = []
        for account in accts:
            account = Account2(account["username"], account["password"], account["auth_service"], self.args,
                               self.search_interval, self.rest_interval,
                               self.apiHashGenerator, self.apiLoginHashGenerator, self.proxy_supplier_to_use(), {},
                               self)
            result.append(account)
        return result

    def remove_accounts_without_lures(self):
        initial_length = len(self.accounts)
        self.accounts = [x for x in self.accounts if x.account_info().lures != 0]
        remaining = len(self.accounts)
        log.info(
            "Initial account pool size {}, {} accounts have all lures spent, "
            "{} accounts (probably) have lures left".format(
                initial_length, (initial_length - remaining), remaining))

    def report_failure(self):
        with self.failureLock:
            self.consecutive_failures += 1

    def clear_failure(self):
        with self.failureLock:
            self.consecutive_failures = 0

    def is_failing(self):
        with self.failureLock:
            return self.consecutive_failures > 20

    def proxy_cycler(self):
        if len(self.args.proxy) != self.currentproxies:
            self.current_cycler = cycle(self.args.proxy)
        return self.current_cycler

    def proxy_supplier_to_use(self):
        if self.currentproxies is None:
            return None
        else:
            return self.proxy_supplier

    def handle_warned(self, pogoservice):
        if self.usingdb:
            db_set_warned(pogoservice.account_info(), datetime.now())
        return self.replace(pogoservice) if self.replace_warned else pogoservice

    def replace(self, old_pogoservice_to_be_replaced):
        newaccount = self.get_account()
        newaccount.update_position(old_pogoservice_to_be_replaced.get_position())
        return newaccount

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

    def update_initial_inventory(self, account):
        level = account["level"]
        if level and self.usingdb:
            db_set_account_level(account.username, level)

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

    def get_account(self, wait_for_account=True):
        tries = 0
        if not wait_for_account and not self.has_free():
            raise OutOfAccounts
        while not self.has_free() and tries < 10:
            log.error("No more free accounts, all gone. Maybe some return?. Probably not")
            time.sleep(10)
            tries += 1
        if tries == 10:
            raise OutOfAccounts

        with self.lock:
            for account in self.accounts:
                if self.reallocate and account.try_reallocate():
                    log.info("Reallocated {}".format(account))
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
        if x.allocated is None and y.allocated is None:
            return 0
        if x.allocated is None:
            return -1
        if y.allocated is None:
            return 1
        if x.allocated < y.allocated:
            return -1
        elif x.allocated == y.allocated:
            return 0
        else:
            return 1

    def sort_accounts(self):
        self.accounts.sort(cmp=self.compare_account_dates)

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
                if account.behaviour == behaviour and self.reallocate and account.try_reallocate():
                    log.info("Reallocated {}".format(account))
                    # dont need to update, basically nothing changed
                    return account
            for account in self.accounts:
                if account.behaviour == behaviour and account.tryallocate():
                    db_update_account(account)
                    return account
            for account in self.accounts:
                if account.behaviour is None and account.tryallocate():
                    db_set_behaviour(account.name(), behaviour)
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
        # self.account_failures.append(account.as_map())
        # self.status[account.name()]['message'] = \
        log.error("IP appears to be banned for account " + account.name())
        get_account = self.get_account()
        if get_account is None:
            raise GaveUp
        return get_account

    def replace_temp_banned(self, account_info):
        # self.account_failures.append(account.as_map())
        self.mark_temp_banned(account_info)
        new_account = self.__get_replacement()
        if new_account is None:
            raise GaveUp
        return new_account

    db_set_warned
    def mark_warned(self, account_info):
        # self.account_failures.append(account.as_map())
        log.error("Account is warned " + account_info.name())
        # account_info.()
        if self.usingdb:
            db_set_warned(account_info.username, datetime.now())

    def mark_temp_banned(self, account_info):
        # self.account_failures.append(account.as_map())
        log.error("Account is temp " + account_info.name())
        account_info.set_banned()
        if self.usingdb:
            db_set_temp_banned(account_info.username, datetime.now())

    def mark_perm_banned(self, account_info):
        # self.account_failures.append(account.as_map())
        log.error("Account is temp " + account_info.name())
        account_info.set_banned()
        if self.usingdb:
            db_set_perm_banned(account_info, datetime.now())

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

    def replace_for_sleep(self, pogoservice):
        current_account_info = pogoservice.account_info()
        current_account_info.set_resting()
        new_pogoservice = self.get_with_behaviour(current_account_info.behaviour)
        recent_position = current_account_info.most_recent_position()
        new_pogoservice.update_position(recent_position)
        self.free_account(current_account_info)
        if self.usingdb:
            db_set_rest_time(pogoservice, current_account_info.rest_until)
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

    def mark_lures_consumed(self, username):
        # account.consumed = True
        if self.usingdb:
            db_consume_lures(username)
        return self.get_account()

    @staticmethod
    def insert_accounts(accounts, system_id, allocation_duration=None, force_system_id=False):
        now = datetime.now()
        allocated = now if allocation_duration else None
        allocation_end = now + allocation_duration if allocation_duration else None
        for account in accounts:
            username_ = account["username"]
            existing = load_account(username_)
            if existing:
                if existing["system_id"] and system_id and not force_system_id:
                    if not system_id == existing["system_id"]:
                        raise ValueError("Account {} exists but is assigned to {}, cannot be loaded for {}".format(
                            username_, existing["system_id"], system_id))
                if system_id:
                    db_set_system_id(username_, system_id)
                if account.get("iOS") and not existing.get("iOS"):
                    db_set_ios(username_, account["iOS"])
                if account.get("model") and not existing.get("model"):
                    db_set_model(username_, account["model"])
                if account.get("id") and not existing.get("device_id"):
                    db_set_device_id(username_, account["id"])
                if account.get("level") and not existing.get("level"):  # never update
                    update_account_level(username_, account["level"])
                if not existing["system_id"] or not existing["allocated"]:
                    update_allocated(username_, allocated)
                if not existing["system_id"] or not existing["allocation_end"]:
                    update_allocation_end(username_, allocation_end)

            else:
                insert_account(account, system_id, allocated, allocation_end)

    @staticmethod
    def load_accounts(accounts_file):  # can be moved back to utils

        if accounts_file is None:
            return None

        if not os.path.isfile(accounts_file):
            raise ValueError("The supplied filename " + accounts_file + " does not exist")

        # Giving num_fields something it would usually not get.
        with open(accounts_file, 'r') as f1:
            first_line = f1.readline()
        if "username" in first_line and "password" in first_line:
            return load_accounts_csv_monocle(accounts_file)

        if not first_line.startswith("ptc") and not first_line.startswith("google"):
            return load_accounts_selly_ptc(accounts_file)

        with open(accounts_file, 'r') as f:
            return AccountManager.__load_accounts_rocketmap(f)

    @staticmethod
    def __load_accounts_rocketmap(f):
        result = []
        num_fields = -1
        for num, line in enumerate(f, 1):
            account = {}
            result.append(account)
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
                account["username"] = line

            # If the number of fields is two then assume this is
            # "username,password". As requested.
            if num_fields == 2:
                # If field length is not longer than 0 something is
                # wrong!
                if len(fields[0]) > 0:
                    account["username"] = fields[0]
                else:
                    field_error = 'username'

                # If field length is not longer than 0 something is
                # wrong!
                if len(fields[1]) > 0:
                    account["password"] = fields[1]
                else:
                    field_error = 'password'

            # If the number of fields is three then assume this is
            # "ptc,username,password". As requested.
            if num_fields == 3:
                # If field 0 is not ptc or google something is wrong!
                if fields[0].lower() == 'ptc' or fields[0].lower() == 'google':
                    account["auth_service"] = fields[0]
                else:
                    field_error = 'method'

                # If field length is not longer then 0 something is
                # wrong!
                if len(fields[1]) > 0:
                    account["username"] = fields[1]
                else:
                    field_error = 'username'

                # If field length is not longer then 0 something is
                # wrong!
                if len(fields[2]) > 0:
                    account["password"] = fields[2]
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
        return result


def load_accounts_csv_monocle(csv_location):
    with open(csv_location, 'rt') as f:
        accounts = []
        reader = DictReader(f)
        for row in reader:
            accounts.append(dict(row))
    return accounts


def load_accounts_selly_ptc(csv_location):
    with open(csv_location, 'rt') as f:
        accounts = []
        for line in f.readlines():
            withcomma = line.replace(":", ",")
            if withcomma.startswith(","):
                withcomma = withcomma[1:]
            usrnamepassword = withcomma.split(",")
            accounts.append({"username": usrnamepassword[0].strip(), "password": usrnamepassword[1].strip(),
                             "auth_service": "ptc"})
    return accounts


class OutOfAccounts:
    """We tried and we tried, but it's simply not going to work out between us...."""

    def __init__(self):
        pass
