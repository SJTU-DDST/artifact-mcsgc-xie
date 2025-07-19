#!/bin/sh

# Check if exactly one argument is provided
if [ $# -ne 1 ]; then
  echo "Usage: $0 <output_filename>"
  exit 1
fi

OUT_FILE="$1"

# If a file with the same name already exists in the current directory, exit
if [ -e "./$OUT_FILE" ]; then
  echo "Error: File '$OUT_FILE' already exists in the current directory. Aborting."
  exit 1
fi

# Create the file in the current directory and set permissions to 777
touch "$OUT_FILE" || {
  echo "Failed to create file: $OUT_FILE"
  exit 1
}
chmod 777 "$OUT_FILE" || {
  echo "Failed to set permissions to 777: $OUT_FILE"
  exit 1
}

# Append output of sudo tail -f to the file
sudo tail -f /var/log/kern.log >> "$OUT_FILE"
