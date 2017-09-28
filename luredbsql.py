import logging

import pymysql.cursors

log = logging.getLogger(__name__)

args = None

'''
CREATE TABLE lurebomber
(
    username VARCHAR(50) PRIMARY KEY NOT NULL,
    lures int default 0,
    max_lures int
);


'''


def set_lure_db_args(new_args):
    global args
    args = new_args


def __lure_db_connection():
    return pymysql.connect(user=args.db_user, password=args.db_pass, database=args.db_name, host=args.db_host,
                           port=args.db_port,
                           charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)


def lures(username):
    connection = __lure_db_connection()
    sql = "SELECT lures, max_lures FROM lures WHERE username=%s"
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, username)
            return cursor.fetchall()
    finally:
        connection.close()


def db_consume_lure(account):
    connection = __lure_db_connection()

    try:
        with connection.cursor() as cursor:
            sql = 'UPDATE lures SET lures=lures+1 WHERE username=%s'
            cursor.execute(sql, account)
        connection.commit()
    finally:
        connection.close()
