from datetime import datetime, timedelta
import logging
from logging import LogRecord
import colorama
import os
import structlog
import sys
import json
import uuid
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from dataclasses import dataclass
from typing import Any, List, Optional, Union
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlparse

from jf_ingest.logging_helper import AGENT_LOG_TAG

'''
Guidance on logging/printing in the agent:

stdout (e.g., print() output) is for the customer/operator; it should be used when
we have customer sensitive data that they do not want to submit to jellyfish (i.e. passwords)

logger output is for Jellyfish and customers; it should include:
 - function entry/exit timings
 - loop iteration timings/liveness indicators
 - errors/warnings encountered

logger output is submitted to Jellyfish, so should only contain non-sensitive data:
e.g., function names, iteration counts
'''

LOG_FILE_NAME = 'jf_agent.log'

SHARED_STRUCTLOG_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
    structlog.processors.CallsiteParameterAdder(
        {
            structlog.processors.CallsiteParameter.FILENAME,
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        }
    ),
]

LOG_LEVEL_COLORS = {
    'critical': colorama.Style.BRIGHT + colorama.Fore.RED,
    'error': colorama.Fore.RED,
    'warning': colorama.Fore.YELLOW,
    'info': colorama.Fore.GREEN,
    'debug': colorama.Fore.CYAN,
}
LOG_LEVEL_PADDING = len(max(LOG_LEVEL_COLORS.keys(), key=len))


# For styling in log files, I think it's best to always use new lines even when we use
# the special character to ignore them. Leverage always_use_newlines for this
def _standardize_log_msg(msg: str, always_use_newlines=False):
    special_code = '[!n]'
    if special_code in msg:
        msg = msg.replace('[!n]', '')
        if always_use_newlines:
            msg += '\n'
    else:
        msg += '\n'
    return msg


def _emit_helper(_self: logging.Handler, record: logging.LogRecord, always_use_newlines=False):
    try:
        if _self.stream is None:
            if isinstance(_self, logging.FileHandler) and (_self.mode != 'w' or not _self._closed):
                _self.stream = _self._open()

        msg = _standardize_log_msg(_self.format(record))

        _self.stream.write(msg)
        _self.flush()
    except RecursionError:  # See issue 36272
        raise
    except Exception:
        _self.handleError(record)


class CustomStreamHandler(logging.StreamHandler):
    """Handler that controls the writing of the newline character"""

    def emit(self, record) -> None:
        _emit_helper(self, record)


class CustomFileHandler(logging.FileHandler):
    """Handler that controls the writing of the newline character"""

    def emit(self, record) -> None:
        _emit_helper(self, record, always_use_newlines=True)


class CustomQueueListener(QueueListener):
    _jf_sentinel = -1

    def handle(self, record: Union[LogRecord, int]) -> None:
        if record == self._jf_sentinel:
            for handler in self.handlers:
                handler.handle(record)
                return
        return super().handle(record)


class CustomQueueHandler(QueueHandler):
    def __init__(self, queue: Queue[Any], webhook_base: str, api_token: str) -> None:
        super().__init__(queue)
        if not webhook_base.startswith('https://') and not webhook_base.startswith('http://'):
            raise ValueError('No protocol provided in jellyfish webhook base.')
        self.webhook_base = webhook_base
        self.api_token = api_token
        self.messages_to_send = []
        self.last_message_send_time = datetime.now()
        # Indication that logging has finished and we should send whatever is left in the current batch.
        self._sentinel = -1
        self.initiated_at = datetime.strftime(datetime.now(), '%Y-%m-%d-%H-%M-%S')
        self.webhook_path = '/agent-logs'
        self.secure = True
        self.post_errors = 0
        self.post_error_threshold = 10
        self.batches_sent = 0
        self._set_webhook_url()

    def _set_webhook_url(self):
        parsed = urlparse(self.webhook_base)
        self.secure = parsed.scheme == 'https'
        if parsed.path:
            self.webhook_path = parsed.path + self.webhook_path
        self.webhook_base = parsed.scheme + '://' + parsed.netloc

    def get_connection(self):
        '''establish an HTTP[S] connection to the jellyfish webhook service'''

        conn = (
            HTTPSConnection(self.webhook_base[8:])
            if self.secure
            else HTTPConnection(self.webhook_base[7:])
        )
        return conn

    def post_logs_to_jellyfish(self, now: datetime) -> bool:
        '''post a list of log data to the jellyfish webhook service. we are using the lower-level
        HTTP[S]Client as opposed to something like the requests library as these clients do not do
        any logging. generating logs at this point will send us into an infinite loop.'''

        headers = {'Content-Type': 'application/json', 'X-JF-API-Token': self.api_token}
        conn = self.get_connection()
        try:
            conn.request(
                "POST",
                self.webhook_path,
                body=json.dumps(
                    {'logs': self.messages_to_send, 'create_stream': self.batches_sent == 0}
                ),
                headers=headers,
            )
        except:
            self.post_errors += 1
            if self.post_errors < self.post_error_threshold:
                print(
                    'Error: could not post logs to Jellyfish. Queue was not cleared and another attempt will be made.'
                )
            elif self.post_errors == self.post_error_threshold:
                print(
                    'Max errors when posting logs to Jellyfish. Giving up, but continuing with the agent run.'
                )
            return

        resp = conn.getresponse()

        if resp.status == 200:
            self.batches_sent += 1
            self.messages_to_send = []
            self.last_message_send_time = now

    def handle(self, record: Union[logging.LogRecord, int]) -> None:
        now = datetime.now()

        if record != self._sentinel:
            msg = _standardize_log_msg(self.format(record))

            # This logic is required to handle the different way that the timestamp attribute
            # is set by structlog loggers
            try:
                asctime = (
                    int(
                        datetime.strptime(
                            record.asctime + '000', '%Y-%m-%d %H:%M:%S,%f'
                        ).timestamp()
                    )
                    * 1000
                )
            except AttributeError:
                created_time = datetime.fromtimestamp(record.created)
                asctime = created_time.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]

            timestamp = int(
                datetime.strptime(asctime + '000', '%Y-%m-%d %H:%M:%S,%f').timestamp() * 1000
            )

            self.messages_to_send.append(
                {'message': msg, 'timestamp': timestamp, 'initiated_at': self.initiated_at,}
            )

        if self.post_errors >= self.post_error_threshold:
            self.messages_to_send = []

        elif (
            record == self._sentinel
            or len(self.messages_to_send) >= 100
            or now - self.last_message_send_time > timedelta(minutes=5)
        ):
            self.post_logs_to_jellyfish(now)


@dataclass
class AgentLoggingConfig:
    level: int
    datefmt: str
    handlers: List[str]
    listener: QueueListener


class AgentConsoleLogFilter(logging.Filter):
    """
    This class is responsible for filtering out any logs that should only exist in Agent log file.
    There is a lot of debugging information and stack traces that we want to hide from the agent
    console logging for a good user experience, so we must filter them out here.
    We DO want all that information the logs, however, where we or a client go see them for debugging
    purposes.
    """

    def filter(self, record: LogRecord) -> bool:
        """Return True if the log should be emitted. Checks to see if the jf_ingest.logging_helper.AGENT_LOG_TAG
        is present in the record object. If it is, and it's set to True, this log should be suppressed (return False)

        Args:
            record (LogRecord): A record log object to potentially filter

        Returns:
            bool: Returns False if jf_ingest.logging_helper.AGENT_LOG_TAG is present and set to True. Returns True on all other records
        """
        return not record.__dict__.get(AGENT_LOG_TAG, False)


def configure_structlog() -> None:
    structlog.stdlib.recreate_defaults()
    colorama.init(autoreset=True)

    structlog.configure(
        processors=[
            *SHARED_STRUCTLOG_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def console_log_formatter() -> structlog.stdlib.ProcessorFormatter:
    custom_console_renderer = structlog.dev.ConsoleRenderer(
        columns=[
            structlog.dev.Column(
                "timestamp",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=colorama.Style.DIM + colorama.Fore.WHITE,
                    reset_style=colorama.Style.RESET_ALL,
                    value_repr=lambda value: datetime.fromisoformat(f"{value[:-1]}+00:00").strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                ),
            ),
            structlog.dev.Column(
                "level",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style="",
                    reset_style="",
                    value_repr=lambda value: _log_level_colorizer(value, LOG_LEVEL_PADDING),
                ),
            ),
            structlog.dev.Column(
                "event",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=colorama.Fore.WHITE,
                    reset_style=colorama.Style.RESET_ALL,
                    value_repr=str,
                ),
            ),
            # Removes the context vars from the log output
            structlog.dev.Column(
                "",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=colorama.Fore.BLACK,
                    reset_style=colorama.Style.RESET_ALL,
                    value_repr=lambda val: "",
                ),
            ),
        ],
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=SHARED_STRUCTLOG_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            custom_console_renderer,
        ],
    )

    return formatter


def _log_level_colorizer(log_level: str, pad_len: int) -> str:
    log_level_padded = log_level + (' ' * (pad_len - len(log_level)))
    color = LOG_LEVEL_COLORS.get(log_level, colorama.Fore.WHITE)
    return f"[{color}{log_level_padded}{colorama.Style.RESET_ALL}]"


def json_log_formatter() -> structlog.stdlib.ProcessorFormatter:
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=SHARED_STRUCTLOG_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    return formatter


def bind_default_agent_context(
    run_mode: str, company_slug: Optional[str], upload_time: str,
) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        run_mode=run_mode,
        company_slug=company_slug,
        upload_time=upload_time,
        agent_run_uuid=str(uuid.uuid4()),
        jf_meta={'commit': os.getenv("SHA"), 'commit_build_time': os.getenv("BUILDTIME")},
    )


def configure(
    outdir: str, webhook_base: str, api_token: str, debug_requests=False
) -> AgentLoggingConfig:
    # Remove default handlers that are added when logging is used before configuring
    logging.getLogger().handlers.clear()

    # Configure structlog and get necessary formatters for the log handlers
    # Note: structlog should be configured before the standard logging module
    configure_structlog()
    console_formatter = console_log_formatter()
    json_formatter = json_log_formatter()

    # Send log messages to std using a stream handler
    # All INFO level and above errors will go to STDOUT
    console_log_handler = CustomStreamHandler(stream=sys.stdout)
    console_log_handler.setFormatter(console_formatter)
    console_log_handler.setLevel(logging.INFO)
    console_log_handler.addFilter(AgentConsoleLogFilter())

    # Send log messages to using more structured format
    # All DEBUG level and above errors will go to the log file
    # We want to catch as much as possible in the Agent Log File!!
    logfile_handler = CustomFileHandler(os.path.join(outdir, LOG_FILE_NAME), mode='a')
    logfile_handler.setFormatter(json_formatter)
    # Set Log File Handler to DEBUG to catch as much debugging information as possible
    logfile_handler.setLevel(logging.DEBUG)

    log_queue = Queue(-1)  # no size bound
    log_queue_handler = CustomQueueHandler(log_queue, webhook_base, api_token)
    log_queue_handler.setFormatter(json_formatter)
    log_queue_handler.setLevel(logging.DEBUG)
    queue_listener = CustomQueueListener(log_queue, log_queue_handler, respect_handler_level=True)
    queue_listener.start()

    # Silence the urllib3 logger to only emit WARNING level logs,
    # because the debug level logs are super noisy
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if debug_requests:
        import http.client

        http_client_logger = logging.getLogger("http.client")
        http_client_logger.setLevel(logging.DEBUG)
        debug_fh = logging.FileHandler('debug.log')
        debug_fh.setLevel(logging.DEBUG)
        http_client_logger.addHandler(debug_fh)

        def print_to_log(*args):
            http_client_logger.debug(" ".join(args))

        # http.client uses `print` directly. Intercept calls and invoke our logger.
        http.client.print = print_to_log
        http.client.HTTPConnection.debuglevel = 1

    config = AgentLoggingConfig(
        level=logging.DEBUG,
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logfile_handler, console_log_handler, log_queue_handler],
        listener=queue_listener,
    )

    logging.basicConfig(
        level=config.level,
        datefmt=config.datefmt,
        handlers=[logfile_handler, console_log_handler, log_queue_handler],
        force=True,
    )

    logger = logging.getLogger(__name__)
    logger.info('Logging setup complete with handlers for log file, stdout, and streaming.')

    return config


def close_out(config: AgentLoggingConfig) -> None:
    # send a custom sentinel so the final log batch sends, then stop the listener
    logger = logging.getLogger(__name__)
    logger.info('Closing the agent log stream.')
    config.listener.queue.put(-1)
    config.listener.stop()
    logger.info('Log stream stopped.')
