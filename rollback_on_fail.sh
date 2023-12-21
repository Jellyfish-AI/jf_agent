#!/bin/bash

rollback () {
    curl -L -o - https://github.com/Jellyfish-AI/jf_agent/archive/refs/tags/stable.tar.gz | tar xz --directory ./
    rm -r jf_agent/
    mv jf_agent-stable/jf_agent/ jf_agent/
    python jf_agent/main.py "$@"
}

echo "checking for time limit override"
if [[ -v OVERRIDE_TIME_LIMIT ]]; then
	echo "Time limit override detected, agent will timeout at ${OVERRIDE_TIME_LIMIT}"
	TIME_LIMIT="${OVERRIDE_TIME_LIMIT}"g c
else
	TIME_LIMIT=$(curl -H "Accept: application/json" -H "Content-Type: application/json" -H "Jellyfish-API-Token: $JELLYFISH_API_TOKEN" https://app.jellyfish.co/endpoints/agent/time-limit | jq -r .time_limit_string)
	TIME_LIMIT="${TIME_LIMIT:-12h}"
	echo "Time limit override not detected, retrieved timelimit from Jellyfish: (${TIME_LIMIT})"
fi
echo "running agent with timelimit $TIME_LIMIT"

timeout --preserve-status "$TIME_LIMIT" python jf_agent/main.py "$@" 
CODE=$?

if [[ $CODE -ne 0 && ! -v ROLLBACK_OVERRIDE ]]; then
    echo "encountered error or timeout, rolling back to stable"

    echo "Will attempt to upload logs from the failed run, for debugging."

    timeout --preserve-status "$TIME_LIMIT" python jf_agent/main.py "$@" "-f"

    rollback "$@"
fi
