#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config="configs/config20_fio_8file-1to1.sh"

sudo "${script_dir}/test.sh" ori "${config}"
sudo "${script_dir}/test.sh" csgc "${config}"
