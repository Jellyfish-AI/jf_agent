import gzip
import json
import jsonstreams


def write_file(outdir, filename_prefix, compress, results):
    if compress:
        with gzip.open(f'{outdir}/{filename_prefix}.json.gz', 'wb') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str).encode('utf-8'))
    else:
        with open(f'{outdir}/{filename_prefix}.json', 'w') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str))


class StrDefaultEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        kwargs.update({'default': str})
        super().__init__(*args, **kwargs)


def download_and_write_streaming(
        outdir, filename_prefix, compress, generator_func, generator_func_args, item_id_dict_key
):
    if compress:
        outfile = gzip.open(f'{outdir}/{filename_prefix}.json.gz', 'wt')
    else:
        outfile = open(f'{outdir}/{filename_prefix}.json', 'w')

    item_ids = set()
    with jsonstreams.Stream(jsonstreams.Type.array, fd=outfile, encoder=StrDefaultEncoder) as s:
        for item in generator_func(*generator_func_args):
            if isinstance(item, list):
                for i in item:
                    s.write(i)
                    item_ids.add(i[item_id_dict_key])
            else:
                s.write(item)
                item_ids.add(item[item_id_dict_key])

    outfile.close()
    return item_ids
