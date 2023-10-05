#!/bin/bash

rollback () {
    curl -L -o - https://github.com/Jellyfish-AI/jf_agent/archive/refs/tags/stable.tar.gz | tar -xzv --directory ./
    python jf_agent-stable/jf_agent/main.py
}

echo "Retrieving time limit value from JF"
TIME_LIMIT=$(curl -H "Accept: application/json" -H "Content-Type: application/json" -H "Jellyfish-API-Token: $JELLYFISH_API_TOKEN" https://app.jellyfish.co/endpoints/agent/time-limit | jq .time_limit_string)
TIME_LIMIT="${TIME_LIMIT:-12h}"
echo "checking for time limit override"
TIME_LIMIT="${OVERRIDE_TIME_LIMIT:-$TIME_LIMIT}"
echo "running agent with timelimit $TIME_LIMIT"
timeout --preserve-status "$TIME_LIMIT" python jf_agent/main.py || echo "encountered error or timeout, rolling back to stable" && rollback
