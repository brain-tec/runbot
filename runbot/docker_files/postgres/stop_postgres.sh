#!/bin/bash
if [ "$PGHOST" = "/var/run/postgresql" ]; then
    echo "Skipping postgres server stop, using /var/run/postgresql socket"
elif [ -n "${PGHOST:-}" ] && [ -n "${PGDATA:-}" ]; then
    PG_BIN="/usr/lib/postgresql/$PG_VERSION/bin"
    if [ -n "$CLUSTER_ZSTD_NAME" ]; then
        echo "Compacting PostgreSQL databases before backup..."
        "$PG_BIN/vacuumdb" -U postgres -h "$PGHOST" --all --full --quiet || echo "Warning: vacuumdb failed, continuing backup."
    fi
    echo "Shutting down PostgreSQL safely..."
    /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl stop -D $PGDATA -m fast

    if [ -n "$CLUSTER_ZSTD_NAME" ]; then
        echo "Trimming PostgreSQL WAL and transient state..."
        "$PG_BIN/pg_resetwal" -D "$PGDATA" || echo "Warning: pg_resetwal failed, archiving untrimmed WAL."
        rm -rf "$PGDATA"/base/pgsql_tmp/* "$PGDATA"/base/*/pgsql_tmp*
        rm -rf "$PGDATA"/pg_stat/* "$PGDATA"/pg_stat_tmp/*
        rm -rf "$PGDATA"/log/* "$PGDATA"/pg_log/*
        rm -rf "$PGDATA"/pg_replslot/* "$PGDATA"/pg_snapshots/*

        echo "Creating backup cluster"
        BACKUP_DEST="/data/build/logs/$CLUSTER_ZSTD_NAME"
        cd /data/build

        BACKUP_PATHS=("${PGDATA#/data/build/}")
        if [ -d "$ODOO_FILESTORE" ]; then
            BACKUP_PATHS+=("${ODOO_FILESTORE#/data/build/}")
        else
            echo "Warning: Filestore directory not found, skipping in backup."
        fi

        # Default zstd arguments are merged with optional ZSTD_ARGS environment value (e.g. "-T2 -6").
        # zstd accepts multiple value definitions, last definition wins.
        ZSTD_DEFAULT=(-T1 -3)
        read -ra ZSTD_EXTRA <<< "${ZSTD_ARGS:-}"
        set -o pipefail
        tar -cf - "${BACKUP_PATHS[@]}" | zstd "${ZSTD_DEFAULT[@]}" "${ZSTD_EXTRA[@]}" > "$BACKUP_DEST"

        echo "Backup completed successfully at $BACKUP_DEST!"
    fi
fi
