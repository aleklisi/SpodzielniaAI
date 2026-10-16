"""Prompt-injection classifier (chunk 3.3, layer 1): the model LLM-Guard's ``PromptInjection``
scanner wraps (``protectai/deberta-v3-base-prompt-injection-v2``), run directly with
``transformers``, offline from the read-only models volume.

LLM-Guard itself is not a dependency: it pins ``presidio-anonymizer`` to a release that needs
``cryptography<44.1`` (see 03 as-built notes). The behaviour matches it: overlapping token
windows (512 tokens), per-window probability of the ``INJECTION`` label.

Typed adapter (conventions §2.2): transformers/torch are imported lazily, inside the worker.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

WINDOW = 510  # + 2 special tokens = the model's 512
STRIDE = 384


@dataclass(frozen=True, slots=True)
class WindowScore:
    start: int  # character offsets in the scanned text
    end: int
    score: float  # probability of INJECTION


class InjectionClassifier:
    def __init__(self, model_dir: Path) -> None:
        self._model_dir = model_dir
        self._lock = threading.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._injection_index = 1

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._model is None:
                from transformers import (  # pyright: ignore[reportMissingTypeStubs]
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                path = str(self._model_dir)
                self._tokenizer = cast(
                    "Any",
                    AutoTokenizer.from_pretrained(path, local_files_only=True),  # pyright: ignore[reportUnknownMemberType]
                )
                model = cast(
                    "Any",
                    AutoModelForSequenceClassification.from_pretrained(  # pyright: ignore[reportUnknownMemberType]
                        path, local_files_only=True
                    ),
                )
                model.eval()
                id2label = cast("dict[Any, Any]", model.config.id2label)
                labels = {str(v).upper(): int(k) for k, v in id2label.items()}
                self._injection_index = labels.get("INJECTION", 1)
                self._model = model
            return self._tokenizer, self._model

    def score(self, text: str) -> list[WindowScore]:
        if not text.strip():
            return []
        import torch

        tokenizer, model = self._load()
        encoded: Any = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        ids: list[int] = list(encoded["input_ids"])
        offsets: list[tuple[int, int]] = list(encoded["offset_mapping"])
        scores: list[WindowScore] = []
        start = 0
        while True:
            window = ids[start : start + WINDOW]
            # [CLS] … [SEP] (DeBERTa-v3 framing, from the tokenizer's own special ids).
            framed = [int(tokenizer.cls_token_id), *window, int(tokenizer.sep_token_id)]
            batch = torch.tensor([framed])
            with torch.no_grad():
                logits = model(input_ids=batch, attention_mask=torch.ones_like(batch)).logits
            probability = float(torch.softmax(logits, dim=-1)[0, self._injection_index])
            span = offsets[start : start + len(window)]
            scores.append(WindowScore(span[0][0], span[-1][1], probability))
            if start + WINDOW >= len(ids):
                return scores
            start += STRIDE
