import logging
from exceptions import ValueError
from itertools import islice

from geography import step_position, center_geolocation, box_around, move_towards
from gymdbsql import spawnpoints_in_box
from scannerutil import equi_rect_distance_m
from spawnpoint import SpawnPoint

log = logging.getLogger(__name__)


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
        return equi_rect_distance_m(self.coords, other.coords) <= m

    def add_neighbours(self, otherpokestop, distance_requirement):
        if otherpokestop == self:
            return
        if self.is_within_range(otherpokestop, distance_requirement):
            self.neighbours.append(otherpokestop)
            otherpokestop.neighbours.append(self)

    def print_gmaps_coordinates(self):
            intersected = self.collected_neighbours()
            if len(intersected) >= 2:
                print("{} neighbours @ https://www.google.com/maps/?daddr={},{}".format(
                    len(intersected), str(self.coords[0]), str(self.coords[1])))
                return True
            else:
                return False


def create_pokestop(stop):
    latitude_ = stop["latitude"]
    longitude_ = stop["longitude"]
    altitude_ = stop["altitude"]
    return Pokestop(stop["pokestop_id"], latitude_, longitude_, altitude_)


def create_pokestops(stops_to_check):
    point_list = []

    for stop in stops_to_check:
        pokestop = create_pokestop(stop)
        point_list.append(pokestop)
    return point_list


def update_distances(point_list, radius=39):
    DISTANCE = 2 * radius
    for idx, point in enumerate(point_list):
        if idx % 500 == 0:
            print("Processing point at index " + str(idx))
        cutoff_long = step_position(point.coords, 0, DISTANCE)
        for point2 in islice(point_list, idx + 1, None):
            point_longitude = point2.coords[1]
            if point_longitude > cutoff_long[1]:
                break
            point.add_neighbours(point2, DISTANCE)


def find_largest_stop_group(stops):
    result = 0
    for poke_stop in stops:
        result = max(result, len(poke_stop.collected_neighbours()))
    return result


def find_largest_groups(point_list,min_size=3):
    all_coords = {}
    for stop in point_list:
        all_coords[stop.coords] = stop

    result_coords = []
    num_stops_found = 0
    max_stop_group = find_largest_stop_group(point_list)
    for counter in range(max_stop_group, min_size-1, -1):
        for poke_stop_ in point_list:
            intersected_ = poke_stop_.collected_neighbours()
            if len(intersected_) == counter and poke_stop_.coords in all_coords:
                locations = [n.coords for n in intersected_]
                result_coords.append((center_geolocation(locations), poke_stop_.collected_neighbours()))
                num_stops_found += len(locations)
                for location in locations:
                    if location in all_coords:
                        del all_coords[location]
                # clear out neighbours so they dont contribute to further collected_neighhbours
                for stop in intersected_:
                    stop.neighbours = []
    log.info("Found {} stops".format(str(num_stops_found)))
    return result_coords


def sort_by_distance(result_coords):
    arranged = [result_coords[0]]
    del result_coords[0]

    while len(result_coords) > 0:
        current = None
        dist = 10000000
        for idx, pair in enumerate(result_coords):
            coord = pair[0]
            distance = equi_rect_distance_m(coord, arranged[-1][0])
            if distance < dist:
                dist = distance
                current = idx
        if isinstance(current, int):
            arranged.append(result_coords[current])
            del result_coords[current]
        else:
            log.info("No more found ?")
            break
    return arranged


def sort_by_distance_with_treshhold(primary_candidates, additional_candidates, threshold=375):
    primary_candidates_ = list(primary_candidates)
    additional_candidates_ = list(additional_candidates)
    arranged = [primary_candidates_[0]]
    del primary_candidates_[0]

    while len(primary_candidates_) > 0:
        current, dist = index_of_closest_match(arranged[-1][0], primary_candidates_)
        if dist > threshold:
            currentX, distX = index_of_closest_match(arranged[-1][0], additional_candidates_)
            if currentX and (distX < threshold or distX < dist):
                arranged.append(additional_candidates_[current])
                del additional_candidates_[current]
            else:
                arranged.append(primary_candidates_[current])
                del primary_candidates_[current]
        else:
            arranged.append(primary_candidates_[current])
            del primary_candidates_[current]

    return arranged


def index_of_closest_match(coord, elements):
    current = None
    dist = 10000000
    for idx, pair in enumerate(elements):
        distance = equi_rect_distance_m(coord, pair[0])
        if distance < dist:
            dist = distance
            current = idx
    return current, dist


def print_gmaps_coordinates(stops):
    global poke_stop, intersected
    singles = 0
    for poke_stop in stops:
        if not poke_stop.print_gmaps_coordinates():
            singles += 1
    print(("{} single stops".format(singles)))


def find_optimal_location(stop_coords,SPIN_RANGE=38.5, CATCH_RANGE=20):
    global num_locs
    stop_box = box_around(stop_coords, SPIN_RANGE + CATCH_RANGE)
    sp = spawnpoints_in_box(stop_box)
    points = [SpawnPoint(x) for x in sp]
    in_range_of_stop = [p for p in points if p.is_within_range(stop_coords, SPIN_RANGE + CATCH_RANGE)]
    for idx, x in enumerate(in_range_of_stop):
        for n in points[idx + 1:]:
            x.add_neighhbours(n, 60)

    z = 0
    curr = None
    for x in in_range_of_stop:
        num_neigh = x.collected_neighbours()
        if num_neigh > z:
            curr = x
            z = num_neigh
    if not curr:
        return ()
    neighbours = curr.collected_neighbours()
    max_spawns = center_geolocation([x.location() for x in neighbours])

    m = equi_rect_distance_m(max_spawns, stop_coords)
    if m > SPIN_RANGE:
        max_spawns = move_towards(max_spawns, stop_coords, m - SPIN_RANGE)

    distance = equi_rect_distance_m(max_spawns, stop_coords)

    return max_spawns, len(neighbours), distance
