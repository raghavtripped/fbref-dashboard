#!/bin/bash
cd "$(dirname "$0")/backend"
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
