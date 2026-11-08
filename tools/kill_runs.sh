#!/usr/bin/env bash
# stop any gold/eval runs still going inside WSL and clear their leftover sandboxes
pkill -f 'tools/gold_check.py' && echo "killed gold_check" || echo "nothing running"
sleep 1
rm -rf /tmp/swegemma_sandbox_* 2>/dev/null
echo "sandboxes cleared"
