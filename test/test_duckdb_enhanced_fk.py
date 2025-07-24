"""Enhanced FK constraint test for DuckDB"""

import pytest
import duckdb
from jsonschema2ddl import JSONSchemaToDuckDB


def test_duckdb_foreign_key_constraint_enforcement(duckdb_db):
    """Test that DuckDB FK constraints are properly enforced"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "category": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "pk": True},
                    "name": {"type": "string"}
                }
            }
        },
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},
            "title": {"type": "string"},
            "category_ref": {"$ref": "#/definitions/category"}
        }
    }
    
    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="fk_enforcement",
        root_table_name="items",
    )

    translator.create_tables(connection, auto_commit=True)
    translator.create_links(connection)
    
    cursor = connection.cursor()
    
    # Insert valid parent record
    cursor.execute('INSERT INTO "fk_enforcement"."category" (name) VALUES (?)', ("Electronics",))
    
    # Insert valid child record with FK
    cursor.execute('INSERT INTO "fk_enforcement"."items" (title, category_ref) VALUES (?, ?)', ("Phone", 1))
    
    # Verify the valid relationship works
    result = cursor.execute("""
        SELECT i.title, c.name 
        FROM "fk_enforcement"."items" i 
        JOIN "fk_enforcement"."category" c ON i.category_ref = c.id
    """).fetchall()
    
    assert len(result) == 1
    assert result[0][0] == "Phone"
    assert result[0][1] == "Electronics"
    
    # Test FK constraint enforcement - this should fail
    with pytest.raises(Exception) as exc_info:
        cursor.execute('INSERT INTO "fk_enforcement"."items" (title, category_ref) VALUES (?, ?)', ("Invalid Item", 999))
    
    # Verify it's a foreign key constraint error
    assert "foreign key constraint" in str(exc_info.value).lower() or "violates" in str(exc_info.value).lower()


def test_duckdb_complex_fk_relationships(duckdb_db):
    """Test multiple FK relationships in the same table"""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "user": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "pk": True},
                    "name": {"type": "string"}
                }
            },
            "category": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "pk": True},
                    "name": {"type": "string"}
                }
            }
        },
        "type": "object",
        "properties": {
            "id": {"type": "integer", "pk": True},
            "title": {"type": "string"},
            "owner_ref": {"$ref": "#/definitions/user"},
            "category_ref": {"$ref": "#/definitions/category"}
        }
    }
    
    connection = duckdb_db["connection"]
    translator = JSONSchemaToDuckDB(
        schema,
        db_schema_name="complex_fk",
        root_table_name="items",
    )

    translator.create_tables(connection, auto_commit=True)
    translator.create_links(connection)
    
    cursor = connection.cursor()
    
    # Insert parent records
    cursor.execute('INSERT INTO "complex_fk"."user" (name) VALUES (?)', ("Alice",))
    cursor.execute('INSERT INTO "complex_fk"."category" (name) VALUES (?)', ("Electronics",))
    
    # Insert child record with multiple FKs
    cursor.execute(
        'INSERT INTO "complex_fk"."items" (title, owner_ref, category_ref) VALUES (?, ?, ?)', 
        ("Phone", 1, 1)
    )
    
    # Verify complex join works
    result = cursor.execute("""
        SELECT i.title, u.name as owner, c.name as category
        FROM "complex_fk"."items" i 
        JOIN "complex_fk"."user" u ON i.owner_ref = u.id
        JOIN "complex_fk"."category" c ON i.category_ref = c.id
    """).fetchall()
    
    assert len(result) == 1
    assert result[0][0] == "Phone"
    assert result[0][1] == "Alice"
    assert result[0][2] == "Electronics"
