import time
import unittest

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool

    from vn300_qt_app import VN300QtApp, Worker
except ImportError:
    QCoreApplication = None
    QThreadPool = None
    VN300QtApp = None
    Worker = None


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


if __name__ == "__main__":
    unittest.main()
