package org.openmetadata.service.jdbi3;

import java.util.Map;
import java.util.UUID;
import org.openmetadata.schema.governance.workflows.WorkflowInstance;
import org.openmetadata.service.Entity;
import org.openmetadata.service.resources.governance.WorkflowInstanceResource;
import org.openmetadata.service.util.JsonUtils;

public class WorkflowInstanceRepository extends EntityTimeSeriesRepository<WorkflowInstance> {
  public WorkflowInstanceRepository() {
    super(
        WorkflowInstanceResource.COLLECTION_PATH,
        Entity.getCollectionDAO().workflowInstanceTimeSeriesDAO(),
        WorkflowInstance.class,
        Entity.WORKFLOW_INSTANCE);
  }

  public WorkflowInstance createNewRecord(WorkflowInstance recordEntity, String recordFQN) {
    storeInternal(recordEntity, recordFQN);
    storeRelationshipInternal(recordEntity);
    return recordEntity;
  }

  public void addNewWorkflowInstance(
      String workflowDefinitionName,
      UUID workflowInstanceId,
      Long startedAt,
      Map<String, Object> variables) {
    WorkflowDefinitionRepository workflowDefinitionRepository =
        (WorkflowDefinitionRepository) Entity.getEntityRepository(Entity.WORKFLOW_DEFINITION);
    UUID workflowDefinitionId = workflowDefinitionRepository.getIdFromName(workflowDefinitionName);

    createNewRecord(
        new WorkflowInstance()
            .withId(workflowInstanceId)
            .withWorkflowDefinitionId(workflowDefinitionId)
            .withStartedAt(startedAt)
            .withVariables(variables)
            .withTimestamp(System.currentTimeMillis()),
        workflowDefinitionName);
  }

  public void updateWorkflowInstance(UUID workflowInstanceId, Long endedAt) {
    WorkflowInstance workflowInstance =
        JsonUtils.readValue(timeSeriesDao.getById(workflowInstanceId), WorkflowInstance.class);

    workflowInstance.setEndedAt(endedAt);

    getTimeSeriesDao().update(JsonUtils.pojoToJson(workflowInstance), workflowInstanceId);
  }
}
