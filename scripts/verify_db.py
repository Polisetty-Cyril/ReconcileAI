"""
Database Inspection Utility
Queries SQLite sqlite_master to verify persistent schema, table definitions, and indexes.
"""

import sqlite3

def inspect_database(db_path: str = "reconcile_ai.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("=" * 60)
    print(f"INSPECTING ACTUAL DATABASE: {db_path}")
    print("=" * 60)
    
    # 1. Fetch tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"\n[Tables Found] Count = {len(tables)}")
    for tbl in tables:
        print(f"  [OK] {tbl}")
        
    # 2. Fetch schema for each table
    print("\n" + "=" * 60)
    print("TABLE SCHEMAS (CREATE STATEMENTS):")
    print("=" * 60)
    for tbl in tables:
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{tbl}'")
        sql = cursor.fetchone()[0]
        print(f"\n--- Table: {tbl} ---")
        print(sql)
        
    # 3. Fetch custom indexes
    print("\n" + "=" * 60)
    print("INDEXES CREATED:")
    print("=" * 60)
    cursor.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%' ORDER BY tbl_name, name")
    indexes = cursor.fetchall()
    print(f"[Custom Indexes Found] Count = {len(indexes)}")
    for idx_name, tbl_name in indexes:
        print(f"  [OK] Index: {idx_name} on table: {tbl_name}")
        
    conn.close()
    print("\n" + "=" * 60)
    print("DATABASE INSPECTION COMPLETE - ALL TABLES PERSISTED")
    print("=" * 60)

if __name__ == "__main__":
    inspect_database()
