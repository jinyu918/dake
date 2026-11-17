export const fieldDefaults = {
  corrected_into: null,
  identity_corrections: [],
  version: 0,
  field_states: {},
  field_issues: [],
  manual_operations: [],
  relations: [],
};

export const revisionDefaults = {
  source: 'automatic' as const,
  manual_operation: null,
};
