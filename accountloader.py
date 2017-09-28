import datetime

from accountdbsql import set_account_db_args
from accounts import AccountManager
from argparser import std_config

parser = std_config("accountmanager")
parser.add_argument('-on', '--system-id',
                    help='Define the name of the node that will be used to identify accounts in the account table',
                    default=None)
parser.add_argument('-force', '--force-system-id',
                    help='Force the accounts to the system id regardless of previous value',
                    default=False)
parser.add_argument('-lvl', '--level', default=30,
                    help='Level of the loaded accounts')
parser.add_argument('-ad', '--allocation-duration', default=None,
                    help='If set, the accounts will be allocated from now() and the specified number of hours')

args = parser.parse_args()
set_account_db_args(args)

def set_account_level(accounts):
    if args.level and accounts:
        for acc in accounts:
            acc["level"] = args.level

monocle_accounts = AccountManager.load_accounts(args.accountcsv)
set_account_level(monocle_accounts)
duration = datetime.timedelta(hours=int(args.allocation_duration))
AccountManager.insert_accounts(monocle_accounts, args.system_id, duration, args.force_system_id)
print ("Done")





