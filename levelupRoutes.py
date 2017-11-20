import logging
import os
import sys

from accountdbsql import set_account_db_args
from argparser import basic_std_parser, add_geofence
from geofence import get_geofences
from geography import lat_routed
from gymdbsql import pokestops_in_box, pokestops_in_box_2
from levelup_tools import stop_string, xp_stop_string, find_xp_route, write_gpx_route
from pokestopModel import create_pokestops, update_distances, find_optimal_location
from pokestops import add_altitudes
from scannerutil import setup_logging

dirname=os.path.dirname(os.path.realpath(sys.argv[0]))

parser = basic_std_parser("pokestops")
parser.add_argument('-k', '--gmaps-key',
                    help='Google Maps Javascript API Key.',
                    required=False)
add_geofence(parser)
args = parser.parse_args()
set_account_db_args(args)

setup_logging()
log = logging.getLogger(__name__)

num_locs = 0


def create_one(fence, gpx_filename, target_positions=190, xp_route=False):
    box_stops = pokestops_in_box_2(fence.box())
    stops = fence.filter_forts(box_stops)
    add_altitudes(stops)

    point_list_hl = create_pokestops(stops)
    update_distances(point_list_hl, radius=50)

    xp_route_1 = find_xp_route(point_list_hl, fence.box(), target_positions)
    write_gpx_route( gpx_filename, xp_route_1)

    def loc_find_optimal_location(stop_coords):
        global num_locs
        num_locs += 1
        if num_locs % 50 == 0:
            log.info("Found {} optimal spawn points".format(str(num_locs)))
        return find_optimal_location(stop_coords)

    fenced78 = lat_routed(fence, 120, 39, point_list_hl)
    if xp_route:
        with_spawns = [x + ((),) for x in fenced78]
    else:
        with_spawns = [x + (loc_find_optimal_location(x[1].coords),) for x in fenced78]
    return xp_route_1, with_spawns

big_xp_route_right, ditche_right = create_one(get_geofences(dirname + "/levelup_fences.txt", ["HamburgRight"]), "big_route_hr.gpx", 600, xp_route=True)

big_xp_route_left, ditch_left = create_one(get_geofences(dirname + "/levelup_fences.txt", ["HamburgLeft"]), "big_route_hl.gpx", 360, xp_route=True)


xp_route_initial, spawns_initial = create_one(get_geofences(dirname + "/levelup_fences.txt", ["InitialHamburg"]),
                                          "route_init.gpx")

xp_route_left, spawns_left = create_one(get_geofences(dirname + "/levelup_fences.txt", ["HamburgLeft"]), "route_hl.gpx")

xp_route_right, spawns_right = create_one(get_geofences(dirname + "/levelup_fences.txt", ["HamburgRight"]),
                                          "route_hr.gpx")



hbg_grind = """
hamburg_grind = [(53.477084, 10.259286, 50.22897338867188), (53.478151, 10.238244, 5.319664478302002),
                 (53.479974, 10.225083, 2.908063411712646), (53.483188, 10.213013, 4.66163969039917),
                 (53.486141, 10.202619, 4.805699348449707), (53.476941, 10.191635, 2.355108499526978),
                 (53.480539, 10.166163, 2.917171955108643), (53.48522, 10.141119, 1.655865669250488)]
"""
with open("rm/hamburg.py", "w") as text_file:
    text_file.write(hbg_grind)
    text_file.write("stop_route_initial = [" + "\n, ".join([stop_string(x) for x in spawns_initial]) + "]\n")
    text_file.write("xp_route_1 = [" + "\n, ".join([xp_stop_string(x) for x in xp_route_left]) + "]\n")
    text_file.write("stop_route_1 = [" + "\n, ".join([stop_string(x) for x in spawns_left]) + "]\n")
    text_file.write("xp_route_2 = [" + "\n, ".join([xp_stop_string(x) for x in xp_route_right]) + "]\n")
    text_file.write("stop_route_2 = [" + "\n, ".join([stop_string(x) for x in spawns_right]) + "]\n")
    text_file.write("big_xp_route_1 = [" + "\n, ".join([xp_stop_string(x) for x in big_xp_route_left]) + "]\n")
    text_file.write("big_xp_route_2 = [" + "\n, ".join([xp_stop_string(x) for x in big_xp_route_right]) + "]\n")
