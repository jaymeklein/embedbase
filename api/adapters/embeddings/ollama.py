import asyncio

import httpx


class OllamaAdapter:
    def __init__(self, base_url: str, model: str, concurrency: int = 8) -> None:
        self.base_url = base_url
        self.model = model
        self._semaphore = asyncio.Semaphore(concurrency)
        self._dimensions: int | None = None

    async def _embed_one(self, client: httpx.AsyncClient, text: str) -> list[float]:
        async with self._semaphore:
            response = await client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()["embedding"]

    async def _embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            return list(await asyncio.gather(*[self._embed_one(client, t) for t in texts]))

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return asyncio.run(self._embed_batch_async(texts))

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            sample = self.embed("probe")
            self._dimensions = len(sample)
        return self._dimensions
