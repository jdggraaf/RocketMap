def has_lucky_egg(inventory):
    return 301 in __inv(inventory)


def egg_count(worker):
    return __inv(worker).get(301, 0)


def lure_count(worker):
    return __inv(worker).get(501, 0)


def ultra_balls(worker):
    return __inv(worker).get(3, 0)


def poke_balls(worker):
    return __inv(worker).get(1, 0)


def blue_ball(worker):
    return __inv(worker).get(2, 0)


def total_balls(worker):
    return ultra_balls(worker) + blue_ball(worker) + poke_balls(worker)


def total_iventory_count(worker):
    total = 0
    for key, value in __inv(worker).iteritems():
        total += value
    return total


def __inv(worker):
    return worker.account_info()["items"]
