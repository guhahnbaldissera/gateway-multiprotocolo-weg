#!/bin/bash
# Sobe os simuladores (IED Modbus + IED IEC 61850) e roda a demonstracao.
# Executar dentro do WSL Ubuntu.

set -u

VENV=/root/venv-weg/bin/python
PROJ=/mnt/c/Users/gusta/Documents/Hackathon-WEG-Integre-2026/gateway
LIB=/root/vendor/libiec61850/build

echo "== subindo IED Modbus simulado (porta 5020) =="
if ! ss -ltn | grep -q :5020; then
    cd "$PROJ/sim" || exit 1
    setsid nohup $VENV modbus_server.py > /tmp/modbus_sim.log 2>&1 < /dev/null &
    sleep 4
fi
ss -ltn | grep -q :5020 && echo "   OK porta 5020" || { echo "   FALHOU"; cat /tmp/modbus_sim.log; }

echo "== subindo IED IEC 61850 simulado (porta 102) =="
if ! ss -ltn | grep -q :102; then
    cd "$LIB/examples/server_example_basic_io" || exit 1
    setsid nohup ./server_example_basic_io > /tmp/srv61850.log 2>&1 < /dev/null &
    sleep 3
fi
ss -ltn | grep -q :102 && echo "   OK porta 102" || echo "   FALHOU"

echo "== executando demonstracao =="
cd "$PROJ" || exit 1
$VENV demo.py
