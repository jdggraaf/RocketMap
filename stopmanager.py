from pgoapi.hash_server import HashServer

from accounts import *
from behaviours import PHASE_0_ITEM_LIMITS, L20_ITEM_LIMITS, \
    beh_aggressive_bag_cleaning, L12_ITEM_LIMITS, beh_spin_pokestop, beh_spin_nearby_pokestops
from geography import *
from inventory import inventory
from scannerutil import setup_logging, nice_number_2

log = logging.getLogger(__name__)


class StopManager(object):
    def __init__(self, worker, catch_manager, worker_manager, max_stops):
        self.max_stops = max_stops
        self.worker = worker
        self.next_spin_log = 10
        self.save_pokestops = False
        self.catch_manager = catch_manager
        self.worker_manager = worker_manager
        self.spin_timestamps = []
        self.spun_stops = set()
        self.log_xp_at = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)

    def spin_stops(self, map_objects, pokestop_id, player_position, index, exclusions={}):
        if self.should_spin(index):
            if self.worker_manager.has_active_lucky_egg():
                self.spin_all_stops(map_objects, player_position, exclusion=exclusions)
            else:
                self.spin_single_stop(map_objects, player_position, pokestop_id, exclusions)

    def should_spin(self, index):
        return (self.save_pokestops and index % 2 == 0) or not self.save_pokestops

    def spin_all_stops(self, map_objects, player_position, range_m=39, exclusion={}):
        spuns = beh_spin_nearby_pokestops(self.worker, map_objects, player_position, range_m, self.spun_stops,
                                          exclusion)
        for stop in spuns:
            self.spun_stops.add(stop)
        return len(spuns)

    def spin_single_stop(self, map_objects, player_position, pokestop_id, exclusions):
        if pokestop_id in exclusions:
            log.info("Not spinning excluded stop {}".format(pokestop_id))
            return
        if pokestop_id in self.spun_stops:
            log.info("Skipping stop {}, already spun".format(pokestop_id))
        spin_pokestop = beh_spin_pokestop(self.worker, map_objects, player_position, pokestop_id)
        if spin_pokestop == 4:
            beh_aggressive_bag_cleaning(self.worker)
            spin_pokestop = beh_spin_pokestop(self.worker, map_objects, player_position, pokestop_id)

        if spin_pokestop == 1:
            self.spun_stops.add(pokestop_id)
            if len(self.spun_stops) == 2500 and self.catch_manager.pokemon_caught < 1200:
                self.save_pokestops = True
            self.spin_timestamps.append(datetime.now())
        else:
            log.info("Spinning failed {}".format(str(spin_pokestop)))

    def num_spins_last_30_minutes(self):
        self.__trim_to30_minutes()
        return len(self.spin_timestamps)

    def __trim_to30_minutes(self):
        thirty_minutes_ago = datetime.now() - timedelta(minutes=30)
        self.spin_timestamps = [x for x in self.spin_timestamps if x > thirty_minutes_ago]

    def log_status(self, egg_active, has_egg, egg_number, index, phase):
        if datetime.now() > self.log_xp_at:
            self.log_xp_at = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
            self.next_spin_log = len(self.spun_stops) + 10
            num_stops = self.num_spins_last_30_minutes()
            rem = HashServer.status.get('remaining', 0)
            ratio = float(self.catch_manager.pokemon_caught) / len(self.spun_stops) if len(self.spun_stops) > 0 else 0
            xp = self.worker.account_info()["xp"]
            self.worker_manager.register_xp(xp)
            xp_30min_ago = self.worker_manager.xp_30_minutes_ago()
            log.info("P{}L{}, {}S/{}P//R{}, {}E/{}EW, {}XP/{}@30min{}{}, {}S@30min. idx={}, {} hash"
                     .format(str(phase), str(self.worker_manager.level), str(len(self.spun_stops)),
                             str(self.catch_manager.pokemon_caught), str(nice_number_2(ratio)),
                             str(self.catch_manager.evolves),
                             str(self.catch_manager.num_evolve_candidates()),
                             str(xp), str(xp - xp_30min_ago), 'E' + str(egg_number) if egg_active else '',
                             'H' if has_egg else '',
                             str(num_stops), str(index), str(rem)))

    def reached_limits(self):
        if len(self.spun_stops) > self.max_stops:
            log.info("Reached target spins {}".format(str(len(self.spun_stops))))
            return True
        if self.worker_manager.reached_target_level():
            return True
        return False

    def log_inventory(self):
        log.info("Inventory:{}".format(str(inventory(self.worker))))

    def clear_state(self):
        self.spun_stops = set()
