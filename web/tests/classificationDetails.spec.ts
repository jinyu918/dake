import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClassificationDetails } from '../src/components/ClassificationDetails';
import { EventPane } from '../src/pages/events/EventPane';
import { CyclePanel } from '../src/pages/runs/CyclePanel';
import { fieldDefaults } from './fieldDefaults';

function event(overrides: Partial<API.EventResponse> = {}): API.EventResponse {
  return {
    ...fieldDefaults,
    event_id: 'synthetic-classification-event',
    uid: 'synthetic-customer',
    stream: 'synthetic-stream',
    type: '资源需求',
    types: ['资源需求', '商务需求'],
    type_evidence: {
      资源需求: [
        {
          evidence_ids: ['synthetic:resource-message'],
          quote: '请提高虚构环境配额。',
        },
      ],
      商务需求: [
        {
          evidence_ids: ['synthetic:business-message'],
          quote: '请提供虚构服务报价。',
        },
      ],
    },
    removed_types: {
      商机: [
        {
          evidence_ids: ['synthetic:correction-message'],
          quote: '本次没有采购计划。',
        },
      ],
    },
    classification_evidence_status: '已记录',
    classification_status: '已分类',
    other_reason: null,
    topic_key: '虚构环境配额',
    start_time: '2038-06-01T09:00:00+08:00',
    end_time: '2038-06-01T09:00:00+08:00',
    status: '待处理',
    severity: '未知',
    summary: '虚构环境配额申请和报价。',
    created_on: '2038-06-01T09:00:00+08:00',
    assignees: [],
    evidence: [],
    attributes: {},
    typed_attributes: {
      资源需求: {
        目标总量与增减量: {
          type_label: '资源需求',
          name: '目标总量与增减量',
          value: '2000次/分钟',
          evidence_ids: ['synthetic:resource-message'],
          quote: '请将虚构环境提升到2000次/分钟。',
        },
      },
    },
    extensions: {},
    review_flag: null,
    closed_at: null,
    recovery: null,
    recurrence_of: null,
    schema_version: 1,
    updated_by: [],
    conversations: [],
    platforms: [],
    ...overrides,
  };
}

test('事件列表详情按标签展示各自依据和专属属性证据', () => {
  const markup = renderToStaticMarkup(
    createElement(EventPane, { event: event() }),
  );

  expect(markup).toContain('资源需求');
  expect(markup).toContain('商务需求');
  expect(markup).toContain('请提高虚构环境配额。');
  expect(markup).toContain('请提供虚构服务报价。');
  expect(markup).toContain('2000次/分钟');
  expect(markup).toContain('请将虚构环境提升到2000次/分钟。');
  expect(markup).toContain('消息 resource-message');
  expect(markup).toContain('已撤销标签');
  expect(markup).toContain('本次没有采购计划。');
});

test('待分类事项说明尚无已确认标签', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        type: '待分类',
        types: [],
        type_evidence: {},
        removed_types: {},
        classification_evidence_status: '未记录',
        classification_status: '待分类',
        typed_attributes: {},
      }),
    }),
  );

  expect(markup).toContain('待分类：已有事项依据，类型仍待确认。');
  expect(markup).toContain('尚未确认类型');
  expect(markup).not.toContain('资源需求依据');
});

test('旧记录缺少逐标签依据时不借用事项级证据', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        types: [],
        type_evidence: {},
        typed_attributes: {},
        removed_types: {},
        classification_evidence_status: '未记录',
        evidence: [
          {
            source: 'synthetic',
            message_id: 'legacy',
            send_time: '2038-06-01T09:00:00+08:00',
            excerpt: '事项级依据不能充当标签依据',
            content: null,
            digest: null,
            platform: 'synthetic',
            platform_label: '合成来源',
            conversation_id: '',
            conversation_name: '',
            sender_name: '',
            internal_hint: null,
            sender_role: 'unknown',
            sender_kind: 'unknown',
            origin_event_id: null,
          },
        ],
      }),
    }),
  );
  expect(markup).toContain('历史记录未保存逐标签依据');
  expect(markup).toContain('标签依据未记录');
  expect(markup).not.toContain('事项级依据不能充当标签依据');
});

test('详情展示具体待核对信息与未知属性，不把空值变成已知事实', () => {
  const markup = renderToStaticMarkup(
    createElement(EventPane, {
      event: event({
        review_flag: '合成币种待核实',
        typed_attributes: {
          商务需求: {
            价格与金额口径: { value: null, evidence_ids: [], quote: null },
          },
        },
      }),
    }),
  );
  expect(markup).toContain('合成币种待核实');
  expect(markup).toContain('未知');
  expect(markup).toContain('依据未记录');
  expect(markup).not.toContain('[object Object]');
});

test('分类详情展示自动采用的字段事实、原始依据及未决字段问题', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        field_states: {
          'attribute:资源需求:目标总量与增减量': {
            target: {
              kind: 'attribute',
              type_label: '资源需求',
              name: '目标总量与增减量',
            },
            facts: [
              {
                id: 'synthetic-fact',
                value: '2000次/分钟',
                value_state: 'value',
                stage: '申请',
                scope: '虚构环境',
                business_time: null,
                time_kind: 'unknown',
                intent: 'supplement',
                replaces: [],
                reason: '直接请求',
                evidence: [
                  {
                    source: 'synthetic',
                    message_id: 'resource-message',
                    quote: '请将虚构环境提升到2000次/分钟。',
                  },
                ],
                source: 'automatic',
                evidence_status: 'active',
                status: 'adopted',
                input_ref: null,
              },
            ],
            adopted_ids: ['synthetic-fact'],
            archived: false,
          },
        },
        field_issues: [
          {
            id: 'synthetic-issue',
            field_key: 'attribute:资源需求:目标总量与增减量',
            target: {
              kind: 'attribute',
              type_label: '资源需求',
              name: '目标总量与增减量',
            },
            reason: '存在两个未解决的目标值',
            fact_id: 'synthetic-fact',
            affected_ids: ['synthetic-fact'],
            status: 'open',
            resolution: null,
          },
        ],
      }),
    }),
  );
  expect(markup).toContain('当前采用');
  expect(markup).toContain('2000次/分钟');
  expect(markup).toContain('请将虚构环境提升到2000次/分钟。');
  expect(markup).toContain('存在两个未解决的目标值');
});

test('类型专属属性分别绑定各自标签和依据', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        typed_attributes: {
          资源需求: {
            目标量: {
              value: '合成资源量',
              evidence_ids: ['synthetic:resource'],
              quote: '合成资源量依据',
            },
          },
          商务需求: {
            报价: {
              value: '合成报价口径',
              evidence_ids: ['synthetic:business'],
              quote: '合成报价依据',
            },
          },
        },
      }),
    }),
  );
  const resource = markup
    .split('<h4>资源需求依据</h4>')[1]
    .split('</section>')[0];
  const business = markup
    .split('<h4>商务需求依据</h4>')[1]
    .split('</section>')[0];
  expect(resource).toContain('合成资源量依据');
  expect(resource).not.toContain('合成报价依据');
  expect(business).toContain('合成报价依据');
  expect(business).not.toContain('合成资源量依据');
});

test('其他与六类共存时展示具体理由及其消息依据', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        type: '其他',
        types: ['其他', '资源需求'],
        other_reason: {
          value: '合成专项培训与临时配额不可分验收',
          evidence_ids: ['synthetic:training'],
          quote: '合成培训原话',
        },
      }),
    }),
  );
  expect(markup).toContain('其他类型说明');
  expect(markup).toContain('合成专项培训与临时配额不可分验收');
  expect(markup).toContain('消息 training');
});

test('其他理由和撤销依据缺失时如实标明未记录', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        type: '其他',
        types: ['其他'],
        other_reason: null,
        type_evidence: {},
        typed_attributes: {},
        removed_types: { 商机: null },
      }),
    }),
  );
  expect(markup).toContain('业务理由未记录');
  expect(markup.split('<h4>已撤销标签</h4>')[1]).toContain('依据未记录');
});

test('周期变化条目显示快照的完整标签集合', () => {
  const markup = renderToStaticMarkup(
    createElement(CyclePanel, {
      selected: 0,
      onSelect: () => {},
      cycles: [
        {
          uid: 'synthetic-customer',
          stream: 'synthetic-stream',
          window: {
            start: '2038-06-01T09:00:00+08:00',
            end: '2038-06-01T09:15:00+08:00',
          },
          ingested: 1,
          processed: 1,
          created: ['synthetic-classification-event'],
          updated: [],
          review: [],
          events: [event()],
          watermark: {
            next_start: null,
            last_send_time: null,
            last_message_id: null,
          },
          filter_audit: [],
          recovered_from_history: false,
          counts_complete: true,
        },
      ],
    }),
  );
  expect(markup).toContain('资源需求、商务需求');
});

test('恢复待核实和关系撤销不会显示成已确认结论', () => {
  const proof = {
    source: 'synthetic',
    message_id: 'old-proof',
    digest: 'old-digest',
    conversation_id: 'synthetic-room',
    send_time: '2038-06-01T09:00:00+08:00',
    quote: '合成旧恢复依据',
    evidence_status: 'superseded' as const,
    sender_id: 'synthetic-account',
    sender_role: 'customer' as const,
  };
  const recovery: API.RecoveryConfirmation = {
    confirmed_at: proof.send_time,
    evidence: [proof],
    identity_basis: 'confirmed_account',
    policy_version: 'synthetic-policy',
    review_status: 'pending',
    supporting_evidence: [],
    scope: null,
  };
  const markup = renderToStaticMarkup(
    createElement(EventPane, {
      event: event({
        recovery,
        recurrence_of: {
          event_id: 'previous',
          reason: '原轮次关系',
          recovery,
          evidence: [proof],
          policy_version: 'synthetic-policy',
          review_status: 'pending',
          review_reason: '原恢复依据失效',
          retraction_evidence: [],
          retracted_by: null,
        },
        relations: [
          {
            kind: 'caused_by',
            event_id: 'cause',
            reason: '原因果解释',
            evidence: [proof],
            review_status: 'retracted',
            review_reason: '已明确撤回因果解释',
            retraction_evidence: [],
            origin_event_id: null,
          },
        ],
      }),
    }),
  );
  expect(markup).toContain('恢复依据待核实');
  expect(markup).toContain('前后发生轮次待核对');
  expect(markup).toContain('旧依据已修订，仅供历史核对');
  expect(markup).toContain('已撤销');
  expect(markup).not.toContain('客户已明确确认恢复');
  expect(markup).not.toContain('本次为恢复后再次发生');
});

test('支持方范围核验和独立依据明确区分客户验收', () => {
  const proof = {
    source: 'synthetic',
    message_id: 'support-proof',
    digest: 'support-digest',
    conversation_id: 'synthetic-room',
    send_time: '2038-06-01T09:00:00+08:00',
    quote: '合成测试环境已核验恢复',
    evidence_status: null,
    sender_id: 'synthetic-support',
    sender_role: 'internal' as const,
  };
  const markup = renderToStaticMarkup(
    createElement(EventPane, {
      event: event({
        recovery: {
          confirmed_at: proof.send_time,
          evidence: [proof],
          identity_basis: 'confirmed_support_account',
          policy_version: 'synthetic-policy',
          review_status: 'confirmed',
          supporting_evidence: [proof],
          scope: '合成测试环境',
        },
      }),
    }),
  );
  expect(markup).toContain('支持方已核验指定范围恢复，客户验收未确认');
  expect(markup).toContain('核验范围：合成测试环境');
  expect(markup).toContain('独立确认依据');
  expect(markup).not.toContain('客户已明确确认恢复');
});

test('标签旧依据与仍有效的独立依据不会混作全部失效', () => {
  const markup = renderToStaticMarkup(
    createElement(ClassificationDetails, {
      event: event({
        type_evidence: {
          资源需求: [{ quote: '合成旧依据', evidence_status: 'superseded' }],
          商务需求: [
            { quote: '合成独立依据', evidence_status: 'partially_superseded' },
          ],
        },
      }),
    }),
  );
  expect(markup).toContain('采用结论待复核');
  expect(markup).toContain('仍保留有效的独立依据');
});
