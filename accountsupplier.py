class AccountSupplier(object):
    def __init__(self, account_manager, require_lures):
        self.require_lures = require_lures
        self.account_manager = account_manager

    def get_account(self):
        pass

    def get_account_with_lures(self, pos):
        worker = wrap_account_no_replace(self.account_manager.get_account(), self.account_manager)
        worker.account_info().update_position(pos)
        if worker.account_info().lures == 0:
            return self.get_account_with_lures(pos)
        try:
            branded = self.brander(worker)
        except LoginSequenceFail as e:
            self.account_manager.report_failure()
            return None
        self.account_manager.clear_failure()
        return branded


    def get_worker_with_nonzero_lures(self, pos):
        while self.worker is None or self.worker.account_info().lures == 0:
            if self.worker:
                log.info("Skipping {}, lures are spent".format(self.worker.name()))
            self.replace_worker(self.get_account_with_lures(pos))

