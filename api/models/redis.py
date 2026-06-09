"""Redis model definitions for corpus configuration and parsing."""

from json import loads

from pydantic import BaseModel


class CorpusConfig(BaseModel):
    """Configuration values for a corpus stored in Redis.

    Attributes:
        collection_id (str): Identifier for the collection.
        corpus_key (str): Redis key for the stored corpus.
        version_key (str): Redis key for the corpus version.
    """

    collection_id: str
    corpus_key: str
    version_key: str

    def __init__(self, collection_id: str):
        """Initialize configuration for a corpus.

        Args:
            collection_id (str): Identifier for the collection.
        """
        super().__init__(
            collection_id=collection_id,
            corpus_key=f"bm25:{collection_id}:corpus",
            version_key=f"bm25:{collection_id}:version",
        )


class Corpus(BaseModel):
    """Represents corpus data parsed from Redis.

    Attributes:
        data (list[tuple[str, str]]): Parsed corpus data as (doc_id, text) tuples.
    """

    data: list[tuple[str, str]] = []

    def __init__(self, raw: str | bytes | None):
        """Initialize a corpus object from raw JSON data.

        Args:
            raw (str | bytes | None): Raw JSON string containing corpus entries,
                or None/empty when the corpus does not exist yet.
        """
        super().__init__(data=Corpus._parse_corpus(raw))

    @staticmethod
    def _parse_corpus(raw: str | bytes | None) -> list[tuple[str, str]]:
        """Parse a raw JSON corpus string into a list of tuples.

        Args:
            raw (str | bytes | None): Raw JSON string representing corpus data.

        Returns:
            list[tuple[str, str]]: Parsed corpus entries as (doc_id, text) tuples.
        """
        if not raw:
            return []

        corpus: list[list[str]] = loads(raw)
        return [(entry[0], entry[1]) for entry in corpus]
    
    @property
    def tokenized(self) -> list[list[str]]:
        """Return tokenized text for each document in the corpus.

        Returns:
            list[list[str]]: A list of token lists where each inner list contains
                the lowercased tokens for a document.
        """
        return [text.lower().split() for _, text in self.data]
    
    @property
    def doc_ids(self) -> list[str]:
        """Return document IDs for each entry in the corpus.

        Returns:
            list[str]: A list of document IDs corresponding to the corpus entries.
        """
        return [doc_id for doc_id, _ in self.data]
