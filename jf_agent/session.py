import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


class ReauthSession(requests.Session):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def request(self, method, url, **kwargs):
        # If we get HTTP 401, re-authenticate and try again
        response = super().request(method, url, **kwargs)
        if response.status_code == 401:
            print(f'WARN: received 401 for the request [{method}] {url} - resetting client session')
            # Clear cookies and re-auth
            self.cookies.clear()
            response = super().request(method, url, **kwargs)
            self.cookies = response.cookies
        return response


def retry_session(**kwargs):
    """
    Obtains a requests session with retry settings.
    :return: session: Session
    """

    session = ReauthSession(**kwargs)

    retries = 3
    backoff_factor = 0.5
    status_forcelist = (500, 502, 504)

    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    return session
