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
Gaussdb Query parser module
"""
import traceback
from abc import ABC
from typing import Iterable, Optional

from sqlalchemy.engine.base import Engine

from metadata.generated.schema.entity.services.connections.database.gaussdbConnection import (
    GaussdbConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.type.tableQuery import TableQuery
from metadata.ingestion.api.steps import InvalidSourceException
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_connection
from metadata.ingestion.source.database.gaussdb.queries import GAUSSDB_GET_DATABASE
from metadata.ingestion.source.database.gaussdb.utils import (
    get_gaussdb_time_column_name,
)
from metadata.ingestion.source.database.query_parser_source import QueryParserSource
from metadata.utils.helpers import get_start_and_end
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class GaussdbQueryParserSource(QueryParserSource, ABC):
    """
    Gaussdb base for Usage and Lineage
    """

    filters: str

    def __init__(self, config: WorkflowSource, metadata: OpenMetadata):
        super().__init__(config, metadata)
        duration = min(self.source_config.queryLogDuration, 6)
        self.start, self.end = get_start_and_end(duration)

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

    def get_sql_statement(self, *_) -> str:
        """
        returns sql statement to fetch query logs.
        We don't use any start or end times as they are not available
        """
        return self.sql_stmt.format(
            result_limit=self.config.sourceConfig.config.resultLimit,
            filters=self.get_filters(),
            time_column_name=get_gaussdb_time_column_name(engine=self.engine),
        )

    # pylint: disable=no-member
    def get_table_query(self) -> Iterable[TableQuery]:
        try:
            if self.config.sourceConfig.config.queryLogFilePath:
                yield from super().yield_table_queries_from_logs()
            else:
                database = self.config.serviceConnection.root.config.database
                if database:
                    self.engine: Engine = get_connection(self.service_connection)
                    yield from self.process_table_query()
                else:
                    results = self.engine.execute(GAUSSDB_GET_DATABASE)
                    for res in results:
                        row = list(res)
                        logger.info(f"Ingesting from database: {row[0]}")
                        self.config.serviceConnection.root.config.database = row[0]
                        self.engine = get_connection(self.service_connection)
                        yield from self.process_table_query()

        except Exception as err:
            logger.error(f"Source usage processing error - {err}")
            logger.debug(traceback.format_exc())

    @staticmethod
    def get_database_name(data: dict) -> str:
        """
        Method to get database name
        """
        return data.get("database_name")
