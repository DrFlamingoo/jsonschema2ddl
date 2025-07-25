"""Test cases for DuckDB support in jsonschema2ddl"""

import datetime
import pytest
import duckdb

from jsonschema2ddl import JSONSchemaToDuckDB


def test_duckdb_flat_schema(duckdb_db, schema_flat):
    """Test basic table creation with flat schema in DuckDB"""
    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema_flat,
        db_schema_name="schm",
        root_table_name="my_table",
    )

    # Test table creation
    translator.create_tables(connection, auto_commit=True)
    translator.create_links(connection)
    translator.analyze(connection)

    # Test data insertion and retrieval
    cursor = connection.cursor()

    # Insert test data
    data = [
        (1, "john", 20, "USA"),
        (2, "doe", 21, "USA"),
    ]

    cursor.executemany('INSERT INTO "schm"."my_table" (user_id, user_name, age, address) VALUES (?, ?, ?, ?)', data)

    # Verify data was inserted
    result = cursor.execute('SELECT * FROM "schm"."my_table"').fetchall()
    assert len(result) == 2
    assert result[0][0] == 1  # user_id
    assert result[0][1] == "john"  # user_name
    assert result[1][0] == 2
    assert result[1][1] == "doe"


def test_duckdb_schema_with_definitions(duckdb_db, schema):
    """Test schema with definitions (nested objects) in DuckDB"""
    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="schm",
        root_table_name="my_table",
    )

    # Create tables
    translator.create_tables(connection, auto_commit=True)
    translator.create_links(connection)
    translator.analyze(connection)

    cursor = connection.cursor()

    # Verify tables were created
    result = cursor.execute(
        """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'schm'
        ORDER BY table_name
    """
    ).fetchall()

    table_names = [row[0] for row in result]
    assert "my_table" in table_names
    assert "address" in table_names


def test_duckdb_type_mappings(duckdb_db):
    """Test that DuckDB type mappings work correctly"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "bool_field": {"type": "boolean"},
            "int_field": {"type": "integer"},
            "float_field": {"type": "number"},
            "string_field": {"type": "string", "maxLength": 50},
            "date_field": {"type": "string", "format": "date"},
            "datetime_field": {"type": "string", "format": "date-time"},
            "json_field": {"type": "object"},
            "array_field": {"type": "array"},
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="test_types",
        root_table_name="type_test",
    )

    translator.create_tables(connection, auto_commit=True)

    # Check column types
    cursor = connection.cursor()
    result = cursor.execute(
        """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'test_types' AND table_name = 'type_test'
        ORDER BY column_name
    """
    ).fetchall()

    column_types = {row[0]: row[1] for row in result}

    # Verify DuckDB-specific type mappings
    assert "BOOLEAN" in column_types.get("bool_field", "").upper()
    assert "BIGINT" in column_types.get("int_field", "").upper()
    assert "DOUBLE" in column_types.get("float_field", "").upper()
    assert "VARCHAR" in column_types.get("string_field", "").upper()
    assert "DATE" in column_types.get("date_field", "").upper()
    assert "TIMESTAMP" in column_types.get("datetime_field", "").upper()


def test_duckdb_primary_key_auto_increment(duckdb_db):
    """Test that DuckDB auto-increment primary keys work correctly"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},  # Use standard integer with pk flag
            "name": {"type": "string"},
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="test_pk",
        root_table_name="pk_test",
    )

    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()

    # Insert data without specifying ID (should auto-increment)
    cursor.execute('INSERT INTO "test_pk"."pk_test" (name) VALUES (?)', ("test1",))
    cursor.execute('INSERT INTO "test_pk"."pk_test" (name) VALUES (?)', ("test2",))

    # Verify auto-increment worked
    result = cursor.execute('SELECT id, name FROM "test_pk"."pk_test" ORDER BY id').fetchall()
    assert len(result) == 2
    assert result[0][0] == 1
    assert result[0][1] == "test1"
    assert result[1][0] == 2
    assert result[1][1] == "test2"


def test_duckdb_foreign_keys(duckdb_db):
    """Test foreign key creation in DuckDB"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "category": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "pk": True},  # Use standard integer with pk flag
                    "name": {"type": "string"},
                },
            }
        },
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},  # Use standard integer with pk flag
            "title": {"type": "string"},
            "category_ref": {"type": "integer", "ref": "#/definitions/category"},  # Use integer instead of link
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="test_fk",
        root_table_name="items",
    )

    translator.create_tables(connection, auto_commit=True)
    translator.create_links(connection)  # Now works with FK constraints during table creation

    cursor = connection.cursor()

    # Insert parent record
    cursor.execute('INSERT INTO "test_fk"."category" (name) VALUES (?)', ("Electronics",))

    # Insert child record with valid FK
    cursor.execute('INSERT INTO "test_fk"."items" (title, category_ref) VALUES (?, ?)', ("Phone", 1))

    # Verify the relationship
    result = cursor.execute(
        """
        SELECT i.title, c.name 
        FROM "test_fk"."items" i 
        JOIN "test_fk"."category" c ON i.category_ref = c.id
    """
    ).fetchall()

    assert len(result) == 1
    assert result[0][0] == "Phone"
    assert result[0][1] == "Electronics"


def test_duckdb_schema_drop_and_recreate(duckdb_db, schema_flat):
    """Test dropping and recreating schema in DuckDB"""
    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema_flat,
        db_schema_name="drop_test",
        root_table_name="test_table",
    )

    # Create tables first time
    translator.create_tables(connection, auto_commit=True)

    # Create again with drop_schema=True
    translator.create_tables(connection, auto_commit=True, drop_schema=True)

    cursor = connection.cursor()

    # Verify table still exists and is empty
    result = cursor.execute('SELECT COUNT(*) FROM "drop_test"."test_table"').fetchone()
    assert result[0] == 0


def test_duckdb_table_comments(duckdb_db):
    """Test that table and column comments work in DuckDB"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "comment": "Test table comment",
        "properties": {
            "id": {"type": "integer", "pk": True},  # Use standard integer with pk flag
            "name": {"type": "string", "comment": "Test column comment"},
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="comment_test",
        root_table_name="commented_table",
    )

    # This should not raise an error even if DuckDB doesn't support comments
    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()

    # Verify table was created successfully
    result = cursor.execute(
        """
        SELECT COUNT(*) 
        FROM information_schema.tables 
        WHERE table_schema = 'comment_test' AND table_name = 'commented_table'
    """
    ).fetchone()

    assert result[0] == 1
