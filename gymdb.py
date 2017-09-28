import logging
from threading import Thread

from geopy.distance import vincenty
from s2sphere import LatLng, CellId

from gymdbsql import create_or_update_gym, log_gym_change_in_db, update_defenders, singlegym_Defenders, \
    do_with_backoff_for_deadlock, db_load_spawn_points, db_load_spawn_points_missing_s2, db_set_s2_cellid, \
    db_load_spawn_points_for_cell, db_load_spawn_points_missing_altitude, db_set_altitude, gym_names
from pogom.fnord_altitude import get_gmaps_altitude
from spawnpoint import SpawnPoint, SpawnPoints

log = logging.getLogger(__name__)

def log_gym_change(g, previousgym):
    latitude_ = g["latitude"]
    longitude_ = g["longitude"]
    modified_ = g["last_modified"]
    kmh = None
    distance = None
    previous_gym = None
    name_ = g["name"].encode('utf-8')
    if not previousgym is None:
        previous_latitude = previousgym["latitude"]
        previous_longitude = previousgym["longitude"]
        previous_lastmodified = previousgym["last_modified"]
        previous_gym = previousgym["name"].encode('utf-8')

        elapsed_seconds = (modified_  - previous_lastmodified).total_seconds()

        prevgymcoords = (previous_latitude, previous_longitude)
        thisgymccords = (latitude_, longitude_)
        distance = vincenty(prevgymcoords, thisgymccords).m

        print "Distance between " + previous_gym + str(prevgymcoords) + " and "  + name_ + str(thisgymccords) + " is" + str(distance) +", elapsed is " + str(elapsed_seconds)
        if distance == 0:
            distance = None
        elif elapsed_seconds  > 0 and distance  > 0:
            kmh = distance/elapsed_seconds * 3.6
        else:
            previous_gym = None
            distance = None

    log_gym_change_in_db(g, previous_gym, kmh, distance)

def update_gym_from_details(gym):
    ''' main entry point for updating gyms'''
    #if not "gym_state" in gym:
    #    return
    state_ = gym["gym_status_and_defenders"]
    data_ = state_["pokemon_fort_proto"]
    gym_id = data_["id"]
    create_or_update_gym(gym_id, gym)

    '''
    newlysScannedMembers = []
    if "memberships" in state_:
        newlysScannedMembers = state_["memberships"]
    newIds  = list(map(lambda m: m["pokemon_data"]["id"], newlysScannedMembers))
    added = {}
    removed = []
    pokemon_from_db = singlegym_Defenders(gymid)
    for idx, memberId in enumerate(newIds):
        if memberId not in pokemon_from_db["defenders"]:
            added[memberId] = newlysScannedMembers[idx]

    for existingMember in pokemon_from_db["defenders"]:
        if existingMember not in newIds:
            removed.append(existingMember)

    last_prev_scan = None
    if "last_scanned" in pokemon_from_db:
        last_prev_scan = pokemon_from_db["last_scanned"]
    do_with_backoff_for_deadlock(lambda:  update_defenders(gym, added, removed, last_prev_scan))
    '''


def load_spawn_points():
    points = {}
    spawn_points = db_load_spawn_points()
    print "Calculating map"
    for spawnpoint in spawn_points:
        latlng = LatLng.from_degrees(spawnpoint["latitude"], spawnpoint["longitude"])
        cell = CellId.from_lat_lng(latlng)
        while cell.level() != 15:
            cell = cell.parent()
        current = points.get( cell.id(), [])
        current.append( spawnpoint)
        points[cell.id()] = current
    return points


def cell_spawnpoints(cell_id):
    sp = db_load_spawn_points_for_cell(cell_id)
    points = []
    for s in sp:
        nu = SpawnPoint(s)
        points.append(nu)
    return SpawnPoints(points)


def update_missing_s2_ids():
    log.info("Looking for spawn points with missing s2 coordinates")
    for spawnpoint in db_load_spawn_points_missing_s2():
        latlng = LatLng.from_degrees(spawnpoint["latitude"], spawnpoint["longitude"])
        cell = CellId.from_lat_lng(latlng).parent(15)
        db_set_s2_cellid(spawnpoint["id"], cell.id())
    log.info("Done establishing s2 points")


def update_missing_altitudes(gmaps_key):
    log.info("Looking for spawn points with missing altitudes")
    missing_altitude = db_load_spawn_points_missing_altitude()
    threads = []
    for chunk in chunks(missing_altitude, len(missing_altitude)/4):
        thread = Thread(target=update_altidtude, args=(chunk, gmaps_key))
        threads.append(thread)
        thread.start()

    for thread in threads:
            thread.join()


def update_altidtude( points, gmaps_key):
    for spawnpoint in points:
        altitude = get_gmaps_altitude(spawnpoint["latitude"], spawnpoint["longitude"], gmaps_key)
        if altitude[0]:
            db_set_altitude(spawnpoint["id"], altitude[0])

def gym_map(fences):
    result = {}
    for name in gym_names():
        if fences.within_fences(name["latitude"], name["longitude"]):
            result[name["gym_id"]] = name["name"].encode('utf-8')
    return result


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in xrange(0, len(l), n):
        yield l[i:i + n]
