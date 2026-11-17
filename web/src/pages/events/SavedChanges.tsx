import { useState } from 'react';
import { formatTime } from '@/lib/utils';
import { VersionDifference } from './ChangeEvidence';
import { minuteTime, savedChanges } from './changeTimeline';
import { ExpandableText } from './ExpandableText';

/** 每轮先展示触发与增量变化，完整摘要仅在对比中展开一次。 */
export function SavedChanges({ data }: { data: API.EventChangesResponse }) {
  const [oldestFirst, setOldestFirst] = useState(false);
  const saved = savedChanges(data);
  const rows = oldestFirst ? [...saved].reverse() : saved;
  const days = [...new Set(rows.map((row) => row.day))];
  return (
    <section className="saved-changes" aria-label="已保存的事件变化">
      <h3>已保存的事件变化 · {rows.length} 条</h3>
      <p className="change-muted">
        按触发消息分日展示，使用上海时间，精确到分钟。
      </p>
      <button
        type="button"
        className="expandable-toggle"
        onClick={() => setOldestFirst((value) => !value)}
      >
        {oldestFirst
          ? '当前从早到晚 · 切换最新在前'
          : '当前最新在前 · 从头查看流转'}
      </button>
      {!rows.length ? <p>暂无已保存的事件变化。</p> : null}
      {days.map((day) => (
        <section className="change-day" key={day} aria-label={day}>
          <h4>{day}</h4>
          <ol className="saved-change-list">
            {rows
              .filter((row) => row.day === day)
              .map((row) => (
                <li
                  key={row.index}
                  className="saved-change"
                  data-revision-index={row.index}
                >
                  <div className="saved-change-heading">
                    {row.time ? (
                      <time dateTime={row.time}>{minuteTime(row.time)}</time>
                    ) : (
                      <span>时间未记录</span>
                    )}
                    <strong>{row.title}</strong>
                    <small>版本 {row.revision.revision_no ?? '未记录'}</small>
                  </div>
                  <div className="change-delta">
                    <h5>本轮变化</h5>
                    {row.evidenceOnly ? (
                      <p className="evidence-only-label">
                        仅补证据，不表示新的业务进展。
                      </p>
                    ) : null}
                    {row.descriptions.length ? (
                      row.descriptions.map((text) => <p key={text}>{text}</p>)
                    ) : (
                      <p>变化说明未记录。</p>
                    )}
                  </div>
                  {row.previous && row.revision.snapshot ? (
                    <p className="change-muted">
                      {row.previous.status === row.revision.snapshot.status
                        ? '状态未变化'
                        : `状态：${row.previous.status} → ${row.revision.snapshot.status}`}
                    </p>
                  ) : null}
                  <details className="saved-change-details">
                      <summary>触发消息 · {row.triggers.length} 条</summary>
                      {row.triggers.length ? (
                        row.triggers.map((ref) => (
                          <blockquote
                            className="change-proof"
                            key={JSON.stringify([
                              ref.source,
                              ref.message_id,
                              ref.digest,
                            ])}
                          >
                            <ExpandableText
                              lines={4}
                              text={ref.quote || ''}
                              empty="触发消息正文未记录"
                            />
                            <small>
                              {ref.send_time
                                ? formatTime(ref.send_time)
                                : '消息时间未记录'}{' '}
                              · 消息 {ref.message_id || '未记录'}
                            </small>
                          </blockquote>
                        ))
                      ) : (
                        <p>触发消息未记录。</p>
                      )}
                  </details>
                  <p className="change-muted">
                    版本 {row.revision.revision_no ?? '未记录'} · 记录时间{' '}
                    {row.revision.recorded_at
                      ? formatTime(row.revision.recorded_at)
                      : '未记录'}
                  </p>
                  <details className="saved-change-details">
                    <summary>完整摘要对比</summary>
                    <dl className="change-diff">
                      <dt>变化前摘要</dt>
                      <dd>
                        {row.revision.action === 'created'
                          ? '首次创建，无前版'
                          : (row.previous?.summary ?? '未记录')}
                      </dd>
                      <dt>变化后摘要</dt>
                      <dd>{row.revision.snapshot?.summary ?? '未记录'}</dd>
                    </dl>
                  </details>
                  {row.previous &&
                  row.revision.snapshot &&
                  row.previous.summary === row.revision.snapshot.summary ? (
                    <p className="change-muted">摘要未变化</p>
                  ) : null}
                  <details className="saved-change-details">
                    <summary>核对记录 · 保存时间与消息标识</summary>
                    <p className="change-muted">
                      消息指纹用于核对当时引用的内容，不是事件版本号。相同消息
                      ID 的指纹不同，表示保存的内容版本不同。
                    </p>
                    {row.triggers.map((ref) => (
                      <dl
                        className="change-diff"
                        key={JSON.stringify([
                          ref.source,
                          ref.message_id,
                          ref.digest,
                        ])}
                      >
                        <dt>消息标识</dt>
                        <dd>{ref.message_id || '未记录'}</dd>
                        <dt>来源 / 群</dt>
                        <dd>
                          {ref.source || '未记录'} /{' '}
                          {ref.conversation_id || '未记录'}
                        </dd>
                        <dt>内容指纹</dt>
                        <dd>
                          <code>{ref.digest || '未记录'}</code>
                        </dd>
                      </dl>
                    ))}
                    <VersionDifference
                      revision={row.revision}
                      previous={row.previous}
                      showSummary={false}
                      showEvidence={false}
                    />
                  </details>
                </li>
              ))}
          </ol>
        </section>
      ))}
    </section>
  );
}
