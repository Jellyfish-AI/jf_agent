import sys

from jf_agent import main as jf_agent_main


def _run_with_args(args: list[str]) -> int:
    original_argv = sys.argv
    try:
        sys.argv = args
        success = jf_agent_main.main()
        return 0 if success else 1
    finally:
        sys.argv = original_argv


def main() -> int:
    exit_code = _run_with_args([sys.argv[0], *sys.argv[1:]])
    if exit_code != 0:
        print("encountered error")
        print("Will attempt to upload logs from the failed run, for debugging.")
        return _run_with_args([sys.argv[0], *sys.argv[1:], "-f"])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
