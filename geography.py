import logging
import math
import unittest
from collections import defaultdict
from math import cos, sin, atan2, sqrt
from geopy.distance import vincenty
from datetime import datetime as dt

from scannerutil import nice_coordinate_string

log = logging.getLogger(__name__)



def fnords_box_moves_generator(topleft, bottomright, step_distance):
    current_pos = step_position(bottomright, step_distance/2, -step_distance/2)
    moving_left = True
    while is_inside_box_coords(current_pos, topleft, bottomright):
        yield current_pos
        if moving_left:
            next_pos = step_position(current_pos, 0.0, -step_distance)
        else:
            next_pos = step_position(current_pos, 0.0, +step_distance)
        if not is_inside_box_coords(next_pos, topleft, bottomright):
            next_pos = step_position(current_pos, step_distance, 0.0)
            if not is_inside_box_coords(next_pos, topleft, bottomright):
                return
            moving_left = not moving_left
        current_pos = next_pos


def box_moves_generator(topleft, bottomright):
    current_pos = step_position(bottomright, 303.5, -303.0)
    moving_left = True
    while is_inside_box_coords(current_pos, topleft, bottomright):
        yield current_pos
        if moving_left:
            next_pos = step_position(current_pos, 0.0, -707.0)
        else:
            next_pos = step_position(current_pos, 0.0, +707.0)
        if not is_inside_box_coords(next_pos, topleft, bottomright):
            next_pos = step_position(current_pos, 707.0, 0.0)
            if not is_inside_box_coords(next_pos, topleft, bottomright):
                return
            moving_left = not moving_left
        current_pos = next_pos


def num_box_steps(topleft, bottomright):
    current_pos = step_position(bottomright, 303.5, -303.0)
    left = 0
    north = 0
    while is_inside_box_coords(current_pos, topleft, bottomright):
        current_pos = step_position(current_pos, 0.0, -707.0)
        left += 1
    current_pos = step_position(bottomright, 303.5, -303.0)
    while is_inside_box_coords(current_pos, topleft, bottomright):
        current_pos = step_position(current_pos, 707.0, 0.0)
        north += 1
    return left * north


def gym_moves_generator(gyms):
    num = 0
    while num < (len(gyms)):
        currentpos = gyms[num]
        yield currentpos
        num += 1


def moves_generator(pos, steps):
    num = 0
    currentpos = pos
    while num < steps:
        yield currentpos
        currentpos = step_position(currentpos, 0.0, -707.0)
        num += 1


def width_generator(pos, steps):
    num = 0
    currentpos = pos
    while num < steps:
        yield currentpos
        currentpos = step_position(currentpos, 707.0, 0.0)
        num += 1


def step_position(pos, north, east):
    dy = north
    dx = east
    lat0 = pos[0]
    lon0 = pos[1]
    lat = lat0 + (180 / 3.1415929) * (dy / 6378137)
    lon = lon0 + (180 / 3.1415929) * (dx / 6378137) / math.cos(3.1415929 / 180.0 * lat0)
    if len(pos) == 2:
        return lat, lon
    else:
        return lat, lon, pos[2]


def geo_chunk(coordinates, gridsize=4):
    box = geo_box(coordinates)
    for box in chunk_box(box, gridsize):
        for coord in coordinates:
            if is_inside_box(coord, box):
                yield coord

def geo_chunk_map(coordinates, gridsize=4):
    box = geo_box(coordinates)
    log.info("Geo box is {}".format(str(box)))
    result = defaultdict(list)
    boxes = list(chunk_box(box, gridsize))
    for coord in coordinates:
        for box in boxes:
            if is_inside_box(coord, box):
                result[box].append(coord)
    return result



def chunk_box(box, gridsize=4):
    lat_step = float(box[1][0] - box[0][0]) / gridsize
    long_step = float(box[1][1] - box[0][1]) / gridsize
    for lat in range(0, gridsize):
        for lng in range(0, gridsize):
            topleft = (box[0][0] + (lat_step * lat), box[0][1] + (long_step * lng))
            yield topleft, (topleft[0] + lat_step, topleft[1] + long_step)


def geo_box(coordinates):
    min_lat = 180
    max_lat = -180
    min_lon = 180
    max_lon = -180
    for coord in coordinates:
        min_lat = min(min_lat, coord[0])
        max_lat = max(max_lat, coord[0])
        min_lon = min(min_lon, coord[1])
        max_lon = max(max_lon, coord[1])
    return (max_lat, min_lon), (min_lat, max_lon)


def time_between_locations(start, end, meters_per_second):
    if not end:
        return 0
    distance = vincenty(start, end).m
    return distance / meters_per_second if distance > 0 else 0


def is_inside_box(pos, box):
    return is_inside_box_coords(pos, box[0], box[1])


def is_inside_box_coords(pos, top_left, bottom_right):
    latmatch = top_left[0] >= pos[0] >= bottom_right[0]
    longmatch = top_left[1] <= pos[1] <= bottom_right[1]
    return latmatch and longmatch


def within_fences(latitude, longitude, fences):
    if len(fences) == 0:
        return True
    for fence in fences:
        if fence.contains(latitude, longitude):
            return True
    return False


def steps_between_points(start, stop,num_steps):
    ax= start[0]
    ay = start[1]
    bx = stop[0]
    by = stop[1]
    dx, dy = (bx - ax, by - ay)
    result = []
    stepx, stepy = (dx / float(num_steps+1), dy / float(num_steps+1))
    for i in range(num_steps):
        result.append((start[0] + (1+i)*stepx, start[1] + (1+i)*stepy))
    return result


def center_geolocation(geolocations):
    """
    Provide a relatively accurate center lat, lon returned as a list pair, given
    a list of list pairs.
    ex: in: geolocations = ((lat1,lon1), (lat2,lon2),)
        out: (center_lat, center_lon)
    """
    x = 0
    y = 0
    z = 0

    for tuple_ in geolocations:
        lat = tuple_[0]
        lon = tuple_[1]
        lat = float(math.radians(lat))
        lon = float(math.radians(lon))
        x += cos(lat) * cos(lon)
        y += cos(lat) * sin(lon)
        z += sin(lat)

    x = float(x / len(geolocations))
    y = float(y / len(geolocations))
    z = float(z / len(geolocations))

    rlat = float(math.degrees(atan2(z, sqrt(x * x + y * y))))
    rlng = float(math.degrees(atan2(y, x)))
    if len(geolocations[0]) == 3:
        return rlat, rlng, geolocations[0][2]
    else:
        return rlat, rlng


class BoxMovesTest(unittest.TestCase):
    def test(self):
        generator = box_moves_generator((59.934862, 10.71567),
                                        (59.905849, 10.768023))
        items = list(generator)
        self.assertEqual(len(items), 20)
        self.assertEquals(
            num_box_steps((59.934862, 10.71567), (59.905849, 10.768023)), 20)
        self.assertEqual(items[0][0], items[1][0])
        self.assertEqual(items[0][0], items[2][0])
        self.assertEqual(items[0][0], items[3][0])
        self.assertNotEquals(items[0][0], items[4][0])
        self.assertEqual(items[3][1], items[4][1])  # turning point

class BoxTest(unittest.TestCase):
    def test(self):
        box = geo_box([(59.935684, 10.682678), (59.935684, 10.682478), (59.921234, 10.684459), (59.926481, 10.712504)])
        self.assertEqual(box[0][0], 59.935684)
        self.assertEqual(box[0][1], 10.682478)
        self.assertEqual(box[1][0], 59.921234)
        self.assertEqual(box[1][1], 10.712504)

class BoxChunkTest(unittest.TestCase):
    def test(self):
        box = geo_box([(59, 9.5), (58.9, 9), (58.5, 10), (58, 9.75)])
        self.assertEqual(box, ((59,9),(58,10)))
        chunks = list(chunk_box(box, 2))
        self.assertEqual(chunks[0], ((59.0,9.0),(58.5,9.5)))
        self.assertEqual(chunks[1], ((59.0,9.5),(58.5,10.0)))
        self.assertEqual(chunks[2], ((58.5,9.0),(58.0,9.5)))
        self.assertEqual(chunks[3], ((58.5,9.5),(58.0,10.0)))


class MyTest2(unittest.TestCase):
    def test(self):
        top_left = (60.0, 9.0)
        box = (top_left, (58.0, 10.0))
        self.assertEqual(is_inside_box((59.0, 9.5), box), True)
        self.assertEqual(is_inside_box((61.0, 9.5), box), False)
        self.assertEqual(is_inside_box((57.0, 9.5), box), False)
        self.assertEqual(is_inside_box((59.0, 11), box), False)
        self.assertEqual(is_inside_box((59.0, 8), box), False)
        self.assertEqual(is_inside_box((60.0, 9.5), box), True)
        self.assertEqual(is_inside_box((59.0, 9.0), box), True)
        self.assertEqual(is_inside_box((58.0, 9.5), box), True)
        self.assertEqual(is_inside_box((57.9999, 9.5), box), False)
        self.assertEqual(is_inside_box((59.0, 10.0), box), True)


class TestGeo(unittest.TestCase):
    def test(self):
        coords = [(59.925818, 10.7032860), (59.925846, 10.7035530), (59.926148, 10.7027230), (59.926396, 10.7032060)]
        center = center_geolocation(coords)
        print "center is {}".format(str(center))
        for coord in coords:
            print("dist to {} is {}".format(str(coord), vincenty(center, coord).m))


class TestGeo2(unittest.TestCase):
    def test(self):
        coords = [(59.940370, 10.721415), (59.940166, 10.7206500), (59.939642, 10.7221430), (59.939620, 10.7215500)]
        center = center_geolocation(coords)
        print "center is {}".format(str(center))
        for coord in coords:
            print("dist to {} is {}".format(str(coord), vincenty(center, coord).m))

class TestSteps(unittest.TestCase):
    def test(self):
        start = 59.904162, 10.842091
        stop = 59.898157, 10.831147
        offset = steps_between_points(start, stop, 2)
        self.assertEquals( (59.902160333333335,10.838443), offset[0])
        self.assertEquals( (59.90015866666666, 10.834795), offset[1])

    def test_other_direction(self):
        start = 59.898157, 10.831147
        stop = 59.904162, 10.842091
        offset = steps_between_points(start, stop, 3)
        self.assertEquals( (59.902160333333335,10.838443), offset[1])
        self.assertEquals( (59.90015866666666, 10.834795), offset[0])

    def test_misc_stuff(self):
        start = 59.898157, 10.831147
        stop = 59.904162, 10.842091
        seconds = 49.1
        num_steps = int(seconds / 10)
        offset = steps_between_points(start, stop, num_steps)
        print offset
