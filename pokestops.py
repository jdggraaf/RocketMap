import logging
from itertools import islice

from accountdbsql import set_account_db_args
from argparser import basic_std_parser, add_geofence
from geofence import get_geofences
from geography import step_position, center_geolocation, lat_routed, as_3d_coord_array
from gymdbsql import pokestops, altitudes, insert_altitude
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.utils import cellid
from pokestopModel import create_pokestops, update_distances, print_gmaps_coordinates, find_largest_groups, \
    sort_by_distance
from scannerutil import precise_coordinate_string, equi_rect_distance

parser = basic_std_parser("pokestops")
parser.add_argument('-k', '--gmaps-key',
                    help='Google Maps Javascript API Key.',
                    required=False)
add_geofence(parser)
args = parser.parse_args()
set_args(args)
set_account_db_args(args)


logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)


def add_altitudes(stops):
    added = 0
    for stop in stops:
        if stop["altitude"] is None:
            pos = (stop["latitude"], stop["longitude"])
            RADIUS = 70.0
            topleft_box = step_position(pos, RADIUS, -RADIUS)
            bottomright_box = step_position(pos, -RADIUS, RADIUS)
            altitude_candidates = altitudes(topleft_box, bottomright_box)
            if len(altitude_candidates) > 0:
                stop["altitude"] = altitude_candidates[0]["altitude"]
                insert_altitude(cellid(pos), pos[0], pos[1], altitude_candidates[0]["altitude"])
                added += 1
            else:
                pos = with_gmaps_altitude(pos, args.gmaps_key)
                insert_altitude(cellid(pos), pos[0], pos[1], pos[2])
    if added > 0:
        log.info("Found {} altitudes by approximating DB data, {} total stops".format(str(added), str(len(stops))))
    return stops


def approximate_pokestop_alts():
    add_altitudes(pokestops())


def print_coordinates(points):
    global poke_stop, intersected
    for poke_stop in points:
        intersected = poke_stop.collected_neighbours()
        if len(intersected) > 2:
            print("{},{}".format(str(poke_stop.coords[0]), str(poke_stop.coords[1])))


if __name__ == "__main__":
    print("Loading stops")
    fences_to_use = get_geofences(args.geofence, args.fencename)
    stops_to_check = fences_to_use.filter_forts(pokestops())
    log.info("There are {} stops within fence".format(str(len(stops_to_check))))
    add_altitudes(stops_to_check)

    #for pokestop in stops_to_check:
    #    db_delete_pokestop(pokestop["pokestop_id"])

    point_list = create_pokestops(stops_to_check)

    update_distances(point_list, radius=39)

    print_gmaps_coordinates(point_list)
    result_coords = find_largest_groups(point_list)
    arranged = sort_by_distance(result_coords)

    msg = "["
    for coord in arranged:
        stop_list = [(x.coords[0],x.coords[1],x.coords[2], x.id) for x in coord[1]]
        msg += "((" + precise_coordinate_string(coord[0]) + "), " + str(stop_list) + "),\n"
    print("Traversal route for all pokestops")
    print(msg + "]")
