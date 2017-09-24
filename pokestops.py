from accountdbsql import set_account_db_args
from geofence import filter_for_geofence
from gymdbsql import spawnpoints,spawns, pokestops
from datetime import datetime, timedelta
from geopy.distance import vincenty
from argparser import basic_std_parser,add_geofence
from gymdbsql import set_args
from itertools import islice
from geography import within_fences,step_position

parser = basic_std_parser("spawnpoints")
add_geofence(parser)
args = parser.parse_args()
set_args(args)
set_account_db_args(args)


class Pokestop:
    def __init__(self, id, latitude, longitude):
        self.id = id
        self.coords = ( latitude, longitude)
        self.twelves = 0
        self.neighbours = []

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        else:
            return False

    def __hash__(self):
        return hash(self.id)

    def add_neighbour(self, pokestop):
        self.neighbours.append( pokestop)

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
            current_result = neightbour.intersected_with( current_result)
        return current_result

    def is_within_range(self, other, m):
        return vincenty(self.coords, other.coords).m <= m

    def add_neighbours(self, otherpokestop, distance_requirement):
        if otherpokestop == self:
            return
        if self.is_within_range(otherpokestop, distance_requirement):
            self.neighbours.append(otherpokestop)
            otherpokestop.neighbours.append( self)

points = {}
point_list = []

print "Loading stops"
stops_to_check = filter_for_geofence(pokestops(), args.geofence, args.fencename)

for stop in stops_to_check:
    latitude_ = stop["latitude"]
    longitude_ = stop["longitude"]
    spawn_point = Pokestop(stop["pokestop_id"], latitude_, longitude_)
    points[stop["pokestop_id"]] = spawn_point
    point_list.append( spawn_point)

print "{} pokestops in area".format(str(len(point_list)))

DISTANCE = 78.0
for idx, point in enumerate(point_list):
    if idx % 500 == 0:
        print "Processing point at index " + str(idx)
    cutoff_long = step_position(point.coords, 0, DISTANCE)
    for point2 in islice(point_list, idx + 1 , None):
        point_longitude = point2.coords[1]
        if point_longitude > cutoff_long[1]:
            break
        point.add_neighbours(point2, DISTANCE)

for poke_stop in point_list:
    intersected = poke_stop.collected_neighbours()
    if len(intersected) > 3:
        print "{} neighbours ved https://www.google.com/maps/?daddr={},{}".format(
            len(intersected), str(poke_stop.coords[0]), str(poke_stop.coords[1]) )







