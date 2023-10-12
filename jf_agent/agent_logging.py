import logging
import os
import sys
import urllib3

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
def _emit_helper(_self: logging.Handler, record: logging.LogRecord, always_use_newlines=False):
    try:
        special_code = '[!n]'

        if _self.stream is None:
            if isinstance(_self, logging.FileHandler) and (_self.mode != 'w' or not _self._closed):
                _self.stream = _self._open()

        msg = _self.format(record)
        if special_code in msg:
            msg = msg.replace('[!n]', '')
            if always_use_newlines:
                msg += '\n'
        else:
            msg += '\n'

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


def configure(outdir):

    # Send log messages to std using a stream handler
    # All INFO level and above errors will go to STDOUT
    stdout_handler = CustomStreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(fmt='%(message)s'))
    stdout_handler.setLevel(logging.INFO)

    # Send log messages to using more structured format
    # All DEBUG level and above errors will go to the log file
    logfile_handler = CustomFileHandler(os.path.join(outdir, LOG_FILE_NAME), mode='a')
    logfile_handler.setFormatter(
        logging.Formatter(fmt='%(asctime)s %(threadName)s %(levelname)s %(name)s %(message)s')
    )
    logfile_handler.setLevel(logging.DEBUG)

    # Silence the urllib3 logger to only emit WARNING level logs,
    # because the debug level logs are super noisy
    logging.getLogger(urllib3.__name__).setLevel(logging.WARNING)

    logging.basicConfig(
        level=logging.DEBUG, datefmt='%Y-%m-%d %H:%M:%S', handlers=[logfile_handler, stdout_handler]
    )
