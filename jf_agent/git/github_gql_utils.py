import json
from typing import Generator

from requests import Session

from jf_agent.session import retry_session


def get_github_gql_base_url(base_url: str):
    if base_url and 'api/v3' in base_url:
        # Github server clients provide an API with a trailing '/api/v3'
        # replace this with the graphql endpoint
        return base_url.replace('api/v3', 'api/graphql')
    else:
        return 'https://api.github.com/graphql'


def get_github_gql_session(token: str, verify: bool = True, session: Session = None):
    if not session:
        session = retry_session()

    session.verify = verify
    session.headers.update(
        {'Authorization': f'token {token}', "Accept": "application/vnd.github+json",}
    )
    return session


def page_results(
    query_body: str, path_to_page_info: str, session: Session, base_url: str, cursor: str = 'null'
) -> Generator[dict, None, None]:

    # TODO: Write generalized paginator
    hasNextPage = True
    while hasNextPage:
        # Fetch results
        result = get_raw_result(
            query_body=(query_body % cursor), base_url=base_url, session=session
        )

        yield result

        # Get relevant data and yield it
        path_tokens = path_to_page_info.split('.')
        for token in path_tokens:
            result = result[token]

        page_info = result['pageInfo']
        # Need to grab the cursor and wrap it in quotes
        _cursor = page_info['endCursor']
        cursor = f'"{_cursor}"'
        hasNextPage = page_info['hasNextPage']


def get_raw_result(query_body: str, base_url: str, session: Session) -> dict:
    response = session.post(url=base_url, json={'query': query_body})
    response.raise_for_status()
    json_str = response.content.decode()
    json_data = json.loads(json_str)
    if 'errors' in json_data:
        raise Exception(
            f'Exception encountered when trying to query: {query_body}. Error: {json_data["errors"]}'
        )
    return json_data
