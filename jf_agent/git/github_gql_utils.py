from datetime import datetime, timezone


def github_gql_format_to_datetime(datetime_str: str) -> datetime:
    return datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def datetime_to_gql_str_format(_datetime: datetime) -> str:
    return _datetime.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
