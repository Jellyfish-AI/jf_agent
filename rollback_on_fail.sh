#!/bin/bash

python jf_agent/main.py "$@"
CODE=$?

if [[ $CODE -ne 0 ]]; then
    echo "encountered error"

    echo "Will attempt to upload logs from the failed run, for debugging."

    python jf_agent/main.py "$@" "-f"
fi