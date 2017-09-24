import logging
import pymysql.cursors

log = logging.getLogger(__name__)

args = None

'''


CREATE TABLE account
(
    username VARCHAR(50) PRIMARY KEY NOT NULL,
    password VARCHAR(100),
    provider VARCHAR(6),
    model VARCHAR(20),
    ios VARCHAR(10),
    id VARCHAR(40),
    captcha boolean,
    banned boolean,
    created datetime,
    asset_time datetime,
    template_time datetime,
    location VARCHAR(30),
    behaviour VARCHAR(60),
    inventory_timestamp datetime,
    level int,
    auth VARCHAR(150),
    expiry datetime,
    rest_until datetime null,
    last_allocated datetime null,
    blinded datetime null,
    blindchecked datetime null,
    times_blinded int default 0,
    owner VARCHAR(20),
    lures int null,
    items varchar(200)
);

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


def db_consume_lures(account):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET lures=0 WHERE username=%s'
            cursor.execute(sql, account)
        connection.commit()
    finally:
        connection.close()


def db_set_blinded(account, when):
    connection = __account_db_connection()

    try:

        with connection.cursor() as cursor:
            sql = 'UPDATE account SET blinded=%s WHERE username=%s'
            cursor.execute(sql, (when, account))
        connection.commit()
    finally:
        connection.close()


def db_set_rest_time(account, when):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET rest_until=%s WHERE username=%s'
            cursor.execute(sql, (when, account))
        connection.commit()
    finally:
        connection.close()


def db_set_account_level(account, level):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET level=%s WHERE username=%s'
            cursor.execute(sql, (level, account))
        connection.commit()
    finally:
        connection.close()


def db_set_banned(username, bancount):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET banned=%s WHERE username=%s'
            cursor.execute(sql, (bancount, username))
        connection.commit()
    finally:
        connection.close()


def db_set_behaviour(account, behaviour):
    sql = 'UPDATE account SET behaviour=%s WHERE username=%s'
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (behaviour, account))
        connection.commit()
    finally:
        connection.close()


def db_load_accounts(owner):
    sql = "SELECT username, password, provider as auth_service,last_allocated FROM account " \
          "WHERE owner=%s and ( banned is null or banned < 10) " \
          "order by last_allocated;"
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, owner)
            return cursor.fetchall()
    finally:
        connection.close()


def db_load_reallocated_accounts(owner, from_time, to_time):
    sql = "SELECT username, password, provider as auth_service,last_allocated FROM account " \
          "WHERE owner=%s and (last_allocated > %s and last_allocated < %s) and ( banned is null or banned < 10) " \
          "order by last_allocated;"
    connection = __account_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (owner, from_time, to_time))
            return cursor.fetchall()
    finally:
        connection.close()


def db_increment_banned(username):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET banned=Coalesce(banned, 0) + 1  WHERE username=%s'
            cursor.execute(sql, username)
        connection.commit()
    finally:
        connection.close()


def db_set_last_allocated(username, last_allocated):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE account SET last_allocated=%s WHERE username=%s'
            cursor.execute(sql, (last_allocated, username))
        connection.commit()
    finally:
        connection.close()


def db_update_account(account_info):
    connection = __account_db_connection()
    sql = 'UPDATE account SET banned=%s,blinded=%s,times_blinded=%s,rest_until=%s,last_allocated=%s WHERE username=%s'
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (
                account_info.banned, account_info.blinded, account_info.times_blinded, account_info.rest_until,
                account_info.last_allocated, account_info.username))
        connection.commit()
    finally:
        connection.close()


def load_accounts(owner):
    if not owner:
        raise ValueError("need owner")
    sql = "SELECT username,password,provider as auth,lures,rest_until,last_allocated,banned,blinded,times_blinded,behaviour,'level' FROM account WHERE owner=%s and Coalesce(banned, 0) < 4 ORDER BY username;"  # todo: Consider insertiondate
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, owner)
            return cursor.fetchall()
    finally:
        connection.close()


def upsert_account(username, password, provider, owner):
    connection = __account_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = "SELECT username,owner FROM account WHERE username=%s"
            cursor.execute(sql, username)
            fetchone = cursor.fetchone()
            if fetchone is None:
                sql = "INSERT INTO account(username,password,provider,owner) VALUES(%s,%s,%s,%s)"
                cursor.execute(sql, (username, password, provider, owner))
            elif fetchone['owner'] and fetchone['owner'] != owner:
                raise ValueError(
                    "Account {} exits in database with other owner ({}), cannot be created for this owner".format(
                        username, fetchone["owner"].encode("utf-8")))
        connection.commit()
    finally:
        connection.close()
