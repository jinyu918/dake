import { ExpandableText } from './ExpandableText';
import { hasImageLink, MessageImages } from './MessageImages';

/** 只收起消息末尾明确标记的引用行，不猜测普通正文或图片内容。 */
function splitReferenceFooter(lines: string[]) {
  let end = lines.length;
  const sources: string[] = [];
  while (end > 0) {
    const match = /^>[ \t]*引用[:：][ \t]*(\S.*)$/.exec(lines[end - 1]);
    if (!match) break;
    sources.unshift(match[1].trim());
    end -= 1;
  }
  return { end, sources };
}

/** 仅识别有成对引号、作者及分隔线的完整回复；末尾来源允许缺省。 */
export function parseQuotedReply(content: string) {
  const lines = content.replace(/\r\n/g, '\n').trim().split('\n');
  const header = /^(["“])([^"“”\n:：]+)[:：][ \t]*$/.exec(lines[0] || '');
  const lastLine = lines.at(-1) || '';
  const footer = /^>[ \t]*引用[:：][ \t]*(\S.*)$/.exec(lastLine);
  const references = splitReferenceFooter(lines);
  const separators = lines.flatMap((line, index) =>
    /^-{3,}[ \t]*$/.test(line) ? [index] : [],
  );
  if (!header || separators.length !== 1) return null;
  // 有来源标记但内容缺失时，不把残缺来源误当成回复正文。
  if (/^>[ \t]*引用[:：]/.test(lastLine) && !footer) return null;
  const separator = separators[0];
  const quoted = lines.slice(1, separator).join('\n').trimEnd();
  const closingQuote = header[1] === '“' ? '”' : '"';
  if (!quoted.endsWith(closingQuote)) return null;
  const author = header[2].trim();
  const quote = quoted.slice(0, -1).trim();
  const body = lines
    .slice(separator + 1, references.end)
    .join('\n')
    .trim();
  if (!author || !quote || !body) return null;
  return { author, quote, body, source: references.sources.join('\n') || null };
}

function parseFooterOnly(content: string) {
  const lines = content.replace(/\r\n/g, '\n').trim().split('\n');
  if (/^["“]/.test(lines[0])) return null;
  const { end, sources } = splitReferenceFooter(lines);
  const body = lines.slice(0, end).join('\n').trim();
  return sources.length && body
    ? { author: '引用', quote: sources.join('\n'), body, source: null }
    : null;
}

function cleanExcerpt(value: string) {
  if (value.startsWith('SYNTHETIC|')) {
    const parts = value.split('|');
    return parts[parts.length - 1] || value;
  }
  return value;
}

export function ChatMessage({
  excerpt,
  content,
}: Pick<API.EvidenceResponse, 'excerpt' | 'content'>) {
  // 只用当前证据版本的完整正文拆分；截断摘要不推断引用边界。
  const reply = content
    ? parseQuotedReply(content) || parseFooterOnly(content)
    : null;
  if (!reply) {
    const imageText = cleanExcerpt(content || excerpt);
    if (hasImageLink(imageText))
      return (
        <div className="chat-bubble">
          <div className="message-image-text">
            <MessageImages text={imageText} />
          </div>
        </div>
      );
    return (
      <div className="chat-bubble">
        <p>{cleanExcerpt(excerpt)}</p>
        {content && content.length > excerpt.length ? (
          <details>
            <summary>展开全文</summary>
            <p>{cleanExcerpt(content)}</p>
          </details>
        ) : null}
      </div>
    );
  }
  return (
    <>
      <div className="chat-bubble">
        {hasImageLink(reply.body) ? (
          <div className="message-image-text">
            <MessageImages text={reply.body} />
          </div>
        ) : (
          <ExpandableText
            text={reply.body}
            lines={4}
            className="chat-reply-text"
          />
        )}
      </div>
      <details className="chat-quote">
        <summary title="展开或收起引用">
          <span className="chat-quote-preview">
            {reply.author}：{reply.quote}
          </span>
        </summary>
        <div className="chat-quote-content">
          {hasImageLink(reply.quote) ? (
            <div className="message-image-text">
              <MessageImages text={reply.quote} />
            </div>
          ) : (
            <p>{reply.quote}</p>
          )}
          {reply.source && reply.source !== reply.quote ? (
            <div className="chat-quote-source">
              引用来源：
              <MessageImages text={reply.source} />
            </div>
          ) : null}
          {reply.quote.includes('[图片]') && !hasImageLink(reply.quote) ? (
            <p>仅记录图片标记，未提供图片内容。</p>
          ) : null}
          <details>
            <summary>查看原始消息</summary>
            <p>{content}</p>
          </details>
        </div>
      </details>
    </>
  );
}
