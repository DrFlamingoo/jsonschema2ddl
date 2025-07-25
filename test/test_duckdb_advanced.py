"""Additional comprehensive tests for DuckDB auto-increment edge cases"""

import pytest
import duckdb
from jsonschema2ddl import JSONSchemaToDuckDB


def test_duckdb_multiple_tables_with_sequences(duckdb_db):
    """Test that multiple tables can have their own sequences"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "category": {
                "type": "object",
                "properties": {"id": {"type": "integer", "pk": True}, "name": {"type": "string"}},
            }
        },
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},
            "title": {"type": "string"},
            "category_ref": {"$ref": "#/definitions/category"},
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="multi_seq",
        root_table_name="items",
    )

    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()

    # Check that both sequences were created
    sequences = cursor.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
    sequence_names = [seq[0] for seq in sequences]

    assert "multi_seq_items_seq" in sequence_names
    assert "multi_seq_category_seq" in sequence_names

    # Insert into both tables
    cursor.execute('INSERT INTO "multi_seq"."category" (name) VALUES (?)', ("Electronics",))
    cursor.execute('INSERT INTO "multi_seq"."category" (name) VALUES (?)', ("Books",))

    cursor.execute('INSERT INTO "multi_seq"."items" (title) VALUES (?)', ("Phone",))
    cursor.execute('INSERT INTO "multi_seq"."items" (title) VALUES (?)', ("Laptop",))
    cursor.execute('INSERT INTO "multi_seq"."items" (title) VALUES (?)', ("Novel",))

    # Verify independent auto-increment
    categories = cursor.execute('SELECT id, name FROM "multi_seq"."category" ORDER BY id').fetchall()
    items = cursor.execute('SELECT id, title FROM "multi_seq"."items" ORDER BY id').fetchall()

    assert categories == [(1, "Electronics"), (2, "Books")]
    assert items == [(1, "Phone"), (2, "Laptop"), (3, "Novel")]


def test_duckdb_schema_recreation_with_sequences(duckdb_db):
    """Test that sequences are properly recreated when dropping and recreating schema"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"id": {"type": "integer", "pk": True}, "data": {"type": "string"}},
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="recreation_test",
        root_table_name="test_table",
    )

    # Create first time
    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()
    cursor.execute('INSERT INTO "recreation_test"."test_table" (data) VALUES (?)', ("first",))

    # Recreate with drop_schema=True
    translator.create_tables(connection, auto_commit=True, drop_schema=True)

    # Insert new data - should start from 1 again
    cursor.execute('INSERT INTO "recreation_test"."test_table" (data) VALUES (?)', ("second",))
    cursor.execute('INSERT INTO "recreation_test"."test_table" (data) VALUES (?)', ("third",))

    result = cursor.execute('SELECT id, data FROM "recreation_test"."test_table" ORDER BY id').fetchall()
    assert result == [(1, "second"), (2, "third")]


def test_duckdb_mixed_pk_types(duckdb_db):
    """Test tables with both auto-increment and non-auto-increment primary keys"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "manual_pk_table": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "pk": True},  # String PK, no auto-increment
                    "description": {"type": "string"},
                },
            }
        },
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},  # Auto-increment PK
            "name": {"type": "string"},
            "manual_ref": {"$ref": "#/definitions/manual_pk_table"},
        },
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="mixed_pk",
        root_table_name="auto_table",
    )

    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()

    # Check sequences - only auto_table should have a sequence
    sequences = cursor.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
    sequence_names = [seq[0] for seq in sequences]

    assert "mixed_pk_auto_table_seq" in sequence_names
    # manual_pk_table should not have a sequence since PK is string
    assert not any("manual_pk_table" in seq for seq in sequence_names)

    # Insert data
    cursor.execute(
        'INSERT INTO "mixed_pk"."manual_pk_table" (code, description) VALUES (?, ?)', ("CODE1", "First code")
    )
    cursor.execute(
        'INSERT INTO "mixed_pk"."manual_pk_table" (code, description) VALUES (?, ?)', ("CODE2", "Second code")
    )

    cursor.execute('INSERT INTO "mixed_pk"."auto_table" (name) VALUES (?)', ("Auto 1",))
    cursor.execute('INSERT INTO "mixed_pk"."auto_table" (name) VALUES (?)', ("Auto 2",))

    # Verify results
    manual_result = cursor.execute(
        'SELECT code, description FROM "mixed_pk"."manual_pk_table" ORDER BY code'
    ).fetchall()
    auto_result = cursor.execute('SELECT id, name FROM "mixed_pk"."auto_table" ORDER BY id').fetchall()

    assert manual_result == [("CODE1", "First code"), ("CODE2", "Second code")]
    assert auto_result == [(1, "Auto 1"), (2, "Auto 2")]


def test_duckdb_sequence_naming_with_special_characters(duckdb_db):
    """Test sequence naming when table names have special characters"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"id": {"type": "integer", "pk": True}, "name": {"type": "string"}},
    }

    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="test_special",  # Schema with underscore (valid)
        root_table_name="my-table",  # Table with dash
    )

    translator.create_tables(connection, auto_commit=True)

    cursor = connection.cursor()

    # Check that sequence was created with proper name handling
    sequences = cursor.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
    sequence_names = [seq[0] for seq in sequences]

    # Should handle special characters by replacing them with underscores
    expected_sequence = "test_special_my_table_seq"
    assert expected_sequence in sequence_names

    # Insert data to verify it works
    cursor.execute('INSERT INTO "test_special"."my-table" (name) VALUES (?)', ("test",))
    result = cursor.execute('SELECT id, name FROM "test_special"."my-table"').fetchone()

    assert result == (1, "test")
