from typing import Any

from api.models.redis import Corpus, CorpusConfig


def get_corpus(redis_client: Any, corpus_config: CorpusConfig) -> Corpus:
    """Retrieve and deserialize a corpus stored in Redis.

    Args:
        redis_client: A Redis client instance.
        corpus_config: Configuration containing the Redis key for the corpus.

    Returns:
        A Corpus object.
    """
    raw = redis_client.get(corpus_config.corpus_key)
    return Corpus(raw)



def get_corpus_version(redis_client: Any, corpus_config: CorpusConfig) -> int:
    """Get the current version number of the corpus.

    Args:
        redis_client: A Redis client instance.
        corpus_config: Configuration containing the Redis key for the version.

    Returns:
        The current version number as an integer. Returns 0 if the version key is absent.
    """
    raw = redis_client.get(corpus_config.version_key)
    return int(raw) if raw else 0