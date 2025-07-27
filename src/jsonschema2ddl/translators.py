import json
import logging
import os
import re
from typing import Dict, List
from urllib.request import urlopen, Request

import jsonschema

from jsonschema2ddl.models import Column, Table
from jsonschema2ddl.types import COLUMNS_TYPES_PREFERENCE
from jsonschema2ddl.utils import db_column_name, db_table_name, get_one_schema


class JSONSchemaToDatabase:
    """JSONSchemaToDatabase is the mother class for everything.

    Typically you want to instantiate a `JSONSchemaToPostgres` object, and
    run :func:`create_tables` to create all the tables. Run :func:`create_links`
    to populate all references properly and add foreign keys between tables.
    Optionally you can run :func:`analyze` finally which optimizes the tables.

    Attributes:
        schema (Dict): the schema to translate to tables.
        database_flavor (str): the flavor of the db. One of Postgres or Redshift.
        db_schema_name (str): the name of the schema in the database to create the tables.
        root_table_name (str): Name of the root table for the schema.
        abbreviations (Dict): Dictionary of abbreviations for columns.
        extra_columns (List[Dict]): List of extra columns.
        log_level (str): Log level of the deployment. Default 'DEBUG'.
    """

    logger: logging.Logger = logging.getLogger("JSONSchemaToDatabase")

    def __init__(
        self,
        schema: Dict,
        database_flavor: str = "postgres",
        db_schema_name: str = None,
        abbreviations: Dict = None,  # TODO: Implement abbreviations
        extra_columns: List = None,  # TODO: Implement extra columns
        root_table_name: str = "root",
        log_level: str = os.getenv("LOG_LEVEL", "DEBUG"),
    ):
        self.logger.setLevel(log_level)
        Table.logger = self.logger.getChild("Table")
        Column.logger = self.logger.getChild("Column")
        Table.logger.setLevel(log_level)
        Column.logger.setLevel(log_level)

        self.schema = schema

        self.database_flavor = database_flavor
        self.db_schema_name = db_schema_name
        self.root_table_name = db_table_name(root_table_name, schema_name=self.db_schema_name)
        self.extra_columns = extra_columns or list()
        self.abbreviations = abbreviations or dict()

        self._validate_schema()

        self.table_definitions = self._create_table_definitions()
        self.logger.info("Table definitions initialized")

    def _validate_schema(self):
        """Validates the jsonschema itself against the `$schema` url.

        Currently, some redirections are not supported.

        Raises:
            jsonschema.ValidationError: Schema is invalid
        """
        metaschema_uri = self.schema.get("$schema", "https://json-schema.org/draft-07/schema")
        r = Request(metaschema_uri, headers={"User-Agent": "Mozilla/5.0"})

        meta_schema = json.loads(urlopen(r).read())
        jsonschema.validate(instance=self.schema, schema=meta_schema)
        self.logger.debug("Schema is valid")

    def _create_table_definitions(self):
        """Creates the table definitions.

        Returns:
            Dict[str, Table]: A dictionary with tables ids and the tables objects to create.
        """
        # NOTE: create first empty tables to reference later in columns
        table_definitions = dict()
        columns_definitions = dict()
        schema_definitions = self.schema.get("definitions", {})
        for name, object_schema in schema_definitions.items():
            ref = object_schema.get("$id") or f"#/definitions/{name}"
            if "type" not in object_schema:
                object_schema = get_one_schema(object_schema)

            if object_schema["type"] == "object":
                table = Table(
                    ref=ref,
                    database_flavor=self.database_flavor,
                    name=db_table_name(name, schema_name=self.db_schema_name),
                    comment=object_schema.get("comment"),
                    jsonschema_fields=object_schema,
                )
                table_definitions[table.ref] = table
            else:
                # NOTE: Create new column for main table
                schema_type: str = object_schema["type"]
                if "format" in object_schema and object_schema["format"] in COLUMNS_TYPES_PREFERENCE:
                    schema_type: str = object_schema["format"]
                column = Column(
                    name=db_column_name(name),
                    database_flavor=self.database_flavor,
                    jsonschema_type=schema_type,
                    jsonschema_fields=object_schema,
                )
                columns_definitions[ref] = column

        root_table = Table(
            ref="root",
            database_flavor=self.database_flavor,
            name=self.root_table_name,
            comment=self.schema.get("comment", ""),
            jsonschema_fields=self.schema,
        )
        table_definitions[root_table.ref] = root_table

        for ref, table in table_definitions.items():
            table_definitions[ref] = table.expand_columns(table_definitions, columns_definitions)

        return table_definitions

    def _execute(self, cursor, query, args=None, query_ok_to_print=True):
        """Helper method to execute and debug a query.

        Args:
            cursor (psycopg2.cursor): Cursor object of the db connection.
            query (str): query to execute.
            args (List, optional): List of arguments for the execute command. Defaults to None.
            query_ok_to_print (bool, optional): Defaults to True.
        """
        if query_ok_to_print:
            self.logger.debug(query)
        cursor.execute(query, args)

    def create_tables(
        self,
        conn,
        drop_schema: bool = False,
        drop_tables: bool = False,
        drop_cascade: bool = True,
        auto_commit: bool = False,
    ):
        """Create the tables for the schema

        Args:
            conn (psocopg2.connection): Connection object to the db.
            drop_schema (bool, optional): Whether or not drop the schema if exists.
                Defaults to False.
            drop_tables (bool, optional): Whether or not drop the tables if exists.
                Defaults to False.
            drop_cascade (bool, optional): Execute drops with cascade. Defaults to True.
            auto_commit (bool, optional): autocomit after finishing. Defaults to False.
        """
        with conn.cursor() as cursor:
            self.logger.info(f"Creating tables in the schema {self.db_schema_name}")
            if self.db_schema_name is not None:
                if drop_schema:
                    self.logger.info(f"Dropping schema {self.db_schema_name}!!")
                    self._execute(
                        cursor,
                        f'DROP SCHEMA IF EXISTS {self.db_schema_name} {"CASCADE;" if drop_cascade else ";"}',
                    )
                self._execute(cursor, f"CREATE SCHEMA IF NOT EXISTS {self.db_schema_name};")

            self.logger.debug(self.table_definitions.keys())
            for table_ref, table in self.table_definitions.items():
                # FIXME: Move to a separate method
                self.logger.info(f"Trying to create table {table.name}")
                self.logger.debug(table_ref)
                self.logger.debug(table)
                if drop_tables:
                    self.logger.info(f"Dropping table {table.name}!!")
                    self._execute(
                        cursor,
                        f'DROP TABLE IF EXISTS {table.name} {"CASCADE;" if drop_cascade else ";"}',
                    )
                all_cols = [f' "{col.name}" {col.data_type}' for col in table.columns]
                unique_cols = [f'"{col}"' for col in table.columns if col.is_unique]
                create_q = (
                    f"""CREATE TABLE {table.name} ( """
                    f"""{','.join(all_cols)} """
                    f"""{", UNIQUE (" + ','.join(unique_cols) +  ")" if len(unique_cols) > 0 else ""} """
                    f"""{", PRIMARY KEY (" + table.primary_key.name +  ")" if table.primary_key else ""}); """
                )
                self._execute(cursor, create_q)
                if table.comment:
                    self.logger.debug(f"Set the following comment on table {table.name}: {table.comment}")
                    self._execute(cursor, f"COMMENT ON TABLE {table.name} IS '{table.comment}'")
                for col in table.columns:
                    if col.comment:
                        self.logger.debug(f"Set the following comment on column {col.name}: {col.comment}")
                        self._execute(
                            cursor,
                            f'COMMENT ON COLUMN {table.name}."{col.name}" IS ' + f"'{col.comment}'",
                        )
                self.logger.info("Table created!")

        if auto_commit:
            conn.commit()

    def create_links(self, conn, auto_commit: bool = True):
        """Adds foreign keys between tables.

        Args:
            conn (psocopg2.connection): connection object.
            auto_commit (bool, Optional): Defaults to False.
        """
        for table_ref, table in self.table_definitions.items():
            for col in table.columns:
                if col.is_fk():
                    fk_q = (
                        f"""ALTER TABLE {table.name} """
                        f"""ADD CONSTRAINT fk_{col.table_ref.name.split('"')[-2]} """  # FIXME: Formatting hack
                        f"""FOREIGN KEY ({col.name}) """
                        f"""REFERENCES {col.table_ref.name} ({col.table_ref.primary_key.name}); """
                    )
                    with conn.cursor() as cursor:
                        self._execute(cursor, fk_q)
                    if auto_commit:
                        conn.commit()

    def analyze(self, conn):
        """Runs `analyze` on each table. This improves performance.

        See the `Postgres documentation for Analyze
        <https://www.postgresql.org/docs/9.1/static/sql-analyze.html>`_

        Args:
            conn (psocopg2.connection): connection object.
        """
        self.logger.info("Analyzing tables...")
        with conn.cursor() as cursor:
            for table_ref, table in self.table_definitions.items():
                self.logger.info(f"Launch analyze for {table.name}")
                self._execute(cursor, "ANALYZE %s" % table.name)


class JSONSchemaToPostgres(JSONSchemaToDatabase):
    """Shorthand for JSONSchemaToDatabase(..., database_flavor='postgres')"""

    def __init__(self, *args, **kwargs):
        kwargs["database_flavor"] = "postgres"
        super(JSONSchemaToPostgres, self).__init__(*args, **kwargs)


class JSONSchemaToRedshift(JSONSchemaToDatabase):
    """Shorthand for JSONSchemaToDatabase(..., database_flavor='redshift')"""

    def __init__(self, *args, **kwargs):
        kwargs["database_flavor"] = "redshift"
        super(JSONSchemaToRedshift, self).__init__(*args, **kwargs)


class JSONSchemaToDuckDB(JSONSchemaToDatabase):
    """Shorthand for JSONSchemaToDatabase(..., database_flavor='duckdb')

    DuckDB-specific implementation that handles DuckDB's SQL dialect differences:
    - Uses DuckDB-specific data types (BOOLEAN, DOUBLE, VARCHAR, etc.)
    - Handles auto-increment primary keys differently (INTEGER PRIMARY KEY)
    - Uses DuckDB's schema and table creation syntax
    - Manages foreign key constraints in DuckDB format
    """

    def __init__(self, *args, **kwargs):
        kwargs["database_flavor"] = "duckdb"
        super(JSONSchemaToDuckDB, self).__init__(*args, **kwargs)

    def create_tables(self, conn, auto_commit: bool = True, drop_schema: bool = False, drop_cascade: bool = False):
        """Creates all tables in DuckDB with FK constraints during table creation.

        DuckDB-specific implementation that:
        - Creates schemas using DuckDB syntax
        - Creates sequences for auto-increment primary keys
        - Handles DuckDB-specific data types
        - Uses proper DuckDB table creation syntax with FK constraints
        - Orders table creation to respect FK dependencies

        Args:
            conn: DuckDB connection object
            auto_commit (bool): Whether to auto-commit transactions
            drop_schema (bool): Whether to drop schema before creating tables
            drop_cascade (bool): Whether to use CASCADE when dropping
        """
        if drop_schema and self.db_schema_name:
            with conn.cursor() as cursor:
                self.logger.info(f"Dropping schema {self.db_schema_name}!")
                self._execute(cursor, f"DROP SCHEMA IF EXISTS {self.db_schema_name} CASCADE;")

        if self.db_schema_name:
            with conn.cursor() as cursor:
                self.logger.info(f"Creating schema {self.db_schema_name}")
                self._execute(cursor, f"CREATE SCHEMA IF NOT EXISTS {self.db_schema_name};")

        # Create sequences for auto-increment primary keys first
        for table_ref, table in self.table_definitions.items():
            if table.primary_key and (table.primary_key.jsonschema_type in ["integer", "id"]):
                # Clean table name for sequence naming - replace all non-alphanumeric with underscore
                clean_table_name = table.name.replace('"', "").replace(".", "_").replace("-", "_")
                # Remove any other special characters that might cause issues

                clean_table_name = re.sub(r"[^a-zA-Z0-9_]", "_", clean_table_name)
                sequence_name = f"{clean_table_name}_seq"
                with conn.cursor() as cursor:
                    self.logger.info(f"Creating sequence {sequence_name} for table {table.name}")
                    # Drop sequence if it exists (for schema recreation)
                    if drop_schema:
                        self._execute(cursor, f"DROP SEQUENCE IF EXISTS {sequence_name};")
                    self._execute(cursor, f"CREATE SEQUENCE IF NOT EXISTS {sequence_name} START 1;")

        # Get table creation order respecting FK dependencies
        creation_order = self._get_table_creation_order()

        for table_ref in creation_order:
            table = self.table_definitions[table_ref]
            with conn.cursor() as cursor:
                self.logger.info(f"Trying to create table {table.name}")
                if drop_schema:
                    self.logger.info(f"Dropping table {table.name}!!")
                    self._execute(
                        cursor,
                        f'DROP TABLE IF EXISTS {table.name} {"CASCADE" if drop_cascade else ""};',
                    )

                # Handle DuckDB-specific column definitions
                all_cols = []
                foreign_keys = []

                for col in table.columns:
                    col_def = f'"{col.name}" {col.data_type}'

                    # Add DEFAULT NEXTVAL for auto-increment primary keys
                    if col.is_pk and col.jsonschema_type in ["integer", "id"]:
                        # Use same cleaning logic as sequence creation
                        clean_table_name = table.name.replace('"', "").replace(".", "_").replace("-", "_")

                        clean_table_name = re.sub(r"[^a-zA-Z0-9_]", "_", clean_table_name)
                        sequence_name = f"{clean_table_name}_seq"
                        col_def += f" PRIMARY KEY DEFAULT NEXTVAL('{sequence_name}')"
                    elif col.is_pk:
                        col_def += " PRIMARY KEY"

                    all_cols.append(col_def)

                    # Collect FK constraints for DuckDB
                    if col.is_fk():
                        fk_constraint = self._build_fk_constraint(col)
                        foreign_keys.append(fk_constraint)

                unique_cols = [f'"{col.name}"' for col in table.columns if col.is_unique]

                # Build CREATE TABLE statement for DuckDB
                create_parts = [f"CREATE TABLE {table.name} ("]
                create_parts.append(f"{', '.join(all_cols)}")

                if unique_cols:
                    create_parts.append(f", UNIQUE ({', '.join(unique_cols)})")

                # Add FK constraints to CREATE TABLE
                for fk_constraint in foreign_keys:
                    create_parts.append(f", {fk_constraint}")

                create_parts.append(");")
                create_q = " ".join(create_parts)

                self._execute(cursor, create_q)

                # DuckDB uses COMMENT ON syntax similar to PostgreSQL
                if table.comment:
                    self.logger.debug(f"Set the following comment on table {table.name}: {table.comment}")
                    self._execute(cursor, f"COMMENT ON TABLE {table.name} IS '{table.comment}'")

                for col in table.columns:
                    if col.comment:
                        self.logger.debug(f"Set the following comment on column {col.name}: {col.comment}")
                        self._execute(
                            cursor,
                            f"COMMENT ON COLUMN {table.name}.\"{col.name}\" IS '{col.comment}'",
                        )
                self.logger.info("Table created!")

        if auto_commit:
            conn.commit()

    def create_links(self, conn, auto_commit: bool = True):
        """Foreign keys are created during table creation in DuckDB.

        DuckDB foreign key constraints must be defined during table creation,
        not via ALTER TABLE ADD CONSTRAINT. This method is now a no-op for DuckDB.

        Args:
            conn: DuckDB connection object
            auto_commit (bool): Whether to auto-commit transactions
        """
        self.logger.info("DuckDB foreign keys were created during table creation. Skipping create_links.")
        # FK constraints are already created in create_tables() method

    def _get_table_creation_order(self):
        """Get table creation order respecting FK dependencies.

        Uses a simple heuristic: tables without FK dependencies first,
        then tables with FK dependencies.

        Returns:
            List of table references in creation order
        """
        tables_without_fk = []
        tables_with_fk = []

        for table_ref, table in self.table_definitions.items():
            has_fk = any(col.is_fk() for col in table.columns)
            if has_fk:
                tables_with_fk.append(table_ref)
            else:
                tables_without_fk.append(table_ref)

        # Simple heuristic: non-FK tables first, then FK tables
        # This works for most common cases where parent tables don't have FKs
        creation_order = tables_without_fk + tables_with_fk

        self.logger.debug(f"Table creation order: {creation_order}")
        return creation_order

    def _build_fk_constraint(self, col):
        """Build FK constraint string for DuckDB CREATE TABLE.

        Args:
            col: FKColumn object with FK information

        Returns:
            String: FK constraint in DuckDB format
        """
        # Format: FOREIGN KEY (column_name) REFERENCES parent_table(parent_column)
        constraint = f'FOREIGN KEY ("{col.name}") REFERENCES {col.table_ref.name} ("{col.table_ref.primary_key.name}")'
        self.logger.debug(f"Built FK constraint: {constraint}")
        return constraint

    def analyze(self, conn):
        """Runs analyze on each table for DuckDB.

        DuckDB has different syntax for analyzing tables.

        Args:
            conn: DuckDB connection object
        """
        self.logger.info("Analyzing tables...")
        with conn.cursor() as cursor:
            for table_ref, table in self.table_definitions.items():
                self.logger.info(f"Launch analyze for {table.name}")
                # DuckDB uses ANALYZE instead of ANALYZE table_name
                self._execute(cursor, f"ANALYZE {table.name}")
