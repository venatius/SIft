"""Paged saved-library review with cancellable background image preparation."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .image_worker import ImageWorker


class ImageBrowser(QDialog):
    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.worker = None
        self._closing = False
        self._operation = None
        self._recognition_summary = None
        self._after = 0
        self._history = []
        self._rows = []
        self._has_more = False
        self._filters = None
        self._pending_page = None
        self.setWindowTitle("Sift — Review")
        self.resize(1000, 700)
        self.setModal(True)

        self.root_filter = QComboBox()
        self.root_filter.addItem("All saved folders", None)
        self.status_filter = QComboBox()
        for label, value in (
            ("All files", "all"),
            ("Previews ready", "ready"),
            ("Needs preparation / rescan", "pending"),
            ("Preview failed", "failed"),
            ("Unsupported for preview", "unsupported"),
        ):
            self.status_filter.addItem(label, value)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter saved paths (literal text)")
        self.apply_btn = QPushButton("Apply filters")
        self.category_filter = QComboBox()
        self.category_filter.setAccessibleName("Image category")
        self.category_filter.addItem("All categories", "all")
        for category in ("Screenshots", "Documents", "Everyday photos", "Possible clutter"):
            self.category_filter.addItem(category, category)
        self.recognize_btn = QPushButton("Categorize locally")
        self.prepare_btn = QPushButton("Prepare JPEG/PNG previews")
        self.similar_btn = QPushButton("Find possible visual matches for selected image")
        self.force_check = QCheckBox("Rebuild saved previews")
        self.cancel_btn = QPushButton("Cancel current job")
        self.previous_btn = QPushButton("Previous page")
        self.next_btn = QPushButton("Next page")
        self.close_btn = QPushButton("Close")
        self.status_label = QLabel("Loading saved library...")
        self.notice = QLabel(
            "Saved records from your library; missing files may remain listed. "
            "Rescan, then prepare previews to refresh. Originals are never changed."
        )
        self.notice.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["File", "Preview status", "Saved path", "Library root", "Category"]
        )
        self.tree.setIconSize(QSize(96, 96))
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 170)
        self.tree.setColumnWidth(2, 350)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        self.matches = QTreeWidget()
        self.matches.setHeaderLabels(["Hash distance / 64", "Possible match", "Library root"])
        self.matches.setIconSize(QSize(64, 64))
        self.matches.setColumnWidth(0, 170)
        self.matches.setColumnWidth(1, 540)
        filters = QHBoxLayout()
        for control in (
            self.root_filter,
            self.status_filter,
            self.category_filter,
            self.search,
            self.apply_btn,
        ):
            filters.addWidget(control)
        actions = QHBoxLayout()
        for control in (self.prepare_btn, self.recognize_btn, self.force_check):
            actions.addWidget(control)
        pages = QHBoxLayout()
        for control in (self.previous_btn, self.next_btn, self.cancel_btn, self.close_btn):
            pages.addWidget(control)
        self.tabs = QTabBar()
        for name in ("Browse", "Duplicates", "Screenshots && Clutter"):
            self.tabs.addTab(name)
        self.stack = QStackedWidget()
        browse_panel = QWidget()
        browse_layout = QVBoxLayout(browse_panel)
        browse_layout.addLayout(filters)
        browse_layout.addLayout(actions)
        browse_layout.addWidget(self.tree)
        browse_layout.addWidget(self.similar_btn)
        browse_layout.addWidget(self.matches)
        browse_layout.addWidget(self.details)
        self.duplicate_report = QPlainTextEdit()
        self.duplicate_report.setReadOnly(True)
        self.duplicate_report.setAccessibleName("Exact duplicate results")
        self.stack.addWidget(browse_panel)
        self.stack.addWidget(self.duplicate_report)
        layout = QVBoxLayout(self)
        layout.addWidget(self.notice)
        layout.addWidget(self.tabs)
        layout.addWidget(self.status_label)
        layout.addWidget(self.stack)
        layout.addLayout(pages)
        self.tabs.currentChanged.connect(self.switch_tab)
        self.recognize_btn.clicked.connect(self.recognize_images)
        self.category_filter.currentIndexChanged.connect(self.on_filter_edited)
        self.apply_btn.clicked.connect(self.load_first_page)
        self.search.returnPressed.connect(self.load_first_page)
        self.prepare_btn.clicked.connect(self.prepare_images)
        self.similar_btn.clicked.connect(self.find_similar)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.previous_btn.clicked.connect(self.previous_page)
        self.next_btn.clicked.connect(self.next_page)
        self.close_btn.clicked.connect(self.reject)
        self.tree.itemSelectionChanged.connect(self.show_selection)
        self.root_filter.currentIndexChanged.connect(self.on_filter_edited)
        self.status_filter.currentIndexChanged.connect(self.on_filter_edited)
        self.search.textChanged.connect(self.on_filter_edited)

    def filter_values(self):
        return {
            "root_id": self.root_filter.currentData(),
            "text": self.search.text(),
            "status": self.status_filter.currentData(),
            "category": (
                "review" if self.tabs.currentIndex() == 2 else self.category_filter.currentData()
            ),
        }

    def switch_tab(self, index):
        self.stack.setCurrentIndex(int(index == 1))
        self.category_filter.setVisible(index != 2)
        if index == 1:
            self.duplicate_report.setPlainText("Checking saved fingerprints…")
            self.start_job("duplicates")
        else:
            self.load_first_page()

    def recognize_images(self):
        self.status_label.setText("Categorizing saved previews on this PC…")
        self.start_job("recognize", root_id=self.root_filter.currentData())

    def on_filter_edited(self):
        if self.worker is None:
            self.set_busy(False)

    def set_busy(self, busy):
        for control in (
            self.root_filter,
            self.status_filter,
            self.search,
            self.apply_btn,
            self.prepare_btn,
            self.force_check,
            self.tabs,
            self.recognize_btn,
            self.category_filter,
        ):
            control.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        applied = self.tabs.currentIndex() != 1 and self._filters == self.filter_values()
        self.previous_btn.setEnabled(not busy and applied and bool(self._history))
        self.next_btn.setEnabled(not busy and applied and self._has_more)
        self.tree.setEnabled(not busy)
        self.similar_btn.setEnabled(
            not busy
            and applied
            and bool(self.tree.selectedItems())
            and self.tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)["image_status"]
            == "ready"
        )

    def start_job(self, operation, **options):
        if self.worker is not None or self._closing:
            return
        self._operation = operation
        self.worker = ImageWorker(self.db_path, operation, **options)
        self.worker.result_ready.connect(self.on_result)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.set_busy(True)
        self.worker.start()

    def load_first_page(self):
        if self.worker is not None:
            return
        self.load_page(0, [], self.filter_values())

    def load_page(self, after, history, filters):
        self._pending_page = (after, history, filters)
        self.status_label.setText("Loading saved library...")
        self.start_job("browse", **filters, after=after)

    def next_page(self):
        if self.worker is None and self._has_more and self._rows and self.next_btn.isEnabled():
            self.load_page(self._rows[-1]["id"], [*self._history, self._after], self._filters)

    def previous_page(self):
        if self.worker is None and self._history and self.previous_btn.isEnabled():
            self.load_page(self._history[-1], self._history[:-1], self._filters)

    def prepare_images(self):
        self.matches.clear()
        self.status_label.setText("Preparing images from this folder filter...")
        self.details.setPlainText(
            "Preparation reads eligible saved files in the folder filter; text and preview-status "
            "filters only affect browsing. Cancellation is checked between bounded image decodes."
        )
        self.start_job(
            "prepare", root_id=self.root_filter.currentData(), force=self.force_check.isChecked()
        )

    def find_similar(self):
        items = self.tree.selectedItems()
        if not items or self.worker is not None or not self.similar_btn.isEnabled():
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.matches.clear()
        self.status_label.setText("Comparing saved visual features...")
        self.start_job("similarity", file_id=row["id"], root_id=self._filters["root_id"])

    def cancel_job(self):
        if self.worker is not None:
            self.status_label.setText("Cancelling after the current bounded operation...")
            self.worker.cancel()

    def on_progress(self, summary):
        if "classified" in summary:
            self.status_label.setText(
                f"Local recognition: {summary['classified']} categorized, {summary['failed']} failed"
            )
            return
        self.status_label.setText(
            f"Images: {summary['prepared']} prepared, {summary['reused']} reused, "
            f"{summary['failed']} failed, {summary['unsupported']} unsupported"
        )

    def on_result(self, result):
        if self._closing:
            return
        if self._operation == "duplicates":
            groups, empty = result
            lines = [
                f"{len(groups)} exact duplicate groups across all saved folders.",
                f"{empty} empty files (not listed as duplicates). Rescan to refresh saved results.",
            ]
            for number, (_, paths) in enumerate(groups, 1):
                lines.extend((f"\nGroup {number} · {len(paths)} files", *paths))
            self.duplicate_report.setPlainText("\n".join(lines))
            self.status_label.setText("Exact duplicate results ready")
            return
        if self._operation == "recognize":
            self._recognition_summary = result
            return
        if self._operation == "prepare":
            self.on_progress(result)
            lines = ["Preparation complete. Apply filters to load the updated saved previews."]
            lines.extend(f"{path}: {message}" for path, message in result["errors"])
            self.details.setPlainText("\n".join(lines))
            return
        if self._operation == "similarity":
            self.show_matches(result)
            return
        self.matches.clear()
        self._rows = result["rows"]
        self._has_more = result["has_more"]
        self._after, self._history, self._filters = self._pending_page
        selected_root = self.root_filter.currentData()
        self.root_filter.clear()
        self.root_filter.addItem("All saved folders", None)
        for root in result["roots"]:
            self.root_filter.addItem(root["path"], root["id"])
        self.root_filter.setCurrentIndex(max(0, self.root_filter.findData(selected_root)))
        self.tree.clear()
        for row in self._rows:
            category = row.get("category") if row["image_status"] == "ready" else None
            label = category + (" · uncertain" if row["uncertain"] else "") if category else ""
            item = QTreeWidgetItem(
                [Path(row["path"]).name, row["image_status"], row["path"], row["root"], label]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            item.setToolTip(0, row["path"])
            if row["thumbnail"]:
                pixmap = QPixmap()
                if pixmap.loadFromData(row["thumbnail"], "PNG"):
                    item.setIcon(0, QIcon(pixmap))
            self.tree.addTopLevelItem(item)
        self.status_label.setText(
            f"{len(self._rows)} saved records on page {len(self._history) + 1}"
        )
        self.details.setPlainText("Select a record to see its saved path and any preview error.")
        if not self._rows and self.tabs.currentIndex() == 2:
            self.details.setPlainText(
                "No matching screenshots or clutter. Prepare previews, then choose Categorize locally."
            )

    def show_selection(self):
        self.matches.clear()
        items = self.tree.selectedItems()
        if not items:
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        suggested = row.get("suggested") if row["image_status"] == "ready" else None
        suggestion = f"\nSuggested name (select and copy): {suggested}" if suggested else ""
        self.details.setPlainText(
            f"Path: {row['path']}\nRoot: {row['root']}\nSaved size: {row['size']} bytes\n"
            f"Preview: {row['image_status']}\n{row['error'] or ''}{suggestion}"
        )
        if self.worker is None:
            self.set_busy(False)

    def show_matches(self, result):
        self.matches.clear()
        for row in result["matches"]:
            item = QTreeWidgetItem([str(row["distance"]), row["path"], row["root"]])
            item.setToolTip(1, f"RGB mean difference: {row['colour_delta']} / 255")
            pixmap = QPixmap()
            if row["thumbnail"] and pixmap.loadFromData(row["thumbnail"], "PNG"):
                item.setIcon(1, QIcon(pixmap))
            self.matches.addTopLevelItem(item)
        self.status_label.setText(
            f"{result['matched']} possible matches across {result['examined']} compared records"
        )
        limit = (
            "Showing the best 100 matches; additional matches are omitted.\n"
            if result["truncated"]
            else ""
        )
        self.details.setPlainText(
            f"Selected image: {result['reference']['path']}\n{limit}"
            "Possible matches are estimates, not proof of duplicates. Lower hash distance "
            "means more similar edge patterns (maximum shown: 8 of 64 bits), with similar "
            "average colour and aspect ratio. Flat images are excluded. Exact SHA-256 "
            "duplicates are excluded here; use Find Exact Duplicates for those.\n"
            "Results use saved features only. Rescan and prepare images to refresh missing "
            "or changed files. Crops, rotations and colour edits can be missed."
        )

    def on_failed(self, message):
        self.status_label.setText("Image job failed")
        self.details.setPlainText(message)
        if self._operation == "duplicates":
            self.duplicate_report.setPlainText(f"Could not load exact duplicates:\n{message}")

    def on_cancelled(self):
        self.status_label.setText("Image job cancelled; completed previews are kept")
        if self._operation == "duplicates":
            self.duplicate_report.setPlainText(
                "Duplicate lookup cancelled. Reopen this tab to retry."
            )

    def on_finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.set_busy(False)
        if self._closing:
            super().reject()
        elif self._recognition_summary is not None:
            summary = self._recognition_summary
            self._recognition_summary = None
            self.load_first_page()
            self.on_progress(summary)

    def reject(self):
        if self.worker is not None:
            self._closing = True
            self.cancel_job()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self._closing = True
            self.cancel_job()
            event.ignore()
        else:
            event.accept()
