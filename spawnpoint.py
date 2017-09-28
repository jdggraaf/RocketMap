import unittest

from datetime import datetime, timedelta

from geopy.distance import vincenty


class SpawnPoints:
    def __init__(self, spawnpoints):
        self.spawnpoints = sorted(spawnpoints, key=lambda spawnpoint: spawnpoint.start())

    def points_that_can_spawn(self, last_not_seen_time, seen_time):
        return [x for x in self.spawnpoints if x.could_have_spawned(last_not_seen_time, seen_time)]

    def all_matching_spanwpoints(self, seen_at):
        return [x for x in self.spawnpoints if x.spawns_at(seen_at)]

    def search_points_for_runner(self, last_not_seen_time, seen_time):
        expanded_start_window = last_not_seen_time - timedelta(minutes=5)
        first_window = [x for x in self.spawnpoints if x.could_have_spawned(expanded_start_window, seen_time)]
        if len(first_window) > 0:
            return first_window
        expanded_start_window = last_not_seen_time - timedelta(minutes=10)
        return [x for x in self.spawnpoints if x.could_have_spawned(expanded_start_window, seen_time)]

    def spawn_point(self, spawn_point_id):
        for spawnpoint in self.spawnpoints:
            if spawnpoint.id == spawn_point_id:
                return spawnpoint

    def explain(self,pokemon_id, last_not_seen_time, seen_time):
        result = "Pokeomn {} in window {}-{} with".format(str(pokemon_id), str(second_of_hour(last_not_seen_time)), str(second_of_hour(seen_time)))
        for spawnpoint in self.spawnpoints:
            result += str(spawnpoint.start())
            result += "/"
        return result

    def __str__(self):
        result = ""
        for spawnpoint in self.spawnpoints:
            result += str(spawnpoint)
            result += " "
        return result


def second_of_hour(time):
    return time.minute * 60 + time.second


class SpawnPoint:
    def __init__(self, row):
        self.id = row["id"]
        self.latitude = row["latitude"]
        self.longitude = row["longitude"]
        self.altitude = row.get("altitude",None)
        self.kind = row["kind"]
        self.links = row["links"]
        self.latest_seen = row["latest_seen"]
        self.earliest_unseen = row["earliest_unseen"]
        self.neighbours = []

    def add_neighhbours(self, otherspawnpoint, distance_requirement):
        if otherspawnpoint == self:
            return
        if self.is_within_range(otherspawnpoint.location(), distance_requirement):
            self.neighbours.append(otherspawnpoint)
            otherspawnpoint.neighbours.append(self)

    def is_within_range(self, position, m):
        return vincenty(self.location(), position).m <= m


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


    def location(self):
        return self.latitude, self.longitude, self.altitude

    def __str__(self):
        startwindow = self.startwindow()
        start = startwindow[0]
        end = startwindow[1]
        return "{}/{}:{}-{}:{}".format(self.id, int(start / 60), start % 60, int(end / 60), end % 60)

    def duration(self):
        if self.kind == "hhhs":
            return 900
        if self.kind == "hhss":
            return 1800
        if self.kind == "ssss":
            return 3600
        if self.kind == "hsss":
            return 2700
        if self.kind == "hshs":
            return 2700
        raise ValueError("Dont know spawnpoint kind {}".format(self.kind))

    def startwindow(self):
        dur = self.duration()
        stop = (self.earliest_unseen - dur) % 3600
        return self.start(), stop

    def start(self):
        dur = self.duration()
        return (self.latest_seen - dur) % 3600

    def expires_at(self):
        return self.expires_at_with_time(datetime.now())

    def expires_at_with_time(self, now):
        dt = now.replace(minute=0, second=0, microsecond=0)
        if second_of_hour(now) > self.latest_seen:
            dt = dt + timedelta(hours=1)
        return dt + timedelta(seconds=self.latest_seen)

    def could_have_spawned(self, last_not_seen_time, seen_time):
        return self.could_have_spawned_soh(second_of_hour(last_not_seen_time),
                               second_of_hour(seen_time))

    def could_have_spawned_soh(self, last_not_seen_time_soh, seen_time_soh):
        pokemon_observation = (last_not_seen_time_soh,
                               seen_time_soh)
        return self.overlaps(pokemon_observation, self.startwindow())

    def spawns_at(self, instant):  # fix the hshs type
        pokemon_observation = (second_of_hour(instant),
                               second_of_hour(instant))
        return self.overlaps(pokemon_observation, self.startwindow())

    @staticmethod
    def overlaps(observations, spawnpoint_time):
        if observations[1] < observations[0]:  # normalize to non-wrapping time
            observations = (observations[0], observations[1] + 3600)
        if spawnpoint_time[1] < spawnpoint_time[0]:   # normalize to non-wrapping time
            spawnpoint_time = (spawnpoint_time[0], spawnpoint_time[1] + 3600)
        if observations[0] < spawnpoint_time[0] < observations[1]:
            return True
        if observations[0] < spawnpoint_time[1] < observations[1]:
            return True
        return False



class SpawnPoint_duration_test(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 600,
                 "earliest_unseen": 700, "s2cell": 1234, "altitude": 40}
        self.assertEqual(1800, SpawnPoint(point).duration())
        point["kind"] = "hhhs"
        self.assertEqual(900, SpawnPoint(point).duration())
        point["kind"] = "hshs"
        self.assertEqual(2700, SpawnPoint(point).duration())


class SpawnpointCouldHaveSpawned(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 3400,
                 "earliest_unseen": 3500, "s2cell": 1234, "altitude": 40}
        self.assertEqual((1600, 1700), SpawnPoint(point).startwindow())  # 26:40->28:20

        spawn_point = SpawnPoint(point)
        unseen = datetime(2016, 12, 1, 2, 25, 0)
        seen = datetime(2016, 12, 1, 2, 27, 0)
        self.assertTrue(spawn_point.could_have_spawned(unseen, seen))

        unseen = datetime(2016, 12, 1, 2, 27, 30)
        seen = datetime(2016, 12, 1, 2, 27, 30)
        self.assertFalse(spawn_point.could_have_spawned(unseen, seen))   # not really a use case

        outside_unseen = datetime(2016, 12, 1, 2, 39, 0)
        outside_seen = datetime(2016, 12, 1, 2, 30, 0)
        self.assertFalse(spawn_point.could_have_spawned(outside_unseen, outside_seen))


class SpawnPoint_Could_Have_Spawned_Wrapping_Hour(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 1680,
                 "earliest_unseen": 1680, "s2cell": 1234, "altitude": 40}

        spawn_point = SpawnPoint(point)
        unseen = datetime(2016, 12, 1, 2, 2, 0)
        expanded_start_window = unseen - timedelta(minutes=5)

        self.assertTrue(spawn_point.could_have_spawned_soh(second_of_hour(expanded_start_window),159))

# Pokeomn 63 in window 78-159 with185/1153/1638/1909/2170/2364/2389/2616/2890/3046/3490/
class SpawnPoints_could_have_spawned(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 3400,
                 "earliest_unseen": 3500, "s2cell": 1234, "altitude": 40}
        point2 = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 1900,
                 "earliest_unseen": 2000, "s2cell": 1234, "altitude": 40}

        spawn_point = SpawnPoint(point)  # 1600-1700 26:40-28:30
        print(spawn_point.startwindow())
        spawn_point2 = SpawnPoint(point2) # 100-200   1:40-3:20
        print(spawn_point2.startwindow())
        points = SpawnPoints([spawn_point, spawn_point2])
        unseen = datetime(2016, 12, 1, 2, 25, 0)
        seen = datetime(2016, 12, 1, 2, 27, 0)
        self.assertEqual(1, len(points.points_that_can_spawn(unseen, seen)))
        unseen2 = datetime(2016, 12, 1, 2, 2, 0)
        seen2 = datetime(2016, 12, 1, 2, 3, 30)
        self.assertEqual(1, len(points.points_that_can_spawn(unseen2, seen2)))

class SpawnPoints_expanded_start_window(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 3400,
                 "earliest_unseen": 3500, "s2cell": 1234, "altitude": 40}
        point2 = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 3200,
                 "earliest_unseen": 3200, "s2cell": 1234, "altitude": 40}
        point3 = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 2800,
                 "earliest_unseen": 2800, "s2cell": 1234, "altitude": 40}

        spawn_point = SpawnPoint(point)  # 1600-1700 26:40-28:30
        print(spawn_point.startwindow())
        spawn_point2 = SpawnPoint(point2) # 100-200   1:40-3:20
        print(spawn_point2.startwindow())
        spawn_point3 = SpawnPoint(point3) # 100-200   1:40-3:20
        print(spawn_point3.startwindow())
        points = SpawnPoints([spawn_point, spawn_point2, spawn_point3])
        unseen = datetime(2016, 12, 1, 2, 25, 0)
        seen = datetime(2016, 12, 1, 2, 27, 0)
        self.assertEqual(1, len(points.points_that_can_spawn(unseen, seen)))
        self.assertEqual(2, len(points.search_points_for_runner(unseen, seen)))



class overlap_test(unittest.TestCase):
    def test(self):
        spawn_point_time = (20, 40)

        inside = (21, 39)
        around = (19, 41)
        tangenting = (0, 20)  # Unsure if this is important. Should probably add 1 sec to spawn point window anyway
        outsidebefore = (0, 19)
        outsideafter = (41, 50)

        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 600,
                 "earliest_unseen": 700, "s2cell": 1234, "altitude": 40}
        spawn_point = SpawnPoint(point) # start window 2400-2500 40:0-41:40
        print(str(spawn_point))
        # todo: think very hard about what to do when spawn point uncertainty > obeservation window (spawn point time fully contained in obs)
        # self.assertTrue(spawn_point.overlaps(inside, spawn_point_time))
        self.assertFalse(spawn_point.overlaps(outsidebefore, spawn_point_time))
        self.assertFalse(spawn_point.overlaps(outsideafter, spawn_point_time))
        self.assertTrue(spawn_point.overlaps(around, spawn_point_time))


class SpawnPoint_startwindow_test(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 3400,
                 "earliest_unseen": 3500, "s2cell": 1234, "altitude": 40}
        self.assertEqual((1600, 1700), SpawnPoint(point).startwindow())
        point["kind"] = "hhhs"
        self.assertEqual((2500, 2600), SpawnPoint(point).startwindow())
        point["kind"] = "hshs"
        self.assertEqual((700, 800), SpawnPoint(point).startwindow())


class SpawnPoint_startwindow_wrapping_test(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 600,
                 "earliest_unseen": 700, "s2cell": 1234, "altitude": 40}
        self.assertEqual((2400, 2500), SpawnPoint(point).startwindow())
        point["kind"] = "hhhs"
        self.assertEqual((3300, 3400), SpawnPoint(point).startwindow())
        point["kind"] = "hshs"
        self.assertEqual((1500, 1600), SpawnPoint(point).startwindow())


class SpawnPoint_expires_at(unittest.TestCase):
    def test(self):
        point = {"id": 123, "latitude": 43.2, "longitude": 48.6, "kind": "hhss", "links": "hh??", "latest_seen": 600,
                 "earliest_unseen": 700, "s2cell": 1234, "altitude": 40}
        print(str(SpawnPoint(point).expires_at()))
