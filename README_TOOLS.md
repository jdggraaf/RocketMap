Tools for rocketmap
===================

These tools do not require that you run rocketmap, but are based on rocketmap code.

You might put the "bin" subfolder of your checkout in your path. If your checkout is /home/username/RocketMap you can add /home/username/RocketMap/bin to your path.

Large scale lure dumper
===========
See config/lureparty.ini.example and config/locations_sample.json

Requires mysql with "account" table described below.

** One pokestop per coordinate in the routes section. Pokestop closest to coordinate will be lured.

```
python lureparty.py -cf lureparty.ini --json-locations=locations.json --owner=lureparty --proxy-file=proxies.txt --accountcsv=accts_rocketmap_format.txt --base-name=BrandednName --base-name=Bname2 
```

When adding more lure accounts you can just change file. The accounts accumulate in the database.

Commands you can run in the database:
-------
See how many accounts have not yet been emptied of lures. (Actually the DB value is always NULL or 0 with lureParty.py)
```
select count(*) from account where owner='lureparty' and Coalesce(lures,1)<>0;
```

Other useful sql:
```
select count(*) from account where owner="lureparty"
delete from account where owner="lureparty";
```

Tips
-----
* Lures are dropped in routes or subsets of a route. Hence sometimes a route might lose its lure for a minute or so if an
  account needs replacing or encounters a captcha.
* If you are luring a specific grinding route that people walk in a single direction, it's smart to declare the route in the opposite direction.
* Users should be discouraged from luring manually in the routes, it makes the bot work less well.

Rocketmap with improved account manager
==========
Standard rocketmap with proper databse backed account manager. 
Also more sophisticated L30 account handling, including rest intervals.

How to use:

1. Create database table account as decribed below
2. Rocketmap parameter "status-name" *must* be set, this will be the "owner" in the account table. (name and name_CP).
   Upon first starting with a text/csv file, all accounts will be written to db. After that file can be emptied
   or left as-is. If any accounts are added to this file, they will be added to the database.
   When the "banned" flag in the database passes 10 the account is considered truly dead.
   Only "banned" and "last_allocated" are populated for regular accounts, L30 account pool get more information.
3. To use the sophisticated L30 manager, the *filename* of the accounts file must START with accounts30 (e.g.) accounts30.csv
   accounts30.txt or accounts30foobar.txt



pokestops
===========

Identifies pokestop clusters with 3-4 reachable pokestops or more. Copy config/pokestops.ini.example to RocketMap folder.

Usage: pokestops


levelUp
=========

Effectively bring accounts to level 5 by looting pokestops and catching pokemon at a predefined set of locations,
repeatedly if necessary. Cycles through ALL accounts before repeating initial, giving just a few minutes for each 
botting session. Will reach level 5 in 2 cycles if 5-6 locations are defined.

Usage: levelUp accountfile

Make sure levelup.ini.exmple is moved from "config" folder to "RocketMap" folder and fill in correct values.

The config file contains a set of SPACE separate gps coordinates where your accounts will be leveled to level 5. Give it a few thousand accounts and a night
The locations should be somewhere with access to pokestops and a reasonable amount of pokemons. Make sure they are not too
far apart. 5-6 locations should be enough. Please also make sure there are no extra spaces in the "locations" setting :)

Blindcheck needs this database table:


Also note if you run multiple instances of the bot they should have different names in levelup.ini, since the accounts in
the database are attached through the "owner" field


blindCheck
============

Usage blindCheck accountfile

Also uses a location from the blindcheck.ini file (in RocketMap folder - use config/blindcheck.ini.example as starter start)

Uses the ACTUAL rocketmap code to check for blindbess. If this breaks your accounts, so will rocketmap.

The location should probably point at some local nest 

Once the blindcheck has completed you will get three new files with additional suffixes.


Please note if ALL your accounts are blinded you may want to double check that the location actually HAS pokemons in the 
"blided" category.


Account database
============

Any mysql database with the following table. You can put it inside your RocketMap database or make a separate one. 

```
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
```
