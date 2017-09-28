class GaveUp:
    """We tried and we tried, but it's simply not going to work out between us...."""


class GaveUpApiAction:
    """We tried and we tried, but it's simply not going to work out between us...."""

    def __init__(self, msg):
        self.msg = msg


class NoMoreWorkers:
    pass


class TooFarAway:
    def __init__(self, distance):
        self.distance = distance


class SkippedDueToOptional:

    def __init__(self, distance):
        self.distance = distance
