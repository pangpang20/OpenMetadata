#  Copyright 2021 Collate
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""
SQL Queries used during ingestion
"""

import textwrap

GAUSSDB_SQL_STATEMENT = textwrap.dedent(
    """
      SELECT
        u.usename,
        d.datname database_name,
        s.query query_text,
        s.{time_column_name} duration
      FROM
        dbe_perf.statement s
        JOIN pg_catalog.pg_database d ON s.db_id = d.oid
        JOIN pg_catalog.pg_user u ON s.user_id = u.usesysid
      WHERE
        s.query NOT LIKE '/* {{"app": "OpenMetadata", %%}} */%%' AND
        s.query NOT LIKE '/* {{"app": "dbt", %%}} */%%'
        {filters}
      LIMIT {result_limit}
    """
)

# r = ordinary table, v = view, m = materialized view, c = composite type, f = foreign table, p = partitioned table,
GAUSSDB_GET_TABLE_NAMES = """
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind in ('r', 'p', 'f') AND c.parttype='n'
"""
GAUSSDB_TABLE_OWNERS = """
select schemaname, tablename, tableowner from pg_catalog.pg_tables where schemaname <> 'pg_catalog' order by schemaname,tablename;
"""
GAUSSDB_PARTITION_DETAILS = textwrap.dedent(
    """
    select
        ns.nspname  as schema,
        par.relname as table_name,
        partition_strategy,
        col.column_name
    from
        (select
            parentid partrelid,
            array_length(partkey, 1) partnatts,
            case partstrategy
                when 'l' then 'list'
                when 'h' then 'hash'
                when 'r' then 'range' end as partition_strategy,
            unnest(partkey) column_index
        from
            pg_partition) pt
    join
        pg_class par
    on
        par.oid = pt.partrelid
    join pg_namespace ns
    on  par.relnamespace=ns.oid
    left join
        information_schema.columns col
    on
        col.table_schema = ns.nspname
        and col.table_name = par.relname
        and col.ordinal_position = pt.column_index
    where par.relname=%(table_name)s and  ns.nspname=%(schema_name)s
    """
)

GAUSSDB_GET_ALL_TABLE_PG_POLICY = """
SELECT object_id, polname, table_catalog, table_schema, table_name
FROM information_schema.tables AS it
JOIN (SELECT pc.oid as object_id, pc.relname, pp.*
      FROM pg_rlspolicy AS pp
      JOIN pg_class AS pc ON pp.polrelid = pc.oid
      JOIN pg_namespace as pn ON pc.relnamespace = pn.oid) AS ppr ON it.table_name = ppr.relname
WHERE it.table_schema='{schema_name}' AND it.table_catalog='{database_name}';
"""

GAUSSDB_SCHEMA_COMMENTS = """
    SELECT n.nspname AS schema_name,
            d.description AS comment
    FROM pg_catalog.pg_namespace n
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = n.oid AND d.objsubid = 0;
"""

GAUSSDB_TABLE_COMMENTS = """
    SELECT n.nspname as schema,
            c.relname as table_name,
            pgd.description as table_comment
    FROM pg_catalog.pg_class c
        LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_description pgd ON pgd.objsubid = 0 AND pgd.objoid = c.oid
    WHERE c.relkind in ('r', 'v', 'm', 'f', 'p')
      AND pgd.description IS NOT NULL
      AND n.nspname <> 'pg_catalog'
    ORDER BY "schema", "table_name"
"""

# GAUSSDB views definitions only contains the select query
# hence we are appending "create view <schema>.<table> as " to select query
# to generate the column level lineage
GAUSSDB_VIEW_DEFINITIONS = """
SELECT
	n.nspname "schema",
	c.relname view_name,
	'create view ' || n.nspname || '.' || c.relname || ' as ' || pg_get_viewdef(c.oid,true) view_def
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('v', 'm')
AND n.nspname not in ('pg_catalog','information_schema')
"""

GAUSSDB_GET_DATABASE = """
select datname from pg_catalog.pg_database
"""

GAUSSDB_TEST_GET_TAGS = """
SELECT object_id, polname, table_catalog , table_schema ,table_name
FROM information_schema.tables AS it
JOIN (SELECT pc.oid as object_id, pc.relname, pp.*
      FROM pg_rlspolicy AS pp
      JOIN pg_class AS pc ON pp.polrelid = pc.oid
      JOIN pg_namespace as pn ON pc.relnamespace = pn.oid) AS ppr ON it.table_name = ppr.relname
      LIMIT 1
"""

GAUSSDB_TEST_GET_QUERIES = """
      SELECT
        u.usename,
        d.datname database_name,
        s.query query_text,
        s.{time_column_name} duration
      FROM
        dbe_perf.statement s
        JOIN pg_catalog.pg_database d ON s.db_id = d.oid
        JOIN pg_catalog.pg_user u ON s.user_id = u.usesysid
        LIMIT 1
    """


GAUSSDB_GET_DB_NAMES = """
select datname from pg_catalog.pg_database
"""

GAUSSDB_SQL_COLUMNS = """
        SELECT a.attname,
            pg_catalog.format_type(a.atttypid, a.atttypmod),
            (
            SELECT pg_catalog.pg_get_expr(d.adbin, d.adrelid)
            FROM pg_catalog.pg_attrdef d
            WHERE d.adrelid = a.attrelid AND d.adnum = a.attnum
            AND a.atthasdef
            ) AS DEFAULT,
            a.attnotnull,
            a.attrelid as table_oid,
            pgd.description as comment,
            {generated},
            {identity}
        FROM pg_catalog.pg_attribute a
        LEFT JOIN pg_catalog.pg_description pgd ON (
            pgd.objoid = a.attrelid AND pgd.objsubid = a.attnum)
        WHERE a.attrelid = :table_oid
        AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """

GAUSSDB_GET_SERVER_VERSION = """
select version()
"""

GAUSSDB_FETCH_FK = """
    SELECT r.conname,
        pg_catalog.pg_get_constraintdef(r.oid, true) as condef,
        n.nspname as conschema,
        d.datname AS con_db_name
    FROM  pg_catalog.pg_constraint r,
        pg_namespace n,
        pg_class c
    JOIN pg_database d ON d.datname = current_database()
    WHERE r.conrelid = :table AND
        r.contype = 'f' AND
        c.oid = confrelid AND
        n.oid = c.relnamespace
    ORDER BY 1
"""

GAUSSDB_GET_JSON_FIELDS = """
    WITH RECURSIVE json_hierarchy AS (
        SELECT
            key AS path,
            json_typeof(value) AS type,
            value,
            json_build_object() AS properties,
            key AS title
        FROM
            {table_name} tbd,
            LATERAL json_each({column_name}::json)
    ),
    build_hierarchy AS (
        SELECT
            path,
            type,
            title,
            CASE
                WHEN type = 'object' THEN
                    json_build_object(
                        'title', title,
                        'type', 'object',
                        'properties', (
                            SELECT json_object_agg(
                                key,
                                json_build_object(
                                    'title', key,
                                    'type', json_typeof(value),
                                    'properties', (
                                        CASE
                                            WHEN json_typeof(value) = 'object' THEN
                                                (
                                                    SELECT json_object_agg(
                                                        key,
                                                        json_build_object(
                                                            'title', key,
                                                            'type', json_typeof(value),
                                                            'properties',
                                                            json_build_object()
                                                        )
                                                    )
                                                    FROM json_each(value::json) AS sub_key_value
                                                )
                                            ELSE json_build_object()
                                        END
                                    )
                                )
                            )
                            FROM json_each(value::json) AS key_value
                        )
                    )
                WHEN type = 'array' THEN
                    json_build_object(
                        'title', title,
                        'type', 'array',
                        'properties', json_build_object()
                    )
                ELSE
                    json_build_object(
                        'title', title,
                        'type', type
                    )
            END AS hierarchy
        FROM
            json_hierarchy
    ),
    aggregate_hierarchy AS (
        select
            json_build_object(
            'title','{column_name}',
            'type','object',
            'properties',
            json_object_agg(
                path,
                hierarchy
            )) AS result
        FROM
            build_hierarchy
    )
    SELECT
        result
    FROM
        aggregate_hierarchy;
"""

GAUSSDB_GET_STORED_PROCEDURES = """
    SELECT proname AS procedure_name,
        nspname AS schema_name,
        proargtypes AS argument_types,
        prorettype::regtype AS return_type,
        prosrc AS definition
    FROM pg_proc
    JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid
    WHERE prokind = 'p';
"""
