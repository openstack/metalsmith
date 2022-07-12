#!/bin/bash

set -euxo pipefail

find playbooks -maxdepth 1 -type f -regex '.*.ya?ml' -print0 | \
    xargs -t -n1 -0 ansible-lint -x metadata -vv --nocolor
find roles -maxdepth 1 -mindepth 1 -type d -printf "%p/\n" | \
    xargs -t -n1 ansible-lint -x metadata -vv --nocolor
