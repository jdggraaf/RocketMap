import logging

from geography import step_position, chunk_box, is_inside_box, move_in_direction_of
from pokestopModel import find_largest_groups
from scannerutil import setup_logging, precise_coordinate_string, full_precision_coordinate_string, equi_rect_distance_m

setup_logging()
log = logging.getLogger(__name__)


def get_pos_to_use(tuple_to_use, fallback_altitude, worker_role):
    if not tuple_to_use:
        return
    if type(tuple_to_use) is not tuple:
        return as_coordinate(tuple_to_use, fallback_altitude)
    if len(tuple_to_use) == 3 and type(tuple_to_use[1]) is not tuple:  # direct cordinate
        return tuple_to_use
    '''
     Legal formats:
     to_catch
     (lat,lng,alt)
    ((53.48523,10.26412,44.1),(53.485582,10.264123,44.0852546692,u'652b606bade04bc2a519d401106c1223.16'),())
    '''
    try:
        if len(tuple_to_use[1]) < 4:
            log.error("Incorrect pokestop definition {}".format(str(tuple_to_use[1])))
    except TypeError:
        log.exception("Corrupt pokestop {}".format(str(tuple_to_use)))
        raise

    try:
        next_cluster_pos = __get_cluster_pos(tuple_to_use[1], tuple_to_use[2], worker_role)
        return next_cluster_pos if next_cluster_pos else tuple_to_use[0]
    except TypeError:
        log.exception("Corrupt pokestop {}".format(str(tuple_to_use)))
        raise


def __get_cluster_pos(pokestop_position, spawn_cluster, worker_role):
    if not worker_role:
        return pokestop_position[0], pokestop_position[1], pokestop_position[2]
    role_mod = worker_role % 4
    if len(spawn_cluster) > 0 and spawn_cluster[1] > 2:  # use spawn cluster for positioning
        max_spawn_pos = spawn_cluster[0]
        max_spawn_pos = max_spawn_pos[0], max_spawn_pos[1], pokestop_position[2]
        if role_mod == 0:
            return max_spawn_pos
        if role_mod == 1:
            to_stop = equi_rect_distance_m(max_spawn_pos, pokestop_position)
            move_in_direction_of(max_spawn_pos, pokestop_position, to_stop + 39)
        if role_mod == 2:
            return step_position(max_spawn_pos, 39, 0)  # not really catch length ?
        if role_mod == 3:
            return step_position(max_spawn_pos, -39, 0)  # not really catch length ?

    if role_mod == 0:
        return step_position(pokestop_position, 39, 0)
    if role_mod == 1:
        return step_position(pokestop_position, -39, 0)
    if role_mod == 2:
        return step_position(pokestop_position, 0, 39)
    if role_mod == 3:
        return step_position(pokestop_position, 0, -39)
    log.error("No modulo")


def is_encounter_to(tuple_to_use):
    return type(tuple_to_use) is not tuple


def is_plain_coordinate(tuple_to_use):
    return len(tuple_to_use) == 3 and type(tuple_to_use[0]) is not tuple


def as_coordinate(global_feed_map_pokemon, fallback_altitude):
    return global_feed_map_pokemon.latitude, global_feed_map_pokemon.longitude, fallback_altitude


def gpx_string(combined):
    """  <trkpt lat="47.644548" lon="-122.326897">"""
    return "<trkpt lat='" + str(combined[0][0]) + "' lon='" + str(combined[0][1]) + "'/>"

def stop_string(combined):
    return "((" + precise_coordinate_string(combined[0]) +"),(" + full_precision_coordinate_string(combined[1].coords) + "," + repr(combined[1].id) + ")," + str(combined[2]) +")"

def stop_node(stop):
    return "(" + full_precision_coordinate_string(stop.coords) + ", '" + str(stop.id) + "')"

def xp_stop_string(xp_tuple):
    stops = "[" + ", ".join([stop_node(x) for x in xp_tuple[1]]) + "]"

    return "((" + precise_coordinate_string(xp_tuple[0]) + "), " + stops + ")"

def location_string(pos):
    return "(" + precise_coordinate_string(pos) +")"

def as_gpx(route):
    return initial_gpx + "\n".join([gpx_string(x) for x in route]) + post_gpx

def write_gpx_route(filename, xp_route):
    with open(filename, "w") as text_file:
        text_file.write(as_gpx(xp_route))


def find_xp_route(point_list, fence_box):
    result_coords = find_largest_groups(point_list, min_size=2)

    goodies = [x for x in result_coords if len(x[1]) > 2]
    notgoodies = [x for x in result_coords if len(x[1]) < 3]

    eastwest = sorted(goodies, key=lambda item: x[0][1])

    xp_route = make_optimal_route(fence_box, eastwest, notgoodies, 190)
    return xp_route

def exclusion_pokestops(list):
    result = []
    for x in list:
        for y in x[1]:
            result.append(y[3])
    return set(result)

def make_optimal_route(fece_nox, requireds, optionals, target):
    result = []
    grid_size=5
    items_per_box = target/ (grid_size*grid_size)
    for chunk_num, chunk in enumerate(chunk_box(fece_nox, grid_size)):
        chunk_stuff = []
        for req in requireds:
            if is_inside_box(req[0], chunk):
                chunk_stuff.append(req)
        for xtra in optionals:
            if len(chunk_stuff) >= items_per_box:
                break
            if is_inside_box(xtra[0], chunk):
                chunk_stuff.append(xtra)
        reverse_it = (int(chunk_num / grid_size) % 2) == 1
        eastwest = sorted(chunk_stuff, key=lambda item: item[0][1], reverse=reverse_it)
        result += eastwest
    return result

initial_gpx="""
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.0">
	<name>Example gpx</name>
	<trk><name>Example gpx</name><number>1</number><trkseg>
"""

post_gpx = """
	</trkseg></trk>
</gpx>
"""
