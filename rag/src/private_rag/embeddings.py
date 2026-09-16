from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Protocol, Sequence


def _loader_kwargs(model_name: str) -> dict[str, object]:
    """加载参数：本地目录必须显式离线。

    本机 huggingface.co / hf-mirror 都不可达，若让 sentence-transformers
    对本地路径做联网解析，加载会卡到超时。权重已从 ModelScope 备齐，
    直接按目录离线加载。
    """
    if Path(model_name).is_dir():
        return {"local_files_only": True}
    return {}


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedding:
    """Deterministic offline embedding for tests and lexical fallback."""

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("dimensions must be at least 32")
        self.dimensions = dimensions

    @property
    def name(self) -> str:
        return f"hashing-{self.dimensions}"

    @staticmethod
    def _tokens(text: str) -> list[str]:
        lowered = text.lower()
        words = re.findall(r"[a-z0-9_+-]+", lowered)
        cjk = re.findall(r"[\u3400-\u9fff]", lowered)
        return words + cjk + ["".join(cjk[i : i + 2]) for i in range(len(cjk) - 1)]

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in self._tokens(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest, "big") % self.dimensions
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class SentenceTransformerEmbedding:
    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, **_loader_kwargs(model_name))

    @property
    def name(self) -> str:
        """嵌入的身份标识，也是写进索引 manifest 的字符串。

        本地目录一律规范化成 POSIX 形式。原因：`store._load_manifest()` 对
        manifest 里的 embedding 字段是**字符串直接比较**，而同一个目录写成
        `D:\\a\\b` 还是 `D:/a/b` 会产生两个不同的"身份" → 误报
        「embedding model changed, rebuild the index」，明明用的是同一份权重。
        在源头规范化，比要求每个调用方都拼对分隔符可靠。

        hub 模型 id（如 `sentence-transformers/xxx`）不含反斜杠、也不是本地目录，
        原样返回，行为不变。
        """
        if "\\" in self.model_name or Path(self.model_name).is_dir():
            return Path(self.model_name).as_posix()
        return self.model_name

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        values = self._model.encode(list(texts), normalize_embeddings=True)
        return values.tolist()