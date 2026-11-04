#!/bin/bash
# TouchStream Spoke setup (kept for older instructions) - runs install.sh
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" "$@"
