"""命令行预览模块化提示词的分块清单、哈希指纹与组装全文。"""

import argparse
import sys

from .composer import compose_system_prompt


def main(argv: list[str] | None = None) -> int:
    """支持列出激活分块或直接打印组装后的完整系统提示词，便于开发审阅与排障。"""
    parser = argparse.ArgumentParser(description="预览大客售后模块化系统提示词")
    parser.add_argument(
        "--mode",
        choices=("attribution", "extraction"),
        default="attribution",
        help="组装模式：attribution（正式增量归属）或 extraction（基础独立抽取）",
    )
    parser.add_argument(
        "--with-context",
        action="store_true",
        help="在归属模式下包含 08_history_context 补充聊天背景分块",
    )
    parser.add_argument("--priority-preview", action="store_true", help="组装独立事项定级预览的证据规则")
    parser.add_argument(
        "--list",
        action="store_true",
        help="仅输出分块拓扑与各分块 SHA-256 指纹，不打印正文",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    assembled = compose_system_prompt(
        mode=args.mode,
        include_history_context=args.with_context,
        priority_preview=args.priority_preview,
    )
    if args.list:
        print(f"bundle_version: {assembled.bundle_version}")
        print(f"mode:           {assembled.mode}")
        print(f"bundle_hash:    {assembled.bundle_hash}")
        print(f"content_hash:   {assembled.content_hash}")
        print("sections:")
        for index, section in enumerate(assembled.sections):
            title = section.title or "(前言/无编号)"
            print(f"  [{index:02d}] {section.section_id:<30} {section.sha256[:12]}  {title}")
        return 0

    print(assembled.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
