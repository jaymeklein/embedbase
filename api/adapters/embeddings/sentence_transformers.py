from api.models.chunk import Chunk  # noqa: F401  — satisfies Protocol type check


class SentenceTransformersAdapter:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name, local_files_only=True)

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, show_progress_bar=False).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, show_progress_bar=False).tolist()

    @property
    def dimensions(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())
