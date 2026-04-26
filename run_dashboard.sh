#!/bin/bash
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
/opt/homebrew/bin/streamlit run dashboard.py "$@"
