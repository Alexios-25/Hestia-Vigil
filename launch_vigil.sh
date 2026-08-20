#!/bin/bash
# Launches Vigil processes detached from the parent shell (macOS compatible)
cd /Users/AlexHollema/Hestia/Vigil || exit 1
mkdir -p logs

# Launch worker detached - nohup prevents SIGHUP propagation
nohup .venv/bin/python3 src/vigil_worker.py > logs/worker.log 2>&1 &
WPID=$!

# Launch dashboard detached
nohup .venv/bin/python3 src/dashboard.py > logs/dashboard.log 2>&1 &
DPID=$!

echo "Worker PID: $WPID"
echo "Dashboard PID: $DPID"
echo "Both launched with nohup"