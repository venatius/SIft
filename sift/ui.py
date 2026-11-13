import os
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import database
from .duplicates import DuplicateWorker
from .image_browser import ImageBrowser
from .local_paths import is_network_path
from .worker import ScanWorker

LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
SIFT_DATA_DIR = LOCAL_APP_DATA / "Sift"
SIFT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(SIFT_DATA_DIR / "sift.db")
LEGACY_DB_PATH = str(Path(__file__).resolve().parent.parent / "sift.db")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sift")
        self.resize(700, 500)

        self.folder = None
        self.thread = None
        self.worker = None
        self.duplicate_worker = None
        self.image_browser = None
        self.paused = False

        self.folder_label = QLabel("No folder selected")
        self.choose_btn = QPushButton("Choose Folder")
        self.start_btn = QPushButton("Start Scan")
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Cancel")
        self.duplicates_btn = QPushButton("Find Exact Duplicates (All Folders)")
        self.browse_btn = QPushButton("Review Library")
        self.status_label = QLabel("Ready")
        self.count_label = QLabel("Files found: 0")
        self.saved_label = QLabel("")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.force_rehash_check = QCheckBox("Verify all file contents (slower)")

        buttons = QHBoxLayout()
        for b in (
            self.choose_btn,
            self.start_btn,
            self.pause_btn,
            self.cancel_btn,
            self.browse_btn,
        ):
            buttons.addWidget(b)

        layout = QVBoxLayout()
        for w in (
            self.folder_label,
            buttons,
            self.force_rehash_check,
            self.status_label,
            self.count_label,
            self.saved_label,
            self.report,
        ):
            if isinstance(w, QHBoxLayout):
                layout.addLayout(w)
            else:
                layout.addWidget(w)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.choose_btn.clicked.connect(self.choose_folder)
        self.start_btn.clicked.connect(self.start_scan)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.duplicates_btn.clicked.connect(self.find_exact_duplicates)
        self.browse_btn.clicked.connect(self.browse_library)

        self.set_scanning(False)
        self.init_database()

    # ----- setup -----
    def init_database(self):
        database.migrate_database(LEGACY_DB_PATH, DB_PATH)
        conn = database.connect(DB_PATH)
        database.init_db(conn)
        interrupted = database.mark_interrupted(conn)
        latest_root = database.get_latest_root(conn)
        conn.close()
        if (
            latest_root
            and not is_network_path(latest_root["path"])
            and os.path.isdir(latest_root["path"])
        ):
            self.folder = latest_root["path"]
            self.folder_label.setText(self.folder)
            self.start_btn.setEnabled(True)
        if interrupted:
            message = "The previous scan was interrupted. Saved results are kept."
            if self.folder:
                message += " Start again to rescan this folder from the beginning."
            else:
                message += " Choose the folder to scan again from the beginning."
            self.status_label.setText(message)
        self.refresh_saved_count()

    def refresh_saved_count(self):
        conn = database.connect(DB_PATH)
        n = database.count_files(conn)
        conn.close()
        self.saved_label.setText(f"Saved file records in database: {n}")

    def set_scanning(self, scanning):
        self.paused = False
        self.pause_btn.setText("Pause")
        self.choose_btn.setEnabled(not scanning)
        self.start_btn.setEnabled(not scanning and self.folder is not None)
        self.pause_btn.setEnabled(scanning)
        self.cancel_btn.setEnabled(scanning)
        self.duplicates_btn.setEnabled(not scanning)
        self.browse_btn.setEnabled(not scanning)

    def browse_library(self):
        if self.image_browser is not None or self.thread is not None:
            return
        self.image_browser = ImageBrowser(DB_PATH, self)
        self.image_browser.finished.connect(self.on_browser_finished)
        self.image_browser.open()
        self.image_browser.load_first_page()

    def on_browser_finished(self):
        browser = self.image_browser
        self.image_browser = None
        browser.deleteLater()

    # ----- user actions -----
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if folder:
            self.folder = os.path.normpath(folder)
            self.folder_label.setText(self.folder)
            self.start_btn.setEnabled(True)

    def start_scan(self):
        self.report.clear()
        self.count_label.setText("Files found: 0")
        self.status_label.setText("Scanning...")

        self.thread = QThread()
        self.worker = ScanWorker(
            self.folder, DB_PATH, force_rehash=self.force_rehash_check.isChecked()
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.stage_progress.connect(self.on_stage_progress)
        self.worker.scan_done.connect(self.on_scan_done)

        self.set_scanning(True)
        self.thread.start()

    def toggle_pause(self):
        if self.paused:
            self.worker.resume()
            self.paused = False
            self.pause_btn.setText("Pause")
            self.status_label.setText("Scanning...")
        else:
            self.worker.pause()
            self.paused = True
            self.pause_btn.setText("Resume")
            self.status_label.setText("Paused (between files and hash chunks)")

    def cancel_scan(self):
        self.status_label.setText("Cancelling...")
        self.worker.cancel()

    def find_exact_duplicates(self):
        if self.duplicate_worker is not None:
            return
        self.status_label.setText("Finding exact duplicates from saved fingerprints...")
        self.duplicates_btn.setEnabled(False)
        self.duplicate_worker = DuplicateWorker(DB_PATH)
        self.duplicate_worker.groups_ready.connect(self.on_duplicate_groups)
        self.duplicate_worker.query_failed.connect(self.on_duplicate_query_failed)
        self.duplicate_worker.finished.connect(self.on_duplicate_worker_finished)
        self.duplicate_worker.start()

    def on_duplicate_groups(self, groups, empty_file_count):
        summary = (
            f"{empty_file_count} empty files (not listed as duplicates).\n"
            "Results cover all saved library folders; paths may come from different roots.\n"
            "Rescan to refresh results before relying on them."
        )
        if not groups:
            self.report.setPlainText(
                "No exact duplicates found among current successful SHA-256 fingerprints.\n"
                + summary
            )
        else:
            lines = [
                f"Found {len(groups)} exact duplicate group(s).",
                summary,
            ]
            for index, (digest, paths) in enumerate(groups, start=1):
                lines.extend(
                    (
                        f"\nGroup {index} ({len(paths)} files; SHA-256 {digest}):",
                        *(f"  {path}" for path in paths),
                    )
                )
            self.report.setPlainText("\n".join(lines))
        self.status_label.setText("Exact duplicate results ready (read-only).")

    def on_duplicate_query_failed(self, message):
        self.report.setPlainText(f"Could not load exact duplicate results:\n{message}")
        self.status_label.setText("Could not find exact duplicates")

    def on_duplicate_worker_finished(self):
        self.duplicate_worker = None
        self.duplicates_btn.setEnabled(self.thread is None)

    # ----- updates from the worker -----
    def on_progress(self, total, path):
        self.count_label.setText(f"Files found: {total}")
        if path and not self.paused:
            self.status_label.setText(f"Scanning... {path[-70:]}")

    def on_stage_progress(self, stage, current, total):
        if not self.paused:
            self.status_label.setText(
                f"{stage.capitalize()} current file: {current:,} / {total:,} bytes"
            )

    def on_scan_done(self, s):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.set_scanning(False)

        if s.get("status") == "failed":
            self.status_label.setText("Scan failed")
        elif s["cancelled"]:
            self.status_label.setText("Scan cancelled")
        elif s["failed"] or s["meta_failed"] or s["fp_failed"] or s["errors"]:
            self.status_label.setText("Scan finished with errors")
        else:
            self.status_label.setText("Scan finished")
        lines = [
            f"Scanned: {s['scanned']}",
            f"Skipped: {s['skipped']}",
            f"Failed: {s['failed']}",
            (
                f"Metadata: {s['meta_ok']} extracted, {s['meta_unsupported']} unsupported, "
                f"{s['meta_failed']} failed, {s['meta_reused']} reused"
            ),
            (
                f"Fingerprints: {s['fp_done']} computed, {s['fp_reused']} reused, "
                f"{s['fp_failed']} failed"
            ),
        ]
        if s["cancelled"]:
            lines.append("The scan was cancelled before it finished.")
        if s["errors"]:
            lines += ["", "Problems:"]
            for path, message in s["errors"]:
                lines.append(f"  {path}\n      {message}")
        self.report.setPlainText("\n".join(lines))
        self.refresh_saved_count()
        if s.get("status") == "completed" and self.isVisible():
            self.browse_library()

    def closeEvent(self, event):
        if self.image_browser is not None and self.image_browser.worker is not None:
            self.image_browser.finished.connect(self.close)
            self.image_browser.reject()
            event.ignore()
            return
        if self.worker is not None:
            self.worker.cancel()
            self.thread.quit()
            self.thread.wait()
        if self.duplicate_worker is not None:
            self.duplicate_worker.wait()
        event.accept()
