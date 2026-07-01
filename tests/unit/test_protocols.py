"""The adapter Protocols are @runtime_checkable; verify conformance."""

from api.adapters.base import EmbeddingAdapter, ParserAdapter
from api.adapters.parsers.pdf import PDFParser


def test_parser_adapter_conformance():
    assert isinstance(PDFParser(), ParserAdapter)


def test_embedding_adapter_conformance():
    class StubEmbedder:
        def embed(self, text):
            return [0.0]

        def embed_batch(self, texts):
            return [[0.0] for _ in texts]

        @property
        def dimensions(self):
            return 1

    assert isinstance(StubEmbedder(), EmbeddingAdapter)


def test_incomplete_object_is_not_a_parser():
    class NotAParser:
        def parse(self, file_path, document_id):
            return []

        # missing supported_extensions

    assert not isinstance(NotAParser(), ParserAdapter)
