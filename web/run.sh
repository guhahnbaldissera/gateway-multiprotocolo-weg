#!/bin/bash
# Sobe os simuladores e o dashboard web.
set -u

VENV=/root/venv-weg/bin/python
PROJ=/mnt/c/Users/gusta/Documents/Hackathon-WEG-Integre-2026/gateway
LIB=/root/vendor/libiec61850/build

echo "== IED Modbus simulado (5020) =="
if ! ss -ltn | grep -q :5020; then
    cd "$PROJ/sim" || exit 1
    setsid nohup $VENV modbus_server.py > /tmp/modbus_sim.log 2>&1 < /dev/null &
    sleep 4
fi
ss -ltn | grep -q :5020 && echo "   OK" || echo "   FALHOU"

echo "== IED IEC 61850 simulado (102) =="
if ! ss -ltn | grep -q :102; then
    cd "$LIB/examples/server_example_basic_io" || exit 1
    setsid nohup ./server_example_basic_io > /tmp/srv61850.log 2>&1 < /dev/null &
    sleep 3
fi
ss -ltn | grep -q :102 && echo "   OK" || echo "   FALHOU"

echo "== liberando portas do dashboard =="
fuser -k 8080/tcp 2>/dev/null
fuser -k 4840/tcp 2>/dev/null
fuser -k 4841/tcp 2>/dev/null
sleep 1

echo "== dashboard em http://localhost:8080 =="
cd "$PROJ" || exit 1
exec $VENV web/api.py
