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
Gaussdb source module
"""
import traceback
from collections import namedtuple
from typing import Iterable, Optional, Tuple

from sqlalchemy import String as SqlAlchemyString
from sqlalchemy import sql
from sqlalchemy.dialects.postgresql.base import PGDialect, ischema_names
from sqlalchemy.engine import Inspector

from metadata.generated.schema.api.data.createStoredProcedure import (
    CreateStoredProcedureRequest,
)
from metadata.generated.schema.entity.data.database import Database
from metadata.generated.schema.entity.data.databaseSchema import DatabaseSchema
from metadata.generated.schema.entity.data.storedProcedure import StoredProcedureCode
from metadata.generated.schema.entity.data.table import (
    PartitionColumnDetails,
    PartitionIntervalTypes,
    TablePartition,
    TableType,
)
from metadata.generated.schema.entity.services.connections.database.gaussdbConnection import (
    GaussdbConnection,
)
from metadata.generated.schema.entity.services.ingestionPipelines.status import (
    StackTraceError,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.type.basic import EntityName, FullyQualifiedEntityName
from metadata.ingestion.api.models import Either
from metadata.ingestion.api.steps import InvalidSourceException
from metadata.ingestion.models.ometa_classification import OMetaTagAndClassification
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.database.column_type_parser import create_sqlalchemy_type
from metadata.ingestion.source.database.common_db_source import (
    CommonDbSourceService,
    TableNameAndType,
)
from metadata.ingestion.source.database.mssql.models import STORED_PROC_LANGUAGE_MAP
from metadata.ingestion.source.database.multi_db_source import MultiDBSource
from metadata.ingestion.source.database.gaussdb.models import GaussdbStoredProcedure
from metadata.ingestion.source.database.gaussdb.queries import (
    GAUSSDB_GET_ALL_TABLE_PG_POLICY,
    GAUSSDB_GET_DB_NAMES,
    GAUSSDB_GET_STORED_PROCEDURES,
    GAUSSDB_GET_TABLE_NAMES,
    GAUSSDB_PARTITION_DETAILS,
    GAUSSDB_SCHEMA_COMMENTS,
)
from metadata.ingestion.source.database.gaussdb.utils import (
    get_column_info,
    get_columns,
    get_column_args,
    get_column_default,
    get_etable_owner,
    get_foreign_keys,
    get_json_fields_and_type,
    get_table_comment,
    get_table_owner,
    get_view_definition,
    get_gaussdb_version,
    get_gaussdb_time_column_name
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_database
from metadata.utils.importer import import_side_effects
from metadata.utils.logger import ingestion_logger
from metadata.utils.sqlalchemy_utils import (
    get_all_table_comments,
    get_all_table_ddls,
    get_all_table_owners,
    get_all_view_definitions,
    get_schema_descriptions,
    get_table_ddl,
)
from metadata.utils.tag_utils import get_ometa_tag_and_classification

import_side_effects(
    "metadata.ingestion.source.database.gaussdb.converter_orm",
    "metadata.ingestion.source.database.gaussdb.metrics",
)

TableKey = namedtuple("TableKey", ["schema", "table_name"])

logger = ingestion_logger()


INTERVAL_TYPE_MAP = {
    "list": PartitionIntervalTypes.COLUMN_VALUE,
    "hash": PartitionIntervalTypes.COLUMN_VALUE,
    "range": PartitionIntervalTypes.TIME_UNIT,
}

RELKIND_MAP = {
    "r": TableType.Regular,
    "p": TableType.Partitioned,
    "f": TableType.Foreign,
}

GEOMETRY = create_sqlalchemy_type("GEOMETRY")
POINT = create_sqlalchemy_type("POINT")
POLYGON = create_sqlalchemy_type("POLYGON")

ischema_names.update(
    {
        "geometry": GEOMETRY,
        "point": POINT,
        "polygon": POLYGON,
        "box": create_sqlalchemy_type("BOX"),
        "bpchar": SqlAlchemyString,
        "circle": create_sqlalchemy_type("CIRCLE"),
        "line": create_sqlalchemy_type("LINE"),
        "lseg": create_sqlalchemy_type("LSEG"),
        "path": create_sqlalchemy_type("PATH"),
        "pg_lsn": create_sqlalchemy_type("PG_LSN"),
        "pg_snapshot": create_sqlalchemy_type("PG_SNAPSHOT"),
        "tsquery": create_sqlalchemy_type("TSQUERY"),
        "txid_snapshot": create_sqlalchemy_type("TXID_SNAPSHOT"),
        "xid": SqlAlchemyString,
        "xml": create_sqlalchemy_type("XML"),
    }
)


PGDialect.get_all_table_comments = get_all_table_comments
PGDialect.get_table_comment = get_table_comment
PGDialect._get_column_info = get_column_info  # pylint: disable=protected-access
PGDialect.get_view_definition = get_view_definition
PGDialect.get_columns = get_columns
PGDialect.get_all_view_definitions = get_all_view_definitions

PGDialect.get_all_table_owners = get_all_table_owners
PGDialect.get_table_owner = get_table_owner
PGDialect.ischema_names = ischema_names
PGDialect._get_server_version_info = get_gaussdb_version
PGDialect._get_time_column_name = get_gaussdb_time_column_name
PGDialect.get_column_args = get_column_args
PGDialect.get_column_default = get_column_default

Inspector.get_all_table_ddls = get_all_table_ddls
Inspector.get_table_ddl = get_table_ddl
Inspector.get_table_owner = get_etable_owner
Inspector.get_json_fields_and_type = get_json_fields_and_type

PGDialect.get_foreign_keys = get_foreign_keys


class GaussdbSource(CommonDbSourceService, MultiDBSource):
    """
    Implements the necessary methods to extract
    Database metadata from Gaussdb Source
    """

    def __init__(self, config: WorkflowSource, metadata: OpenMetadata):
        super().__init__(config, metadata)
        self.schema_desc_map = {}

    @classmethod
    def create(
        cls, config_dict, metadata: OpenMetadata, pipeline_name: Optional[str] = None
    ):
        config: WorkflowSource = WorkflowSource.model_validate(config_dict)
        connection: GaussdbConnection = config.serviceConnection.root.config
        if not isinstance(connection, GaussdbConnection):
            raise InvalidSourceException(
                f"Expected GaussdbConnection, but got {connection}"
            )
        return cls(config, metadata)

    def get_schema_description(self, schema_name: str) -> Optional[str]:
        """
        Method to fetch the schema description
        """
        return self.schema_desc_map.get(schema_name)

    def set_schema_description_map(self) -> None:
        self.schema_desc_map = get_schema_descriptions(
            self.engine, GAUSSDB_SCHEMA_COMMENTS
        )

    def query_table_names_and_types(
        self, schema_name: str
    ) -> Iterable[TableNameAndType]:
        """
        Overwrite the inspector implementation to handle partitioned
        and foreign types
        """
        result = self.connection.execute(
            sql.text(GAUSSDB_GET_TABLE_NAMES),
            {"schema": schema_name},
        )

        return [
            TableNameAndType(
                name=name, type_=RELKIND_MAP.get(relkind, TableType.Regular)
            )
            for name, relkind in result
        ]

    def get_configured_database(self) -> Optional[str]:
        if not self.service_connection.ingestAllDatabases:
            return self.service_connection.database
        return None

    def get_database_names_raw(self) -> Iterable[str]:
        yield from self._execute_database_query(GAUSSDB_GET_DB_NAMES)

    def get_database_names(self) -> Iterable[str]:
        if not self.config.serviceConnection.root.config.ingestAllDatabases:
            configured_db = self.config.serviceConnection.root.config.database
            self.set_inspector(database_name=configured_db)
            self.set_schema_description_map()
            yield configured_db
        else:
            for new_database in self.get_database_names_raw():
                database_fqn = fqn.build(
                    self.metadata,
                    entity_type=Database,
                    service_name=self.context.get().database_service,
                    database_name=new_database,
                )

                if filter_by_database(
                    self.source_config.databaseFilterPattern,
                    (
                        database_fqn
                        if self.source_config.useFqnForFiltering
                        else new_database
                    ),
                ):
                    self.status.filter(database_fqn, "Database Filtered Out")
                    continue

                try:
                    self.set_inspector(database_name=new_database)
                    self.set_schema_description_map()
                    yield new_database
                except Exception as exc:
                    logger.debug(traceback.format_exc())
                    logger.error(
                        f"Error trying to connect to database {new_database}: {exc}"
                    )

    def get_table_partition_details(
        self, table_name: str, schema_name: str, inspector
    ) -> Tuple[bool, TablePartition]:
        result = self.engine.execute(
            GAUSSDB_PARTITION_DETAILS, table_name=table_name, schema_name=schema_name
        ).all()

        if result:
            partition_details = TablePartition(
                columns=[
                    PartitionColumnDetails(
                        columnName=row.column_name,
                        intervalType=INTERVAL_TYPE_MAP.get(
                            row.partition_strategy, PartitionIntervalTypes.COLUMN_VALUE
                        ),
                        interval=None,
                    )
                    for row in result
                    if row.column_name
                ]
            )
            return True, partition_details
        return False, None

    def yield_tag(
        self, schema_name: str
    ) -> Iterable[Either[OMetaTagAndClassification]]:
        """
        Fetch Tags
        """
        try:
            result = self.engine.execute(
                GAUSSDB_GET_ALL_TABLE_PG_POLICY.format(
                    database_name=self.context.get().database,
                    schema_name=schema_name,
                )
            ).all()
            for res in result:
                row = list(res)
                fqn_elements = [name for name in row[2:] if name]
                yield from get_ometa_tag_and_classification(
                    tag_fqn=FullyQualifiedEntityName(
                        fqn._build(  # pylint: disable=protected-access
                            self.context.get().database_service, *fqn_elements
                        )
                    ),
                    tags=[row[1]],
                    classification_name=self.service_connection.classificationName,
                    tag_description="Gaussdb Tag Value",
                    classification_description="Gaussdb Tag Name",
                )

        except Exception as exc:
            yield Either(
                left=StackTraceError(
                    name="Tags and Classification",
                    error=f"Skipping Policy Tag: {exc}",
                    stackTrace=traceback.format_exc(),
                )
            )

    def get_stored_procedures(self) -> Iterable[GaussdbStoredProcedure]:
        """List stored procedures"""
        if self.source_config.includeStoredProcedures:
            results = self.engine.execute(GAUSSDB_GET_STORED_PROCEDURES).all()
            for row in results:
                try:
                    stored_procedure = GaussdbStoredProcedure.model_validate(
                        dict(row._mapping)
                    )
                    yield stored_procedure
                except Exception as exc:
                    logger.error()
                    self.status.failed(
                        error=StackTraceError(
                            name=dict(row).get("name", "UNKNOWN"),
                            error=f"Error parsing Stored Procedure payload: {exc}",
                            stackTrace=traceback.format_exc(),
                        )
                    )

    def yield_stored_procedure(
        self, stored_procedure
    ) -> Iterable[Either[CreateStoredProcedureRequest]]:
        """Prepare the stored procedure payload"""
        try:
            stored_procedure_request = CreateStoredProcedureRequest(
                name=EntityName(stored_procedure.name),
                description=None,
                storedProcedureCode=StoredProcedureCode(
                    language=STORED_PROC_LANGUAGE_MAP.get(stored_procedure.language),
                    code=stored_procedure.definition,
                ),
                databaseSchema=fqn.build(
                    metadata=self.metadata,
                    entity_type=DatabaseSchema,
                    service_name=self.context.get().database_service,
                    database_name=self.context.get().database,
                    schema_name=self.context.get().database_schema,
                ),
            )
            yield Either(right=stored_procedure_request)
            self.register_record_stored_proc_request(stored_procedure_request)

        except Exception as exc:
            yield Either(
                left=StackTraceError(
                    name=stored_procedure.name,
                    error=f"Error yielding Stored Procedure [{stored_procedure.name}] due to [{exc}]",
                    stackTrace=traceback.format_exc(),
                )
            )