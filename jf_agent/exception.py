import logging
import traceback
from typing import Optional

logger = logging.getLogger(__name__)


class BadConfigException(Exception):
    def __exit__(
        self,
        msg: str = 'BadConfigException (see earlier messages)',
        exc: Optional[Exception] = None,
    ):
        super().__init__(msg)
        logger.error(msg)

        if exc:
            exc_type = type(exc).__name__
            logger.error(msg=f'{exc_type}: {str(exc)}')
            logger.error(msg=f'Traceback:\n{traceback.format_exc()}')
