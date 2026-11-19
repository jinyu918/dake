import { ArrowUpRight } from 'lucide-react';
import { Fragment } from 'react';
import { Link } from 'umi';
import { PriorityBadge, StatusBadge } from '@/components/EventBadges';
import { EventType } from '@/components/EventType';
import { formatMetricNumber, productDisplayName } from '@/lib/productLabels';
import { formatTime } from '@/lib/utils';
import { AGG_LABELS } from './schemaLabels';

type ResultsProps = {
  data: API.ProductAnalyticsResponse;
  productNames: Map<string, string>;
  onSelectProduct: (key: string) => void;
};

/** 汇总只展示字段配置要求的统计值，关联事件的完整字段可就地查看。 */
export function AnalyticsResults({
  data,
  productNames,
  onSelectProduct,
}: ResultsProps) {
  const scopeName = (
    metric: API.NumericMetricStat | API.TermDistributionStat,
  ) =>
    metric.scope_type === 'product'
      ? productDisplayName(metric.scope_key, productNames)
      : metric.scope_key;
  const productRows = data.product_summaries.filter(
    (product) => product.event_count > 0,
  );
  return (
    <div className="analytics-results">
      {productRows.length > 0 && (
        <details className="analytics-comparison">
          <summary>按产品查看事件数</summary>
          <table aria-label="各产品事件数">
            <thead>
              <tr>
                <th>产品</th>
                <th>事件</th>
                <th>客户</th>
                <th>P0 / P1</th>
              </tr>
            </thead>
            <tbody>
              {productRows.map((product) => (
                <tr key={product.product_key}>
                  <th scope="row">
                    <button
                      type="button"
                      onClick={() => onSelectProduct(product.product_key)}
                    >
                      {product.name}
                    </button>
                  </th>
                  <td>{product.event_count}</td>
                  <td>{product.customer_count}</td>
                  <td>{product.high_priority_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {data.total_events === 0 ? (
        <p className="field-empty">
          {data.selected_event_type || data.selected_product
            ? '当前筛选条件下没有事件。'
            : '暂无可汇总的事件。'}
        </p>
      ) : (
        <>
          {data.numeric_metrics.length > 0 && (
            <section>
              <h2>数值统计</h2>
              <div className="analytics-metrics">
                {data.numeric_metrics.map((metric) => (
                  <article key={metric.field_id}>
                    <h3>{metric.label}</h3>
                    <p>
                      {scopeName(metric)} · {metric.hit_count} 条事件
                    </p>
                    <dl className="metric-values">
                      {metric.agg_ops.map(
                        (op) =>
                          op !== 'terms' && (
                            <div key={op}>
                              <dt>{AGG_LABELS[op]}</dt>
                              <dd>
                                {formatMetricNumber(
                                  metric[`${op}_value`],
                                  metric.unit,
                                )}
                              </dd>
                            </div>
                          ),
                      )}
                    </dl>
                  </article>
                ))}
              </div>
            </section>
          )}
          {data.term_distributions.length > 0 && (
            <section>
              <h2>字段值分布</h2>
              <div className="analytics-metrics">
                {data.term_distributions.map((dist) => {
                  const max = Math.max(
                    1,
                    ...dist.buckets.map((bucket) => bucket.count),
                  );
                  return (
                    <article key={dist.field_id}>
                      <h3>{dist.label}</h3>
                      <p>
                        {scopeName(dist)} · {dist.hit_count} 次命中
                      </p>
                      <dl className="metric-distribution">
                        {dist.buckets.map((bucket) => (
                          <div key={bucket.value}>
                            <dt>{bucket.value}</dt>
                            <dd>{bucket.count} 条</dd>
                            <div
                              className="analytics-bar-track"
                              aria-hidden="true"
                            >
                              <div
                                className="analytics-bar-fill"
                                style={{
                                  width: `${(100 * bucket.count) / max}%`,
                                }}
                              />
                            </div>
                          </div>
                        ))}
                      </dl>
                    </article>
                  );
                })}
              </div>
            </section>
          )}
          <section>
            <h2>
              关联事件{' '}
              <small>
                {data.events.length < data.total_events
                  ? `最近 ${data.events.length} / ${data.total_events} 条`
                  : `${data.total_events} 条`}
              </small>
            </h2>
            <ul className="analytics-events">
              {data.events.map((event) => (
                <li key={event.event_id}>
                  <div className="analytics-event-heading">
                    <Link
                      to={`/events?uid=${encodeURIComponent(event.uid)}&event=${encodeURIComponent(event.event_id)}`}
                    >
                      {event.summary || event.event_id}{' '}
                      <ArrowUpRight size={14} aria-hidden="true" />
                    </Link>
                    <div>
                      <PriorityBadge priority={event.priority} compact />
                      <StatusBadge status={event.lifecycle_status} compact />
                    </div>
                  </div>
                  <div className="analytics-event-meta">
                    <span>
                      客户 {event.uid}
                      {event.customer_level && ` · ${event.customer_level}`}
                    </span>
                    <time dateTime={event.end_time}>
                      {formatTime(event.end_time)}
                    </time>
                    {event.products.length > 0 && (
                      <span>
                        {event.products
                          .map((key) => productDisplayName(key, productNames))
                          .join('、')}
                      </span>
                    )}
                    {(event.types.length
                      ? event.types
                      : [event.primary_type]
                    ).map((type) => (
                      <EventType key={type} type={type} />
                    ))}
                  </div>
                  {event.prop_evidence.length > 0 && (
                    <details className="analytics-event-fields">
                      <summary>
                        已提取字段（{event.prop_evidence.length}）
                      </summary>
                      <dl>
                        {event.prop_evidence.map((item, i) => (
                          <Fragment key={String(item.field_id || i)}>
                            <dt>{String(item.label || '')}</dt>
                            <dd>
                              {formatMetricNumber(
                                item.normalized_value,
                                typeof item.unit === 'string' ? item.unit : '',
                              )}
                            </dd>
                          </Fragment>
                        ))}
                      </dl>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
