#!/bin/bash

# echo "entering directory $(realpath $(dirname $0))"
pushd $(dirname $0) > /dev/null

# Compile the C program
gcc -o file_writer file_writer.c -lpthread

# Check if the compilation was successful
if [ $? -ne 0 ]; then
    echo "Compilation failed."
    exit 1
fi

popd > /dev/null