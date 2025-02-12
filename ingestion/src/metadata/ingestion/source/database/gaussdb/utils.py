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
#  pylint: disable=protected-access

"""
Gaussdb SQLAlchemy util methods
"""
import json
import re
import traceback
from typing import Dict, Optional, Tuple

from packaging import version
from sqlalchemy import sql, util
from sqlalchemy.dialects.postgresql.base import ENUM
from sqlalchemy.engine import reflection
from sqlalchemy.sql import sqltypes

from metadata.generated.schema.entity.data.table import Column
from metadata.ingestion.source.database.gaussdb.queries import (
    GAUSSDB_FETCH_FK,
    GAUSSDB_GET_JSON_FIELDS,
    GAUSSDB_GET_SERVER_VERSION,
    GAUSSDB_SQL_COLUMNS,
    GAUSSDB_TABLE_COMMENTS,
    GAUSSDB_TABLE_OWNERS,
    GAUSSDB_VIEW_DEFINITIONS,
)
from metadata.parsers.json_schema_parser import parse_json_schema
from metadata.utils.logger import utils_logger
from metadata.utils.sqlalchemy_utils import (
    get_table_comment_wrapper,
    get_table_owner_wrapper,
    get_view_definition_wrapper,
)

logger = utils_logger()

OLD_GAUSSDB_VERSION = "2.0"


def get_etable_owner(
    self, connection, table_name=None, schema=None
):  # pylint: disable=unused-argument
    """Return all owners.

    :param schema: Optional, retrieve names from a non-default schema.
        For special quoting, use :class:`.quoted_name`.

    """

    with self._operation_context() as conn:
        return self.dialect.get_table_owner(
            connection=conn,
            query=GAUSSDB_TABLE_OWNERS,
            table_name=table_name,
            schema=schema,
        )


@reflection.cache
def get_foreign_keys(
    self, connection, table_name, schema=None, gaussdb_ignore_search_path=False, **kw
):
    preparer = self.identifier_preparer
    table_oid = self.get_table_oid(
        connection, table_name, schema, info_cache=kw.get("info_cache")
    )

    FK_REGEX = re.compile(
        r"FOREIGN KEY \((.*?)\) REFERENCES (?:(.*?)\.)?(.*?)\((.*?)\)"
        r"[\s]?(MATCH (FULL|PARTIAL|SIMPLE)+)?"
        r"[\s]?(ON UPDATE "
        r"(CASCADE|RESTRICT|NO ACTION|SET NULL|SET DEFAULT)+)?"
        r"[\s]?(ON DELETE "
        r"(CASCADE|RESTRICT|NO ACTION|SET NULL|SET DEFAULT)+)?"
        r"[\s]?(DEFERRABLE|NOT DEFERRABLE)?"
        r"[\s]?(INITIALLY (DEFERRED|IMMEDIATE)+)?"
    )

    t = sql.text(GAUSSDB_FETCH_FK).columns(
        conname=sqltypes.Unicode, condef=sqltypes.Unicode, con_db_name=sqltypes.Unicode
    )
    c = connection.execute(t, dict(table=table_oid))
    fkeys = []
    for conname, condef, conschema, con_db_name in c.fetchall():
        m = re.search(FK_REGEX, condef).groups()

        (
            constrained_columns,
            referred_schema,
            referred_table,
            referred_columns,
            _,
            match,
            _,
            onupdate,
            _,
            ondelete,
            deferrable,
            _,
            initially,
        ) = m

        if deferrable is not None:
            deferrable = True if deferrable == "DEFERRABLE" else False
        constrained_columns = tuple(re.split(r"\s*,\s*", constrained_columns))
        constrained_columns = [
            preparer._unquote_identifier(x) for x in constrained_columns
        ]

        if gaussdb_ignore_search_path:
            # when ignoring search path, we use the actual schema
            # provided it isn't the "default" schema
            if conschema != self.default_schema_name:
                referred_schema = conschema
            else:
                referred_schema = schema
        elif referred_schema:
            # referred_schema is the schema that we regexp'ed from
            # pg_get_constraintdef().  If the schema is in the search
            # path, pg_get_constraintdef() will give us None.
            referred_schema = preparer._unquote_identifier(referred_schema)
        elif schema is not None and schema == conschema:
            # If the actual schema matches the schema of the table
            # we're reflecting, then we will use that.
            referred_schema = schema

        referred_table = preparer._unquote_identifier(referred_table)
        referred_columns = tuple(re.split(r"\s*,\s", referred_columns))
        referred_columns = [preparer._unquote_identifier(x) for x in referred_columns]
        options = {
            k: v
            for k, v in [
                ("onupdate", onupdate),
                ("ondelete", ondelete),
                ("initially", initially),
                ("deferrable", deferrable),
                ("match", match),
            ]
            if v is not None and v != "NO ACTION"
        }
        referred_database = con_db_name if con_db_name else ""
        fkey_d = {
            "name": conname,
            "constrained_columns": constrained_columns,
            "referred_schema": referred_schema,
            "referred_table": referred_table,
            "referred_columns": referred_columns,
            "options": options,
            "referred_database": referred_database,
        }
        fkeys.append(fkey_d)
    return fkeys


@reflection.cache
def get_table_owner(
    self, connection, table_name, schema=None, **kw
):  # pylint: disable=unused-argument
    return get_table_owner_wrapper(
        self,
        connection=connection,
        query=GAUSSDB_TABLE_OWNERS,
        table_name=table_name,
        schema=schema,
    )


@reflection.cache
def get_table_comment(
    self, connection, table_name, schema=None, **kw
):  # pylint: disable=unused-argument
    return get_table_comment_wrapper(
        self,
        connection,
        table_name=table_name,
        schema=schema,
        query=GAUSSDB_TABLE_COMMENTS,
    )


@reflection.cache
def get_json_fields_and_type(
    self, table_name, column_name, schema=None, **kw
):  # pylint: disable=unused-argument
    try:
        query = GAUSSDB_GET_JSON_FIELDS.format(
            table_name=table_name, column_name=column_name
        )
        cursor = self.engine.execute(query)
        result = cursor.fetchone()
        if result:
            parsed_column = parse_json_schema(json.dumps(result[0]), Column)
            if parsed_column:
                return parsed_column[0].children
    except Exception as err:
        logger.warning(
            f"Unable to parse the json fields for {table_name}.{column_name} - {err}"
        )
        logger.debug(traceback.format_exc())
    return None


@reflection.cache
def get_columns(  # pylint: disable=too-many-locals
    self, connection, table_name, schema=None, **kw
):
    """
    Overriding the dialect method to add raw_data_type in response
    """

    table_oid = self.get_table_oid(
        connection, table_name, schema, info_cache=kw.get("info_cache")
    )
    generated = (
        "NULL as generated"
    )
    identity = "NULL as identity_options"

    sql_col_query = GAUSSDB_SQL_COLUMNS.format(
        generated=generated,
        identity=identity,
    )
    sql_col_query = (
        sql.text(sql_col_query)
        .bindparams(sql.bindparam("table_oid", type_=sqltypes.Integer))
        .columns(attname=sqltypes.Unicode, default=sqltypes.Unicode)
    )
    conn = connection.execute(sql_col_query, {"table_oid": table_oid})
    rows = conn.fetchall()

    # dictionary with (name, ) if default search path or (schema, name)
    # as keys
    domains = self._load_domains(connection)

    # dictionary with (name, ) if default search path or (schema, name)
    # as keys
    enums = dict(
        ((rec["name"],), rec) if rec["visible"] else ((rec["schema"], rec["name"]), rec)
        for rec in self._load_enums(connection, schema="*")
    )

    # format columns
    columns = []

    for (
        name,
        format_type,
        default_,
        notnull,
        table_oid,
        comment,
        generated,
        identity,
    ) in rows:
        column_info = self._get_column_info(
            name,
            format_type,
            default_,
            notnull,
            domains,
            enums,
            schema,
            comment,
            generated,
            identity,
        )
        column_info["system_data_type"] = format_type
        columns.append(column_info)
    return columns


def _get_numeric_args(charlen):
    if charlen:
        prec, scale = charlen.split(",")
        return (int(prec), int(scale))
    return ()


def _get_interval_args(charlen, attype, kwargs: Dict):
    field_match = re.match(r"interval (.+)", attype, re.I)
    if charlen:
        kwargs["precision"] = int(charlen)
    if field_match:
        kwargs["fields"] = field_match.group(1)
    attype = "interval"
    return (), attype, kwargs


def _get_bit_var_args(charlen, kwargs):
    kwargs["varying"] = True
    if charlen:
        return (int(charlen),), kwargs

    return (), kwargs


def get_column_args(
    charlen: str, args: Tuple, kwargs: Dict, attype: str
) -> Tuple[Tuple, Dict]:
    """
    Method to determine the args and kwargs
    """
    if attype == "numeric":
        args = _get_numeric_args(charlen)
    elif attype == "double precision":
        args = (53,)
    elif attype == "integer":
        args = ()
    elif attype in ("timestamp with time zone", "time with time zone"):
        kwargs["timezone"] = True
        if charlen:
            kwargs["precision"] = int(charlen)
        args = ()
    elif attype in (
        "timestamp without time zone",
        "time without time zone",
        "time",
    ):
        kwargs["timezone"] = False
        if charlen:
            kwargs["precision"] = int(charlen)
        args = ()
    elif attype == "bit varying":
        args, kwargs = _get_bit_var_args(charlen, kwargs)
    elif attype == "geometry":
        args = ()
    elif attype.startswith("interval"):
        args, attype, kwargs = _get_interval_args(charlen, attype, kwargs)
    elif charlen:
        args = (int(charlen),)

    return args, kwargs, attype


def get_column_default(coltype, schema, default, generated):
    """
    Method to determine the default of column
    """
    autoincrement = False
    # If a zero byte or blank string depending on driver (is also absent
    # for older PG versions), then not a generated column. Otherwise, s =
    # stored. (Other values might be added in the future.)
    if generated not in (None, "", b"\x00"):
        computed = {"sqltext": default, "persisted": generated in ("s", b"s")}
        default = None
    else:
        computed = None
    if default is not None:
        match = re.search(r"""(nextval\(')([^']+)('.*$)""", default)
        if match is not None:
            if issubclass(coltype._type_affinity, sqltypes.Integer):
                autoincrement = True
            # the default is related to a Sequence
            sch = schema
            if "." not in match.group(2) and sch is not None:
                # unconditionally quote the schema name.  this could
                # later be enhanced to obey quoting rules /
                # "quote schema"
                default = (
                    match.group(1)
                    + (f'"{sch}"')
                    + "."
                    + match.group(2)
                    + match.group(3)
                )
    return default, autoincrement, computed


def _handle_array_type(attype):
    return (
        # strip '[]' from integer[], etc.
        re.sub(r"\[\]$", "", attype),
        attype.endswith("[]"),
    )


# pylint: disable=too-many-statements,too-many-branches,too-many-locals,too-many-arguments
def get_column_info(
    self,
    name,
    format_type,
    default,
    notnull,
    domains,
    enums,
    schema,
    comment,
    generated,
    identity,
):
    """
    Method to return column info
    """

    if format_type is None:
        no_format_type = True
        attype = format_type = "no format_type()"
        is_array = False
    else:
        no_format_type = False

        # strip (*) from character varying(5), timestamp(5)
        # with time zone, geometry(POLYGON), etc.
        attype = re.sub(r"\(.*\)", "", format_type)

        # strip '[]' from integer[], etc. and check if an array
        attype, is_array = _handle_array_type(attype)

    # strip quotes from case sensitive enum or domain names
    enum_or_domain_key = tuple(util.quoted_token_parser(attype))

    nullable = not notnull

    charlen = re.search(r"\(([\d,]+)\)", format_type)
    if charlen:
        charlen = charlen.group(1)
    args = re.search(r"\((.*)\)", format_type)
    if args and args.group(1):
        args = tuple(re.split(r"\s*,\s*", args.group(1)))
    else:
        args = ()
    kwargs = {}

    args, kwargs, attype = get_column_args(charlen, args, kwargs, attype)

    while True:
        # looping here to suit nested domains
        if attype in self.ischema_names:
            coltype = self.ischema_names[attype]
            break
        if enum_or_domain_key in enums:
            enum = enums[enum_or_domain_key]
            coltype = ENUM
            kwargs["name"] = enum["name"]
            if not enum["visible"]:
                kwargs["schema"] = enum["schema"]
            args = tuple(enum["labels"])
            break
        if enum_or_domain_key in domains:
            domain = domains[enum_or_domain_key]
            attype = domain["attype"]
            attype, is_array = _handle_array_type(attype)
            # strip quotes from case sensitive enum or domain names
            enum_or_domain_key = tuple(util.quoted_token_parser(attype))
            # A table can't override a not null on the domain,
            # but can override nullable
            nullable = nullable and domain["nullable"]
            if domain["default"] and not default:
                # It can, however, override the default
                # value, but can't set it to null.
                default = domain["default"]
            continue
        coltype = None
        break

    if coltype:
        coltype = coltype(*args, **kwargs)
        if is_array:
            coltype = self.ischema_names["_array"](coltype)
    elif no_format_type:
        util.warn(f"GaussdbSQL format_type() returned NULL for column '{name}'")
        coltype = sqltypes.NULLTYPE
    else:
        util.warn(f"Did not recognize type '{attype}' of column '{name}'")
        coltype = sqltypes.NULLTYPE

    default, autoincrement, computed = get_column_default(
        coltype=coltype, schema=schema, default=default, generated=generated
    )
    column_info = {
        "name": name,
        "type": coltype,
        "nullable": nullable,
        "default": default,
        "autoincrement": autoincrement or identity is not None,
        "comment": comment,
    }
    if computed is not None:
        column_info["computed"] = computed
    if identity is not None:
        column_info["identity"] = identity
    return column_info


@reflection.cache
def get_view_definition(
    self, connection, table_name, schema=None, **kw
):  # pylint: disable=unused-argument
    return get_view_definition_wrapper(
        self,
        connection,
        table_name=table_name,
        schema=schema,
        query=GAUSSDB_VIEW_DEFINITIONS,
    )



import re
from typing import Optional

def get_gaussdb_version(self,engine) -> Optional[str]:
    """
    Return the GaussDB version in major.minor.patch format.
    """
    result = engine.execute(GAUSSDB_GET_SERVER_VERSION)
    result_string = result.scalar()
    if not result_string:
        raise ValueError("Query result is empty or None.")
    logger.debug(f"Query result: {result_string}")


    match = re.match(
        r".*(?:PostgreSQL|EnterpriseDB|GaussDB Kernel) "
        r"(\d+)\.?(\d+)?(?:\.(\d+))?(?:\.\d+)?(?:devel|beta)?",
        result_string,
    )
    if not match:
        logger.error(f"Could not match the version string: {result_string}")
        raise AssertionError(
            "Could not determine version from string '%s'" % result_string
        )

    logger.debug(f"Match groups: {match.groups()}")

    version_tuple = tuple([int(x) for x in match.group(1, 2, 3) if x is not None])
    logger.debug(f"Parsed version tuple: {version_tuple}")

    return version_tuple


# def get_gaussdb_version_wrapper(connection):
    # return get_gaussdb_version(None,connection)

def get_gaussdb_time_column_name(engine) -> str:
    """
    Return the correct column name for the time column based on gaussdb version
    """
    time_column_name = "total_elapse_time"
    return time_column_name
