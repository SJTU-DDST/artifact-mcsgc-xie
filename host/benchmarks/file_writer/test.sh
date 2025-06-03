#!/bin/bash

echo "entering directory $(realpath $(dirname $0))"
pushd $(dirname $0) > /dev/null

# Compile the C program
gcc -o file_writer file_writer.c -lpthread

# Check if the compilation was successful
if [ $? -ne 0 ]; then
    echo "Compilation failed."
    exit 1
fi

# Define common parameters
DIRECTORY="/mnt/openssd_f2fs"
FILENAME_PREFIX="testfile"
NUM_FILES=15
TOTAL_SIZE="4G"
NUM_THREADS=15
BUFFER_SIZE="1M"
USE_FALLOCATE="no"

# Ensure the directory exists
mkdir -p $DIRECTORY

# Test in collaborate mode
echo "Testing in collaborate mode..."
./file_writer $DIRECTORY $FILENAME_PREFIX $NUM_FILES $TOTAL_SIZE $NUM_THREADS $BUFFER_SIZE collaborate $USE_FALLOCATE

echo "Cleaning up test files..."
rm -f $DIRECTORY/${FILENAME_PREFIX}*

# Test in independent mode
echo "Testing in independent mode..."
./file_writer $DIRECTORY $FILENAME_PREFIX $NUM_FILES $TOTAL_SIZE $NUM_THREADS $BUFFER_SIZE independent $USE_FALLOCATE

echo "Cleaning up test files..."
rm -f $DIRECTORY/${FILENAME_PREFIX}*
# rmdir $DIRECTORY

echo "Tests completed."

popd > /dev/null