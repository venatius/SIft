"""CPU-only recognition from explicitly installed model files and saved previews."""

import hashlib
import io
import os
from pathlib import Path

from PIL import Image

from .local_paths import is_link_or_reparse_point, is_network_path

WEIGHTS_SHA256 = "612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b"
RECOGNITION_VERSION = f"siglip2-{WEIGHTS_SHA256}-prompts-v1"
MODEL_FILES = {
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
}

LABELS = (
    ("Screenshots", "screenshot", "a screenshot of a computer or phone interface"),
    ("Documents", "document", "a scanned document, receipt, or page of printed text"),
    ("Possible clutter", "image-to-review", "a blank image or accidental blurry photo"),
    ("Everyday photos", "outdoor-photo", "a photo of an outdoor scene or landscape"),
    ("Everyday photos", "pet-photo", "a photo of a pet"),
    ("Everyday photos", "food-photo", "a photo of food or a meal"),
    ("Everyday photos", "indoor-photo", "a photo of an indoor scene"),
    ("Everyday photos", "everyday-photo", "an ordinary photograph from everyday life"),
)


def suggestion(scores, path):
    ranked = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    best, runner_up = ranked[:2]
    category, stem, _ = LABELS[best]
    uncertain = scores[best] < 0.5 or scores[best] - scores[runner_up] < 0.12
    if uncertain:
        stem = "image-to-review"
    suffix = Path(path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        suffix = ""
    return category, stem + suffix, int(uncertain)


class LocalRecognizer:
    def __init__(self, folder):
        folder = Path(os.path.abspath(folder))
        if is_network_path(folder):
            raise ValueError("The model must be installed on a local drive.")
        try:
            for path in (*reversed(folder.parents), folder):
                if is_link_or_reparse_point(path):
                    raise ValueError("The local model folder cannot use linked paths.")
            for entry in folder.iterdir():
                if entry.name == ".cache":
                    continue  # The download CLI's bookkeeping is not loaded by the app.
                if (
                    entry.name not in MODEL_FILES
                    or is_link_or_reparse_point(entry)
                    or not entry.is_file()
                ):
                    raise ValueError("Unexpected or linked file in the local model folder.")
            for name in MODEL_FILES:
                if not (folder / name).is_file():
                    raise FileNotFoundError(name)
        except (FileNotFoundError, NotADirectoryError) as error:
            raise ValueError(
                "Local model not set up. See the SIFT README for one-time setup."
            ) from error

        weights = folder / "model.safetensors"
        with weights.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != WEIGHTS_SHA256:
            raise ValueError("The local model weights do not match the supported SigLIP2 download.")

        # Set before importing the optional libraries. All loads also require local paths.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        try:
            import torch
            from transformers import AutoProcessor, SiglipModel
        except ImportError as error:
            raise RuntimeError(
                "Local recognition packages are missing. See the SIFT README."
            ) from error

        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(
            str(folder), local_files_only=True, trust_remote_code=False, use_fast=False
        )
        self.model = (
            SiglipModel.from_pretrained(str(folder), local_files_only=True, use_safetensors=True)
            .to("cpu")
            .eval()
        )

    def __call__(self, thumbnail, path):
        with Image.open(io.BytesIO(thumbnail), formats=("PNG",)) as image:
            if max(image.size) > 256:
                raise ValueError("Saved preview is too large")
            inputs = self.processor(
                images=image.convert("RGB"),
                text=[label[2] for label in LABELS],
                padding="max_length",
                max_length=64,
                return_tensors="pt",
            )
        with self.torch.inference_mode():
            scores = self.model(**inputs).logits_per_image.sigmoid()[0].tolist()
        return suggestion(scores, path)
