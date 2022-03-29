from typing import Any, List


def split(lst: List[Any], n: int) -> List[List[Any]]:
    """
    Split list `lst` into `n` approximately equal chunks
    """
    k, m = divmod(len(lst), n)
    return (lst[i * k + min(i, m) : (i + 1) * k + min(i + 1, m)] for i in range(n))
