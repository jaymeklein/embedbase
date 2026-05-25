import httpx


class OpenAICompatAdapter:
    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._dimensions: int | None = None

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            headers={"Authorization": f"Bearer {self._api_key or 'no-key'}"},
            timeout=60.0,
        )
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            sample = self.embed("probe")
            self._dimensions = len(sample)
        return self._dimensions
