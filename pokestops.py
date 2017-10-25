import logging
from exceptions import ValueError
from itertools import islice

from geopy.distance import vincenty

from argparser import basic_std_parser, add_geofence
from geofence import get_geofences
from geography import step_position, center_geolocation, lat_routed, as_3d_coord_array
from gymdbsql import pokestops, altitudes, insert_altitude
from gymdbsql import set_args
from pogom.fnord_altitude import with_gmaps_altitude
from pogom.utils import cellid
from pokestop_routes import all_routes
from scannerutil import precise_coordinate_string, equi_rect_distance

parser = basic_std_parser("pokestops")
parser.add_argument('-k', '--gmaps-key',
                    help='Google Maps Javascript API Key.',
                    required=False)
add_geofence(parser)
args = parser.parse_args()
set_args(args)

logging.basicConfig(
    format='%(asctime)s [%(threadName)12s][%(module)10s][%(levelname)8s] ' +
           '%(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("pgoapi").setLevel(logging.WARN)
logging.getLogger("connectionpool").setLevel(logging.WARN)
logging.getLogger("Account").setLevel(logging.INFO)


class Pokestop:
    def __init__(self, id, latitude, longitude, altitude):
        self.id = id
        if altitude is None:
            raise ValueError
        self.coords = (latitude, longitude, altitude)
        self.twelves = 0
        self.neighbours = []

    def __getitem__(self, key):
        if key == 0:
            return self.coords[0]
        if key == 1:
            return self.coords[1]
        if key == 2:
            return self.coords[2]

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        else:
            return False

    def __hash__(self):
        return hash(self.id)

    def add_neighbour(self, pokestop):
        self.neighbours.append(pokestop)

    def neightbours_with_self(self):
        neighbours = self.neighbours[:]
        neighbours.append(self)
        return neighbours

    def intersected_with(self, other):
        return list(set(self.neightbours_with_self()) & set(other))

    def collected_neighbours(self):
        current_result = self.neightbours_with_self()
        copy = current_result[:]
        for neightbour in copy:
            current_result = neightbour.intersected_with(current_result)
        return current_result

    def is_within_range(self, other, m):
        return vincenty(self.coords, other.coords).m <= m

    def add_neighbours(self, otherpokestop, distance_requirement):
        if otherpokestop == self:
            return
        if self.is_within_range(otherpokestop, distance_requirement):
            self.neighbours.append(otherpokestop)
            otherpokestop.neighbours.append(self)


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


def approximate_pokestop_alts():
    add_altitudes(pokestops())


def update_alts_from_gmaps():
    for route in all_routes.values():
        for pos in route:
            if len(pos) == 3:
                insert_altitude(cellid(pos), pos[0], pos[1], pos[2])


def create_pokestop(stop):
    latitude_ = stop["latitude"]
    longitude_ = stop["longitude"]
    altitude_ = stop["altitude"]
    return Pokestop(stop["pokestop_id"], latitude_, longitude_, altitude_)


points = {}
point_list = []

print "Loading stops"
fences_to_use = get_geofences(args.geofence, args.fencename)

stops_to_check = fences_to_use.filter_forts(pokestops())
log.info("There are {} stops within fence".format(str(len(stops_to_check))))
add_altitudes(stops_to_check)



for stop in stops_to_check:
    pokestop = create_pokestop(stop)
    points[stop["pokestop_id"]] = pokestop
    point_list.append(pokestop)


# print as_3d_coord_array(point_list)

def stop_string(combined):
    return "((" + precise_coordinate_string(combined[0]) +"),(" + precise_coordinate_string(combined[1].coords) + "," + repr(combined[1].id) + "))"

fenced78 = lat_routed(fences_to_use, 78, [x for x in point_list])
print "[" + "\n, ".join([stop_string(x) for x in fenced78]) + "]"

print as_3d_coord_array(fenced78)


DISTANCE = 78.0
for idx, point in enumerate(point_list):
    if idx % 500 == 0:
        print "Processing point at index " + str(idx)
    cutoff_long = step_position(point.coords, 0, DISTANCE)
    for point2 in islice(point_list, idx + 1, None):
        point_longitude = point2.coords[1]
        if point_longitude > cutoff_long[1]:
            break
        point.add_neighbours(point2, DISTANCE)

all_coords = {}
for stop in point_list:
    all_coords[stop.coords] = stop


def find_largest_stop_group():
    result = 0
    for poke_stop in point_list:
        result = max(result, len(poke_stop.collected_neighbours()))
    return result


def print_gmaps_coordinates():
    global poke_stop, intersected
    singles = 0
    for poke_stop in point_list:
        intersected = poke_stop.collected_neighbours()
        if len(intersected) >= 2:
            print "{} neighbours @ https://www.google.com/maps/?daddr={},{}".format(
                len(intersected), str(poke_stop.coords[0]), str(poke_stop.coords[1]))
        else:
            singles += 1
    print "{} single stops".format(singles)


print_gmaps_coordinates()


def print_coordinates():
    global poke_stop, intersected
    for poke_stop in point_list:
        intersected = poke_stop.collected_neighbours()
        if len(intersected) > 2:
            print "{},{}".format(str(poke_stop.coords[0]), str(poke_stop.coords[1]))


# print_coordinates()

result_coords = []
num_stops_found = 0
max_stop_group = find_largest_stop_group()
print "Your area has {} stops reachable from a single position".format(str(max_stop_group))
for counter in range(max_stop_group, 0, -1):
    for poke_stop in point_list:
        intersected = poke_stop.collected_neighbours()
        if len(intersected) == counter and poke_stop.coords in all_coords:
            locations = [n.coords for n in intersected]
            result_coords.append(center_geolocation(locations))
            num_stops_found += len(locations)
            for location in locations:
                if location in all_coords:
                    del all_coords[location]
            # clear out neighbours so they dont contribute to further collected_neighhbours
            for stop in intersected:
                stop.neighbours = []
    if num_stops_found > 2000:
        log.info("Found {} stops, stopping".format(str(num_stops_found)))
        break

log.info("Found {} stops".format(str(num_stops_found)))

arranged = [result_coords[0]]
del result_coords[0]

while len(result_coords) > 0:
    current = None
    dist = 10000000
    for idx, coord in enumerate(result_coords):
        distance = equi_rect_distance(coord, arranged[-1])
        if distance < dist:
            dist = distance
            current = idx
    if isinstance(current, int):
        arranged.append(result_coords[current])
        del result_coords[current]
    else:
        log.info("No more found ?")
        break

with_alt = [with_gmaps_altitude(x, args.gmaps_key) for x in arranged]

msg = "["
for coord in with_alt:
    msg += "(" + precise_coordinate_string(coord) + "), "
print "Traversal route for all pokestops"
print msg + "]"
