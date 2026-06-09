"""Redis model definitions for corpus configuration and parsing."""

import logging
from json import loads

from pydantic import BaseModel

logger = logging.getLogger(__name__)


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

    Each entry is a (chunk_id, document_id, text) triple so that:
    - BM25 scores are keyed by chunk_id (unique per chunk)
    - Deletion can filter by document_id without a separate index

    Attributes:
        data (list[tuple[str, str, str]]): Parsed corpus entries as
            (chunk_id, document_id, text) triples.
    """

    data: list[tuple[str, str, str]] = []

    def __init__(self, raw: str | bytes | None):
        """Initialize a corpus object from raw JSON data.

        Args:
            raw (str | bytes | None): Raw JSON string containing corpus entries,
                or None/empty when the corpus does not exist yet.
        """
        super().__init__(data=Corpus._parse_corpus(raw))

    @staticmethod
    def _parse_corpus(raw: str | bytes | None) -> list[tuple[str, str, str]]:
        """Parse a raw JSON corpus string into a list of triples.

        Each stored entry is ``[chunk_id, document_id, text]``. Entries with
        fewer than three elements (e.g. from a schema migration) are silently
        skipped so a partially-migrated corpus does not crash search.

        Args:
            raw (str | bytes | None): Raw JSON string representing corpus data.

        Returns:
            list[tuple[str, str, str]]: Parsed corpus entries as
            (chunk_id, document_id, text) triples.
        """
        if not raw:
            return []
        try:
            corpus: list[list[str]] = loads(raw)
            return [(e[0], e[1], e[2]) for e in corpus if len(e) >= 3]
        except (ValueError, IndexError):
            logger.warning("Corpus parse failed — treating corpus as empty")
            return []

    @property
    def tokenized(self) -> list[list[str]]:
        """Return tokenized text for each document in the corpus.

        Returns:
            list[list[str]]: A list of token lists where each inner list contains
                the lowercased tokens for a chunk.
        """
        return [text.lower().split() for _, _, text in self.data]

    @property
    def chunk_ids(self) -> list[str]:
        """Return chunk IDs for each entry in the corpus.

        Returns:
            list[str]: A list of chunk IDs corresponding to the corpus entries.
        """
        return [chunk_id for chunk_id, _, _ in self.data]
