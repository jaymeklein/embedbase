import logging

logger = logging.getLogger(__name__)


def debug(data: str, *args) -> None:
    logger.debug(data, *args)
