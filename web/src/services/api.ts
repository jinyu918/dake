import { getConfig } from './generated/config';
import {
  correctSameRound,
  getEvent,
  getEventChanges,
  getFieldDecision,
  getSameRoundCorrection,
  listEvents,
  submitFieldDecision,
} from './generated/events';
import { createRun, getRun, listRuns } from './generated/runs';

/** 页面通过生成客户端访问唯一后端契约；这里仅提供稳定、简短的业务入口。 */
export const api = {
  correctSameRound: (eventId: string, body: API.SameRoundCorrectionRequest) =>
    correctSameRound({ event_id: eventId }, body),
  sameRoundReceipt: (
    eventId: string,
    operationId: string,
    uid: string,
    stream: string,
  ) =>
    getSameRoundCorrection({
      event_id: eventId,
      operation_id: operationId,
      uid,
      stream,
    }),
  event: (eventId: string, uid: string, stream: string) =>
    getEvent({ event_id: eventId, uid, stream }),
  decideFields: (eventId: string, body: API.FieldDecisionRequest) =>
    submitFieldDecision({ event_id: eventId }, body),
  fieldDecision: (
    eventId: string,
    operationId: string,
    uid: string,
    stream: string,
  ) =>
    getFieldDecision({
      event_id: eventId,
      operation_id: operationId,
      uid,
      stream,
    }),
  config: (signal?: AbortSignal) => getConfig({ signal }),
  events: (uid: string, stream: string, type: string, signal?: AbortSignal) =>
    listEvents(
      { uid, stream: stream || undefined, type: type || undefined },
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
  createRun: (body: API.RunRequest) => createRun(body),
};
