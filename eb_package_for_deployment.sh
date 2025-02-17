#!/bin/bash

# Define the zip file name
ZIP_FILE="build.zip"

# Create a zip file containing a.txt and b.txt
zip -r "$ZIP_FILE" prsb Dockerfile poetry.lock pyproject.toml

# Print success message
echo "Files have been zipped into $ZIP_FILE"
