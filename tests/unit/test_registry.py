"""Unit tests for the adapter registries (parsers, embeddings, vector stores)."""

import pytest

from api.adapters.parsers import SUPPORTED_EXTENSIONS, get_parser
from api.adapters.parsers.code import CodeParser
from api.adapters.parsers.csv_parser import CSVParser
from api.adapters.parsers.json_parser import JSONParser
from api.adapters.parsers.markdown import MarkdownParser
from api.adapters.parsers.pdf import PDFParser
from api.adapters.parsers.txt import TXTParser


@pytest.mark.parametrize(
    ("ext", "cls"),
    [
        (".pdf", PDFParser),
        (".txt", TXTParser),
        (".md", MarkdownParser),
        (".markdown", MarkdownParser),
        (".py", CodeParser),
        (".ts", CodeParser),
        (".go", CodeParser),
        (".csv", CSVParser),
        (".json", JSONParser),
    ],
)
def test_get_parser_resolves_extension(ext, cls):
    assert isinstance(get_parser(ext), cls)


def test_get_parser_is_case_insensitive():
    assert isinstance(get_parser(".PDF"), PDFParser)


def test_get_parser_unknown_extension_raises():
    with pytest.raises(ValueError, match="No parser registered"):
        get_parser(".xyz")


def test_supported_extensions_complete():
    for ext in (".pdf", ".txt", ".md", ".py", ".csv", ".json"):
        assert ext in SUPPORTED_EXTENSIONS


# --- embeddings -------------------------------------------------------------

def test_embedding_unknown_provider_raises():
    from api.adapters.embeddings import get_embedding_adapter
    from api.models.config import EmbeddingConfig

    cfg = EmbeddingConfig(provider="nope")
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        get_embedding_adapter(cfg)


# --- vector stores ----------------------------------------------------------

def test_vector_store_backends_registered():
    from api.adapters.vector_store import _registry

    assert {"chroma", "pgvector", "qdrant"}.issubset(_registry.keys())


def test_vector_store_unknown_backend_raises():
    from api.adapters.vector_store import get_vector_store
    from api.models.config import VectorStoreConfig

    cfg = VectorStoreConfig(backend="nope")
    with pytest.raises(ValueError, match="Unknown vector store backend"):
        get_vector_store(cfg, 384)
