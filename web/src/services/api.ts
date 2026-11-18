import { getConfig } from './generated/config';
import {
  createCustomerSync,
  getCustomerSync,
  listCustomerGroups,
  listCustomerProfiles,
  listCustomerSyncs,
} from './generated/customers';
import {
  getEventChanges,
  getEventContext,
  getManualDecision,
  listAlertPreviews,
  listEvents,
  submitManualDecision,
} from './generated/events';
import { getRun, listRuns } from './generated/runs';
import {
  listSenderRules,
  saveSenderRule,
  searchSenderCandidates,
} from './generated/senderRules';
import {
  createTask,
  getTaskExecution,
  listTaskExecutions,
  listTasks,
  previewTask,
  taskAction,
  updateTask,
} from './generated/tasks';

/** 页面通过生成客户端访问唯一后端契约；这里仅提供稳定、简短的业务入口。 */
export const api = {
  senderRules: (signal?: AbortSignal) => listSenderRules({ signal }),
  senderCandidates: (keyword: string, offset: number, signal?: AbortSignal) =>
    searchSenderCandidates({ keyword, offset, limit: 25 }, { signal }),
  saveSenderRule: (body: API.SenderRuleSave) => saveSenderRule(body),
  tasks: (signal?: AbortSignal) => listTasks({ signal }),
  createTask: (body: API.TaskConfig) => createTask(body),
  updateTask: (taskId: string, body: API.TaskUpdate) =>
    updateTask({ task_id: taskId }, body),
  taskAction: (taskId: string, action: API.TaskAction['action']) =>
    taskAction({ task_id: taskId }, { action }),
  taskExecutions: (taskId: string, signal?: AbortSignal) =>
    listTaskExecutions({ task_id: taskId }, { signal }),
  taskExecution: (id: string, signal?: AbortSignal) =>
    getTaskExecution({ execution_id: id }, { signal }),
  previewTask: (body: API.TaskConfig, signal?: AbortSignal) =>
    previewTask(body, { signal }),
  customers: (
    offset: number,
    keyword: string,
    segment: API.listCustomerProfilesParams['segment'],
    signal?: AbortSignal,
    limit = 50,
  ) => listCustomerProfiles({ offset, limit, keyword, segment }, { signal }),
  customerGroups: (
    uid: string | undefined,
    review: boolean,
    offset: number,
    signal?: AbortSignal,
  ) => listCustomerGroups({ uid, review, offset, limit: 50 }, { signal }),
  customerSyncs: (signal?: AbortSignal) => listCustomerSyncs({ signal }),
  customerSync: (runId: string, signal?: AbortSignal) =>
    getCustomerSync({ run_id: runId }, { signal }),
  createCustomerSync: (
    body: API.CustomerSyncRequest,
  ): Promise<API.CustomerSyncResponse> => createCustomerSync(body),
  config: (signal?: AbortSignal) => getConfig({ signal }),
  alertPreviews: (uid?: string, signal?: AbortSignal) =>
    listAlertPreviews({ uid }, { signal }),
  events: (
    uid: string,
    stream: string,
    type: string,
    signal?: AbortSignal,
    status = '',
    priority = '',
  ) =>
    listEvents(
      {
        uid,
        stream: stream || undefined,
        type: type || undefined,
        status: status || undefined,
        priority: priority || undefined,
      },
      { signal },
    ),
  eventContext: (
    eventId: string,
    uid: string,
    stream: string,
    signal?: AbortSignal,
    paddingMinutes = 15,
  ) =>
    getEventContext(
      {
        event_id: eventId,
        uid,
        stream: stream || undefined,
        padding_minutes: paddingMinutes,
      },
      { signal },
    ),
  submitManualDecision: (eventId: string, body: API.ManualDecisionRequest) =>
    submitManualDecision({ event_id: eventId }, body),
  getManualDecision: (
    eventId: string,
    operationId: string,
    uid: string,
    stream: string,
    signal?: AbortSignal,
  ) =>
    getManualDecision(
      { event_id: eventId, operation_id: operationId, uid, stream },
      { signal },
    ),
  eventChanges: (
    eventId: string,
    uid: string,
    stream: string,
    offset: number,
    signal?: AbortSignal,
    executionOffset = 0,
    includeDetails = true,
  ) =>
    getEventChanges(
      {
        event_id: eventId,
        uid,
        stream,
        offset,
        execution_offset: executionOffset,
        include_details: includeDetails,
      },
      { signal },
    ),
  scopedRuns: (uid: string, stream: string, signal?: AbortSignal) =>
    listRuns({ uid, stream }, { signal }),
  scopedRun: (
    runId: string,
    uid: string,
    stream: string,
    signal?: AbortSignal,
  ) => getRun({ run_id: runId, uid, stream }, { signal }),
};
