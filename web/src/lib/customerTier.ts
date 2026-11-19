export const STRATEGIC_CUSTOMER = '战略级客户';

export function customerLevelNote(value: unknown): string {
  return typeof value === 'string'
    ? value.replace(
        /\b(L10[\s·]*)(?:超级大客户|超级大客|超大客)/g,
        `$1${STRATEGIC_CUSTOMER}`,
      )
    : '';
}
