# Sift

Local media inventory and duplicate review for Windows.

Sift scans folders in place, saves an inventory, extracts available metadata,
and helps you review exact duplicates and possible visual matches. Analysis
never modifies, moves, renames or deletes originals. No account, media upload,
GPS extraction or model download is needed for the implemented workflow.

## Current handoff

**Phases 1–4 are Done.** Phase 4 completion was approved on 2026-10-09.
This is a source-run development application; a packaged installer is not included.
Classification, semantic search, people/event grouping and file operations are
product plans, not shipped features. The next phase needs explicit scope approval.

The latest recorded verification ran **55 unit tests successfully**, plus native
Windows smoke checks and synthetic stress cases. The phase verifier recorded
**29 passed, 0 failed, 1 skipped**: this Windows account could not make its test
folder unreadable. Real-phone-file behavior and large-library responsiveness
remain outside that evidence. See the [verification review](docs/development/2026-10-09-phase4-final-review-01.md)
and [completion roadmap](docs/development/ROADMAP.md).

Current guidance lives here and in [AGENTS.md](AGENTS.md). Dated review reports
record their state at the time; they do not override current phase tables.
The [previous README handoff](docs/development/2026-10-09-readme-handoff-snapshot-01.md)
is preserved separately. Recheck Git status and recent commits before resuming.

## Start on Windows

Requires Python 3.11 or newer; tested on 3.13.3 only. Python 3.13 is the documented
development environment. Run these one at a time from the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Create the environment only on a fresh checkout; reuse it on later launches.
Using its interpreter explicitly avoids activating another copy's environment.
Runtime dependencies are pinned in [requirements.txt](requirements.txt): PySide6,
Pillow, pillow-heif and rawpy. Ruff is separate development tooling.

Video and audio metadata need an external `ffprobe` executable on `PATH`.
Sift does not install, download or bundle FFmpeg. Without it, those extractions
report per-file errors; other scan work continues. Format scope, component
licenses and historical sample validation are recorded in [D-005](docs/decisions/DECISIONS.md#d-005-supported-formats-and-representative-collections).
RAW metadata for DNG/NEF/ARW uses LibRaw through rawpy; no RAW rendering is included,
and the recorded real-file ARW sample remains unverified.

## Scan and review

1. **Choose Folder**, then **Start Scan**. Each scan selects one root; saved roots
   accumulate in the library. Pause or cancel scanning without losing saved results.
2. **Find Exact Duplicates (All Folders)** groups successful saved SHA-256
   fingerprints across all library folders. Empty files are counted separately.
3. **Browse Library** displays saved paths, roots and previews in 60-record pages.
   Choose a folder, literal path text or preview status, then **Apply filters**.
4. **Prepare JPEG/PNG previews** reads eligible saved sources and stores previews.
   Preparation follows the folder filter; path/status filters affect browsing only.
   **Rebuild saved previews** bypasses successful preview reuse.
5. Select a ready image and **Find possible visual matches for selected image**.
   Matching covers all saved folders or the selected folder. It uses edge-hash
   distance, average colour and aspect ratio, with the best 100 candidates shown.
   Additional qualifying matches are explicitly reported as omitted.

Exact SHA-256 matches and flat images are excluded from visual candidates.
These candidates are estimates, not proof: crops, rotations and colour edits
may be missed. There are no file-changing actions in these views.

## Saved data and limits

### Personal review and local recognition

Use the **SIFT** desktop shortcut, or **Review Library** from the main window.
When a scan finishes, Review opens with **Browse**, **Duplicates**, and
**Screenshots & Clutter**. In Browse, **Prepare JPEG/PNG previews** creates the
inputs for recognition. **Categorize locally** uses ready previews for the
chosen folder (or all saved folders). Results refresh automatically when it
finishes; select an image and copy its suggested name from the
details. Suggestions never rename originals. Uncertain labels are marked,
and possible clutter is a review cue, never a deletion decision.

The optional recognition engine uses [Google SigLIP2 Base 224](https://huggingface.co/google/siglip2-base-patch16-224),
an Apache-2.0 checkpoint. Its weights are about 1.5 GB, in addition to the Python
runtime packages. Inference runs on the CPU and may take time on a large library.
It recognizes a small set of subjects from previews; it does not read document
text or identify people. The model's label scores are not calibrated probabilities.

One-time setup, from the SIFT root with the app closed:

1. Install the Windows CPU build of PyTorch into this repository's `.venv`
   using the [official PyTorch instructions](https://pytorch.org/get-started/locally/).
2. Install the optional processor and inference libraries:
   `.\.venv\Scripts\python.exe -m pip install "transformers==4.57.6" sentencepiece`.
3. Run the Hugging Face CLI from the same environment to download only model
   assets (no library files are involved):

   ```powershell
   .\.venv\Scripts\hf.exe download google/siglip2-base-patch16-224 --revision 75de2d5 --local-dir "$env:LOCALAPPDATA\Sift\siglip2-base-patch16-224" --include "*.json" tokenizer.model model.safetensors
   ```

4. Open SIFT again and use **Categorize locally**. The app never downloads
   weights on launch, uses only local model assets, and checks the published
   weights checksum. Browse and Duplicates work without these optional packages.

Categories and name suggestions are saved in the local library database, with
the source fingerprint, preview version and recognizer version. The first
category run backs up the database before creating its optional table. Network
shares are not scanned, and ffprobe is restricted to local files and approved
media formats. These application restrictions do not control Windows sync tools.

### Library storage and scan behavior

- The database is `%LOCALAPPDATA%\Sift\sift.db`. Inventory, metadata, hashes,
  image features and previews are stored there, outside scanned folders. First
  image-feature installation makes a sibling `image-features-*.bak` database backup.
- Browsing, exact duplicates and visual matching query saved data only, through
  SQLite read-only connections. They do not check whether media still exists.
  Rescan, then prepare previews to refresh changed or missing sources.
- Fingerprint reuse can miss content changes preserving both size and modification
  time. Enable **Verify all file contents (slower)** during scanning, then prepare
  previews again to refresh dependent results.
- Preview decoding accepts single-frame JPEG/PNG content up to 32 MiB and
  20 million pixels. Previews are at most 256 pixels on the longest side, apply
  orientation, flatten transparency and retain no source metadata. Other formats
  can have metadata and fingerprints without image previews.
- Links/reparse points, including the selected root, are rejected. Corrupt,
  changed, missing, oversized and unsupported sources report per-file outcomes.
- Background jobs cooperate with cancellation. A bounded native image decode
  or the initial database backup must return before cancellation finishes.
- Restart keeps completed results; scanning restarts at the selected root and
  reuses eligible cached stages rather than resuming at an exact interrupted file.

## Development checks

Run from the repository root. The verifier creates synthetic fixtures and
isolates app data and temporary files under ignored `test_data_auto/`:

```powershell
.\.venv\Scripts\python.exe scripts/verify_phase.py
```

It captures unit tests, Ruff lint/format checks and phase-status synchronization,
writes `test_data_auto/verify_report.md`, and exits non-zero on failure.
It deletes and recreates its fixture folder; do not store personal files there.
Ruff must already be installed in the development environment for its checks.

Individual checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts/check_phase_status.py
```

## Project map

| File | Role |
| --- | --- |
| `main.py`, `sift/ui.py` | Launch and main scan interface |
| `sift/scanner.py`, `sift/worker.py` | Inventory and independent analysis stages |
| `sift/metadata.py`, `sift/fingerprint.py` | Metadata and incremental SHA-256 |
| `sift/database.py`, `sift/duplicates.py` | Library storage and exact groups |
| `sift/image_analysis.py`, `sift/image_store.py` | Safe decoding and versioned previews |
| `sift/image_worker.py`, `sift/image_browser.py` | Background image jobs and library review |
| `sift/visual_match.py` | Possible visual matches over saved features |
| `tests/`, `scripts/verify_phase.py` | Synthetic regressions and phase verification |

See [product plans](docs/product/PRODUCT.md), [architecture baseline](docs/architecture/ARCHITECTURE.md),
[decisions](docs/decisions/DECISIONS.md), [roadmap](docs/development/ROADMAP.md),
[changelog](CHANGELOG.md) and the [Markdown review](docs/development/2026-10-09-documentation-review-01.md).
