import logging
import unittest

import datetime
import pymysql.cursors

from scannerutil import auth_service, device_id

log = logging.getLogger(__name__)

args = None

'''
MOnocle data:
{'username': 'AXXXrinGYMZas', 'password': 'xyz', 'provider': 'ptc', 'model': 'iPhone5,2', 'iOS': '8.1.3',
    'id': 'c8a8bc61ea0f45fda79f315ec7d0d214', 'time': 0, 'captcha': False, 'banned': False}
{'username': 'AXXXrinGYMZas', 'password': 'xyz', 'provider': 'ptc', 'model': 'iPhone5,2', 'iOS': '8.1.3',
'id': 'c8a8bc61ea0f45fda79f315ec7d0d214', 'time': 1499410166.0426571, 'captcha': False, 'banned': False,
   'items': {901: 1, 201: 0, 1: 110, 401: 4, 101: 4, 902: 1, 703: 10},
   'created': 1488098226.026, 'asset_time': 1499367677.439479, 'template_time': 1499385192.943,
   'location': (59.9458186071559, 10.681633467360502),
   'inventory_timestamp': 1499410165940, 'level': 7,
   'auth': 'TGT-19513933-evYe5aKQUz3VuaIbbGNzdcD22Mcelak7a7bK2KUVZdSUdJIWMz-sso.pokemon.com',
   'expiry': 1499417320.573184}

'''


def set_account_db_args(new_args):
    global args
    args = new_args


def __account_db_connection():
    return pymysql.connect(user=args.db_user, password=args.db_pass, database=args.db_name, host=args.db_host,
                           port=args.db_port,
                           charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)


def db_load_reallocatable(system_id, ban_time, warn_time, ok_if_blinded_before, now):
    sql = "SELECT username, password, provider FROM account " \
          "WHERE system_id = %s and perm_banned is null and (temp_banned is null or temp_banned < %s) " \
          "AND (warned is null or warned < %s) AND (blinded is null or blinded < %s) " \
          "AND allocated < %s AND %s < allocation_end " \
          "order by allocated"
    params = (system_id, ban_time, warn_time, ok_if_blinded_before, now, now)

    return do_fetch_all(sql, params)


def db_find_allocatable(temp_ban_time, warn_time, blind_time, now):
    sql = "SELECT username, password, provider,iOS,model,device_id as id FROM account " \
          "WHERE perm_banned is null and (temp_banned is null or temp_banned < %s) " \
          "AND (warned is null or warned < %s) AND (blinded is null or blinded < %s) " \
          "AND (allocation_end is null or %s > allocation_end) " \
          "order by allocated"
    params = (temp_ban_time, warn_time, blind_time, now)
    return do_fetch_all(sql, params)


def db_find_allocatable_by_level(temp_ban_time, warn_time, blind_time, now, min_level=0, max_level=40):
    sql = "SELECT username, password, provider,iOS,model,device_id as id FROM account " \
          "WHERE perm_banned is null and (temp_banned is null or temp_banned < %s) " \
          "AND (warned is null or warned < %s) AND (blinded is null or blinded < %s) " \
          "AND (allocation_end is null or %s > allocation_end) " \
          "AND level >= %s AND level <= %s " \
          "order by allocated"
    params = (temp_ban_time, warn_time, blind_time, now, min_level, max_level)
    return do_fetch_all(sql, params)


def db_consume_lures(account):
    do_update('UPDATE account SET lures=0 WHERE username=%s', account)


def db_set_blinded(account, when):
    do_update('UPDATE account SET blinded=%s WHERE username=%s', (when, account))



def db_set_rest_time(account, when):
    do_update('UPDATE account SET rest_until=%s WHERE username=%s', (when, account))


def db_set_account_level(account, level):
    do_update('UPDATE account SET level=%s WHERE username=%s', (level, account))


def db_set_egg_count(account, egg_count):
    do_update('UPDATE account SET eggs=%s WHERE username=%s', (egg_count, account))


def db_set_lure_count(account, lure_count):
    do_update('UPDATE account SET lures=%s WHERE username=%s', (lure_count, account))

def db_set_logged_in_stats(account, lure_count,egg_count,level):
    do_update('UPDATE account SET lures=%s,level=%s,eggs=%s WHERE username=%s', (lure_count, level, egg_count, account))

def db_set_temp_banned(username, when):
    do_update('UPDATE account SET temp_banned=%s WHERE username=%s', (when, username))


def db_set_behaviour(account, behaviour):
    do_update('UPDATE account SET behaviour=%s WHERE username=%s', (behaviour, account))


def db_load_accounts(system_id):
    sql = "SELECT username, password, provider as auth_service,allocated FROM account " \
          "WHERE system_id=%s and ( temp_banned is null and perm_banned is null) " \
          "order by allocated;"
    return do_fetch_all(sql, system_id)


def db_load_reallocated_accounts(system_id, from_time, to_time):
    sql = "SELECT username, password, provider as auth_service,allocated FROM account " \
          "WHERE system_id=%s and (allocated > %s and allocated < %s) and ( temp_banned is null or temp_banned < 10) " \
          "and perm_banned is null " \
          "order by allocated;"
    return do_fetch_all(sql, (system_id, from_time, to_time))


def db_set_allocated_time(username, allocated):
    do_update('UPDATE account SET allocated=%s WHERE username=%s', (allocated, username))


def db_update_account(account_info):
    sql = 'UPDATE account SET temp_banned=%s,blinded=%s,rest_until=%s WHERE username=%s'
    params = (account_info.banned, account_info.blinded, account_info.rest_until,
              account_info.username)
    do_update(sql, params)


def db_set_warned(account_info, when):
    params = (when, account_info.username)
    do_update('UPDATE account SET warned=%s WHERE username=%s', params)


def db_set_perm_banned(account_info, perm_banned):
    params = (perm_banned, account_info.username)
    do_update('UPDATE account SET perm_banned=%s WHERE username=%s', params)


def db_roll_allocated_date_forward(account_info):
    do_update('UPDATE account SET allocated=DATE_ADD(coalesce(allocated, now()), INTERVAL 10 DAY) WHERE username=%s',
              account_info.username)


def load_accounts(system_id):
    if not system_id:
        raise ValueError("need system_id")
    sql = "SELECT username,password,provider as auth,lures,rest_until,allocated,perm_banned,temp_banned,last_login," \
          "blinded,behaviour,'level' " \
          "FROM account WHERE system_id=%s and temp_banned is null and perm_banned is null " \
          "ORDER BY username;"
    return do_fetch_all(sql, system_id)


def account_exists(username):
    sql = "SELECT * FROM account WHERE username=%s"
    return len(do_fetch_all(sql, username)) > 0


def load_account(username):
    sql = "SELECT * FROM account WHERE username=%s"
    return do_fetch_one(sql, username)


def db_set_ios(username, ios):
    do_update('UPDATE account SET ios=%s WHERE username=%s', (ios, username))


def db_set_model(username, model):
    do_update('UPDATE account SET model=%s WHERE username=%s', (model, username))


def db_set_device_id(username, deviceid):
    do_update('UPDATE account SET device_id=%s WHERE username=%s', (deviceid, username))


def db_set_system_id(username, system_id):
    do_update('UPDATE account SET system_id=%s WHERE username=%s', (system_id, username))


def update_account_level(username, level):
    do_update("update account set level=%s where username=%s", (level, username))


def update_allocated(username, allocated):
    do_update("update account set allocated=%s where username=%s", (allocated, username))


def update_allocation_end(username, allocation_end):
    do_update("update account set allocation_end=%s where username=%s", (allocation_end, username))


def insert_account(account, system_id, allocated, allocation_end):
    sql = "insert into account(username,password,provider,model,ios,device_id,system_id,allocated,allocation_end) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    params = (account["username"], account["password"], auth_service(account), account.get("model"), account.get("iOS"),
              device_id(account), system_id, allocated, allocation_end)
    do_update(sql, params)
    if "level" in account:
        update_account_level(account["username"], account["level"])


def upsert_account(username, password, provider, system_id):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = "SELECT username,system_id FROM account WHERE username=%s"
            cursor.execute(sql, username)
            fetchone = cursor.fetchone()
            if fetchone is None:
                sql = "INSERT INTO account(username,password,provider,system_id) VALUES(%s,%s,%s,%s)"
                cursor.execute(sql, (username, password, provider, system_id))
            elif fetchone['system_id'] and fetchone['system_id'] != system_id:
                msg = "Account {} exits in database with other system_id ({}), " \
                      "cannot be created for this system_id".format(username, fetchone["system_id"].encode("utf-8"))
                raise ValueError(msg)
        connection.commit()
    finally:
        connection.close()


def do_update(sql, params):
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            connection.commit()
    finally:
        connection.close()


def do_fetch_one(sql, params):
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()
    finally:
        connection.close()


def do_fetch_all(sql, params):
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        connection.close()


'''
 parser = argparse.ArgumentParser(...)
    parser.add_argument...
    # ...Create your parser as you like...
    return parser.parse_args(args)
'''

'''
Data for test database:
insert into account(username,password,provider,system_id,allocated,allocation_end) values
  ("tu0clean", "tu0pclean", "ptc","testcase1","2012-9-15 20:30:00", "2013-9-15 20:30:00");

insert into account(username,password,provider,system_id,blinded, allocated, allocation_end) values
  ("tu1blinded", "tu1p", "ptc","testcase1","2012-9-16 21:00:00", "2012-9-15 21:00:00","2012-9-17 21:00:00");
insert into account(username,password,provider,system_id,warned, allocated) values
  ("tu2warned", "tu2p", "ptc","testcase1","2012-9-16 22:00:00",  "2012-9-15 21:01:00");
insert into account(username,password,provider,system_id,temp_banned, allocated) values
  ("tu3banned", "tu3p", "ptc","testcase1","2012-9-16 23:00:00",  "2012-9-15 21:02:00");


insert into account(username,password,provider,system_id,allocated) values
  ("tu1bclean", "tu1bpclean", "ptc","testcase1","2012-9-15 21:1:00");
insert into account(username,password,provider,system_id,allocated) values
  ("tu2bclean", "tu2bpclean", "ptc","testcase1","2012-9-15 21:01:30");
insert into account(username,password,provider,system_id,allocated,allocation_end) values
  ("tu3bclean", "tu3bpclean", "ptc","testcase1","2012-9-15 21:02:30","2012-9-18 21:02:30");


insert into account(username,password,provider,system_id) values
  ("tu4neverallocated", "tu4bpclean", "ptc","testcase1");


'''


class TestDatabase(object):
    db_host = "localhost"
    db_name = "account_db_test"
    db_user = "root"
    db_pass = None
    db_port = None


class DbtestAllocatableAllOutsideAllocationWindow(unittest.TestCase):
    def test(self):
        set_account_db_args(TestDatabase())

        now = datetime.datetime(2012, 9, 19, 21, 31, 0)
        reallocatable = db_find_allocatable(None, None, None, now)
        self.assertEqual(4, len(reallocatable))
        self.assertEqual("tu4neverallocated", reallocatable[0]["username"])
        self.assertEqual("tu1bclean", reallocatable[1]["username"])
        self.assertEqual("tu2bclean", reallocatable[2]["username"])
        self.assertEqual("tu3bclean", reallocatable[3]["username"])


class DbtestAllocatableWithOneAccountInAllocationWindow(unittest.TestCase):
    def test(self):
        set_account_db_args(TestDatabase())

        now = datetime.datetime(2012, 9, 18, 21, 00, 0)
        reallocatable = db_find_allocatable(None, None, None, now)
        self.assertEqual(3, len(reallocatable))
        self.assertEqual("tu4neverallocated", reallocatable[0]["username"])
        self.assertEqual("tu1bclean", reallocatable[1]["username"])
        self.assertEqual("tu2bclean", reallocatable[2]["username"])


class DbtestAllocatableWithOneBanShadowWarnWindowsAndAccountInAllocationWindow(unittest.TestCase):
    def test(self):
        set_account_db_args(TestDatabase())

        now = datetime.datetime(2012, 9, 18, 21, 00, 0)
        ban_release_time = datetime.datetime(2012, 9, 16, 23, 01, 0)
        warn_release_time = datetime.datetime(2012, 9, 16, 23, 01, 0)
        blinded_release_time = datetime.datetime(2012, 9, 16, 23, 01, 0)

        reallocatable = db_find_allocatable(ban_release_time, warn_release_time, blinded_release_time, now)
        self.assertEqual(6, len(reallocatable))
        self.assertEqual("tu4neverallocated", reallocatable[0]["username"])
        self.assertEqual("tu1blinded", reallocatable[1]["username"])
        self.assertEqual("tu1bclean", reallocatable[2]["username"])
        self.assertEqual("tu2warned", reallocatable[3]["username"])
        self.assertEqual("tu2bclean", reallocatable[4]["username"])
        self.assertEqual("tu3banned", reallocatable[5]["username"])


class DbtestCleanOnlyReallocation(unittest.TestCase):
    def test(self):
        set_account_db_args(TestDatabase())

        now = datetime.datetime(2012, 9, 15, 21, 1, 0)
        reallocatable = db_load_reallocatable("testcase1", None, None, None, now)
        self.assertEqual(1, len(reallocatable))
        self.assertEqual("tu0clean", reallocatable[0]["username"])


class DbtestBlindedReallocation(unittest.TestCase):  # is this meaningful
    def test(self):
        set_account_db_args(TestDatabase())

        now = datetime.datetime(2012, 9, 15, 21, 1, 0)
        blinded_release_time = datetime.datetime(2012, 9, 14, 21, 01, 0)

        with_blinds = db_load_reallocatable("testcase1", None, None, blinded_release_time, now)
        self.assertEqual(2, len(with_blinds))
        self.assertEqual("tu0clean", with_blinds[0]["username"])
        self.assertEqual("tu1blind", with_blinds[1]["username"])
