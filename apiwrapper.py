import logging

log = logging.getLogger(__name__)

error_codes = {
    0: 'UNSET',
    1: 'SUCCESS',
    2: 'POKEMON_DEPLOYED',
    3: 'FAILED',
    4: 'ERROR_POKEMON_IS_EGG',
    5: 'ERROR_POKEMON_IS_BUDDY'
}


class ReleasePokemon:

    def __init__(self, response):
        self.response = response

    def ok(self):
        result = self.response['responses']['RELEASE_POKEMON']['result']
        if result != 1:
            log.error(u'Error while transfer pokemon: {}'.format(error_codes[result]))
            return False

        return True


class CodenameResult:
    codename_result_error_codes= {
        0: 'UNSET',
        1: 'SUCCESS',
        2: 'CODENAME_NOT_AVAILABLE',
        3: 'CODENAME_NOT_VALID',
        4: 'CURRENT_OWNER',
        5: 'CODENAME_CHANGE_NOT_ALLOWED'
    }

    def __init__(self, response):
        self.response = response

    def ok(self):
        result = self.response.get("responses", {}).get("CLAIM_CODENAME", {})
        status = result.get("status", 0)
        if status == 1:
            return True
        log.error(u'Error while renaming player: {}'.format(self.codename_result_error_codes[status]))
        return False


class EncounterPokemon:
    encounter_error_codes = {
        0: 'ENCOUNTER_ERROR',
        1: 'ENCOUNTER_SUCCESS',
        2: 'ENCOUNTER_NOT_FOUND',
        3: 'ENCOUNTER_CLOSED',
        4: 'ENCOUNTER_POKEMON_FLED',
        5: 'ENCOUNTER_NOT_IN_RANGE',
        6: 'ENCOUNTER_ALREADY_HAPPENED',
        7: 'POKEMON_INVENTORY_FULL'
    }

    def __init__(self, response, encounter_id):
        self.expected_encounter_id = encounter_id
        self.response = response

    def probability(self):
        encounter = self.response.get("responses", {}).get("ENCOUNTER", {})
        status = encounter["status"]
        if status != 1:
            if status == 4:
                log.info(u'Pokemon fled from encounter')
            else:
                log.error(u'Error while encountering pokemon: {}'.format(self.encounter_error_codes[status]))
            return
        resp = encounter.get("capture_probability", None)
        return resp

    def contains_expected_encounter(self):
        wild = self.response.get("responses", {}).get("ENCOUNTER", {}).get("wild_pokemon", {})
        actual_encounter_id = wild.get("encounter_id", None)
        return self.expected_encounter_id == actual_encounter_id


