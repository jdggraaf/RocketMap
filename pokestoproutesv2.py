from hamburg import hamburg_grind, stop_route_initial, stop_route_1, xp_route_1, stop_route_2, xp_route_2, \
    big_xp_route_1, big_xp_route_2

initial_130_stops = {"hamburg": list(reversed(stop_route_initial))}
initial_grind = {"hamburg": hamburg_grind}

routes_p1 = {"hamburg": stop_route_1}
xp_p1 = {"hamburg": xp_route_1}

routes_p2 = {"hamburg": stop_route_2}
xp_p2 = {"hamburg": xp_route_2}


double_xp_1 = {"hamburg" : big_xp_route_1}
double_xp_2 = {"hamburg" : list(reversed(big_xp_route_2))}
