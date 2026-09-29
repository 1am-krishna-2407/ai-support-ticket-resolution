#!/bin/sh
# Runs once, when the Postgres data volume is first initialised.
# Creates a separate database for integration tests so they never touch real data.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE DATABASE "${POSTGRES_DB}_test" OWNER "$POSTGRES_USER";
EOSQL
