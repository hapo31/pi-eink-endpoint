#!/bin/bash

source .env

if [ -z $RASPI_HOST ]; then
  echo "RASPI_HOST is not set in .env file"
  exit 1
fi

if [ -z $RASPI_USER ]; then
  echo "RASPI_USER is not set in .env file"
  exit 1
fi

if [ -z $RASPI_KEY_PATH ]; then
  echo "RASPI_KEY_PATH is not set in .env file"
  exit 1
fi

cp pi_eink_endpoint/**/*.py dist/
cp pyproject.toml dist/
rsync -avz --mkpath --delete dist/ "$RASPI_USER@$RASPI_HOST:/home/${RASPI_USER}/eink-endpoint/dist/" -e "ssh -i $RASPI_KEY_PATH"
