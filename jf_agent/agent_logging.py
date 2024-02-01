from datetime import datetime, timedelta
import logging
from logging import LogRecord
import os
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from dataclasses import dataclass
from typing import Any, List, Union
import requests
from jf_agent.util import temp_disable_request_loggers

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
        if record is self._jf_sentinel:
            for handler in self.handlers:
                handler.handle(record)
                return
        return super().handle(record)


class CustomQueueHandler(QueueHandler):
    def __init__(self, queue: Queue[Any], webhook_base: str, api_token: str) -> None:
        super().__init__(queue)
        self.webhook_base = webhook_base
        self.api_token = api_token
        self.messages_to_send = []
        self.last_message_send_time = datetime.now()
        # Indication that logging has finished and we should send whatever is left in the current batch.
        self._sentinel = -1
        self.initiated_at = datetime.strftime(datetime.now(), '%Y-%m-%d-%H-%M-%S')

    def post_logs_to_jellyfish(self) -> bool:
        headers = {'Content-Type': 'application/json', 'X-JF-API-Token': self.api_token}
        url = self.webhook_base + '/agent-logs'
        with temp_disable_request_loggers():
            resp = requests.post(url, json={'logs': self.messages_to_send}, headers=headers)
        return resp.ok

    def handle(self, record: Union[logging.LogRecord, int]) -> None:
        now = datetime.now()

        if record is not self._sentinel:
            msg = _standardize_log_msg(self.format(record))
            self.messages_to_send.append(
                {
                    'message': msg,
                    'timestamp': int(
                        datetime.strptime(
                            record.asctime + '000', '%Y-%m-%d %H:%M:%S,%f'
                        ).timestamp()
                    ),
                    'initiated_at': self.initiated_at,
                }
            )

        if (
            record is self._sentinel
            or len(self.messages_to_send) >= 100
            or now - self.last_message_send_time > timedelta(minutes=5)
        ):
            self.post_logs_to_jellyfish()
            self.messages_to_send = []
            self.last_message_send_time = now


@dataclass
class AgentLoggingConfig:
    level: str
    datefmt: str
    handlers: List[str]
    listener: QueueListener


def configure(
    outdir: str, webhook_base: str, api_token: str, debug_requests=False
) -> AgentLoggingConfig:
    # Send log messages to std using a stream handler
    # All INFO level and above errors will go to STDOUT
    stdout_handler = CustomStreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(fmt='%(message)s'))
    stdout_handler.setLevel(logging.INFO)

    # logging in agent.log and those sent to the queue should be identical
    file_and_queue_formatter = logging.Formatter(
        fmt='%(asctime)s %(threadName)s %(levelname)s %(name)s %(message)s'
    )

    # Send log messages to using more structured format
    # All DEBUG level and above errors will go to the log file
    logfile_handler = CustomFileHandler(os.path.join(outdir, LOG_FILE_NAME), mode='a')
    logfile_handler.setFormatter(file_and_queue_formatter)
    logfile_handler.setLevel(logging.DEBUG)

    log_queue = Queue(-1)  # no size bound
    log_queue_handler = CustomQueueHandler(log_queue, webhook_base, api_token)
    log_queue_handler.setFormatter(file_and_queue_formatter)
    log_queue_handler.setLevel(logging.DEBUG)
    queue_listener = CustomQueueListener(log_queue, log_queue_handler, respect_handler_level=True)
    queue_listener.start()

    # Silence the urllib3 logger to only emit WARNING level logs,
    # because the debug level logs are super noisy
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
        handlers=[logfile_handler, stdout_handler, log_queue_handler],
        listener=queue_listener,
    )

    logging.basicConfig(
        level=config.level,
        datefmt=config.datefmt,
        handlers=config.handlers,
    )

    return config


def close_out(config: AgentLoggingConfig) -> None:
    # send a custom sentinel so the final log batch sends, then stop the listener
    config.listener.queue.put(-1)
    config.listener.stop()
