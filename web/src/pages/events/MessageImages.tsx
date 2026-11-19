import { Fragment, useRef, useState } from 'react';

function imageLinks(text: string) {
  const links = [];
  for (const match of text.matchAll(
    /https?:\/\/[^\s<>"'“”【】（）。，；！？]+/gi,
  )) {
    const url = match[0].replace(/[)\],.;]+$/, '');
    try {
      const parsed = new URL(url);
      if (
        !parsed.username &&
        !parsed.password &&
        /\.(?:png|jpe?g|gif|webp|avif|bmp)$/i.test(parsed.pathname)
      )
        links.push({ index: match.index, url });
    } catch {
      // 无效链接按原消息展示。
    }
  }
  return links;
}

export function hasImageLink(text: string) {
  return imageLinks(text).length > 0;
}

function MessageImage({ url }: { url: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  if (failed)
    return (
      <a href={url} target="_blank" rel="noreferrer">
        图片加载失败，查看原链接
      </a>
    );
  return (
    <>
      <button
        type="button"
        className="message-thumbnail"
        aria-label="放大查看图片"
        onClick={() => {
          setOpen(true);
          dialog.current?.showModal();
        }}
      >
        <img
          src={url}
          alt="消息图片"
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      </button>
      <dialog
        ref={dialog}
        className="message-image-preview"
        aria-label="消息图片预览"
        onClose={() => setOpen(false)}
      >
        <button type="button" onClick={() => dialog.current?.close()}>
          关闭预览
        </button>
        {open && (
          <img src={url} referrerPolicy="no-referrer" alt="消息图片大图" />
        )}
      </dialog>
    </>
  );
}

export function MessageImages({ text }: { text: string }) {
  let end = 0;
  const parts = imageLinks(text).map(({ index, url }) => {
    const before = text.slice(end, index);
    end = index + url.length;
    return (
      <Fragment key={index}>
        {before}
        <MessageImage url={url} key={url} />
      </Fragment>
    );
  });
  return (
    <>
      {parts}
      {text.slice(end)}
    </>
  );
}
