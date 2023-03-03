import gzip
import json
import jsonstreams
import dataclasses
import logging
from jf_agent.util import batched

logger = logging.getLogger(__name__)

JELLYFISH_API_BASE = 'https://app.jellyfish.co'
VALID_RUN_MODES = (
    'validate',
    'download_and_send',
    'download_only',
    'send_only',
    'print_all_jira_fields',
    'print_apparently_missing_git_repos',
)


class BadConfigException(Exception):
    pass


def write_file(outdir, filename_prefix, compress, results):
    if compress:
        with gzip.open(f'{outdir}/{filename_prefix}.json.gz', 'wb') as outfile:
            outfile.write(json.dumps(results, indent=2, cls=StrDefaultEncoder).encode('utf-8'))
    else:
        with open(f'{outdir}/{filename_prefix}.json', 'w') as outfile:
            outfile.write(json.dumps(results, indent=2, cls=StrDefaultEncoder))


class StrDefaultEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return str(o)


def download_and_write_streaming(
    outdir,
    filename_prefix,
    compress,
    generator_func,
    generator_func_args,
    item_id_dict_key,
    addl_info_dict_key=None,
    batch_size=2000
):
    batch_num = 0
    item_infos = set()
    for batch in batched(generator_func(*generator_func_args), batch_size):
        filepath = f'{outdir}/{filename_prefix}{batch_num if batch_num else ""}'
        if compress:
            outfile = gzip.open(f'{filepath}.json.gz', 'wt')
        else:
            outfile = open(f'{filepath}.json', 'w')

        s = jsonstreams.Stream(jsonstreams.Type.array, fd=outfile, encoder=StrDefaultEncoder)
        for item in batch:
            if isinstance(item, list):
                for i in item:
                    s.write(i)
                    if not addl_info_dict_key:
                        item_infos.add(_get_item_by_key(i, item_id_dict_key))
                    else:
                        item_infos.add(
                            (
                                _get_item_by_key(i, item_id_dict_key),
                                _get_item_by_key(i, addl_info_dict_key),
                            )
                        )
            else:
                s.write(item)
                if not addl_info_dict_key:
                    item_infos.add(_get_item_by_key(item, item_id_dict_key))
                else:
                    item_infos.add(
                        (
                            _get_item_by_key(item, item_id_dict_key),
                            _get_item_by_key(item, addl_info_dict_key),
                        )
                    )
        logger.info(f'File: {filepath}, Size: {round(outfile.tell() / 1000000, 1)}MB')
        outfile.close()
        batch_num += 1
    return item_infos


def _get_item_by_key(item, key):
    if dataclasses.is_dataclass(item):
        return getattr(item, key)
    return item[key]
