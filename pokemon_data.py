import os
from flask import json

def pmdata():
    pokemon = os.path.dirname(os.path.abspath(os.path.realpath(__file__))) + "/docs/pokemon.min.json"
    with open(pokemon) as data_file:
        # just open the file...
        input_file = file(pokemon, "r")
        # read the file and decode possible UTF-8 signature at the beginning
        # which can be the case in some files.
        return json.loads(input_file.read().decode("utf-8-sig"))
        # return json.load(data_file, encoding="utf8")

pokemons = pmdata()

candy12 = {10, 13, 16}
candy25 = {19, 29, 32, 41, 43, 60, 63, 66, 69, 74, 92, 116, 133, 147, 152, 155, 158, 161, 165, 183, 187}
candy50 = {21, 161, 163, 167, 177, 194, 220, 353}

def pokemon_name(pid):
    return pokemons[str(pid)].get("name", str(pid)).encode('utf-8')



