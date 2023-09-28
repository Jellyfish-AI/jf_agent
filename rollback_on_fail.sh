#!/bin/bash

rollback () {
    wget https://github.com/Jellyfish-AI/jf_agent/archive/refs/tags/stable.tar.gz
    tar -zxvf stable.tar.gz --directory ./
    python jf_agent-stable/jf_agent/main.py
}

timeout --preserve-status 10h python jf_agent/main.py || rollback
