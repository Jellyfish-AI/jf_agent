"""Compare two jf_agent Jira download outputs for equality.

Handles batched issue files (jira_issues.json, jira_issues1.json, ...)
by merging them before comparison, since batch boundaries depend on
serialized size and may differ between runs.
"""

import json
import os
import re
import sys


def find_jira_dir(base_dir: str) -> str:
    """Find the jira/ subdirectory inside the timestamped output folder."""
    entries = os.listdir(base_dir)
    for entry in sorted(entries):
        jira_path = os.path.join(base_dir, entry, "jira")
        if os.path.isdir(jira_path):
            return jira_path
    jira_path = os.path.join(base_dir, "jira")
    if os.path.isdir(jira_path):
        return jira_path
    raise FileNotFoundError(f"No jira/ subdirectory found under {base_dir}")


def normalize(data):
    """Recursively sort lists of dicts for stable comparison."""
    if isinstance(data, list):
        normalized = [normalize(item) for item in data]
        if all(isinstance(item, dict) for item in normalized):
            normalized.sort(key=lambda x: json.dumps(x, sort_keys=True))
        return normalized
    if isinstance(data, dict):
        return {k: normalize(v) for k, v in data.items()}
    return data


def load_and_merge_batched_files(jira_dir: str, prefix: str) -> list:
    """Load all batch files for a given prefix and merge into one list.

    e.g. prefix='jira_issues' merges jira_issues.json, jira_issues1.json, ...
    """
    merged = []
    pattern = re.compile(rf'^{re.escape(prefix)}(\d*)\.json$')
    for filename in sorted(os.listdir(jira_dir)):
        m = pattern.match(filename)
        if m:
            with open(os.path.join(jira_dir, filename)) as f:
                data = json.load(f)
            if isinstance(data, list):
                merged.extend(data)
            else:
                merged.append(data)
    return merged


def get_batch_prefixes(files: set[str]) -> dict[str, set[str]]:
    """Group files into batch prefixes and standalone files.

    Returns {prefix: set_of_filenames} where batched files share a prefix.
    e.g. jira_issues.json, jira_issues1.json -> prefix 'jira_issues'
    """
    batch_pattern = re.compile(r'^(.+?)(\d*)\.json$')
    prefix_to_files: dict[str, set[str]] = {}
    for f in files:
        m = batch_pattern.match(f)
        if m:
            prefix = m.group(1)
            prefix_to_files.setdefault(prefix, set()).add(f)
    return prefix_to_files


def compare_data(data_a, data_b) -> tuple[bool, str]:
    """Compare two loaded JSON structures. Returns (match, detail)."""
    norm_a = normalize(data_a)
    norm_b = normalize(data_b)

    if norm_a == norm_b:
        return True, "MATCH"

    if isinstance(data_a, list) and isinstance(data_b, list):
        len_a, len_b = len(data_a), len(data_b)
        if len_a != len_b:
            return False, f"DIFF: list length {len_a} vs {len_b}"

        diff_count = 0
        first_diff_idx = None
        for i, (a, b) in enumerate(zip(norm_a, norm_b)):
            if a != b:
                diff_count += 1
                if first_diff_idx is None:
                    first_diff_idx = i
        detail = f"DIFF: {diff_count}/{len_a} items differ"
        if first_diff_idx is not None and diff_count <= 5:
            # Show keys of first differing item for debugging
            item_a = norm_a[first_diff_idx]
            item_b = norm_b[first_diff_idx]
            if isinstance(item_a, dict) and isinstance(item_b, dict):
                diff_keys = [k for k in set(item_a) | set(item_b) if item_a.get(k) != item_b.get(k)]
                detail += f" (first diff at idx {first_diff_idx}, differing keys: {diff_keys[:10]})"
        return False, detail

    if isinstance(data_a, dict) and isinstance(data_b, dict):
        keys_a = set(data_a.keys())
        keys_b = set(data_b.keys())
        added = keys_b - keys_a
        removed = keys_a - keys_b
        changed = [k for k in keys_a & keys_b if normalize(data_a[k]) != normalize(data_b[k])]
        parts = []
        if added:
            parts.append(f"added keys: {added}")
        if removed:
            parts.append(f"removed keys: {removed}")
        if changed:
            parts.append(f"changed keys: {changed}")
        return False, f"DIFF: {'; '.join(parts)}"

    return False, "DIFF: top-level type or value mismatch"


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <baseline_output_dir> <candidate_output_dir>")
        sys.exit(1)

    baseline_dir = find_jira_dir(sys.argv[1])
    candidate_dir = find_jira_dir(sys.argv[2])

    print(f"Baseline:  {baseline_dir}")
    print(f"Candidate: {candidate_dir}")
    print()

    baseline_files = {f for f in os.listdir(baseline_dir) if f.endswith(".json")}
    candidate_files = {f for f in os.listdir(candidate_dir) if f.endswith(".json")}

    # Identify batched file groups (files sharing a prefix with optional numeric suffix)
    baseline_prefixes = get_batch_prefixes(baseline_files)
    candidate_prefixes = get_batch_prefixes(candidate_files)

    # Find prefixes that have multiple files in either side (i.e. are batched)
    batched_prefixes = set()
    for prefix, files in {**baseline_prefixes, **candidate_prefixes}.items():
        if len(files) > 1:
            batched_prefixes.add(prefix)

    # Collect standalone files (not part of a batch group)
    batched_files_baseline = set()
    batched_files_candidate = set()
    for prefix in batched_prefixes:
        batched_files_baseline |= baseline_prefixes.get(prefix, set())
        batched_files_candidate |= candidate_prefixes.get(prefix, set())

    standalone_baseline = baseline_files - batched_files_baseline
    standalone_candidate = candidate_files - batched_files_candidate

    passed = 0
    failed = 0

    # Compare standalone files
    all_standalone = sorted(standalone_baseline | standalone_candidate)
    for filename in all_standalone:
        path_a = os.path.join(baseline_dir, filename)
        path_b = os.path.join(candidate_dir, filename)

        if filename not in standalone_baseline:
            print(f"  NEW (candidate only): {filename}")
            failed += 1
            continue
        if filename not in standalone_candidate:
            print(f"  MISSING (baseline only): {filename}")
            failed += 1
            continue

        with open(path_a) as f:
            data_a = json.load(f)
        with open(path_b) as f:
            data_b = json.load(f)

        match, detail = compare_data(data_a, data_b)
        status = "PASS" if match else "FAIL"
        print(f"  {status}: {filename} -- {detail}")
        if match:
            passed += 1
        else:
            failed += 1

    # Compare batched file groups (merge all batches, then compare)
    for prefix in sorted(batched_prefixes):
        baseline_batch_files = sorted(baseline_prefixes.get(prefix, set()))
        candidate_batch_files = sorted(candidate_prefixes.get(prefix, set()))
        label = f"{prefix}*.json ({len(baseline_batch_files)} baseline batches, {len(candidate_batch_files)} candidate batches)"

        if not baseline_batch_files:
            print(f"  NEW (candidate only): {label}")
            failed += 1
            continue
        if not candidate_batch_files:
            print(f"  MISSING (baseline only): {label}")
            failed += 1
            continue

        print(f"  Merging {label}...", end=" ", flush=True)
        data_a = load_and_merge_batched_files(baseline_dir, prefix)
        data_b = load_and_merge_batched_files(candidate_dir, prefix)

        match, detail = compare_data(data_a, data_b)
        status = "PASS" if match else "FAIL"
        print(f"{status} -- {detail}")
        if match:
            passed += 1
        else:
            failed += 1

    print()
    total = passed + failed
    print(f"Results: {passed} passed, {failed} failed out of {total} comparisons")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
