#!/bin/bash

#export $(grep -v '^#' env_vars/dev.env | xargs)
#
## get the static files ready
#poetry run python prsb/manage.py collectstatic --noinput
#
#
## Define the zip file name
#ZIP_FILE="build.zip"
#
## Create a zip file containing a.txt and b.txt
#zip -r "$ZIP_FILE" prsb Dockerfile poetry.lock pyproject.toml
#
## Print success message
#echo "Files have been zipped into $ZIP_FILE"




mkdir -p lambda_package

poetry export --with=lambda -f requirements.txt --without-hashes -o lambda_package/lambda-requirements.txt
pip install --target=lambda_package/ -r lambda_package/lambda-requirements.txt

cp -r prsb/* lambda_package

mv lambda_package/scripts/birthday_checker.py lambda_package/

cd lambda_package
zip -r9 ../lambda-deployment.zip .
cd ..

