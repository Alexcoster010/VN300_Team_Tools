import time
import unittest

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool

    from vn300_qt_app import EDITABLE_RUN_METADATA_FIELDS, VN300QtApp, Worker
except ImportError:
    QCoreApplication = None
    QThreadPool = None
    VN300QtApp = None
    Worker = None
    EDITABLE_RUN_METADATA_FIELDS = ()


@unittest.skipUnless(QCoreApplication is not None, "PySide6 is required")
class QtWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def test_worker_is_retained_until_queued_result_is_delivered(self):
        class Owner:
            start_worker = VN300QtApp.start_worker
            release_worker = VN300QtApp.release_worker

            def __init__(self):
                self.thread_pool = QThreadPool()
                self.active_workers = set()

        owner = Owner()
        results = []
        worker = Worker(lambda: {"status": "idle"})
        worker.signals.result.connect(results.append)
        owner.start_worker(worker)
        del worker

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and (not results or owner.active_workers):
            self.application.processEvents()
            time.sleep(0.01)

        self.assertEqual(results, [{"status": "idle"}])
        self.assertFalse(owner.active_workers)
        self.assertTrue(owner.thread_pool.waitForDone(1000))

    def test_drive_day_setup_includes_every_editable_logger_field(self):
        expected = {
            "driver", "test_location", "test_type", "course", "car_config", "tire_compound",
            "cold_fl_psi", "cold_fr_psi", "cold_rl_psi", "cold_rr_psi",
            "hot_fl_psi", "hot_fr_psi", "hot_rl_psi", "hot_rr_psi",
            "ambient_temp_f", "track_temp_f", "front_camber_deg", "rear_camber_deg",
            "front_toe_deg", "rear_toe_deg", "ride_height_front_mm", "ride_height_rear_mm",
            "damper_front", "damper_rear", "anti_roll_bar_front", "anti_roll_bar_rear",
            "brake_bias", "aero_config", "battery_or_fuel_state", "valid_run", "notes",
        }
        self.assertEqual(set(EDITABLE_RUN_METADATA_FIELDS), expected)
        self.assertEqual(len(EDITABLE_RUN_METADATA_FIELDS), len(expected))


if __name__ == "__main__":
    unittest.main()
