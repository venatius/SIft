import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from sift import database, image_store, image_worker, metadata
from sift.image_browser import ImageBrowser
from sift.local_recognition import LABELS, LocalRecognizer, suggestion
from sift.scanner import scan_folder
from sift.worker import ScanWorker


class PersonalReviewTests(unittest.TestCase):
    def test_names_are_suggestions_with_uncertainty(self):
        scores = [0.1] * len(LABELS)
        scores[0] = 0.8
        self.assertEqual(suggestion(scores, "personal.JPG"), ("Screenshots", "screenshot.jpg", 0))
        scores[1] = 0.76
        self.assertEqual(
            suggestion(scores, "personal.JPG"), ("Screenshots", "image-to-review.jpg", 1)
        )

    def test_missing_model_stops_before_loading_dependencies(self):
        with self.assertRaisesRegex(ValueError, "not set up"):
            LocalRecognizer(Path(__file__) / "not-a-model-folder")

    def test_linked_model_directory_is_refused_before_loading(self):
        with (
            patch("sift.local_recognition.is_link_or_reparse_point", return_value=True),
            self.assertRaisesRegex(ValueError, "linked paths"),
        ):
            LocalRecognizer(Path(__file__).parent)

    def test_ffprobe_restricts_input_protocol(self):
        with patch.object(metadata.subprocess, "Popen") as popen:
            popen.return_value.communicate.return_value = (b"", b"")
            metadata._run_ffprobe("ffprobe", "synthetic.mp4", lambda: False)
        arguments = popen.call_args.args[0]
        index = arguments.index("-protocol_whitelist")
        self.assertEqual(arguments[index + 1], "file")
        self.assertLess(index, arguments.index("-i"))
        formats = arguments[arguments.index("-format_whitelist") + 1].split(",")
        self.assertNotIn("hls", formats)
        self.assertNotIn("concat", formats)

    def test_network_root_is_rejected_before_directory_access(self):
        with (
            patch("sift.scanner.is_network_path", return_value=True),
            patch("sift.scanner.os.scandir") as scandir,
        ):
            rows = list(scan_folder("network-root"))
        scandir.assert_not_called()
        self.assertEqual(rows[0].status, "skipped")

    def test_review_tabs_and_categories_use_saved_previews(self):
        base = Path(__file__).resolve().parents[1] / "test_data_auto"
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            root = Path(temporary) / "library"
            root.mkdir()
            db = str(Path(temporary) / "test.db")
            source = root / "capture.png"
            Image.new("RGB", (32, 32), "red").save(source)
            copy = root / "copy.png"
            copy.write_bytes(source.read_bytes())
            before = (source.read_bytes(), source.stat().st_mtime_ns)
            conn = database.connect(db)
            database.init_db(conn)
            conn.close()
            ScanWorker(str(root), db).run()
            image_worker.prepare(db)
            with patch.object(image_worker, "LocalRecognizer") as model:
                model.return_value.return_value = ("Screenshots", "screenshot.png", 0)
                result = image_worker.recognize(db, None, lambda: False, lambda _: None)
                repeated = image_worker.recognize(db, None, lambda: False, lambda _: None)
            self.assertEqual(result, {"classified": 2, "failed": 0})
            self.assertEqual(repeated, result)
            self.assertEqual(model.return_value.call_count, 4)
            self.assertEqual(len(list(Path(temporary).glob("*.bak"))), 2)
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
            source.unlink()
            copy.unlink()
            conn = database.connect_readonly(db)
            try:
                self.assertEqual(len(image_store.browse(conn, category="review")[0]), 2)
                self.assertEqual(image_store.browse(conn, category="Documents")[0], [])
                with patch.object(image_store, "RECOGNITION_VERSION", "new-version"):
                    self.assertEqual(image_store.browse(conn, category="review")[0], [])
            finally:
                conn.close()

            app = QApplication.instance() or QApplication([])
            browser = ImageBrowser(db)

            def wait_for_job():
                deadline = time.monotonic() + 10
                while browser.worker is not None and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.005)
                self.assertIsNone(browser.worker)

            try:
                browser.tabs.setCurrentIndex(1)
                wait_for_job()
                self.assertIn("1 exact duplicate groups", browser.duplicate_report.toPlainText())
                browser.tabs.setCurrentIndex(2)
                wait_for_job()
                self.assertEqual(browser.tree.topLevelItemCount(), 2)
                browser.tree.setCurrentItem(browser.tree.topLevelItem(0))
                self.assertIn("screenshot.png", browser.details.toPlainText())
            finally:
                browser.reject()
