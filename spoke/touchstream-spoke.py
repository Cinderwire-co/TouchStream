#!/usr/bin/env python3
"""
TouchStream Spoke - entry point

The kiosk service runs this file; the app itself lives in the touchstream/ package.

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
from touchstream.app import main

if __name__ == '__main__':
    main()
