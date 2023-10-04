#!/bin/bash

rollback () {
    curl -L -o - https://github.com/Jellyfish-AI/jf_agent/archive/refs/tags/stable.tar.gz | tar -xzv --directory ./
    python jf_agent-stable/jf_agent/main.py
}

TOKEN=$(sed -n 's/JELLYFISH_API_TOKEN=//p' creds.env)
TIME_LIMIT=$(curl -i -H "Accept: application/json" -H "Content-Type: application/json" -H "Jellyfish-API-Token: $TOKEN" https://app.jellyfish.co/endpoints/agent/time-limit | jq time_limit_string)
TIME_LIMIT="${TIME_LIMIT:-12}"
timeout --preserve-status "$TIME_LIMIT"h python jf_agent/main.py || rollback
