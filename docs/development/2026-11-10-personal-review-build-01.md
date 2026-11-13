# Personal SIFT review build — 2026-11-10

Scope: Elan approved the personal Windows workflow, an explicitly installed on-device model, four review categories, suggestions only, and Browse / Duplicates / Screenshots & Clutter tabs. Sources: SIFT AGENTS, README, Phase 4 roadmap, and VEN OS discovery capture `brainstorms/2026-11-10-sift-personal-workflow-173321944.md`. No authorization to stage, commit, push, tag, change a phase, or modify originals.

## Implementation and skill evidence

- Incremental implementation / UI engineering: reused the paged review and background workers. Main window opens Review after a completed scan; Browse displays saved previews and categories, Duplicates uses the read-only exact query, and Screenshots & Clutter filters recognized saved records. Copyable suggestions are text only. Native Windows smoke and screenshot inspection used synthetic media and isolated LOCALAPPDATA.
- Security and hardening / SIFT safety review: classifier reads existing bounded PNG previews, not source files. The app sets Hugging Face offline/telemetry flags, explicitly loads local assets, refuses links/reparse points for model ancestors and assets, requires safetensors and validates the published model weights SHA-256. No model download code in the app. Scanner refuses Windows network roots and skips reparse files; preview prep refuses saved network roots; ffprobe allows the file protocol and explicitly listed media demuxers. The guard is not an OS firewall or a general guarantee about third-party packages, arbitrary inputs, or Windows sync.
- SIFT DB migration: before first categorization, backs up the isolated schema-v3 library and adds image_categories with IF NOT EXISTS and an enforced file foreign key, without changing user_version. Categories use source SHA-256, preview version and recognizer version. Repeated recognition test created no extra backups. Original fixture bytes and mtime preserved; live library not opened.
- Code review and quality: independent Astra review identified missing recognizer version, links in model assets, and indirect references in ffprobe inputs. Added versioning and a regression, checked model paths before opening, and allowed only supported media demuxers. No additional SQLite or Qt lifecycle issue reported. The first ffprobe smoke fixture used .mp4; ffprobe rejected it before selecting HLS. A .m3u8 fixture then exercised explicit whitelist rejection.

## Checks

- `python -m unittest discover -s tests -v`: 61 tests passed in 42.575s; 6 new tests rerun after a test-only lint cleanup, all passed.
- `python -m ruff check .`: passed. `python -m ruff format --check .`: 44 files already formatted; no formatter write.
- `python scripts/check_phase_status.py`: synchronized, Phase 4 Done. `git diff --check`: passed.
- Native Qt smoke: opened isolated review, navigated all three tabs, showed expected exact duplicate group and closed. Real installed ffprobe extracted synthetic WAV metadata and rejected an HLS manifest referencing a loopback URL by demuxer whitelist.
- Full real model inference and operating-system network monitoring remain unverified. Neither torch nor transformers is installed in this venv, and model files were not downloaded. Synthetic categorization exercises the workflow using a substitute model. Model/hash/load cancellation is cooperative and can wait for current work.

## Model and next step

Google SigLIP2 Base 224, Apache-2.0, ~1.5 GB safetensors plus CPU inference runtime; CPU inference time on this PC has not been measured. Sources: https://huggingface.co/google/siglip2-base-patch16-224 and its model.safetensors file page, https://huggingface.co/docs/transformers/model_doc/siglip2, and https://github.com/FFmpeg/FFmpeg/blob/master/doc/protocols.texi. README contains one-time setup. Ask for the new model folder and optional package installation before writing outside the repo or creating package folders. Then test actual recognition with synthetic inputs and verify offline operation before calling it ready. No commits or status changes.
