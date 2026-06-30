import httpx

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


def _raise_for_status(response: httpx.Response) -> httpx.Response:
    """Like ``raise_for_status`` but include Google's error body (it says *why*)."""
    if response.is_error:
        raise httpx.HTTPStatusError(
            f"Gemini API {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )
    return response


class GeminiAdapter:
    """Google Gemini embeddings via the native ``:batchEmbedContents`` endpoint.

    Uses the Generative Language REST API (``x-goog-api-key`` auth), not the
    OpenAI-compat layer, so it reaches multimodal models like ``gemini-embedding-2``
    directly. The whole batch goes in one request.

    ``output_dimensionality`` truncates the (otherwise 3072-dim) vector — the model
    auto-normalises truncated output, so no manual L2 step is needed.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        output_dimensionality: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._output_dimensionality = output_dimensionality
        self._dimensions: int | None = None

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        base: dict = {"model": f"models/{self.model}"}
        if self._output_dimensionality is not None:
            base["output_dimensionality"] = self._output_dimensionality
        requests = [{**base, "content": {"parts": [{"text": t}]}} for t in texts]

        response = httpx.post(
            f"{self.base_url}/v1beta/models/{self.model}:batchEmbedContents",
            json={"requests": requests},
            headers={"x-goog-api-key": self._api_key},
            timeout=60.0,
        )
        _raise_for_status(response)
        return [item["values"] for item in response.json()["embeddings"]]

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._dimensions = len(self.embed("probe"))
        return self._dimensions
