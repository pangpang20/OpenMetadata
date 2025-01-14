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
Gaussdb lineage module
"""
import traceback
from datetime import datetime
from typing import Iterable

from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.services.connections.database.gaussdbConnection import (
    GaussdbScheme,
)
from metadata.generated.schema.type.basic import DateTime
from metadata.generated.schema.type.tableQuery import TableQuery
from metadata.ingestion.api.models import Either
from metadata.ingestion.source.connections import get_connection
from metadata.ingestion.source.database.lineage_source import LineageSource
from metadata.ingestion.source.database.gaussdb.pgspider.lineage import (
    get_lineage_from_multi_tenant_table,
)
from metadata.ingestion.source.database.gaussdb.queries import GAUSSDB_SQL_STATEMENT
from metadata.ingestion.source.database.gaussdb.query_parser import (
    GaussdbQueryParserSource,
)
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class GaussdbLineageSource(GaussdbQueryParserSource, LineageSource):
    """
    Implements the necessary methods to extract
    Database lineage from Gaussdb Source
    """

    sql_stmt = GAUSSDB_SQL_STATEMENT

    filters = """
                AND (
                    s.query ILIKE '%%create table%%as%%select%%'
                    OR s.query ILIKE '%%insert%%into%%select%%'
                    OR s.query ILIKE '%%update%%'
                    OR s.query ILIKE '%%merge%%'
                )
            """

    def _iter(self, *_, **__) -> Iterable[Either[AddLineageRequest]]:
        """
        Based on the query logs, prepare the lineage
        and send it to the sink
        """

        yield from super()._iter()

        if self.service_connection.scheme == GaussdbScheme.pgspider_psycopg2:
            lineages = get_lineage_from_multi_tenant_table(
                self.metadata,
                connection=self.service_connection,
                service_name=self.config.serviceName,
            )

            for lineage_request in lineages or []:
                yield lineage_request

    def process_table_query(self) -> Iterable[TableQuery]:
        """
        Process Query
        """
        try:
            with get_connection(self.service_connection).connect() as conn:
                rows = conn.execute(self.get_sql_statement())
                for row in rows:
                    row = dict(row)
                    try:
                        yield TableQuery(
                            dialect=self.dialect.value,
                            query=row["query_text"],
                            userName=row["usename"],
                            analysisDate=DateTime(datetime.now()),
                            aborted=self.get_aborted_status(row),
                            databaseName=self.get_database_name(row),
                            serviceName=self.config.serviceName,
                            databaseSchema=self.get_schema_name(row),
                            duration=row.get("duration"),
                        )
                    except Exception as err:
                        logger.debug(traceback.format_exc())
                        logger.error(str(err))
        except Exception as err:
            logger.error(f"Source usage processing error - {err}")
            logger.debug(traceback.format_exc())