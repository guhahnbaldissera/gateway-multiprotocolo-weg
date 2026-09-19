#!/bin/bash
/root/venv-weg/bin/pip install -q flask
/root/venv-weg/bin/python - <<'EOF'
import flask
print("flask", flask.__version__)
EOF
